"""
Command line for the sorter — the same engine the web UI drives.

    python3 -m video_sorter serve  ~/Videos
    python3 -m video_sorter list   ~/Videos --sort size --desc
    python3 -m video_sorter sort   ~/Videos --into ~/Videos/Sorted --by resolution
    python3 -m video_sorter undo   --latest
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .metadata import ffprobe_path, human_duration, human_size
from .organizer import (
    ACTIONS, COPY, DEFAULT_RULE, MOVE, RULES, apply_plan, list_journals,
    plan_moves, undo,
)
from .scanner import DEFAULT_WORKERS, ProbeCache, scan
from .server import DEFAULT_PORT, serve
from .sorter import DEFAULT_SORT, SORT_KEYS, filter_files, sort_files, summarise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="video_sorter",
        description="Sort video files by resolution, length, size, format or date.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # -- serve ------------------------------------------------------------
    serve_cmd = subparsers.add_parser("serve", help="launch the web UI")
    serve_cmd.add_argument("path", nargs="?", default=None,
                           help="directory to open on start (default: the root)")
    serve_cmd.add_argument("--root", default=None,
                           help="confine all file access to this directory (default: the path, or $HOME)")
    serve_cmd.add_argument("--host", default="127.0.0.1")
    serve_cmd.add_argument("--port", type=int, default=DEFAULT_PORT)
    serve_cmd.add_argument("--no-browser", action="store_true", help="do not open a browser")
    serve_cmd.add_argument("--no-cache", action="store_true", help="ignore the ffprobe cache")

    # -- list -------------------------------------------------------------
    list_cmd = subparsers.add_parser("list", help="list video files, sorted")
    _add_scan_arguments(list_cmd)
    list_cmd.add_argument("--sort", default=DEFAULT_SORT, choices=sorted(SORT_KEYS),
                          help=f"sort key (default: {DEFAULT_SORT})")
    list_cmd.add_argument("--desc", action="store_true", help="reverse the sort")
    list_cmd.add_argument("--limit", type=int, default=None, help="show only the first N")

    # -- sort -------------------------------------------------------------
    sort_cmd = subparsers.add_parser("sort", help="organise files into folders")
    _add_scan_arguments(sort_cmd)
    sort_cmd.add_argument("--into", required=True, metavar="DIR", help="destination directory")
    sort_cmd.add_argument("--by", default=DEFAULT_RULE, choices=sorted(RULES),
                          help=f"grouping rule (default: {DEFAULT_RULE})")
    sort_cmd.add_argument("--copy", action="store_true", help="copy instead of moving")
    sort_cmd.add_argument("--apply", action="store_true",
                          help="actually perform the moves (default is a dry run)")
    sort_cmd.add_argument("--yes", action="store_true", help="skip the confirmation prompt")

    # -- undo -------------------------------------------------------------
    undo_cmd = subparsers.add_parser("undo", help="reverse a previous sort")
    group = undo_cmd.add_mutually_exclusive_group(required=True)
    group.add_argument("--latest", action="store_true", help="undo the most recent sort")
    group.add_argument("--journal", metavar="FILE", help="undo a specific journal file")
    group.add_argument("--list", action="store_true", dest="list_journals",
                       help="list available journals")
    undo_cmd.add_argument("--apply", action="store_true",
                          help="actually restore the files (default is a dry run)")

    return parser


def _add_scan_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("path", help="directory to scan")
    parser.add_argument("--no-recursive", action="store_true", help="do not descend into subfolders")
    parser.add_argument("--no-probe", action="store_true", help="skip ffprobe (much faster, less data)")
    parser.add_argument("--no-cache", action="store_true", help="ignore the ffprobe cache")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS, help="parallel probes")
    parser.add_argument("--ext", action="append", default=None, metavar="EXT",
                        help="limit to this extension (repeatable)")
    parser.add_argument("--match", default=None, metavar="TEXT", help="only paths containing TEXT")
    parser.add_argument("--min-size", type=float, default=None, metavar="MB")
    parser.add_argument("--min-duration", type=float, default=None, metavar="MINUTES")
    parser.add_argument("--resolution", action="append", default=None, metavar="LABEL",
                        help="only this resolution tier, e.g. 4K (repeatable)")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "serve":
        return _cmd_serve(args)
    if args.command == "list":
        return _cmd_list(args)
    if args.command == "sort":
        return _cmd_sort(args)
    if args.command == "undo":
        return _cmd_undo(args)
    return 1


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def _cmd_serve(args) -> int:
    start = Path(args.path).expanduser().resolve() if args.path else None
    if args.root:
        root = Path(args.root).expanduser().resolve()
    else:
        root = start or Path.home()
    if start and root not in (start, *start.parents):
        print(f"error: {start} is not inside --root {root}", file=sys.stderr)
        return 2
    if not root.is_dir():
        print(f"error: not a directory: {root}", file=sys.stderr)
        return 2

    serve(
        root,
        start_path=start,
        host=args.host,
        port=args.port,
        open_browser=not args.no_browser,
        use_cache=not args.no_cache,
    )
    return 0


def _cmd_list(args) -> int:
    files = _scan_and_filter(args)
    if not files:
        print("No video files matched.")
        return 0

    files = sort_files(files, args.sort, reverse=args.desc)
    shown = files[: args.limit] if args.limit else files

    width = min(58, max(len(f.name) for f in shown))
    for video in shown:
        name = video.name if len(video.name) <= width else video.name[: width - 1] + "…"
        print(
            f"{name:<{width}}  {human_size(video.size):>10}  "
            f"{human_duration(video.duration):>9}  {video.resolution_label:>7}  "
            f"{(video.codec or '—'):>8}"
        )

    stats = summarise(files)
    print(
        f"\n{stats['count']} files · {human_size(stats['total_size'])} · "
        f"{human_duration(stats['total_duration'])} total"
    )
    if args.limit and len(files) > args.limit:
        print(f"(showing the first {args.limit})")
    return 0


def _cmd_sort(args) -> int:
    files = _scan_and_filter(args)
    if not files:
        print("No video files matched — nothing to sort.")
        return 0

    action = COPY if args.copy else MOVE
    plan = plan_moves(files, args.into, args.by, action=action)

    print(f"\n  {plan.rule}: {len(plan.moves)} files → {plan.destination_root}\n")
    for bucket, count in plan.buckets.items():
        print(f"    {bucket or '.':<24} {count:>5}")
    renamed = sum(1 for m in plan.moves if m.renamed)
    if renamed:
        print(f"\n  {renamed} file(s) will be renamed to avoid overwriting an existing file.")
    if plan.skipped:
        print(f"  {len(plan.skipped)} file(s) are already in place.")

    if not plan.moves:
        return 0
    if not args.apply:
        print("\n  Dry run — nothing was moved. Re-run with --apply to commit.\n")
        for move in plan.moves[:12]:
            print(f"    {move.source.name}  →  {move.bucket}/{move.destination.name}")
        if len(plan.moves) > 12:
            print(f"    … and {len(plan.moves) - 12} more")
        return 0

    if not args.yes:
        verb = "Copy" if action == COPY else "Move"
        answer = input(f"\n  {verb} {len(plan.moves)} files? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("  Aborted.")
            return 1

    total = len(plan.moves)

    def progress(index: int, _total: int, move) -> None:
        _status(f"  [{index}/{total}] {move.source.name[:52]:<52}")

    results = apply_plan(plan, dry_run=False, on_progress=progress if _interactive() else None)
    _clear_status()
    print(f"  {len(results['succeeded'])}/{total} files {action}d.")
    for failure in results["failed"]:
        print(f"    failed: {failure['name']} — {failure['error']}", file=sys.stderr)
    if results["journal"]:
        print(f"  Journal: {results['journal']}")
        print("  Undo with: python3 -m video_sorter undo --latest --apply")
    return 1 if results["failed"] else 0


def _cmd_undo(args) -> int:
    journals = list_journals()
    if args.list_journals:
        if not journals:
            print("No journals recorded yet.")
            return 0
        for journal in journals:
            print(journal)
        return 0

    if args.latest:
        if not journals:
            print("No journals to undo.", file=sys.stderr)
            return 1
        journal_path = journals[0]
    else:
        journal_path = Path(args.journal).expanduser()
        if not journal_path.is_file():
            print(f"error: no such journal: {journal_path}", file=sys.stderr)
            return 2

    results = undo(journal_path, dry_run=not args.apply)
    verb = "Would restore" if not args.apply else "Restored"
    print(f"  {verb} {len(results['restored'])} file(s) from {journal_path.name}")
    for item in results["restored"][:12]:
        print(f"    {Path(item['from']).name}  →  {item['to']}")
    if len(results["restored"]) > 12:
        print(f"    … and {len(results['restored']) - 12} more")
    if results.get("removed_dirs"):
        print(f"  Removed {len(results['removed_dirs'])} empty folder(s).")
    if results["left_in_place"]:
        print(f"  {len(results['left_in_place'])} copy/copies left in place — delete them yourself.")
    for failure in results["failed"]:
        print(f"    skipped: {failure['path']} — {failure['error']}", file=sys.stderr)
    if not args.apply:
        print("\n  Dry run — nothing was restored. Re-run with --apply to commit.")
    return 0


# ---------------------------------------------------------------------------
# Progress reporting
#
# Progress goes to stderr, and only to a terminal. Otherwise a carriage-return
# redraw would litter the output of `video_sorter list | grep ...`.
# ---------------------------------------------------------------------------

_STATUS_WIDTH = 64
_status_shown = False


def _interactive() -> bool:
    return sys.stderr.isatty()


def _status(line: str) -> None:
    global _status_shown
    if not _interactive():
        return
    print(f"\r{line[:_STATUS_WIDTH]:<{_STATUS_WIDTH}}", end="", file=sys.stderr, flush=True)
    _status_shown = True


def _clear_status() -> None:
    global _status_shown
    if _status_shown:
        print("\r" + " " * _STATUS_WIDTH + "\r", end="", file=sys.stderr, flush=True)
        _status_shown = False


# ---------------------------------------------------------------------------
# Shared scanning
# ---------------------------------------------------------------------------

def _scan_and_filter(args) -> list:
    do_probe = not args.no_probe
    if do_probe and ffprobe_path() is None:
        print("  note: ffprobe not found — sorting by name, size and date only.", file=sys.stderr)
        do_probe = False

    def progress(done: int, total: int) -> None:
        _status(f"  probing {done}/{total}")

    show_progress = do_probe and _interactive()

    try:
        files = scan(
            args.path,
            recursive=not args.no_recursive,
            extensions=args.ext,
            do_probe=do_probe,
            workers=args.workers,
            cache=None if args.no_cache else ProbeCache(),
            on_progress=progress if show_progress else None,
        )
    except (NotADirectoryError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    finally:
        _clear_status()

    return filter_files(
        files,
        query=args.match,
        extensions=args.ext,
        resolutions=args.resolution,
        min_size=int(args.min_size * 1024 ** 2) if args.min_size else None,
        min_duration=args.min_duration * 60 if args.min_duration else None,
    )
