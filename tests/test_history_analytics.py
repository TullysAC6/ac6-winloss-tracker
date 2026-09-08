"""Read-only analytics, CSV export and integrity checks over history.db."""
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import history_analytics as analytics
from history_store import HistoryStore

# A Wednesday noon, so "today", "week" (Monday) and "month" all differ.
NOW = datetime(2026, 9, 9, 12, 0, 0).timestamp()


def build(root, entries):
    """entries: (created_at, result, streak_after). Uses the real schema."""
    store = HistoryStore(root)
    store.start_session()
    for index, (created_at, result, streak) in enumerate(entries):
        store.record_result(
            f"event-{index}", result, "test",
            {"streak": streak, "wins": index + 1, "losses": 0},
            created_at=created_at,
        )
    return store


class PeriodTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_local_boundaries_are_inclusive_and_week_starts_on_monday(self):
        today = analytics.period_start("today", NOW)
        week = analytics.period_start("week", NOW)
        month = analytics.period_start("month", NOW)
        self.assertIsNone(analytics.period_start("all", NOW))
        for start in (today, week, month):
            moment = datetime.fromtimestamp(start)
            self.assertEqual((moment.hour, moment.minute, moment.second), (0, 0, 0))
        self.assertEqual(datetime.fromtimestamp(week).weekday(), 0)  # Monday
        self.assertEqual(datetime.fromtimestamp(month).day, 1)
        self.assertLessEqual(month, week)
        self.assertLessEqual(week, today)

        # One match exactly on each boundary and one a second earlier.
        build(self.root, [
            (today, "win", 1), (today - 1, "win", 1),
            (week, "win", 1), (week - 1, "win", 1),
            (month, "win", 1), (month - 1, "win", 1),
        ])
        counts = {
            entry["period"]: entry["matches"]
            for entry in analytics.summarize(self.root, NOW)["periods"]
        }
        self.assertEqual(counts["today"], 1)  # boundary in, one second earlier out
        self.assertEqual(counts["week"], 3)   # today, today-1s and the week boundary
        self.assertEqual(counts["month"], 5)
        self.assertEqual(counts["all"], 6)

    def test_draw_is_reported_but_excluded_from_the_win_rate_denominator(self):
        today = analytics.period_start("today", NOW)
        build(self.root, [
            (today + 1, "win", 1), (today + 2, "win", 2), (today + 3, "win", 3),
            (today + 4, "loss", 0), (today + 5, "draw", 0),
        ])
        entry = next(
            e for e in analytics.summarize(self.root, NOW)["periods"] if e["period"] == "today"
        )
        self.assertEqual((entry["wins"], entry["losses"], entry["draws"]), (3, 1, 1))
        self.assertEqual(entry["matches"], 5)      # 総試合数 includes DRAW
        self.assertEqual(entry["counted"], 4)      # denominator excludes DRAW
        self.assertEqual(entry["win_rate"], 75.0)  # 3 / (3 + 1)
        self.assertEqual(entry["best_streak"], 3)
        # Identical to the Tracker's own lifetime definition.
        self.assertEqual(HistoryStore(self.root).lifetime_summary()["win_rate"], 75.0)

    def test_recent_windows_use_the_latest_rows_and_tolerate_short_history(self):
        today = analytics.period_start("today", NOW)
        entries = [(today + n, "loss", 0) for n in range(20)]
        entries += [(today + 100 + n, "win", n + 1) for n in range(10)]
        build(self.root, entries)
        recent = {e["size"]: e for e in analytics.summarize(self.root, NOW)["recent"]}
        self.assertEqual((recent[10]["wins"], recent[10]["losses"]), (10, 0))
        self.assertEqual(recent[10]["available"], 10)
        self.assertEqual((recent[30]["wins"], recent[30]["losses"]), (10, 20))
        self.assertEqual(recent[30]["available"], 30)
        # Only 30 rows exist, so the 100 window falls back to what is there.
        self.assertEqual(recent[100]["available"], 30)
        self.assertEqual(recent[100]["win_rate"], round(10 / 30 * 100.0, 1))

    def test_empty_and_missing_history(self):
        with self.assertRaises(analytics.HistoryUnavailable):
            analytics.summarize(self.root, NOW)
        build(self.root, [])
        summary = analytics.summarize(self.root, NOW)
        self.assertEqual(summary["total_matches"], 0)
        self.assertIsNone(summary["first_match_at"])
        for entry in summary["periods"] + summary["recent"]:
            self.assertEqual(entry["win_rate"], 0.0)
            self.assertEqual(entry["best_streak"] if "best_streak" in entry else 0, 0)

    def test_count_before_keeps_the_boundary_row(self):
        today = analytics.period_start("today", NOW)
        build(self.root, [(today - 1, "win", 1), (today, "win", 2), (today + 1, "win", 3)])
        preview = analytics.count_before(self.root, today)
        self.assertEqual((preview["removable"], preview["kept"], preview["total"]), (1, 2, 3))


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.today = analytics.period_start("today", NOW)
        build(self.root, [
            (self.today + 2, "win", 1), (self.today, "loss", 0), (self.today + 1, "draw", 0),
        ])
        self.database = analytics.database_path(self.root)
        self.before = self.database.read_bytes()

    def test_utf8_bom_ordering_and_row_count(self):
        destination = self.root / "out.csv"
        result = analytics.export_csv(self.root, destination)
        self.assertEqual(result["rows"], 3)
        raw = destination.read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"), "Excel needs the UTF-8 BOM")
        text = raw.decode("utf-8-sig")
        lines = [line for line in text.splitlines() if line]
        self.assertEqual(lines[0].split(","), list(analytics.CSV_COLUMNS))
        self.assertEqual([line.split(",")[3] for line in lines[1:]], ["LOSE", "DRAW", "WIN"])
        self.assertEqual([line.split(",")[0] for line in lines[1:]], ["1", "2", "3"])
        self.assertEqual(self.database.read_bytes(), self.before, "export must not write the DB")

    def test_failure_leaves_no_partial_file_and_never_touches_the_database(self):
        destination = self.root / "out.csv"
        destination.write_bytes(b"original")
        with patch("history_analytics.os.replace", side_effect=OSError("denied")):
            with self.assertRaises(OSError):
                analytics.export_csv(self.root, destination)
        self.assertEqual(destination.read_bytes(), b"original")
        self.assertEqual(list(self.root.glob(".ac6-export-*.tmp")), [])
        self.assertEqual(self.database.read_bytes(), self.before)
        self.assertTrue(analytics.integrity_check(self.root)["ok"])

    def test_read_only_connection_rejects_writes(self):
        with analytics.open_readonly(self.root) as connection:
            with self.assertRaises(sqlite3.Error):
                connection.execute("DELETE FROM matches")
        self.assertEqual(self.database.read_bytes(), self.before)


class IntegrityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_healthy_database_reports_ok(self):
        build(self.root, [(NOW, "win", 1)])
        for thorough in (False, True):
            with self.subTest(thorough=thorough):
                result = analytics.integrity_check(self.root, thorough)
                self.assertTrue(result["ok"])
                self.assertEqual(result["messages"], ["ok"])
                self.assertEqual(result["pragma"],
                                 "integrity_check" if thorough else "quick_check")
                self.assertGreater(result["size_bytes"], 0)
                self.assertEqual(result["schema_version"], HistoryStore.SCHEMA_VERSION)
        self.assertIn("正常", settings_format(result))

    def test_unreadable_database_is_reported_not_repaired(self):
        path = analytics.database_path(self.root)
        path.write_bytes(b"this is not a sqlite database" * 64)
        before = path.read_bytes()
        with self.assertRaises(analytics.HistoryUnavailable):
            analytics.integrity_check(self.root)
        with self.assertRaises(analytics.HistoryUnavailable):
            analytics.summarize(self.root, NOW)
        self.assertEqual(path.read_bytes(), before, "a failed check must not repair or write")

    def test_corrupted_page_is_reported_as_abnormal(self):
        build(self.root, [(NOW + n, "win", n + 1) for n in range(400)])
        path = analytics.database_path(self.root)
        connection = sqlite3.connect(path)
        try:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            connection.execute("PRAGMA journal_mode=DELETE")
            page_size = int(connection.execute("PRAGMA page_size").fetchone()[0])
            page_count = int(connection.execute("PRAGMA page_count").fetchone()[0])
        finally:
            connection.close()
        healthy = path.read_bytes()

        # Which page holds user rows depends on the b-tree layout, so damage
        # each candidate page until the checker reports it.
        reported = None
        for page in range(3, page_count + 1):
            path.write_bytes(healthy)
            with path.open("r+b") as stream:
                stream.seek((page - 1) * page_size + 24)
                stream.write(b"\x7f" * (page_size - 40))
            damaged = path.read_bytes()
            try:
                result = analytics.integrity_check(self.root)
            except analytics.HistoryUnavailable:
                # Unopenable is also "not normal", and must still not repair.
                self.assertEqual(path.read_bytes(), damaged)
                continue
            self.assertEqual(path.read_bytes(), damaged, "a check must never repair")
            if not result["ok"]:
                reported = result
                break
        self.assertIsNotNone(reported, "no damaged page produced an abnormal report")
        self.assertNotEqual(reported["messages"], ["ok"])
        rendered = settings_format(reported)
        self.assertIn("異常", rendered)
        self.assertIn("Diagnostic Report", rendered)
        self.assertIn(str(path), rendered)


def settings_format(result):
    import settings_window
    return settings_window.format_integrity(result)


class MissingDatabaseTests(unittest.TestCase):
    def test_every_entry_point_reports_a_readable_reason(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            for call in (
                lambda: analytics.summarize(root, NOW),
                lambda: analytics.integrity_check(root),
                lambda: analytics.count_before(root, NOW),
                lambda: analytics.export_csv(root, root / "x.csv"),
            ):
                with self.assertRaises(analytics.HistoryUnavailable) as caught:
                    call()
                self.assertIn("history.db", str(caught.exception))
            self.assertEqual(list(root.iterdir()), [], "no file may be created by a read")


if __name__ == "__main__":
    unittest.main()
