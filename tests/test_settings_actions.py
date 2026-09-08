"""Settings actions: new options, history maintenance, updates and diagnostics."""
import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import config_utils
import history_analytics
import settings_window as settings
from history_store import ActiveSessionOverlap, HistoryStore

GUI = unittest.skipUnless(os.name == "nt", "real Tk controls on Windows")


def day_text(offset_days=0):
    """A YYYY-MM-DD date relative to the machine's local today."""
    return (datetime.now() + timedelta(days=offset_days)).strftime("%Y-%m-%d")


class SettingsFileTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "config.json"
        self.raw = dict(config_utils.DEFAULT_CONFIG, port=9123)
        self.path.write_text(json.dumps(self.raw), encoding="utf-8")
        patcher = patch.object(config_utils, "CONFIG_PATH", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)
        config_utils._last_good = config_utils._last_good_signature = None

    def test_new_options_round_trip_and_keep_unrelated_keys(self):
        self.assertEqual(settings.read_settings(), {
            "effect_enabled": True,
            "effect_screenshot_enabled": False,
            "overlay_stats_scope": "session",
        })
        settings.save_settings({"effect_enabled": False, "overlay_stats_scope": "lifetime"})
        stored = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(stored["port"], 9123)
        self.assertEqual(stored["result_detector_enabled"], True)
        self.assertEqual(settings.read_settings(), {
            "effect_enabled": False,
            "effect_screenshot_enabled": False,
            "overlay_stats_scope": "lifetime",
        })
        # The screenshot helper still edits only its own key.
        settings.save_screenshot_setting(True)
        self.assertEqual(settings.read_settings()["overlay_stats_scope"], "lifetime")

    def test_config_written_by_an_older_version_gains_the_defaults(self):
        old = {k: v for k, v in self.raw.items()
               if k not in ("effect_enabled", "overlay_stats_scope")}
        old["config_version"] = 17
        self.path.write_text(json.dumps(old), encoding="utf-8")
        self.assertEqual(settings.read_settings()["effect_enabled"], True)
        self.assertEqual(settings.read_settings()["overlay_stats_scope"], "session")
        settings.save_settings({"overlay_stats_scope": "lifetime"})
        self.assertEqual(config_utils.load_config()["overlay_stats_scope"], "lifetime")

    def test_invalid_values_are_refused_and_the_file_is_untouched(self):
        original = self.path.read_bytes()
        for values in ({"overlay_stats_scope": "everything"}, {"effect_enabled": "yes"},
                       {"effect_enabled": 1}, {"port": 1234}, {}):
            with self.subTest(values=values):
                with self.assertRaises(ValueError):
                    settings.save_settings(values)
                self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.path.parent.glob(".config-*.tmp")), [])

    def test_a_change_during_a_match_is_visible_without_restarting(self):
        config_utils.load_config()  # Prime the process-local cache, as the Tracker does.
        for scope in ("lifetime", "session", "lifetime"):
            settings.save_settings({"overlay_stats_scope": scope, "effect_enabled": False})
            self.assertEqual(config_utils.load_config()["overlay_stats_scope"], scope)
            self.assertFalse(config_utils.load_config()["effect_enabled"])

    def test_date_cutoff_uses_local_midnight_of_the_named_day(self):
        today = day_text()
        cutoff = settings.cutoff_for_date(today)
        self.assertEqual(history_analytics.format_local(cutoff), f"{today} 00:00:00")
        self.assertEqual(cutoff, settings.cutoff_for_date(f"  {today}  "))
        for bad in ("2026/09/09", "20260909", "", "yesterday", None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                settings.cutoff_for_date(bad)

    def test_today_is_the_newest_selectable_deletion_date(self):
        for offset in (0, -1, -30, -400):
            with self.subTest(offset=offset):
                self.assertIsInstance(settings.cutoff_for_date(day_text(offset)), float)
        for offset in (1, 2, 400):
            with self.subTest(offset=offset):
                with self.assertRaises(ValueError) as caught:
                    settings.cutoff_for_date(day_text(offset))
                self.assertIn("今日まで", str(caught.exception))
        # The boundary is the local midnight that starts today.
        self.assertEqual(settings.cutoff_for_date(day_text()),
                         history_analytics.latest_allowed_cutoff())


class PurgeRequestValidationTests(unittest.TestCase):
    """The server applies the same limit as the settings window, independently."""

    def setUp(self):
        import server
        self.server = server
        self.now = datetime(2026, 9, 9, 15, 30, 0).timestamp()
        self.today = datetime(2026, 9, 9).timestamp()

    def test_today_and_earlier_are_accepted(self):
        for cutoff in (self.today, self.today - 1, self.today - 86400, 0.0):
            with self.subTest(cutoff=cutoff):
                self.assertEqual(
                    self.server.validate_purge_request(
                        {"mode": "before", "cutoff": cutoff}, now=self.now),
                    ("before", float(cutoff)),
                )

    def test_anything_after_today_midnight_is_refused(self):
        for offset in (1, 3600, 8 * 3600, 86400, 10 * 86400):
            with self.subTest(offset=offset):
                with self.assertRaises(ValueError) as caught:
                    self.server.validate_purge_request(
                        {"mode": "before", "cutoff": self.today + offset}, now=self.now)
                self.assertIn("today", str(caught.exception))
        self.assertEqual(self.server.validate_purge_request({"mode": "all"}, now=self.now),
                         ("all", None))


class OverlayScopeTests(unittest.TestCase):
    def test_lifetime_payload_validation(self):
        import game_overlay
        good = {"wins": 3, "losses": 1, "draws": 0, "win_rate": 75.0, "best_streak": 3}
        self.assertEqual(game_overlay.normalize_lifetime(good),
                         {"wins": 3, "losses": 1, "best_streak": 3, "win_rate": 75.0})
        for bad in (None, {}, {"wins": -1, "losses": 0, "win_rate": 0.0, "best_streak": 0},
                    {"wins": 1, "losses": 0, "win_rate": 101.0, "best_streak": 1},
                    {"wins": "x", "losses": 0, "win_rate": 0.0, "best_streak": 0}):
            with self.subTest(bad=bad):
                self.assertIsNone(game_overlay.normalize_lifetime(bad))

    def test_scope_switches_the_displayed_totals_but_not_the_streak(self):
        import game_overlay
        overlay = game_overlay.GameOverlay.__new__(game_overlay.GameOverlay)
        overlay.last_stats = {"wins": 2, "losses": 1, "streak": 2, "best_streak": 2,
                              "win_rate": 66.7, "status": "", "status_level": 0}
        overlay._lifetime = {"wins": 40, "losses": 10, "best_streak": 9, "win_rate": 80.0}
        overlay._stats_scope = "session"
        self.assertEqual(overlay._display_values(), (2, 1, 66.7, 2, ""))
        overlay._stats_scope = "lifetime"
        self.assertEqual(overlay._display_values(), (40, 10, 80.0, 9, "累計 "))
        # Without lifetime totals the session values remain in use.
        overlay._lifetime = None
        self.assertEqual(overlay._display_values(), (2, 1, 66.7, 2, ""))

    def test_mid_match_scope_change_is_applied_and_repaints(self):
        import game_overlay
        import queue as queue_module
        overlay = game_overlay.GameOverlay.__new__(game_overlay.GameOverlay)
        overlay._stats_scope = "session"
        overlay._lifetime = None
        overlay._lifetime_queue = queue_module.Queue()
        overlay._render = Mock()
        overlay._lifetime_queue.put({"wins": 5, "losses": 5, "best_streak": 3, "win_rate": 50.0})
        with patch.object(game_overlay, "load_config",
                          return_value={"overlay_stats_scope": "lifetime"}):
            overlay._drain_display_scope()
        self.assertEqual(overlay._stats_scope, "lifetime")
        self.assertEqual(overlay._lifetime["wins"], 5)
        with patch.object(game_overlay, "load_config",
                          return_value={"overlay_stats_scope": "session"}):
            overlay._drain_display_scope()
        self.assertEqual(overlay._stats_scope, "session")
        # An unreadable config must never break the running overlay.
        with patch.object(game_overlay, "load_config", side_effect=OSError("gone")):
            overlay._drain_display_scope()
        self.assertEqual(overlay._stats_scope, "session")
        self.assertEqual(overlay._render.call_count, 3)

    def test_browser_overlay_consumes_the_same_contract(self):
        source = (ROOT / "overlay.html").read_text(encoding="utf-8")
        self.assertIn('es.addEventListener("lifetime"', source)
        self.assertIn('c.overlay_stats_scope==="lifetime"', source)
        self.assertIn("function statsView()", source)
        # Streak and status keep coming from the session payload.
        self.assertIn("連勝 ${s.streak}", source)


class PurgeStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.store = HistoryStore(self.root)
        self.store.start_session()
        self.record((100.0, 200.0, 300.0))
        # Those three belong to a finished session; a new one is now open, so
        # they are ordinary past history rather than live-session rows.
        self.store.close_session("test")
        self.session = self.store.start_session()

    def record(self, timestamps, prefix="e"):
        for index, created_at in enumerate(timestamps):
            self.store.record_result(f"{prefix}{index}", "win", "test",
                                     {"streak": index + 1, "wins": index + 1, "losses": 0},
                                     created_at=created_at)
            self.store.create_match_context(f"c{prefix}{index}", f"{prefix}{index}",
                                            result_detected_at=created_at)

    def rows(self, table):
        with self.store._connection() as connection:
            return connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def test_purge_before_keeps_the_boundary_and_cascades_contexts(self):
        outcome = self.store.purge_before(200.0)
        self.assertEqual(outcome["removed_matches"], 1)
        self.assertEqual(self.rows("matches"), 2)
        self.assertEqual(self.rows("match_contexts"), 2)
        summary = self.store.lifetime_summary()
        self.assertEqual((summary["wins"], summary["best_streak"]), (2, 3))

    def test_a_cutoff_inside_the_active_session_is_refused_before_any_delete(self):
        self.record((400.0, 500.0), prefix="live")
        before = (self.rows("matches"), self.rows("match_contexts"),
                  self.rows("sessions"), self.store.lifetime_summary())
        for cutoff in (450.0, 500.0, 1000.0):
            with self.subTest(cutoff=cutoff):
                with self.assertRaises(ActiveSessionOverlap) as caught:
                    self.store.purge_before(cutoff)
                self.assertGreaterEqual(caught.exception.matches, 1)
                self.assertEqual((self.rows("matches"), self.rows("match_contexts"),
                                  self.rows("sessions"), self.store.lifetime_summary()), before)
                self.assertEqual(self.store.current_session_id, self.session)

    def test_a_cutoff_at_the_first_active_row_leaves_the_session_untouched(self):
        self.record((400.0, 500.0), prefix="live")
        # created_at < cutoff is strict, so the row at exactly 400.0 stays and
        # the active session loses nothing.
        outcome = self.store.purge_before(400.0)
        self.assertEqual(outcome["removed_matches"], 3)
        self.assertEqual(self.rows("matches"), 2)
        self.assertEqual(self.store.current_session_id, self.session)
        self.assertEqual(self.store.lifetime_summary()["wins"], 2)

    def test_purge_all_is_not_restricted_by_the_active_session(self):
        self.record((400.0,), prefix="live")
        outcome = self.store.purge_all()
        self.assertEqual(outcome["removed_matches"], 4)
        self.assertEqual(self.rows("matches"), 0)

    def test_purge_all_clears_everything_and_keeps_recording_possible(self):
        outcome = self.store.purge_all()
        self.assertEqual(outcome["removed_matches"], 3)
        self.assertEqual((self.rows("matches"), self.rows("match_contexts")), (0, 0))
        self.assertEqual(self.rows("sessions"), 1, "a usable session must survive")
        self.assertIsNotNone(self.store.current_session_id)
        self.assertEqual(self.store.lifetime_summary()["wins"], 0)
        self.store.record_result("after", "win", "test", {"streak": 1, "wins": 1, "losses": 0})
        self.assertEqual(self.store.lifetime_summary()["wins"], 1)

    def test_a_failure_midway_leaves_the_history_unchanged(self):
        import sqlite3 as sqlite
        real_connect = sqlite.connect

        class FailingConnection(sqlite.Connection):
            def execute(self, sql, *args):
                if "UPDATE sessions" in sql or "DELETE FROM sessions" in sql:
                    raise RuntimeError("simulated failure")
                return super().execute(sql, *args)

        def connect(*args, **kwargs):
            return real_connect(*args, **dict(kwargs, factory=FailingConnection))

        for method, arguments in ((HistoryStore.purge_before, (150.0,)),
                                  (HistoryStore.purge_all, ())):
            with self.subTest(method=method.__name__):
                with patch("history_store.sqlite3.connect", connect):
                    with self.assertRaises(RuntimeError):
                        method(self.store, *arguments)
                self.assertEqual(self.rows("matches"), 3, "a failed purge must roll back")
                self.assertEqual(self.rows("match_contexts"), 3)
                self.assertEqual(self.store.current_session_id, self.session)


class LiveServerPurgeTests(unittest.TestCase):
    """Real server.main, real history.db, isolated LOCALAPPDATA and port."""

    # server.py owns process-global state, so the whole class shares one
    # instance, exactly as the shipped Tracker runs it.
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="ac6-purge-")
        cls.data = Path(cls.directory.name) / "AC6WinLossTracker"
        cls.data.mkdir()
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            cls.port = reservation.getsockname()[1]
        cls.config = cls.data / "config.json"
        cls.write_config()
        cls.patches = [
            patch.dict(os.environ, {"LOCALAPPDATA": cls.directory.name}),
            patch.object(config_utils, "CONFIG_PATH", cls.config),
        ]
        for patcher in cls.patches:
            patcher.start()
        config_utils._last_good = config_utils._last_good_signature = None
        import server  # Imported under the isolated root so its globals match.
        cls.server = server
        cls.ready = threading.Event()
        cls.thread = threading.Thread(target=server.main,
                                      kwargs={"on_ready": cls.ready.set}, daemon=True)
        cls.thread.start()
        assert cls.ready.wait(15), "server.main did not become ready"

    @classmethod
    def write_config(cls, **overrides):
        payload = {
            "config_version": config_utils.CONFIG_VERSION, "port": cls.port,
            "stats_enabled": True, "result_detector_enabled": False,
            "effect_screenshot_enabled": False, "effect_enabled": True,
            "overlay_stats_scope": "session",
        }
        payload.update(overrides)
        cls.config.write_text(json.dumps(payload), encoding="utf-8")
        config_utils._last_good = config_utils._last_good_signature = None

    @classmethod
    def tearDownClass(cls):
        if cls.ready.is_set():
            try:
                cls.post("/api/system/shutdown")
            except Exception:
                pass
        cls.thread.join(10)
        for patcher in reversed(cls.patches):
            patcher.stop()
        cls.directory.cleanup()

    def setUp(self):
        self.write_config()
        self.server.history.purge_all()
        self.server.history_event_ids.clear()
        self.server.stats.reset()
        self.server.result_gate.clear_for_manual_correction()
        self.server.invalidate_dashboard_summary()

    @classmethod
    def post(cls, endpoint, payload=None, token=None):
        body = b"" if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"http://127.0.0.1:{cls.port}{endpoint}", data=body, method="POST",
            headers={"X-Control-Token": cls.server.CONTROL_TOKEN if token is None else token,
                     "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))

    @classmethod
    def get(cls, endpoint):
        with urllib.request.urlopen(
            f"http://127.0.0.1:{cls.port}{endpoint}", timeout=10
        ) as response:
            return json.loads(response.read().decode("utf-8"))

    def seed(self, timestamps, prefix="seed"):
        """Record into the session that is currently open."""
        for index, created_at in enumerate(timestamps):
            self.server.history.record_result(
                f"{prefix}-{index}", "win", "manual",
                {"streak": index + 1, "wins": index + 1, "losses": 0}, created_at=created_at,
            )
        self.server.invalidate_dashboard_summary()

    def seed_closed(self, timestamps, prefix="old"):
        """Record into a session that is then closed, as a previous run leaves it."""
        self.seed(timestamps, prefix)
        self.server.history.close_session("test")
        self.server.history.start_session()
        self.server.history_event_ids.clear()
        self.server.invalidate_dashboard_summary()

    def test_settings_client_purges_before_a_local_date_boundary(self):
        cutoff = settings.cutoff_for_date(day_text())
        self.seed_closed([cutoff - 1, cutoff, cutoff + 1])
        preview = history_analytics.count_before(self.data, cutoff)
        self.assertEqual((preview["removable"], preview["kept"]), (1, 2))
        self.assertEqual(preview["active_session_removable"], 0)
        with patch("subprocess.Popen", side_effect=AssertionError("unexpected spawn")):
            outcome = settings.purge_history_before(cutoff)
        self.assertEqual(outcome["removed_matches"], 1)
        self.assertEqual(self.get("/api/dashboard/summary")["lifetime"]["wins"], 2)
        # The boundary row itself is kept.
        self.assertEqual(history_analytics.count_before(self.data, cutoff)["removable"], 0)

    def test_a_cutoff_crossing_the_active_session_is_refused_and_changes_nothing(self):
        cutoff = settings.cutoff_for_date(day_text())
        # A session that began before the cutoff and is still open, which is
        # what an overnight session looks like.
        self.seed_closed([cutoff - 5000], prefix="closed")
        self.seed([cutoff - 100, cutoff - 50, cutoff + 10], prefix="live")
        stats_file = self.data / "stats.json"
        before = {
            "stats_bytes": stats_file.read_bytes(),
            "session_id": self.server.history.current_session_id,
            "lifetime": self.server.history.lifetime_summary(),
            "total": history_analytics.count_before(self.data, time.time())["total"],
        }
        preview = history_analytics.count_before(self.data, cutoff)
        self.assertEqual(preview["removable"], 3)
        self.assertEqual(preview["active_session_removable"], 2)
        self.assertEqual(preview["active_session_id"], before["session_id"])

        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.post("/api/history/purge", {"mode": "before", "cutoff": cutoff})
        self.assertEqual(caught.exception.code, 409)
        body = json.loads(caught.exception.read().decode("utf-8"))
        caught.exception.close()
        self.assertEqual(body["error"], history_analytics.ACTIVE_SESSION_PURGE_MESSAGE)
        self.assertEqual(body["active_session_matches"], 2)

        # The settings-window client surfaces the Tracker's own explanation.
        with patch("subprocess.Popen", side_effect=AssertionError("unexpected spawn")):
            with self.assertRaises(settings.TrackerUnavailable) as refused:
                settings.purge_history_before(cutoff)
        self.assertIn("現在のセッション", str(refused.exception))

        self.assertEqual(stats_file.read_bytes(), before["stats_bytes"])
        self.assertEqual(self.server.history.current_session_id, before["session_id"])
        self.assertEqual(self.server.history.lifetime_summary(), before["lifetime"])
        self.assertEqual(history_analytics.count_before(self.data, time.time())["total"],
                         before["total"])
        self.assertIn("wins", self.get("/stats"))

    def test_a_cutoff_at_the_active_session_boundary_is_allowed(self):
        cutoff = settings.cutoff_for_date(day_text())
        self.seed_closed([cutoff - 200], prefix="closed")
        # The live session starts exactly on the boundary: "created_at < cutoff"
        # is strict, so nothing it counts is in range.
        self.seed([cutoff, cutoff + 1], prefix="live")
        preview = history_analytics.count_before(self.data, cutoff)
        self.assertEqual((preview["removable"], preview["active_session_removable"]), (1, 0))
        with patch("subprocess.Popen", side_effect=AssertionError("unexpected spawn")):
            outcome = settings.purge_history_before(cutoff)
        self.assertEqual(outcome["removed_matches"], 1)
        self.assertEqual(self.get("/api/dashboard/summary")["lifetime"]["wins"], 2)

    def test_purge_all_resets_the_session_and_recording_still_works(self):
        detector_before = self.server.detector_snapshot()["status"]
        self.assertTrue(self.server.record_result("win", "manual"))
        self.seed([1000.0, 2000.0])
        self.assertGreaterEqual(self.get("/api/dashboard/summary")["lifetime"]["wins"], 3)
        with patch("subprocess.Popen", side_effect=AssertionError("unexpected spawn")):
            outcome = settings.purge_all_history()
        self.assertEqual(outcome["mode"], "all")
        self.assertTrue(outcome["session_reset"])
        summary = self.get("/api/dashboard/summary")
        self.assertEqual(summary["lifetime"]["wins"], 0)
        self.assertEqual(summary["session"]["wins"], 0)
        self.assertEqual(summary["history_health"]["status"], "active")
        # The detector supervisor and HTTP server are untouched by a purge.
        self.assertEqual(self.server.detector_snapshot()["status"], detector_before)
        self.assertIn("wins", self.get("/stats"))
        # A purge locks the gate, so wait it out and confirm recording resumes.
        time.sleep(5.05)
        self.assertTrue(self.server.record_result("win", "manual"))
        self.assertEqual(self.get("/api/dashboard/summary")["lifetime"]["wins"], 1)

    def test_a_future_cutoff_is_refused_by_the_api_and_changes_nothing(self):
        # Old history in a closed session, plus a live session row, so a future
        # cutoff would be able to remove matches that stats.json still counts.
        self.seed_closed([1000.0, 2000.0])
        self.assertTrue(self.server.record_result("win", "manual"))
        stats_file = self.data / "stats.json"
        before = {
            "stats_bytes": stats_file.read_bytes(),
            "stats": self.server.stats.snapshot(),
            "session_id": self.server.history.current_session_id,
            "lifetime": self.server.history.lifetime_summary(),
            "rows": history_analytics.count_before(self.data, time.time() + 86400)["total"],
        }
        for offset in (1, 2, 30):
            cutoff = datetime.strptime(day_text(offset), "%Y-%m-%d").timestamp()
            with self.subTest(offset=offset):
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    self.post("/api/history/purge", {"mode": "before", "cutoff": cutoff})
                self.assertEqual(caught.exception.code, 400)
                caught.exception.close()
                # The settings window refuses the same date before sending it.
                with self.assertRaises(ValueError):
                    settings.cutoff_for_date(day_text(offset))
        self.assertEqual(stats_file.read_bytes(), before["stats_bytes"])
        self.assertEqual(self.server.stats.snapshot(), before["stats"])
        self.assertEqual(self.server.history.current_session_id, before["session_id"])
        self.assertEqual(self.server.history.lifetime_summary(), before["lifetime"])
        self.assertEqual(
            history_analytics.count_before(self.data, time.time() + 86400)["total"],
            before["rows"],
        )
        # Today itself still works and leaves the live session's own row alone.
        outcome = settings.purge_history_before(settings.cutoff_for_date(day_text()))
        self.assertEqual(outcome["removed_matches"], 2)
        self.assertEqual(self.server.stats.snapshot()["wins"], before["stats"]["wins"])

    def test_bad_requests_are_refused_without_touching_history(self):
        self.seed([1000.0, 2000.0])
        for payload in ({}, {"mode": "everything"}, {"mode": "before"},
                        {"mode": "before", "cutoff": "yesterday"},
                        {"mode": "before", "cutoff": True},
                        {"mode": "before", "cutoff": -1},
                        {"mode": "before", "cutoff": time.time() + 10 * 86400}):
            with self.subTest(payload=payload):
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    self.post("/api/history/purge", payload)
                self.assertEqual(caught.exception.code, 400)
                caught.exception.close()
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.post("/api/history/purge", {"mode": "all"}, token="wrong")
        self.assertEqual(caught.exception.code, 403)
        caught.exception.close()
        self.assertEqual(self.get("/api/dashboard/summary")["lifetime"]["wins"], 2)

    def test_effect_publish_follows_the_setting_while_the_tracker_runs(self):
        published = []
        with patch.object(self.server, "publish",
                          side_effect=lambda *a, **k: published.append(a[0])):
            self.server.stats.reset()
            for streak in range(1, 6):
                self.server.result_gate.clear_for_manual_correction()
                self.assertTrue(self.server.record_result("win", "manual"))
            self.assertIn("effect", published)

            # Turned off mid-session, exactly as the settings window writes it.
            self.write_config(effect_enabled=False)
            published.clear()
            for streak in range(6, 11):
                self.server.result_gate.clear_for_manual_correction()
                self.assertTrue(self.server.record_result("win", "manual"))
        self.assertNotIn("effect", published, "effects must stop when the setting is off")
        self.assertEqual(self.server.stats.snapshot()["streak"], 10, "counting is unaffected")
        self.assertEqual(self.get("/api/dashboard/summary")["lifetime"]["wins"], 10)

    def test_live_diagnostics_flush_persists_the_reason_a_report_needs(self):
        # The recorder is a process-wide singleton; ask it where it writes.
        log = self.server.RECORDER.log_path
        before = log.read_text(encoding="utf-8") if log.exists() else ""
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.post("/api/diagnostics/flush", {}, token="wrong")
        self.assertEqual(caught.exception.code, 403)
        caught.exception.close()

        with patch("subprocess.Popen", side_effect=AssertionError("unexpected spawn")):
            outcome = settings.flush_live_diagnostics()
        self.assertTrue(outcome["ok"])
        self.assertIn("status", outcome["detector"])
        rows = [json.loads(line) for line in
                log.read_text(encoding="utf-8")[len(before):].splitlines() if line]
        requested = [row for row in rows if row["kind"] == "diagnostics_requested"]
        self.assertEqual(len(requested), 1)
        # Everything a "played matches but 0/0" report has to separate.
        for key in ("detector", "stats", "history_health", "config_health"):
            self.assertIn(key, requested[0])
        self.assertIn("status", requested[0]["detector"])
        # The detector never stops just because a report was requested.
        self.assertIn("wins", self.get("/stats"))

    def test_config_endpoint_publishes_the_display_scope_for_the_browser_overlay(self):
        self.assertEqual(self.get("/config")["overlay_stats_scope"], "session")
        self.write_config(overlay_stats_scope="lifetime")
        self.assertEqual(self.get("/config")["overlay_stats_scope"], "lifetime")
        self.assertFalse(self.get("/config")["effect_enabled"] is None)


class StoppedTrackerTests(unittest.TestCase):
    def test_maintenance_reports_a_stopped_tracker_instead_of_starting_one(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / "config.json").write_text(
                json.dumps(config_utils.DEFAULT_CONFIG), encoding="utf-8")
            with patch.object(config_utils, "CONFIG_PATH", root / "config.json"), \
                 patch("subprocess.Popen", side_effect=AssertionError("unexpected spawn")):
                for call in (settings.purge_all_history,
                             lambda: settings.purge_history_before(0.0),
                             settings.read_runtime):
                    with self.assertRaises(settings.TrackerUnavailable) as caught:
                        call()
                    self.assertIn("Tracker", str(caught.exception))
                for broken in ('{"port": 1, "token": "x"}', "{}", "not json",
                               '{"port": 9000, "token": "short"}'):
                    (root / ".runtime.json").write_text(broken, encoding="utf-8")
                    with self.assertRaises(settings.TrackerUnavailable):
                        settings.read_runtime()
            self.assertEqual(sorted(p.name for p in root.iterdir()),
                             [".runtime.json", "config.json"])


class DiagnosticReportTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.home = Path(self.directory.name) / "home"  # No Desktop: falls back to data_dir.
        self.home.mkdir()

    def test_reuses_the_existing_recorder_export(self):
        import diagnostics
        with patch.object(diagnostics.RECORDER, "export",
                          return_value=Path("C:/tmp/report.zip")) as export:
            self.assertEqual(settings.create_diagnostic_report(), Path("C:/tmp/report.zip"))
        export.assert_called_once_with()

    def test_real_export_contains_only_documented_content(self):
        import diagnostics
        diagnostics.RECORDER.record("selftest", note="unit")
        with patch.object(diagnostics.Path, "home", return_value=self.home), \
             patch("subprocess.Popen", side_effect=AssertionError("unexpected spawn")):
            report = settings.create_diagnostic_report()
        self.addCleanup(lambda: report.unlink(missing_ok=True))
        self.assertTrue(report.exists())
        self.assertTrue(report.name.startswith("AC6-Tracker-Diagnostics-"))
        with zipfile.ZipFile(report) as archive:
            names = archive.namelist()
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        self.assertIn("detector.jsonl", names)
        self.assertNotIn("history.db", names)
        self.assertTrue(all(not name.endswith(".db") for name in names))
        self.assertTrue(all(name == "manifest.json" or not name.startswith("roi/")
                            or name.endswith(".png") for name in names))
        self.assertIn("no full-screen capture", manifest["privacy"])
        # The privacy text shown in the UI must match what the exporter collects.
        self.assertIn("detector.jsonl", settings.DIAGNOSTIC_PRIVACY)
        self.assertIn("history.db", settings.DIAGNOSTIC_PRIVACY)
        self.assertIn("フルスクリーン画像", settings.DIAGNOSTIC_PRIVACY)

    def test_every_runtime_dependency_is_reported(self):
        import diagnostics
        with patch.object(diagnostics.Path, "home", return_value=self.home):
            report = settings.create_diagnostic_report()
        self.addCleanup(lambda: report.unlink(missing_ok=True))
        with zipfile.ZipFile(report) as archive:
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        # A missing capture/classifier package is the first thing to rule out.
        # Distribution names are compared case-insensitively so the assertion
        # states the requirement, not one spelling of it.
        self.assertEqual({name.lower() for name in manifest["dependency_status"]},
                         {"mss", "numpy", "opencv-python", "pillow",
                          "ttkbootstrap", "windows-capture"})
        for name, entry in manifest["dependency_status"].items():
            with self.subTest(dependency=name):
                self.assertIn("available", entry)
                self.assertIn("version", entry)
        self.assertIn("依存パッケージの有無", settings.DIAGNOSTIC_PRIVACY)

    def test_install_and_startup_evidence_is_included(self):
        import diagnostics
        from app_paths import data_dir
        root = data_dir()
        (root / "installed-version.json").write_text(
            json.dumps({"channel": "stable", "version": "1.0.1",
                        "resolved_commit": "a" * 40, "python_role": "preferred"}),
            encoding="utf-8")
        (root / "startup.log").write_text("launch line\n", encoding="utf-8")
        (diagnostics.RECORDER.root / "effect-screenshot.jsonl").write_text(
            '{"status":"saved"}\n', encoding="utf-8")
        self.addCleanup(lambda: (root / "installed-version.json").unlink(missing_ok=True))
        self.addCleanup(lambda: (root / "startup.log").unlink(missing_ok=True))
        with patch.object(diagnostics.Path, "home", return_value=self.home):
            report = settings.create_diagnostic_report()
        self.addCleanup(lambda: report.unlink(missing_ok=True))
        with zipfile.ZipFile(report) as archive:
            names = archive.namelist()
        for expected in ("installed-version.json", "startup.log", "effect-screenshot.jsonl"):
            with self.subTest(expected=expected):
                self.assertIn(expected, names)
        self.assertNotIn("history.db", names)
        for expected in ("installed-version.json", "startup.log", "effect-screenshot.jsonl"):
            self.assertIn(expected, settings.DIAGNOSTIC_PRIVACY)

    def test_failure_is_reported_and_raises_no_process(self):
        import diagnostics
        with patch.object(diagnostics.RECORDER, "export", side_effect=OSError("disk full")), \
             patch("subprocess.Popen", side_effect=AssertionError("unexpected spawn")):
            with self.assertRaises(OSError):
                settings.create_diagnostic_report()

    def test_export_still_works_when_no_tracker_is_running(self):
        import diagnostics
        with tempfile.TemporaryDirectory() as name:
            with patch.object(config_utils, "CONFIG_PATH", Path(name) / "config.json"):
                self.assertIsNone(settings.flush_live_diagnostics())
                with patch.object(diagnostics.Path, "home", return_value=self.home):
                    report = settings.create_diagnostic_report()
        self.addCleanup(lambda: report.unlink(missing_ok=True))
        self.assertTrue(report.exists())


class CaptureGapDiagnosticsTests(unittest.TestCase):
    """A session that never captures a frame must still explain itself."""

    def test_capture_gap_rows_are_recorded_and_rate_limited(self):
        import result_detector
        recorder = Mock()
        detector = result_detector.ResultDetector.__new__(result_detector.ResultDetector)
        detector.diagnostics = recorder
        detector.capture = Mock(status="AC6 window unavailable or minimized")
        detector.state = Mock()
        detector.state.snapshot.return_value = {"armed": False}
        detector._last_capture_gap_status = None
        detector._last_capture_gap_log = 0.0

        clock = [1000.0]
        with patch.object(result_detector.time, "monotonic", lambda: clock[0]):
            detector._record_capture_gap()
            self.assertEqual(recorder.record.call_count, 1)
            clock[0] += 1.0
            detector._record_capture_gap()
            self.assertEqual(recorder.record.call_count, 1, "unchanged status must not spam")
            # A different reason is always worth recording immediately.
            detector.capture.status = "WGC unavailable: RuntimeError: worker timed out"
            detector._record_capture_gap()
            self.assertEqual(recorder.record.call_count, 2)
            # The same reason is repeated only after the rate-limit window.
            clock[0] += result_detector.CAPTURE_GAP_LOG_SECONDS + 1
            detector._record_capture_gap()
            self.assertEqual(recorder.record.call_count, 3)

        kind, payload = recorder.record.call_args.args[0], recorder.record.call_args.kwargs
        self.assertEqual(kind, "capture_unavailable")
        self.assertIn("WGC unavailable", payload["status"])
        self.assertEqual(payload["state"], {"armed": False})

    def test_no_recorder_means_no_work(self):
        import result_detector
        detector = result_detector.ResultDetector.__new__(result_detector.ResultDetector)
        detector.diagnostics = None
        detector._last_capture_gap_status = None
        detector._last_capture_gap_log = 0.0
        detector._record_capture_gap()  # Must not raise without a capture attribute.


@GUI
class SettingsWindowTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "config.json"
        self.path.write_text(json.dumps(config_utils.DEFAULT_CONFIG), encoding="utf-8")
        patcher = patch.object(config_utils, "CONFIG_PATH", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)
        config_utils._last_good = config_utils._last_good_signature = None
        self.no_spawn = patch("subprocess.Popen", side_effect=AssertionError("unexpected spawn"))
        self.no_spawn.start()
        self.addCleanup(self.no_spawn.stop)
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.window = settings.open_settings(self.root)
        self.root.update()

    def pump(self, predicate, timeout=10.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.root.update()
            if predicate():
                return True
            time.sleep(0.02)
        return False

    def test_every_option_saves_from_one_window(self):
        self.assertTrue(self.window.effect_enabled.get())
        self.assertEqual(self.window.scope.get(), "session")
        self.window.effect_enabled.set(False)
        self.window.enabled.set(True)
        self.window.scope.set("lifetime")
        self.window.save_button.invoke()
        self.assertEqual(settings.read_settings(), {
            "effect_enabled": False,
            "effect_screenshot_enabled": True,
            "overlay_stats_scope": "lifetime",
        })
        self.window.window.withdraw()
        settings.open_settings(self.root)
        self.assertFalse(self.window.effect_enabled.get())
        self.assertEqual(self.window.scope.get(), "lifetime")

    def test_destructive_actions_require_confirmation(self):
        preview = ("all", {"total_matches": 7})
        self.window._messagebox = Mock()
        self.window._messagebox.askyesno.return_value = False
        with patch.object(settings, "purge_all_history") as purge:
            self.window._purge_preview_done(True, preview)
            purge.assert_not_called()
        self.assertIn("中止", self.window.purge_status.cget("text"))
        self.assertIn("7", self.window._messagebox.askyesno.call_args.args[1])

        self.window._messagebox.askyesno.return_value = True
        with patch.object(settings, "purge_all_history",
                          return_value={"mode": "all", "removed_matches": 7,
                                        "session_reset": True}) as purge:
            self.window._purge_preview_done(True, preview)
            self.assertTrue(self.pump(lambda: "7 件を削除" in
                                      self.window.purge_status.cget("text")))
            purge.assert_called_once_with()

    def test_before_date_confirmation_states_the_boundary(self):
        self.window._messagebox = Mock()
        self.window._messagebox.askyesno.return_value = False
        self.window._purge_preview_done(True, ("before", {
            "cutoff": 1000.0, "cutoff_text": "2026-09-09 00:00:00",
            "removable": 4, "kept": 6, "total": 10,
        }))
        question = self.window._messagebox.askyesno.call_args.args[1]
        self.assertIn("2026-09-09 00:00:00", question)
        self.assertIn("4 件", question)
        self.assertIn("6 件は残ります", question)
        self.window._purge_preview_done(True, ("before", {
            "cutoff": 1000.0, "cutoff_text": "2026-09-09 00:00:00",
            "removable": 0, "kept": 10, "total": 10,
        }))
        self.assertIn("履歴はありません", self.window.purge_status.cget("text"))

    def test_a_cutoff_crossing_the_active_session_is_refused_by_the_window(self):
        self.window._messagebox = Mock()
        with patch.object(settings, "control_request") as control:
            self.window._purge_preview_done(True, ("before", {
                "cutoff": 1000.0, "cutoff_text": "2026-09-09 00:00:00",
                "removable": 5, "kept": 3, "total": 8,
                "active_session_id": 7, "active_session_removable": 2,
            }))
            control.assert_not_called()
        self.window._messagebox.askyesno.assert_not_called()
        message = self.window.purge_status.cget("text")
        self.assertIn("現在のセッションに削除対象の試合が含まれています", message)
        self.assertIn("Trackerを再起動", message)
        self.assertIn("2 件", message)
        self.assertEqual(self.window._busy, set())
        # With none of the active session in range the confirmation still runs.
        self.window._messagebox.askyesno.return_value = False
        self.window._purge_preview_done(True, ("before", {
            "cutoff": 1000.0, "cutoff_text": "2026-09-09 00:00:00",
            "removable": 5, "kept": 3, "total": 8,
            "active_session_id": 7, "active_session_removable": 0,
        }))
        self.window._messagebox.askyesno.assert_called_once()

    def test_invalid_date_never_reaches_the_tracker(self):
        self.window.cutoff_date.set("2026/09/09")
        with patch.object(settings, "control_request") as control:
            self.window.confirm_purge_before()
            control.assert_not_called()
        self.assertIn("YYYY-MM-DD", self.window.purge_status.cget("text"))

    def test_a_future_date_is_refused_by_the_window(self):
        for offset in (1, 5, 365):
            with self.subTest(offset=offset):
                self.window.cutoff_date.set(day_text(offset))
                with patch.object(settings, "control_request") as control, \
                     patch.object(settings.history_analytics, "count_before") as preview:
                    self.window.confirm_purge_before()
                    self.root.update()
                    control.assert_not_called()
                    preview.assert_not_called()
                self.assertIn("今日まで", self.window.purge_status.cget("text"))
                self.assertEqual(self.window._busy, set())
        # Today and earlier still reach the read-only preview.
        for offset in (0, -1):
            self.window.cutoff_date.set(day_text(offset))
            with patch.object(settings.history_analytics, "count_before") as preview:
                preview.return_value = {"cutoff": 0.0, "cutoff_text": "x",
                                        "removable": 0, "total": 0, "kept": 0}
                self.window.confirm_purge_before()
                self.assertTrue(self.pump(lambda: not self.window._busy))
                preview.assert_called_once()

    def test_repeated_diagnostic_clicks_run_one_export(self):
        gate = threading.Event()
        self.addCleanup(gate.set)
        calls = []

        def slow_export():
            calls.append(1)
            gate.wait(5)
            return Path(self.directory.name) / "AC6-Tracker-Diagnostics-test.zip"

        with patch.object(settings, "create_diagnostic_report", side_effect=slow_export):
            for _ in range(5):
                self.window.create_report()
            self.root.update()
            self.assertEqual(len(calls), 1, "a second export must not start")
            self.assertIn("作成中です", self.window.diagnostics_status.cget("text"))
            gate.set()
            self.assertTrue(self.pump(
                lambda: "作成しました" in self.window.diagnostics_status.cget("text")))
        self.assertEqual(str(self.window.open_report_button.cget("state")), "normal")
        self.assertIn("AC6-Tracker-Diagnostics-test.zip",
                      self.window.diagnostics_status.cget("text"))

    def test_diagnostic_failure_keeps_the_window_usable(self):
        with patch.object(settings, "create_diagnostic_report", side_effect=OSError("denied")):
            self.window.create_report()
            self.assertTrue(self.pump(
                lambda: "作成できませんでした" in self.window.diagnostics_status.cget("text")))
        self.assertIn("Trackerは動作したまま", self.window.diagnostics_status.cget("text"))
        self.assertEqual(str(self.window.open_report_button.cget("state")), "disabled")
        self.assertEqual(self.window._busy, set())

    def test_update_check_only_enables_install_when_newer(self):
        for message, expected in (
            ("新しいバージョン v2.0.0 があります（現在 1.0.1）。", "normal"),
            ("最新の公開バージョンです（1.0.1）。", "disabled"),
            ("更新を確認できませんでした: offline", "disabled"),
        ):
            with self.subTest(message=message):
                with patch.object(settings, "check_latest_release", return_value=message):
                    self.window.check()
                    self.assertTrue(self.pump(lambda: not self.window.checking))
                self.assertEqual(self.window.update_status.cget("text"), message)
                self.assertEqual(str(self.window.install_button.cget("state")), expected)

    def test_install_button_only_opens_the_official_page(self):
        with patch.object(settings, "open_releases_page", return_value=True) as opened:
            self.window.open_release_page()
        opened.assert_called_once_with()
        self.assertIn("正式インストーラー", self.window.update_status.cget("text"))
        with patch("settings_window.webbrowser.open", return_value=True) as browser:
            settings.open_releases_page()
        browser.assert_called_once_with(settings.RELEASES_PAGE_URL, new=2)
        self.assertTrue(settings.RELEASES_PAGE_URL.startswith(
            "https://github.com/TullysAC6/ac6-winloss-tracker/releases"))

    def test_analytics_and_export_report_failures_in_the_window(self):
        self.window.refresh_analytics()
        self.assertTrue(self.pump(
            lambda: "集計できませんでした" in self.window.analytics_status.cget("text")))
        with patch.object(self.window, "_filedialog") as dialog:
            dialog.asksaveasfilename.return_value = ""
            self.window.export_csv()
        self.assertIn("中止", self.window.analytics_status.cget("text"))

    def test_analytics_renders_a_real_summary(self):
        store = HistoryStore(self.path.parent)
        store.start_session()
        now = time.time()
        for index, result in enumerate(("win", "win", "loss", "draw")):
            store.record_result(f"a{index}", result, "test",
                                {"streak": 2, "wins": 2, "losses": 1}, created_at=now - index)
        self.window.refresh_analytics()
        self.assertTrue(self.pump(
            lambda: "集計しました" in self.window.analytics_status.cget("text")))
        text = self.window.analytics_text.get("1.0", "end")
        for expected in ("今日", "今週（月曜開始）", "今月", "全期間",
                         "直近10戦", "直近30戦", "直近100戦",
                         "DRAWは勝率の分母に含みません"):
            self.assertIn(expected, text)
        destination = Path(self.directory.name) / "export.csv"
        with patch.object(self.window, "_filedialog") as dialog:
            dialog.asksaveasfilename.return_value = str(destination)
            self.window.export_csv()
            self.assertTrue(self.pump(
                lambda: "件を書き出しました" in self.window.analytics_status.cget("text")))
        self.assertTrue(destination.read_bytes().startswith(b"\xef\xbb\xbf"))

    def test_pending_work_is_cancelled_when_the_window_is_destroyed(self):
        with patch.object(settings, "create_diagnostic_report", return_value=Path("x.zip")):
            self.window.create_report()
            self.window.window.destroy()
            self.root.update()
        self.assertIsNone(self.window._task_poll_id)
        self.assertIsNone(self.window.poll_id)


if __name__ == "__main__":
    unittest.main()
