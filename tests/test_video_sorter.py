"""
Tests for the video sorter.

Run: python3 -m unittest discover -s tests -v

No ffprobe and no real video data is needed — probe results are injected
through apply_probe_payload, which is the same path ffprobe output takes.
"""

import json
import os
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from video_sorter import metadata, organizer, scanner, sorter
from video_sorter import server as server_module


def make_file(directory: Path, name: str, size: int = 1024, mtime: float | None = None) -> Path:
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0" * size)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def video(path: Path, **overrides) -> metadata.VideoFile:
    item = metadata.VideoFile.from_path(path)
    for key, value in overrides.items():
        setattr(item, key, value)
    if any(k in overrides for k in ("duration", "width", "height", "codec")):
        item.probed = True
    return item


class TempDirCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name).resolve()
        self.addCleanup(self._tmp.cleanup)


# ---------------------------------------------------------------------------
# metadata
# ---------------------------------------------------------------------------

class TestMetadata(TempDirCase):
    def test_human_size_scales_by_unit(self):
        self.assertEqual(metadata.human_size(512), "512 B")
        self.assertEqual(metadata.human_size(1536), "1.5 KB")
        self.assertEqual(metadata.human_size(5 * 1024 ** 3), "5.0 GB")

    def test_human_duration_adds_hours_only_when_needed(self):
        self.assertEqual(metadata.human_duration(65), "1:05")
        self.assertEqual(metadata.human_duration(3725), "1:02:05")
        self.assertEqual(metadata.human_duration(None), "—")

    def test_resolution_uses_short_edge_so_vertical_video_is_honest(self):
        landscape = video(make_file(self.tmp, "wide.mp4"), width=1920, height=1080)
        portrait = video(make_file(self.tmp, "tall.mp4"), width=1080, height=1920)
        self.assertEqual(landscape.resolution_label, "1080p")
        self.assertEqual(portrait.resolution_label, "1080p")

    def test_resolution_tiers(self):
        cases = [(7680, 4320, "8K"), (3840, 2160, "4K"), (2560, 1440, "1440p"),
                 (1280, 720, "720p"), (640, 480, "480p"), (320, 240, "SD")]
        for width, height, expected in cases:
            item = video(make_file(self.tmp, f"{expected}.mp4"), width=width, height=height)
            self.assertEqual(item.resolution_label, expected, f"{width}x{height}")

    def test_unprobed_file_reports_unknown_not_a_guess(self):
        item = video(make_file(self.tmp, "mystery.mkv"))
        self.assertEqual(item.resolution_label, metadata.UNKNOWN)
        self.assertEqual(item.duration_label, metadata.UNKNOWN)
        self.assertIsNone(item.pixels)

    def test_apply_probe_payload_reads_format_and_stream(self):
        item = video(make_file(self.tmp, "clip.mp4"))
        metadata.apply_probe_payload(item, {
            "format": {"duration": "212.5", "bit_rate": "4500000"},
            "streams": [
                {"codec_type": "audio", "codec_name": "aac"},
                {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080},
            ],
        })
        self.assertAlmostEqual(item.duration, 212.5)
        self.assertEqual((item.width, item.height), (1920, 1080))
        self.assertEqual(item.codec, "h264")
        self.assertEqual(item.bitrate, 4500000)
        self.assertTrue(item.probed)

    def test_quarter_turn_rotation_swaps_dimensions(self):
        item = video(make_file(self.tmp, "phone.mov"))
        metadata.apply_probe_payload(item, {
            "format": {"duration": "10"},
            "streams": [{"codec_type": "video", "codec_name": "hevc",
                         "width": 1920, "height": 1080, "tags": {"rotate": "90"}}],
        })
        self.assertEqual((item.width, item.height), (1080, 1920))

    def test_unmeasurable_duration_stays_none(self):
        item = video(make_file(self.tmp, "stream.ts"))
        metadata.apply_probe_payload(item, {
            "format": {"duration": "N/A"},
            "streams": [{"codec_type": "video", "codec_name": "mpeg2video"}],
        })
        self.assertIsNone(item.duration)


# ---------------------------------------------------------------------------
# scanner
# ---------------------------------------------------------------------------

class TestScanner(TempDirCase):
    def setUp(self):
        super().setUp()
        make_file(self.tmp, "alpha.mp4")
        make_file(self.tmp, "notes.txt")
        make_file(self.tmp, ".hidden.mp4")
        make_file(self.tmp, "nested/beta.MKV")
        make_file(self.tmp, "node_modules/junk.mp4")

    def test_scan_finds_video_files_only(self):
        found = {f.name for f in scanner.scan(self.tmp, do_probe=False)}
        self.assertEqual(found, {"alpha.mp4", "beta.MKV"})

    def test_scan_skips_hidden_and_noise_directories(self):
        found = {f.name for f in scanner.scan(self.tmp, do_probe=False)}
        self.assertNotIn(".hidden.mp4", found)
        self.assertNotIn("junk.mp4", found)

    def test_non_recursive_scan_stays_at_the_top_level(self):
        found = {f.name for f in scanner.scan(self.tmp, do_probe=False, recursive=False)}
        self.assertEqual(found, {"alpha.mp4"})

    def test_extension_filter_accepts_either_spelling(self):
        found = {f.name for f in scanner.scan(self.tmp, do_probe=False, extensions=["MKV"])}
        self.assertEqual(found, {"beta.MKV"})

    def test_relative_path_is_recorded_for_display(self):
        by_name = {f.name: f for f in scanner.scan(self.tmp, do_probe=False)}
        self.assertEqual(by_name["beta.MKV"].relative, os.path.join("nested", "beta.MKV"))

    def test_scan_rejects_a_path_that_is_not_a_directory(self):
        with self.assertRaises(NotADirectoryError):
            scanner.scan(self.tmp / "alpha.mp4", do_probe=False)

    def test_probe_cache_round_trips(self):
        cache = scanner.ProbeCache(self.tmp / "cache.json")
        item = video(self.tmp / "alpha.mp4", duration=90.0, width=1280, height=720, codec="h264")
        cache.put(item)
        cache.save()

        fresh = scanner.ProbeCache(self.tmp / "cache.json")
        reloaded = metadata.VideoFile.from_path(self.tmp / "alpha.mp4")
        self.assertTrue(fresh.get(reloaded))
        self.assertEqual(reloaded.duration, 90.0)
        self.assertEqual(reloaded.resolution_label, "720p")

    def test_cache_misses_when_the_file_changed(self):
        cache = scanner.ProbeCache(self.tmp / "cache.json")
        item = video(self.tmp / "alpha.mp4", duration=90.0)
        cache.put(item)

        (self.tmp / "alpha.mp4").write_bytes(b"\0" * 9999)  # different size
        self.assertFalse(cache.get(metadata.VideoFile.from_path(self.tmp / "alpha.mp4")))

    def test_corrupt_cache_file_is_ignored_not_fatal(self):
        (self.tmp / "cache.json").write_text("{not json")
        cache = scanner.ProbeCache(self.tmp / "cache.json")
        self.assertFalse(cache.get(metadata.VideoFile.from_path(self.tmp / "alpha.mp4")))


# ---------------------------------------------------------------------------
# sorter
# ---------------------------------------------------------------------------

class TestSorter(TempDirCase):
    def setUp(self):
        super().setUp()
        self.files = [
            video(make_file(self.tmp, "Ep 10.mp4", size=300), duration=600.0, width=1920, height=1080),
            video(make_file(self.tmp, "Ep 2.mp4", size=100), duration=120.0, width=3840, height=2160),
            video(make_file(self.tmp, "ep 1.mkv", size=200), duration=None),
        ]

    def test_natural_sort_orders_episode_numbers_numerically(self):
        names = [f.name for f in sorter.sort_files(self.files, "name")]
        self.assertEqual(names, ["ep 1.mkv", "Ep 2.mp4", "Ep 10.mp4"])

    def test_sort_by_size(self):
        sizes = [f.size for f in sorter.sort_files(self.files, "size")]
        self.assertEqual(sizes, [100, 200, 300])

    def test_unknown_values_sort_last_in_both_directions(self):
        ascending = sorter.sort_files(self.files, "duration")
        descending = sorter.sort_files(self.files, "duration", reverse=True)
        self.assertEqual(ascending[-1].name, "ep 1.mkv")
        self.assertEqual(descending[-1].name, "ep 1.mkv")

    def test_unknown_sort_key_is_rejected(self):
        with self.assertRaises(ValueError):
            sorter.sort_files(self.files, "vibes")

    def test_query_filter_matches_the_whole_path(self):
        self.assertEqual(len(sorter.filter_files(self.files, query="ep ")), 3)
        self.assertEqual(len(sorter.filter_files(self.files, query="mkv")), 1)

    def test_duration_filter_excludes_unmeasured_files(self):
        # ep 1.mkv has no duration, so it cannot be shown to satisfy the bound.
        result = sorter.filter_files(self.files, min_duration=60)
        self.assertEqual({f.name for f in result}, {"Ep 10.mp4", "Ep 2.mp4"})

    def test_filters_are_anded(self):
        result = sorter.filter_files(self.files, resolutions=["4K"], min_size=50)
        self.assertEqual([f.name for f in result], ["Ep 2.mp4"])

    def test_summarise_counts_and_totals(self):
        stats = sorter.summarise(self.files)
        self.assertEqual(stats["count"], 3)
        self.assertEqual(stats["total_size"], 600)
        self.assertEqual(stats["total_duration"], 720.0)
        self.assertEqual(stats["unknown_duration"], 1)
        self.assertEqual(stats["extensions"], {"mkv": 1, "mp4": 2})


# ---------------------------------------------------------------------------
# organizer — planning
# ---------------------------------------------------------------------------

class TestPlanning(TempDirCase):
    def setUp(self):
        super().setUp()
        self.source = self.tmp / "src"
        self.dest = self.tmp / "dest"
        self.source.mkdir()

    def test_groups_by_resolution(self):
        files = [
            video(make_file(self.source, "a.mp4"), width=3840, height=2160),
            video(make_file(self.source, "b.mp4"), width=1920, height=1080),
            video(make_file(self.source, "c.mp4"), width=1920, height=1080),
        ]
        plan = organizer.plan_moves(files, self.dest, "resolution")
        self.assertEqual(plan.buckets, {"1080p": 2, "4K": 1})

    def test_groups_by_extension_and_year(self):
        stamp = 1700000000.0  # 2023
        files = [video(make_file(self.source, "a.mkv", mtime=stamp))]
        self.assertEqual(organizer.plan_moves(files, self.dest, "extension").buckets, {"MKV": 1})

        year = organizer.plan_moves(files, self.dest, "year").moves[0].bucket
        self.assertRegex(year, r"^\d{4}$")

    def test_month_rule_nests_two_levels(self):
        files = [video(make_file(self.source, "a.mp4", mtime=1700000000.0))]
        bucket = organizer.plan_moves(files, self.dest, "month").moves[0].bucket
        self.assertRegex(bucket, r"^\d{4}/\d{4}-\d{2}$")

    def test_flat_rule_puts_everything_in_the_root(self):
        files = [video(make_file(self.source, "a.mp4"))]
        move = organizer.plan_moves(files, self.dest, "flat").moves[0]
        self.assertEqual(move.destination, self.dest / "a.mp4")

    def test_alpha_rule_buckets_non_letters_under_hash(self):
        files = [
            video(make_file(self.source, "zeta.mp4")),
            video(make_file(self.source, "9lives.mp4")),
        ]
        self.assertEqual(organizer.plan_moves(files, self.dest, "alpha").buckets, {"#": 1, "Z": 1})

    def test_planning_touches_nothing_on_disk(self):
        files = [video(make_file(self.source, "a.mp4"), width=1920, height=1080)]
        organizer.plan_moves(files, self.dest, "resolution")
        self.assertFalse(self.dest.exists())
        self.assertTrue((self.source / "a.mp4").exists())

    def test_two_files_with_one_name_are_suffixed_apart(self):
        files = [
            video(make_file(self.source, "one/clip.mp4"), width=1920, height=1080),
            video(make_file(self.source, "two/clip.mp4"), width=1920, height=1080),
        ]
        plan = organizer.plan_moves(files, self.dest, "resolution")
        destinations = [m.destination.name for m in plan.moves]
        self.assertEqual(destinations, ["clip.mp4", "clip (2).mp4"])
        self.assertEqual(len(set(destinations)), 2)
        self.assertTrue(plan.moves[1].renamed)

    def test_an_existing_destination_is_never_overwritten(self):
        make_file(self.dest / "1080p", "clip.mp4")
        files = [video(make_file(self.source, "clip.mp4"), width=1920, height=1080)]
        move = organizer.plan_moves(files, self.dest, "resolution").moves[0]
        self.assertEqual(move.destination.name, "clip (2).mp4")
        self.assertTrue(move.renamed)

    def test_a_file_already_in_place_is_skipped_not_moved(self):
        existing = make_file(self.dest / "1080p", "clip.mp4")
        files = [video(existing, width=1920, height=1080)]
        plan = organizer.plan_moves(files, self.dest, "resolution")
        self.assertEqual(plan.moves, [])
        self.assertEqual(len(plan.skipped), 1)
        self.assertEqual(plan.skipped[0]["reason"], "already in place")

    def _plan_case_variants(self):
        files = [
            video(make_file(self.source, "one/Clip.mp4"), width=1920, height=1080),
            video(make_file(self.source, "two/clip.mp4"), width=1920, height=1080),
        ]
        return organizer.plan_moves(files, self.dest, "resolution")

    def test_case_variants_collide_on_a_case_insensitive_filesystem(self):
        # On macOS and Windows "Clip.mp4" and "clip.mp4" are one file, so the
        # second must be suffixed apart rather than overwrite the first.
        with unittest.mock.patch.object(organizer, "CASE_INSENSITIVE_FS", True):
            names = [m.destination.name for m in self._plan_case_variants().moves]
        self.assertEqual(len(set(n.lower() for n in names)), 2, names)

    def test_case_variants_coexist_on_a_case_sensitive_filesystem(self):
        # On Linux they are genuinely two files; renaming either would be wrong.
        with unittest.mock.patch.object(organizer, "CASE_INSENSITIVE_FS", False):
            moves = self._plan_case_variants().moves
        self.assertEqual([m.destination.name for m in moves], ["Clip.mp4", "clip.mp4"])
        self.assertFalse(any(m.renamed for m in moves))

    def test_rule_output_cannot_escape_the_destination_root(self):
        organizer.RULES["evil"] = (lambda v: "../../escape", "Evil")
        self.addCleanup(organizer.RULES.pop, "evil", None)
        files = [video(make_file(self.source, "a.mp4"))]
        move = organizer.plan_moves(files, self.dest, "evil").moves[0]
        self.assertIn(self.dest, move.destination.parents)

    def test_unknown_rule_or_action_is_rejected(self):
        files = [video(make_file(self.source, "a.mp4"))]
        with self.assertRaises(ValueError):
            organizer.plan_moves(files, self.dest, "by-vibes")
        with self.assertRaises(ValueError):
            organizer.plan_moves(files, self.dest, "flat", action="delete")


# ---------------------------------------------------------------------------
# organizer — applying and undoing
# ---------------------------------------------------------------------------

class TestApplyAndUndo(TempDirCase):
    def setUp(self):
        super().setUp()
        self.source = self.tmp / "src"
        self.dest = self.tmp / "dest"
        self.journal = self.tmp / "journal.json"
        self.source.mkdir()
        self.files = [
            video(make_file(self.source, "a.mp4", size=10), width=3840, height=2160),
            video(make_file(self.source, "b.mp4", size=20), width=1920, height=1080),
        ]
        self.plan = organizer.plan_moves(self.files, self.dest, "resolution")

    def test_dry_run_is_the_default_and_moves_nothing(self):
        results = organizer.apply_plan(self.plan)
        self.assertTrue(results["dry_run"])
        self.assertEqual(len(results["succeeded"]), 2)
        self.assertTrue((self.source / "a.mp4").exists())
        self.assertFalse(self.dest.exists())

    def test_apply_moves_files_into_their_buckets(self):
        organizer.apply_plan(self.plan, dry_run=False, journal_path=self.journal)
        self.assertTrue((self.dest / "4K" / "a.mp4").exists())
        self.assertTrue((self.dest / "1080p" / "b.mp4").exists())
        self.assertFalse((self.source / "a.mp4").exists())

    def test_copy_leaves_the_original_in_place(self):
        plan = organizer.plan_moves(self.files, self.dest, "resolution", action=organizer.COPY)
        organizer.apply_plan(plan, dry_run=False, journal_path=self.journal)
        self.assertTrue((self.source / "a.mp4").exists())
        self.assertTrue((self.dest / "4K" / "a.mp4").exists())

    def test_journal_records_every_move(self):
        organizer.apply_plan(self.plan, dry_run=False, journal_path=self.journal)
        payload = json.loads(self.journal.read_text())
        self.assertEqual(len(payload["entries"]), 2)
        self.assertEqual(payload["rule"], "resolution")
        self.assertIn("finished", payload)

    def test_one_failure_does_not_abandon_the_rest(self):
        # A directory where a file should go makes exactly one move fail.
        (self.dest / "4K").mkdir(parents=True)
        (self.dest / "4K" / "a.mp4").mkdir()
        plan = organizer.Plan(action=organizer.MOVE, destination_root=self.dest, rule="resolution")
        plan.moves = [
            organizer.Move(self.source / "a.mp4", self.dest / "4K" / "a.mp4", "4K"),
            organizer.Move(self.source / "b.mp4", self.dest / "1080p" / "b.mp4", "1080p"),
        ]
        results = organizer.apply_plan(plan, dry_run=False, journal_path=self.journal)
        self.assertEqual(len(results["failed"]), 1)
        self.assertEqual(len(results["succeeded"]), 1)
        self.assertTrue((self.dest / "1080p" / "b.mp4").exists())

    def test_undo_restores_every_moved_file(self):
        organizer.apply_plan(self.plan, dry_run=False, journal_path=self.journal)
        results = organizer.undo(self.journal, dry_run=False)
        self.assertEqual(len(results["restored"]), 2)
        self.assertTrue((self.source / "a.mp4").exists())
        self.assertTrue((self.source / "b.mp4").exists())
        self.assertFalse((self.dest / "4K" / "a.mp4").exists())

    def test_undo_dry_run_changes_nothing(self):
        organizer.apply_plan(self.plan, dry_run=False, journal_path=self.journal)
        results = organizer.undo(self.journal, dry_run=True)
        self.assertEqual(len(results["restored"]), 2)
        self.assertTrue((self.dest / "4K" / "a.mp4").exists())
        self.assertFalse((self.source / "a.mp4").exists())

    def test_undo_never_deletes_a_copy(self):
        plan = organizer.plan_moves(self.files, self.dest, "resolution", action=organizer.COPY)
        organizer.apply_plan(plan, dry_run=False, journal_path=self.journal)
        results = organizer.undo(self.journal, dry_run=False)
        self.assertEqual(len(results["left_in_place"]), 2)
        self.assertEqual(results["restored"], [])
        self.assertTrue((self.dest / "4K" / "a.mp4").exists())

    def test_undo_refuses_when_the_original_path_is_occupied(self):
        organizer.apply_plan(self.plan, dry_run=False, journal_path=self.journal)
        make_file(self.source, "a.mp4", size=1)  # something new took the old name
        results = organizer.undo(self.journal, dry_run=False)
        self.assertEqual(len(results["failed"]), 1)
        self.assertEqual((self.source / "a.mp4").stat().st_size, 1)
    def test_undo_removes_the_bucket_folders_it_emptied(self):
        organizer.apply_plan(self.plan, dry_run=False, journal_path=self.journal)
        self.assertTrue((self.dest / "4K").is_dir())
        organizer.undo(self.journal, dry_run=False)
        self.assertFalse((self.dest / "4K").exists())
        self.assertFalse(self.dest.exists())

    def test_undo_keeps_a_destination_folder_that_still_holds_something(self):
        organizer.apply_plan(self.plan, dry_run=False, journal_path=self.journal)
        make_file(self.dest / "4K", "someone-elses-file.txt")
        organizer.undo(self.journal, dry_run=False)
        self.assertTrue((self.dest / "4K" / "someone-elses-file.txt").exists())
        self.assertFalse((self.dest / "1080p").exists())  # this one was empty

    def test_undo_dry_run_removes_no_folders(self):
        organizer.apply_plan(self.plan, dry_run=False, journal_path=self.journal)
        results = organizer.undo(self.journal, dry_run=True)
        self.assertEqual(results["removed_dirs"], [])
        self.assertTrue((self.dest / "4K").is_dir())


# ---------------------------------------------------------------------------
# server — path confinement
# ---------------------------------------------------------------------------

class TestServiceConfinement(TempDirCase):
    def setUp(self):
        super().setUp()
        from video_sorter.server import SorterService

        self.root = self.tmp / "library"
        (self.root / "inside").mkdir(parents=True)
        (self.tmp / "outside").mkdir()
        self.service = SorterService(self.root, use_cache=False)

    def test_a_path_inside_the_root_resolves(self):
        self.assertEqual(self.service.resolve(str(self.root / "inside")), self.root / "inside")

    def test_the_root_itself_resolves(self):
        self.assertEqual(self.service.resolve(str(self.root)), self.root)

    def test_traversal_out_of_the_root_is_refused(self):
        with self.assertRaises(PermissionError):
            self.service.resolve(str(self.root / ".." / "outside"))

    def test_an_unrelated_absolute_path_is_refused(self):
        with self.assertRaises(PermissionError):
            self.service.resolve("/etc")

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unsupported")
    def test_a_symlink_pointing_out_of_the_root_is_refused(self):
        link = self.root / "escape"
        try:
            link.symlink_to(self.tmp / "outside")
        except (OSError, NotImplementedError):
            self.skipTest("cannot create symlinks here")
        with self.assertRaises(PermissionError):
            self.service.resolve(str(link))

    def test_an_empty_path_is_refused(self):
        with self.assertRaises(ValueError):
            self.service.resolve("   ")

    def test_a_relative_path_is_taken_as_relative_to_the_root(self):
        self.assertEqual(self.service.resolve("inside"), self.root / "inside")

    def test_planning_reuses_probe_data_from_the_last_scan(self):
        # Regression: the service used to rebuild each VideoFile from disk and
        # drop everything ffprobe had measured, bucketing a whole library under
        # "Unknown" whenever the probe cache was cold or disabled.
        target = make_file(self.root, "clip.mp4")
        scanned = video(target, width=3840, height=2160, duration=120.0)
        self.service._scanned = {str(target): scanned}

        plan = self.service.plan({
            "paths": [str(target)],
            "destination": str(self.root / "Sorted"),
            "rule": "resolution",
        })
        self.assertEqual(plan.buckets, {"4K": 1})

    def test_stale_scan_data_is_not_reused_after_the_file_changes(self):
        target = make_file(self.root, "clip.mp4", size=10)
        stale = video(target, width=3840, height=2160)
        self.service._scanned = {str(target): stale}

        make_file(self.root, "clip.mp4", size=4096)  # rewritten since the scan
        loaded = self.service._load(self.root / "clip.mp4", needs_probe=False)
        self.assertIsNot(loaded, stale)
        self.assertFalse(loaded.probed)

    def test_a_probe_dependent_rule_probes_on_demand(self):
        target = make_file(self.root, "clip.mp4")

        def fake_probe(item):
            item.width, item.height, item.probed = 1920, 1080, True
            return item

        with unittest.mock.patch.object(server_module, "probe", side_effect=fake_probe) as probe_mock:
            plan = self.service.plan({
                "paths": [str(target)],
                "destination": str(self.root / "Sorted"),
                "rule": "resolution",
            })
        probe_mock.assert_called_once()
        self.assertEqual(plan.buckets, {"1080p": 1})

    def test_a_stat_only_rule_does_not_pay_for_a_probe(self):
        target = make_file(self.root, "clip.mp4")
        with unittest.mock.patch.object(server_module, "probe") as probe_mock:
            plan = self.service.plan({
                "paths": [str(target)],
                "destination": str(self.root / "Sorted"),
                "rule": "extension",
            })
        probe_mock.assert_not_called()
        self.assertEqual(plan.buckets, {"MP4": 1})

    def test_planning_with_no_surviving_files_is_an_error_not_an_empty_plan(self):
        with self.assertRaises(ValueError):
            self.service.plan({
                "paths": [str(self.root / "ghost.mp4")],
                "destination": str(self.root / "Sorted"),
                "rule": "extension",
            })

    def test_each_service_gets_its_own_token(self):
        from video_sorter.server import SorterService

        other = SorterService(self.root, use_cache=False)
        self.assertNotEqual(self.service.token, other.token)
        self.assertGreaterEqual(len(self.service.token), 24)


if __name__ == "__main__":
    unittest.main()
