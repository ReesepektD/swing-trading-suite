"""
Video file metadata — the record we keep for every file we find.

Metadata comes from two places:

1. ``os.stat`` — always available: size, mtime, ctime.
2. ``ffprobe`` — optional: duration, resolution, codec, bitrate.

ffprobe is not required. When it is missing every probed field stays ``None``
and the app degrades to sorting by the stat fields only.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# Containers we treat as video. Deliberately generous — a file that turns out
# not to be video just fails the probe and still sorts by name/size/date.
VIDEO_EXTENSIONS = frozenset({
    ".3gp", ".asf", ".avi", ".divx", ".f4v", ".flv", ".m2ts", ".m2v", ".m4v",
    ".mkv", ".mov", ".mp4", ".mpeg", ".mpg", ".mts", ".mxf", ".ogv", ".rm",
    ".rmvb", ".ts", ".vob", ".webm", ".wmv",
})

# Short edge (in pixels) -> label. Using the short edge rather than the height
# keeps vertical phone video honest: 1080x1920 is still "1080p".
_RESOLUTION_TIERS = (
    (4320, "8K"),
    (2160, "4K"),
    (1440, "1440p"),
    (1080, "1080p"),
    (720, "720p"),
    (480, "480p"),
    (1, "SD"),
)

# Upper bound (bytes, exclusive) -> label.
_SIZE_TIERS = (
    (100 * 1024 ** 2, "Tiny"),
    (1024 ** 3, "Small"),
    (5 * 1024 ** 3, "Medium"),
    (20 * 1024 ** 3, "Large"),
    (float("inf"), "Huge"),
)

# Upper bound (seconds, exclusive) -> label.
_DURATION_TIERS = (
    (60, "Clips"),
    (10 * 60, "Shorts"),
    (45 * 60, "Episodes"),
    (float("inf"), "Features"),
)

UNKNOWN = "Unknown"


def ffprobe_path() -> str | None:
    """Absolute path to ffprobe, or None when it is not installed."""
    return shutil.which("ffprobe")


@dataclass
class VideoFile:
    """One video file on disk plus whatever we managed to learn about it."""

    path: Path
    size: int
    modified: float
    created: float
    duration: float | None = None
    width: int | None = None
    height: int | None = None
    codec: str | None = None
    bitrate: int | None = None
    probed: bool = False
    probe_error: str | None = None
    # Populated by the scanner so the UI can show paths relative to the root.
    relative: str = field(default="")

    # -- identity ---------------------------------------------------------

    @classmethod
    def from_path(cls, path: Path) -> "VideoFile":
        stat = path.stat()
        return cls(
            path=path,
            size=stat.st_size,
            modified=stat.st_mtime,
            # st_birthtime where the platform has it (macOS/BSD), else ctime.
            created=getattr(stat, "st_birthtime", stat.st_ctime),
        )

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def stem(self) -> str:
        return self.path.stem

    @property
    def extension(self) -> str:
        """Lowercase extension without the dot, e.g. ``mp4``."""
        return self.path.suffix.lower().lstrip(".")

    @property
    def folder(self) -> str:
        return self.path.parent.name

    @property
    def pixels(self) -> int | None:
        if self.width is None or self.height is None:
            return None
        return self.width * self.height

    # -- bucket labels (also used as destination folder names) ------------

    @property
    def resolution_label(self) -> str:
        if not self.width or not self.height:
            return UNKNOWN
        short_edge = min(self.width, self.height)
        for threshold, label in _RESOLUTION_TIERS:
            if short_edge >= threshold:
                return label
        return UNKNOWN

    @property
    def size_label(self) -> str:
        for upper, label in _SIZE_TIERS:
            if self.size < upper:
                return label
        return UNKNOWN

    @property
    def duration_label(self) -> str:
        if self.duration is None:
            return UNKNOWN
        for upper, label in _DURATION_TIERS:
            if self.duration < upper:
                return label
        return UNKNOWN

    # -- display ----------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "path": str(self.path),
            "relative": self.relative or self.name,
            "name": self.name,
            "folder": self.folder,
            "extension": self.extension,
            "size": self.size,
            "size_label": self.size_label,
            "size_human": human_size(self.size),
            "modified": self.modified,
            "created": self.created,
            "duration": self.duration,
            "duration_human": human_duration(self.duration),
            "duration_label": self.duration_label,
            "width": self.width,
            "height": self.height,
            "pixels": self.pixels,
            "resolution": (
                f"{self.width}x{self.height}" if self.width and self.height else UNKNOWN
            ),
            "resolution_label": self.resolution_label,
            "codec": self.codec or UNKNOWN,
            "bitrate": self.bitrate,
            "probed": self.probed,
            "probe_error": self.probe_error,
        }


def human_size(num_bytes: int) -> str:
    """1536 -> '1.5 KB'."""
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(value) < 1024.0 or unit == "TB":
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} TB"


def human_duration(seconds: float | None) -> str:
    """3725 -> '1:02:05'. None -> '—'."""
    if seconds is None:
        return "—"
    total = int(round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def probe(video: VideoFile, *, timeout: float = 30.0) -> VideoFile:
    """Fill in duration/resolution/codec/bitrate via ffprobe, in place.

    Never raises: a missing ffprobe, a timeout, or an unreadable file all land
    in ``probe_error`` and leave the stat-derived fields untouched.
    """
    exe = ffprobe_path()
    if exe is None:
        video.probe_error = "ffprobe not installed"
        return video

    cmd = [
        exe, "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", str(video.path),
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired:
        video.probe_error = "ffprobe timed out"
        return video
    except OSError as exc:
        video.probe_error = f"ffprobe failed: {exc}"
        return video

    if result.returncode != 0:
        video.probe_error = f"ffprobe exit {result.returncode}"
        return video

    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        video.probe_error = "ffprobe returned invalid JSON"
        return video

    return apply_probe_payload(video, payload)


def apply_probe_payload(video: VideoFile, payload: dict) -> VideoFile:
    """Merge a parsed ffprobe document into ``video``.

    Split out from :func:`probe` so it can be tested without ffprobe present.
    """
    container = payload.get("format") or {}
    streams = payload.get("streams") or []
    video_stream = next(
        (s for s in streams if s.get("codec_type") == "video"), None
    )

    duration = _as_float(container.get("duration"))
    if duration is None and video_stream is not None:
        duration = _as_float(video_stream.get("duration"))
    video.duration = duration
    video.bitrate = _as_int(container.get("bit_rate"))

    if video_stream is not None:
        video.width = _as_int(video_stream.get("width"))
        video.height = _as_int(video_stream.get("height"))
        video.codec = video_stream.get("codec_name")
        if video.bitrate is None:
            video.bitrate = _as_int(video_stream.get("bit_rate"))
        # Rotated recordings report storage dimensions; swap to display order.
        if _rotation_is_quarter_turn(video_stream):
            video.width, video.height = video.height, video.width

    video.probed = True
    video.probe_error = None
    return video


def _rotation_is_quarter_turn(stream: dict) -> bool:
    rotation = stream.get("tags", {}).get("rotate")
    if rotation is None:
        for side_data in stream.get("side_data_list") or []:
            if "rotation" in side_data:
                rotation = side_data["rotation"]
                break
    value = _as_float(rotation)
    if value is None:
        return False
    return int(abs(value)) % 180 == 90


def _as_int(value) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _as_float(value) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    # ffprobe emits "N/A" as NaN for streams it cannot measure.
    return result if result == result else None


def is_video(path: Path | str) -> bool:
    return os.path.splitext(str(path))[1].lower() in VIDEO_EXTENSIONS
