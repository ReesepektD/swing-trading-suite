"""
Planning and applying file moves.

The organiser is deliberately split in two. :func:`plan_moves` is pure — it
touches no files and hands back exactly what would happen, so the UI can show
the whole plan before anything is committed. :func:`apply_plan` performs it and
writes a journal so :func:`undo` can put everything back.

Rules that never bend:

* Nothing is overwritten. A destination that already exists gets a ``(2)``
  suffix, and two sources that would collide are suffixed apart in the plan.
* A move onto its own current path is a no-op and is dropped from the plan.
* Every operation lands in the journal *before* the next one starts, so an
  interrupted run is still fully undoable.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

from .metadata import UNKNOWN, VideoFile

MOVE = "move"
COPY = "copy"
ACTIONS = (MOVE, COPY)

# Rule name -> (bucket function, human label).
RULES: dict[str, tuple[Callable[[VideoFile], str], str]] = {
    "extension": (lambda v: v.extension.upper() or UNKNOWN, "By format"),
    "resolution": (lambda v: v.resolution_label, "By resolution"),
    "size": (lambda v: v.size_label, "By size"),
    "duration": (lambda v: v.duration_label, "By length"),
    "year": (lambda v: _stamp(v.modified, "%Y"), "By year"),
    "month": (lambda v: _stamp(v.modified, "%Y/%Y-%m"), "By year and month"),
    "codec": (lambda v: (v.codec or UNKNOWN).lower(), "By codec"),
    "alpha": (lambda v: _initial(v.name), "Alphabetical"),
    "folder": (lambda v: v.folder or UNKNOWN, "Keep source folder"),
    "flat": (lambda v: "", "No subfolders"),
}

DEFAULT_RULE = "resolution"

# Rules whose bucket comes from ffprobe rather than os.stat. Planning one of
# these on unprobed files would silently sweep the whole library into
# "Unknown", so callers must ensure the files are probed first.
PROBE_DEPENDENT_RULES = frozenset({"resolution", "duration", "codec"})


@dataclass
class Move:
    """One planned relocation."""

    source: Path
    destination: Path
    bucket: str
    renamed: bool = False  # True when a name collision forced a suffix.

    def to_dict(self) -> dict:
        return {
            "source": str(self.source),
            "destination": str(self.destination),
            "bucket": self.bucket or "/",
            "name": self.source.name,
            "new_name": self.destination.name,
            "renamed": self.renamed,
        }


@dataclass
class Plan:
    """A complete, reviewable set of moves."""

    action: str
    destination_root: Path
    rule: str
    moves: list[Move] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)

    @property
    def buckets(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for move in self.moves:
            label = move.bucket or "/"
            counts[label] = counts.get(label, 0) + 1
        return dict(sorted(counts.items()))

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "destination_root": str(self.destination_root),
            "rule": self.rule,
            "rule_label": RULES[self.rule][1] if self.rule in RULES else self.rule,
            "moves": [m.to_dict() for m in self.moves],
            "skipped": self.skipped,
            "buckets": self.buckets,
            "count": len(self.moves),
        }


def plan_moves(
    files: Sequence[VideoFile],
    destination_root: Path | str,
    rule: str = DEFAULT_RULE,
    *,
    action: str = MOVE,
) -> Plan:
    """Build the set of moves that would sort ``files`` under ``destination_root``.

    Touches the filesystem only to test whether a destination already exists.
    """
    if rule not in RULES:
        raise ValueError(
            f"Unknown rule {rule!r}. Valid rules: {', '.join(sorted(RULES))}"
        )
    if action not in ACTIONS:
        raise ValueError(f"Unknown action {action!r}. Valid actions: {', '.join(ACTIONS)}")

    bucket_of, _ = RULES[rule]
    root = Path(destination_root).expanduser().resolve()
    plan = Plan(action=action, destination_root=root, rule=rule)

    # Destinations claimed earlier in this plan, so two sources heading for the
    # same name are separated instead of one silently clobbering the other.
    claimed: set[str] = set()

    for video in files:
        try:
            bucket = _safe_component(bucket_of(video))
        except Exception as exc:  # A bad rule must not abort the whole plan.
            plan.skipped.append({"path": str(video.path), "reason": f"rule failed: {exc}"})
            continue

        target_dir = root / bucket if bucket else root
        destination = target_dir / video.name

        if destination == video.path:
            plan.skipped.append({"path": str(video.path), "reason": "already in place"})
            continue

        destination, renamed = _deconflict(destination, claimed)
        claimed.add(_claim_key(destination))
        plan.moves.append(
            Move(
                source=video.path,
                destination=destination,
                bucket=bucket,
                renamed=renamed,
            )
        )

    return plan


def apply_plan(
    plan: Plan,
    *,
    dry_run: bool = True,
    journal_path: Path | str | None = None,
    on_progress: Callable[[int, int, Move], None] | None = None,
) -> dict:
    """Execute ``plan``. Defaults to a dry run — pass ``dry_run=False`` to commit.

    Returns a result dict with per-file outcomes. Individual failures are
    recorded and the run continues; one unwritable file does not strand the
    rest of the library half-sorted.
    """
    results = {
        "action": plan.action,
        "dry_run": dry_run,
        "succeeded": [],
        "failed": [],
        "journal": None,
    }
    if dry_run:
        results["succeeded"] = [m.to_dict() for m in plan.moves]
        return results

    journal = Journal(journal_path or default_journal_path())
    journal.begin(plan)

    total = len(plan.moves)
    for index, move in enumerate(plan.moves, start=1):
        try:
            _execute(move, plan.action)
        except OSError as exc:
            results["failed"].append({**move.to_dict(), "error": str(exc)})
        else:
            # Recorded immediately: an interrupted run stays undoable.
            journal.record(move, plan.action)
            results["succeeded"].append(move.to_dict())
        if on_progress:
            on_progress(index, total, move)

    journal.close()
    results["journal"] = str(journal.path)
    return results


def _execute(move: Move, action: str) -> None:
    move.destination.parent.mkdir(parents=True, exist_ok=True)
    # Re-check at the last moment: the plan may have been built minutes ago.
    if move.destination.exists():
        raise OSError(f"destination appeared before the move: {move.destination}")
    if action == COPY:
        shutil.copy2(move.source, move.destination)
    else:
        # shutil.move handles the cross-filesystem case that os.rename cannot.
        shutil.move(str(move.source), str(move.destination))


# ---------------------------------------------------------------------------
# Journal / undo
# ---------------------------------------------------------------------------

JOURNAL_DIR = Path.home() / ".local" / "share" / "video_sorter" / "journals"


def default_journal_path() -> Path:
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    return JOURNAL_DIR / f"sort-{stamp}.json"


class Journal:
    """Append-only record of what was moved, so it can be reversed."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._payload: dict = {}

    def begin(self, plan: Plan) -> None:
        self._payload = {
            "started": dt.datetime.now().isoformat(timespec="seconds"),
            "action": plan.action,
            "rule": plan.rule,
            "destination_root": str(plan.destination_root),
            "entries": [],
        }
        self._flush()

    def record(self, move: Move, action: str) -> None:
        self._payload["entries"].append(
            {"action": action, "source": str(move.source), "destination": str(move.destination)}
        )
        self._flush()

    def close(self) -> None:
        self._payload["finished"] = dt.datetime.now().isoformat(timespec="seconds")
        self._flush()

    def _flush(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._payload, indent=2))


def list_journals(directory: Path | str = JOURNAL_DIR) -> list[Path]:
    directory = Path(directory)
    if not directory.is_dir():
        return []
    return sorted(directory.glob("sort-*.json"), reverse=True)


def undo(journal_path: Path | str, *, dry_run: bool = True) -> dict:
    """Reverse the moves recorded in a journal, most recent first.

    Copies are reported but never deleted: undoing a copy means removing a
    file, and this tool does not delete anything it did not create a duplicate
    of on purpose. Removing those is left to the user.
    """
    journal_path = Path(journal_path)
    payload = json.loads(journal_path.read_text())
    results = {
        "restored": [], "failed": [], "left_in_place": [],
        "removed_dirs": [], "dry_run": dry_run,
    }

    for entry in reversed(payload.get("entries", [])):
        source = Path(entry["source"])
        destination = Path(entry["destination"])

        if entry.get("action") == COPY:
            results["left_in_place"].append(str(destination))
            continue
        if not destination.exists():
            results["failed"].append(
                {"path": str(destination), "error": "file is no longer where it was moved to"}
            )
            continue
        if source.exists():
            results["failed"].append(
                {"path": str(source), "error": "something else now occupies the original path"}
            )
            continue
        if dry_run:
            results["restored"].append({"from": str(destination), "to": str(source)})
            continue
        try:
            source.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(destination), str(source))
        except OSError as exc:
            results["failed"].append({"path": str(destination), "error": str(exc)})
        else:
            results["restored"].append({"from": str(destination), "to": str(source)})

    if not dry_run and results["restored"]:
        results["removed_dirs"] = _prune_empty_dirs(payload.get("destination_root"))

    return results


def _prune_empty_dirs(destination_root: str | None) -> list[str]:
    """Remove the bucket folders a sort created, once they are empty again.

    Only ever calls rmdir, which refuses a non-empty directory — so a folder
    holding anything at all, including files this tool never touched, stays.
    """
    if not destination_root:
        return []
    root = Path(destination_root)
    if not root.is_dir():
        return []

    removed = []
    # Bottom-up, so a nested bucket like 2024/2024-03 collapses fully.
    for dirpath, _dirnames, _filenames in os.walk(root, topdown=False):
        directory = Path(dirpath)
        try:
            directory.rmdir()
        except OSError:
            continue  # Not empty, or not ours to remove.
        removed.append(str(directory))
    return removed


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _deconflict(destination: Path, claimed: set[str]) -> tuple[Path, bool]:
    """Return a destination that neither exists on disk nor is already claimed."""
    if _claim_key(destination) not in claimed and not destination.exists():
        return destination, False

    stem, suffix = destination.stem, destination.suffix
    counter = 2
    while True:
        candidate = destination.with_name(f"{stem} ({counter}){suffix}")
        if _claim_key(candidate) not in claimed and not candidate.exists():
            return candidate, True
        counter += 1


# Windows (NTFS) and macOS (APFS/HFS+) are case-insensitive by default, so
# "Clip.mp4" and "clip.mp4" are one file there and planning both into the same
# folder would overwrite one with the other. Linux filesystems are
# case-sensitive, where folding the two together would instead rename a file
# that had no conflict. os.path.normcase is no help here: it only folds case on
# Windows, which would leave macOS silently overwriting.
CASE_INSENSITIVE_FS = sys.platform in ("win32", "cygwin", "darwin")


def _claim_key(path: Path) -> str:
    """Key for comparing claimed destinations, folded to match the filesystem."""
    text = str(path)
    return text.lower() if CASE_INSENSITIVE_FS else text


_UNSAFE = str.maketrans({c: "_" for c in '<>:"\\|?*'})


def _safe_component(bucket: str) -> str:
    """Make a rule's output safe to use as a path component.

    Forward slashes survive (the ``month`` rule nests with them); everything
    else that could escape the destination root or upset a filesystem does not.
    """
    cleaned = (bucket or "").strip().translate(_UNSAFE)
    parts = [p.strip(" .") for p in cleaned.split("/")]
    parts = [p for p in parts if p and p not in (".", "..")]
    return "/".join(parts)


def _stamp(timestamp: float, fmt: str) -> str:
    return dt.datetime.fromtimestamp(timestamp).strftime(fmt)


def _initial(name: str) -> str:
    first = name.strip()[:1].upper()
    return first if first.isalpha() else "#"
