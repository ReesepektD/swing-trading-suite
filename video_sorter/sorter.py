"""
Sorting and filtering.

Files whose probed fields are unknown (no ffprobe, or a probe that failed)
sort to the end in *both* directions — a file with no duration is not
"shortest", it is simply unmeasured, and burying it under a descending sort
would be misleading.
"""

from __future__ import annotations

import re
from typing import Callable, Iterable, Sequence

from .metadata import UNKNOWN, VideoFile

# Sort key -> (extractor, human label). An extractor returning None marks the
# value as unknown, which parks the file at the end of the list.
SORT_KEYS: dict[str, tuple[Callable[[VideoFile], object], str]] = {
    "name": (lambda v: _natural_key(v.name), "Name"),
    "path": (lambda v: _natural_key(str(v.path)), "Path"),
    "folder": (lambda v: (_natural_key(v.folder), _natural_key(v.name)), "Folder"),
    "extension": (lambda v: (v.extension, _natural_key(v.name)), "Format"),
    "size": (lambda v: v.size, "Size"),
    "modified": (lambda v: v.modified, "Modified"),
    "created": (lambda v: v.created, "Created"),
    "duration": (lambda v: v.duration, "Duration"),
    "resolution": (lambda v: v.pixels, "Resolution"),
    "bitrate": (lambda v: v.bitrate, "Bitrate"),
    "codec": (lambda v: v.codec, "Codec"),
}

DEFAULT_SORT = "name"


def sort_files(
    files: Sequence[VideoFile], key: str = DEFAULT_SORT, *, reverse: bool = False
) -> list[VideoFile]:
    """Return ``files`` sorted by ``key``, unknowns last."""
    if key not in SORT_KEYS:
        raise ValueError(
            f"Unknown sort key {key!r}. Valid keys: {', '.join(sorted(SORT_KEYS))}"
        )
    extract, _ = SORT_KEYS[key]

    known, unknown = [], []
    for video in files:
        (unknown if extract(video) is None else known).append(video)

    known.sort(key=extract, reverse=reverse)
    unknown.sort(key=lambda v: _natural_key(v.name))
    return known + unknown


def filter_files(
    files: Iterable[VideoFile],
    *,
    query: str | None = None,
    extensions: Iterable[str] | None = None,
    resolutions: Iterable[str] | None = None,
    min_size: int | None = None,
    max_size: int | None = None,
    min_duration: float | None = None,
    max_duration: float | None = None,
) -> list[VideoFile]:
    """Filter by any combination of criteria. All criteria are ANDed.

    A duration bound excludes unprobed files — there is no way to know whether
    they qualify, and silently keeping them would corrupt a move plan.
    """
    needle = (query or "").strip().lower()
    ext_set = {e.strip().lower().lstrip(".") for e in extensions} if extensions else None
    res_set = {r.strip() for r in resolutions} if resolutions else None

    result = []
    for video in files:
        if needle and needle not in str(video.path).lower():
            continue
        if ext_set and video.extension not in ext_set:
            continue
        if res_set and video.resolution_label not in res_set:
            continue
        if min_size is not None and video.size < min_size:
            continue
        if max_size is not None and video.size > max_size:
            continue
        if min_duration is not None:
            if video.duration is None or video.duration < min_duration:
                continue
        if max_duration is not None:
            if video.duration is None or video.duration > max_duration:
                continue
        result.append(video)
    return result


def summarise(files: Sequence[VideoFile]) -> dict:
    """Aggregate stats for the fleet readout in the UI."""
    durations = [v.duration for v in files if v.duration is not None]
    extensions: dict[str, int] = {}
    resolutions: dict[str, int] = {}
    for video in files:
        extensions[video.extension] = extensions.get(video.extension, 0) + 1
        resolutions[video.resolution_label] = (
            resolutions.get(video.resolution_label, 0) + 1
        )
    return {
        "count": len(files),
        "total_size": sum(v.size for v in files),
        "total_duration": sum(durations) if durations else 0.0,
        "probed": sum(1 for v in files if v.probed),
        "unknown_duration": len(files) - len(durations),
        "extensions": dict(sorted(extensions.items())),
        "resolutions": dict(
            sorted(
                resolutions.items(),
                # Unknown last, otherwise most common first.
                key=lambda kv: (kv[0] == UNKNOWN, -kv[1], kv[0]),
            )
        ),
    }


_NUMBER_RUN = re.compile(r"(\d+)")


def _natural_key(text: str) -> tuple:
    """Sort key where 'Ep 2' precedes 'Ep 10'.

    Digit runs become ints; everything else is lowercased. The leading type
    tag keeps int and str chunks from being compared against each other.
    """
    return tuple(
        (0, int(chunk), "") if chunk.isdigit() else (1, 0, chunk.lower())
        for chunk in _NUMBER_RUN.split(text)
        if chunk
    )
