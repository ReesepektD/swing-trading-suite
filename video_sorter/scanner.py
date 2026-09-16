"""
Directory scanning — walk a tree, find video files, optionally probe them.

Probing shells out to ffprobe once per file, which dominates the cost of a
scan on a large library. Two things keep it bearable: a thread pool (ffprobe
is a subprocess, so the GIL is irrelevant) and an on-disk cache keyed by
path + size + mtime, so a rescan of an unchanged library is nearly free.
"""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Iterable, Iterator

from .metadata import VIDEO_EXTENSIONS, VideoFile, probe

DEFAULT_WORKERS = 8
DEFAULT_CACHE_PATH = Path.home() / ".cache" / "video_sorter" / "probe_cache.json"

# Directories that never hold a user's video library but are expensive to walk.
SKIP_DIRECTORIES = frozenset({
    ".git", ".svn", ".hg", "node_modules", "__pycache__", ".venv", "venv",
    ".Trash", "$RECYCLE.BIN", "System Volume Information",
})

_CACHED_FIELDS = ("duration", "width", "height", "codec", "bitrate")


class ProbeCache:
    """Persistent ffprobe result cache. Missing/corrupt cache files are ignored."""

    def __init__(self, path: Path | str | None = DEFAULT_CACHE_PATH):
        self.path = Path(path) if path is not None else None
        self._entries: dict[str, dict] = {}
        self._dirty = False
        self.load()

    @staticmethod
    def key(video: VideoFile) -> str:
        return f"{video.path}|{video.size}|{int(video.modified)}"

    def load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError):
            return
        if isinstance(raw, dict):
            self._entries = {k: v for k, v in raw.items() if isinstance(v, dict)}

    def get(self, video: VideoFile) -> bool:
        """Apply a cached probe to ``video``. Returns True on a cache hit."""
        entry = self._entries.get(self.key(video))
        if entry is None:
            return False
        for field_name in _CACHED_FIELDS:
            setattr(video, field_name, entry.get(field_name))
        video.probed = True
        video.probe_error = None
        return True

    def put(self, video: VideoFile) -> None:
        if not video.probed:
            return
        self._entries[self.key(video)] = {
            field_name: getattr(video, field_name) for field_name in _CACHED_FIELDS
        }
        self._dirty = True

    def save(self) -> None:
        if self.path is None or not self._dirty:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self._entries))
            self._dirty = False
        except OSError:
            # A cache we cannot write is a performance loss, not an error.
            pass


def iter_video_files(
    root: Path | str,
    *,
    recursive: bool = True,
    extensions: Iterable[str] | None = None,
    follow_symlinks: bool = False,
) -> Iterator[Path]:
    """Yield paths of video files under ``root``.

    ``extensions`` entries may be given with or without a leading dot.
    """
    root = Path(root).expanduser()
    if not root.is_dir():
        raise NotADirectoryError(f"Not a directory: {root}")

    allowed = _normalise_extensions(extensions)

    for dirpath, dirnames, filenames in os.walk(root, followlinks=follow_symlinks):
        # Prune in place so os.walk never descends into them.
        dirnames[:] = [
            d for d in sorted(dirnames)
            if d not in SKIP_DIRECTORIES and not d.startswith(".")
        ]
        if not recursive:
            dirnames[:] = []
        for filename in sorted(filenames):
            if filename.startswith("."):
                continue
            if os.path.splitext(filename)[1].lower() not in allowed:
                continue
            yield Path(dirpath) / filename


def scan(
    root: Path | str,
    *,
    recursive: bool = True,
    extensions: Iterable[str] | None = None,
    follow_symlinks: bool = False,
    do_probe: bool = True,
    workers: int = DEFAULT_WORKERS,
    cache: ProbeCache | None = None,
    limit: int | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> list[VideoFile]:
    """Scan ``root`` and return the video files found.

    ``on_progress(done, total)`` is called as probes complete, for the CLI's
    progress line. Files that vanish mid-scan are skipped rather than fatal.
    """
    root = Path(root).expanduser().resolve()
    videos: list[VideoFile] = []

    for path in iter_video_files(
        root,
        recursive=recursive,
        extensions=extensions,
        follow_symlinks=follow_symlinks,
    ):
        try:
            video = VideoFile.from_path(path)
        except OSError:
            continue  # Deleted or unreadable between walk and stat.
        video.relative = str(path.relative_to(root))
        videos.append(video)
        if limit is not None and len(videos) >= limit:
            break

    if do_probe and videos:
        _probe_all(videos, workers=workers, cache=cache, on_progress=on_progress)

    return videos


def _probe_all(
    videos: list[VideoFile],
    *,
    workers: int,
    cache: ProbeCache | None,
    on_progress: Callable[[int, int], None] | None,
) -> None:
    pending = [v for v in videos if not (cache and cache.get(v))]
    total = len(videos)
    done = total - len(pending)
    if on_progress:
        on_progress(done, total)

    if pending:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            for video in pool.map(probe, pending):
                if cache:
                    cache.put(video)
                done += 1
                if on_progress:
                    on_progress(done, total)

    if cache:
        cache.save()


def _normalise_extensions(extensions: Iterable[str] | None) -> frozenset[str]:
    if extensions is None:
        return VIDEO_EXTENSIONS
    normalised = {
        "." + ext.strip().lower().lstrip(".")
        for ext in extensions
        if ext and ext.strip().strip(".")
    }
    return frozenset(normalised) or VIDEO_EXTENSIONS
