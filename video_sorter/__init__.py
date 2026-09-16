"""
Stellar Sorter — sort a library of video files by what is actually in them.

    from video_sorter import scan, sort_files, plan_moves, apply_plan

    files = scan("~/Videos")
    plan  = plan_moves(sort_files(files, "size"), "~/Videos/Sorted", "resolution")
    apply_plan(plan, dry_run=False)
"""

from .metadata import VIDEO_EXTENSIONS, VideoFile, human_duration, human_size, is_video
from .organizer import RULES, Plan, apply_plan, plan_moves, undo
from .scanner import ProbeCache, scan
from .sorter import SORT_KEYS, filter_files, sort_files, summarise

__version__ = "1.0.0"

__all__ = [
    "VIDEO_EXTENSIONS", "VideoFile", "human_duration", "human_size", "is_video",
    "RULES", "Plan", "apply_plan", "plan_moves", "undo",
    "ProbeCache", "scan",
    "SORT_KEYS", "filter_files", "sort_files", "summarise",
    "__version__",
]
