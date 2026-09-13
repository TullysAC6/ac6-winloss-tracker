"""An accepted result is durable in history and counted in stats -- or neither.

Independent review 5189617002 of 81b1824 installed a real SQLite trigger that
refused every match row, then recorded five WINs and a LOSE through the real
ResultGate. Stats and the overlay showed 5W/1L with a milestone-5 effect;
history showed nothing, every undo identity was null, and a successful
dashboard read reported history "active" again while rows still failed. A
restart could not bring those six matches back.

stats.json and history.db cannot share a transaction, so none of this is
atomic, and nothing here claims it is. What is asserted is the ordering the
Tracker relies on, and each failure's compensation:

* the history row is written first, from the stats the result would produce;
  if that fails nothing is counted: no streak, effect, undo identity or event;
* stats are counted second; if that fails the row is taken back out, and if
  history refuses even that, no further result is accepted until it is gone;
* undo removes a result from both stores or from neither;
* a failed write stays degraded until a write succeeds -- a read cannot clear it;
* with no history store at all, nothing is accepted.

Throughout: accepted results == durable history rows == counted stats results.

Faults are real: SQLite triggers in the isolated database, a directory where
stats.json's temporary file has to go, and a database whose session table
refuses rows at a real ``server.main`` startup. Every test owns its
LOCALAPPDATA and ports; the ResultGate is honoured by advancing a test clock.
"""
import importlib
import json
import os
import socket
import sqlite3
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import test_purge_history_writability as writable  # noqa: E402  the real-handler harness

REFUSE_ROWS = ("CREATE TRIGGER refuse_match_rows BEFORE INSERT ON matches "
               "BEGIN SELECT RAISE(ABORT, 'injected: match row refused'); END")
REFUSE_DELETES = ("CREATE TRIGGER refuse_match_deletes BEFORE DELETE ON matches "
                  "BEGIN SELECT RAISE(ABORT, 'injected: match delete refused'); END")
REFUSE_SESSIONS = ("CREATE TRIGGER refuse_sessions BEFORE INSERT ON sessions "
                   "BEGIN SELECT RAISE(ABORT, 'injected: session refused'); END")


def run_sql(database, statement):
    connection = sqlite3.connect(database)
    try:
        connection.execute(statement)
        connection.commit()
    finally:
        connection.close()


class DurableResultHarness(writable.WritableHistoryHarness):
    """The purge harness, counting every accepted result against both stores."""

    def setUp(self):
        self.accepted_rows = 0     # accepted results that must be durable in history
        self.accepted_session = 0  # accepted results the current stats session counts
        super().setUp()            # records one WIN and one LOSE through record()
        self.assert_counts_agree(self.observe(), "after seeding")

    def record(self, result, *, advance=True):
        accepted = super().record(result, advance=advance)
        if accepted:
            self.accepted_rows += 1
            self.accepted_session += 1
        return accepted

    def observe(self):
        state = super().observe()
        on_disk = json.loads((self.data / "stats.json").read_text(encoding="utf-8"))
        _, snapshot = self.reconnect(None)
        state["streak"] = (self.stats.snapshot()["streak"], on_disk["streak"],
                           snapshot["stats"]["streak"])
        return state

    # ---------------------------------------------------------------- faults
    def refuse(self, statement, name):
        run_sql(self.data / "history.db", statement)
        self.addCleanup(self.allow, name)

    def refuse_rows(self):
        self.refuse(REFUSE_ROWS, "refuse_match_rows")

    def allow(self, name):
        run_sql(self.data / "history.db", f"DROP TRIGGER IF EXISTS {name}")

    def dashboard_health(self):
        self.server.invalidate_dashboard_summary()
        return self.get("/api/dashboard/summary")["history_health"]["status"]

    # ------------------------------------------------------------ invariants
    def assert_counts_agree(self, state, where):
        """accepted results == durable history rows == counted stats results."""
        self.assertEqual(state["rows"][0], self.accepted_rows,
                         f"{where}: durable history rows differ from accepted results")
        self.assertEqual(sum(state["stats"]), self.accepted_session,
                         f"{where}: counted stats results differ from accepted results")
        self.assertEqual(sum(state["stats_json"]), self.accepted_session,
                         f"{where}: stats.json differs from accepted results")
        self.assertEqual(len(set(state["streak"])), 1,
                         f"{where}: streak differs across memory, stats.json and SSE "
                         f"{state['streak']}")

    def attempt(self, result, *, advance=True):
        """One result through the production path, with every contract checked."""
        before = self.observe()
        accepted = self.record(result, advance=advance)
        events = self.last_events
        after = self.observe()
        kinds = [kind for kind, _ in events]
        effects = [payload["milestone"] for kind, payload in events if kind == "effect"]
        if accepted:
            streak = before["streak"][0] + 1 if result == "win" else 0
            self.assertEqual(after["streak"][0], streak)
            self.assertEqual(effects, [streak] if result == "win" and streak % 5 == 0 else [],
                             "a milestone effect follows an accepted result, and only one")
            self.assertEqual(after["health"], "active")
        else:
            self.assertEqual(after["streak"], before["streak"], "a refused result moved the streak")
            self.assertEqual(effects, [], "a refused result published a milestone effect")
            self.assertEqual([kind for kind in kinds if kind in ("stats", "lifetime", "effect")],
                             [], "a refused result published an accepted-result event")
            self.assertEqual((after["lifetime"], after["sse_lifetime"]),
                             (before["lifetime"], before["sse_lifetime"]))
            self.assertEqual(after["undo"], before["undo"], "a refused result took an undo identity")
            self.assertEqual(after["health"], "degraded")
        self.assert_counts_agree(after, f"after {result}")
        return accepted, before, after


class RowWriteFailureIsNotAccepted(DurableResultHarness):

    def test_a_a_normal_win_is_durable_in_both_stores(self):
        accepted, before, after = self.attempt("win")
        self.assertTrue(accepted)
        self.assertEqual(after["stats"], (before["stats"][0] + 1, before["stats"][1]))
        self.assertEqual(after["lifetime"], (before["lifetime"][0] + 1, before["lifetime"][1]))

    def test_a_normal_result_takes_nothing_back(self):
        with patch.object(self.history, "discard_result",
                          wraps=self.history.discard_result) as discard:
            self.assertTrue(self.attempt("win")[0])
        discard.assert_not_called()

    def test_b_a_refused_row_counts_nothing_and_stays_degraded(self):
        self.refuse_rows()
        self.assertFalse(self.attempt("win")[0])
        for read in range(3):
            self.assertEqual(self.dashboard_health(), "degraded",
                             f"read {read}: a successful read reported history active "
                             "while rows still fail")
        # The refusal released its gate reservation, so the very next attempt
        # reaches history again -- and is refused again.
        self.assertFalse(self.attempt("win", advance=False)[0])
        failures = [call for call in self.server.RECORDER.record.call_args_list
                    if call.args[:1] == ("history_error",)
                    and call.kwargs.get("operation") == "record_result"]
        self.assertEqual(len(failures), 2)

    def test_c_five_refusals_at_the_milestone_boundary_publish_no_effect(self):
        for _ in range(4):
            self.assertTrue(self.attempt("win")[0])
        self.assertEqual(self.observe()["streak"][0], 4)
        self.refuse_rows()
        for _ in range(5):
            self.assertFalse(self.attempt("win")[0])
        self.allow("refuse_match_rows")
        accepted, _, after = self.attempt("win", advance=False)
        self.assertTrue(accepted)
        self.assertEqual(after["streak"][0], 5)  # attempt() requires exactly one effect, 5

    def test_c2_reviewer_reproduction_trigger_installed_before_the_purge(self):
        """Five WINs and a LOSE after a purge that ran with rows already refused.

        81b1824 counted 5W/1L, published a milestone-5 effect, kept every undo
        identity null and wrote no row; a restart could not recover them.
        """
        self.refuse_rows()
        status, body, state, _, _, _ = self.purge()
        self.assert_success_is_writable(status, body, state, "purge with rows refused")
        self.accepted_rows = self.accepted_session = 0
        for result in ("win",) * 5 + ("loss",):
            self.assertFalse(self.attempt(result)[0], f"{result} accepted with no durable row")
        state = self.observe()
        self.assertEqual((state["stats"], state["lifetime"], state["rows"][0], state["undo"]),
                         ((0, 0), (0, 0), 0, []))
        self.assertEqual(self.dashboard_health(), "degraded")
        status, body = self.post("/api/stats/undo")
        self.assertEqual((status, body["removed"]), (200, None), "there is nothing to undo")

        self.allow("refuse_match_rows")
        self.assertTrue(self.attempt("win", advance=False)[0])
        restarted = self.restart()
        self.assertEqual(restarted["lifetime"], (1, 0), "the accepted result survives a restart")
        self.accepted_session = 0
        self.assertTrue(self.attempt("loss")[0])

    def test_d_a_refused_lose_leaves_the_streak_alone(self):
        for _ in range(2):
            self.assertTrue(self.attempt("win")[0])
        self.assertEqual(self.observe()["streak"][0], 2)
        self.refuse_rows()
        accepted, before, after = self.attempt("loss")
        self.assertFalse(accepted)
        self.assertEqual(after["streak"][0], 2, "a LOSE that was not stored broke the streak")
        self.assertEqual(after["stats"], before["stats"])

    def test_e_recording_resumes_once_rows_can_be_written(self):
        self.refuse_rows()
        self.assertFalse(self.attempt("win")[0])
        self.allow("refuse_match_rows")
        self.assertTrue(self.attempt("win", advance=False)[0])
        self.assertEqual(self.dashboard_health(), "active")

    def test_g_restart_keeps_exactly_the_accepted_results(self):
        self.assertTrue(self.attempt("win")[0])
        self.refuse_rows()
        for result in ("win", "loss", "win"):
            self.assertFalse(self.attempt(result)[0])
        self.allow("refuse_match_rows")
        restarted = self.restart()
        self.accepted_session = 0
        self.assertEqual(restarted["rows"][0], self.accepted_rows)
        self.assertEqual(restarted["stats"], (0, 0))
        self.assertTrue(self.attempt("win")[0])

    def test_h_undo_removes_a_result_from_both_stores_or_neither(self):
        self.assertTrue(self.attempt("win")[0])
        self.refuse(REFUSE_DELETES, "refuse_match_deletes")
        before = self.observe()
        self.drain()
        status, body = self.post("/api/stats/undo")
        events = self.drain()
        after = self.observe()
        self.assertEqual(status, 500, body)
        self.assertIn("UndoFailed", body.get("error", ""))
        for key in ("stats", "stats_json", "streak", "rows", "lifetime", "undo"):
            self.assertEqual(after[key], before[key], f"a refused undo changed {key}")
        self.assertEqual(after["health"], "degraded")
        restored = self.last(events, "stats")
        self.assertEqual((restored["wins"], restored["losses"]), before["stats"],
                         "connected clients must be shown the session as it still is")
        self.assert_counts_agree(after, "after a refused undo")

        self.allow("refuse_match_deletes")
        body, before, after = self.undo()
        self.assertIsNotNone(body["removed"])
        self.accepted_rows -= 1
        self.accepted_session -= 1
        self.assertEqual(after["rows"][0], before["rows"][0] - 1)
        self.assert_counts_agree(after, "after the undo")
        self.assertEqual(after["health"], "active")

    def test_i_sse_replay_and_snapshot_carry_only_durable_results(self):
        marker = self.event_marker()
        self.refuse_rows()
        for result in ("win", "loss", "win"):
            self.assertFalse(self.attempt(result)[0])
        self.allow("refuse_match_rows")
        self.assertTrue(self.attempt("win", advance=False)[0])
        replay, snapshot = self.reconnect(marker)
        stats_events = [payload for kind, payload in replay if kind == "stats"]
        self.assertEqual(len(stats_events), 1, "only the accepted result may be replayed")
        state = self.observe()
        self.assertEqual((stats_events[0]["wins"], stats_events[0]["losses"]), state["stats"])
        self.assertEqual((snapshot["stats"]["wins"], snapshot["stats"]["losses"]), state["stats"])
        self.assertEqual((snapshot["lifetime"]["wins"], snapshot["lifetime"]["losses"]),
                         state["lifetime"])
        self.assertEqual([kind for kind, _ in replay if kind == "effect"], [])


class StatsWriteFailureLeavesNoHistoryOnlyResult(DurableResultHarness):

    def block_stats_writes(self):
        blocker = self.data / "stats.json.tmp"
        blocker.mkdir()
        self.addCleanup(lambda: blocker.exists() and blocker.rmdir())
        return blocker

    def test_f_a_stats_write_failure_takes_the_history_row_back(self):
        blocker = self.block_stats_writes()
        self.clock.advance()
        before = self.observe()
        self.drain()
        with self.assertRaises(OSError):
            self.server.record_result("win", "manual")
        events = self.drain()
        after = self.observe()
        for key in ("rows", "stats", "stats_json", "streak", "lifetime", "undo"):
            self.assertEqual(after[key], before[key],
                             f"a stats write failure changed {key}: a history-only result")
        self.assertEqual([kind for kind, _ in events if kind in ("stats", "effect")], [])
        self.assertEqual([payload["wins"] for kind, payload in events if kind == "lifetime"],
                         [before["lifetime"][0]])
        self.assertEqual(self.server.uncounted_event_ids, [])
        self.assert_counts_agree(after, "after a stats write failure")

        blocker.rmdir()
        self.assertTrue(self.attempt("win", advance=False)[0])

    def test_f2_a_row_history_will_not_take_back_blocks_results_until_it_is_gone(self):
        blocker = self.block_stats_writes()
        self.refuse(REFUSE_DELETES, "refuse_match_deletes")
        self.clock.advance()
        before = self.observe()
        with self.assertRaises(OSError):
            self.server.record_result("win", "manual")
        after = self.observe()
        self.assertEqual(after["rows"][0], before["rows"][0] + 1,
                         "precondition: history refused to take the row back")
        self.assertEqual(len(self.server.uncounted_event_ids), 1)
        self.assertEqual((after["stats"], after["streak"]), (before["stats"], before["streak"]))
        self.assertEqual(after["health"], "degraded")

        # Stats can be written again, but the uncounted row is still there:
        # no result is accepted on top of it.
        blocker.rmdir()
        self.drain()
        self.assertFalse(self.server.record_result("win", "manual"))
        self.assertEqual([kind for kind, _ in self.drain()
                          if kind in ("stats", "lifetime", "effect")], [])
        self.assertEqual(self.observe()["rows"][0], before["rows"][0] + 1)

        self.allow("refuse_match_deletes")
        self.assertTrue(self.server.record_result("win", "manual"))
        self.accepted_rows += 1
        self.accepted_session += 1
        final = self.observe()
        self.assertEqual(self.server.uncounted_event_ids, [])
        self.assert_counts_agree(final, "after the uncounted row was taken back")
        self.assertEqual(final["health"], "active")


class HealthDistinguishesReadsFromWrites(DurableResultHarness):

    def test_a_failed_read_is_cleared_by_a_read(self):
        with patch.object(self.history, "session_metadata",
                          side_effect=sqlite3.OperationalError("injected read failure")):
            self.assertEqual(self.dashboard_health(), "degraded")
        self.assertEqual(self.dashboard_health(), "active")

    def test_a_failed_write_is_cleared_only_by_a_write(self):
        self.refuse_rows()
        self.assertFalse(self.attempt("win")[0])
        self.allow("refuse_match_rows")
        self.assertEqual(self.dashboard_health(), "degraded",
                         "rows can be written again, but no write has proved it yet")
        self.assertTrue(self.attempt("win", advance=False)[0])
        self.assertEqual(self.dashboard_health(), "active")


class HistoryUnavailableAtStartup(unittest.TestCase):
    """A real ``server.main`` whose history store cannot start a session."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="ac6-history-unavailable-")
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.data = root / "AC6WinLossTracker"
        self.data.mkdir()
        environment = patch.dict(os.environ, {"LOCALAPPDATA": str(root)})
        environment.start()
        self.addCleanup(environment.stop)

        import app_paths
        import config_utils
        importlib.reload(app_paths)
        importlib.reload(config_utils)
        self.assertEqual(config_utils.CONFIG_PATH.parent.resolve(), self.data.resolve())
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            self.port = reservation.getsockname()[1]
        self.assertNotEqual(self.port, 8765)
        (self.data / "config.json").write_text(json.dumps({
            "config_version": config_utils.CONFIG_VERSION, "port": self.port,
            "stats_enabled": True, "result_detector_enabled": False,
            "effect_screenshot_enabled": False, "effect_enabled": True,
            "overlay_stats_scope": "session"}), encoding="utf-8")

        import history_store
        import stats_manager
        importlib.reload(history_store)
        importlib.reload(stats_manager)
        history_store.HistoryStore(self.data)  # a real database at the current schema
        run_sql(self.data / "history.db", REFUSE_SESSIONS)

        import server
        importlib.reload(server)
        self.server = server
        recorder = patch.object(server, "RECORDER", Mock())
        recorder.start()
        self.addCleanup(recorder.stop)

        self.ready = threading.Event()
        self.thread = threading.Thread(target=server.main, kwargs={"on_ready": self.ready.set},
                                       daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.assertTrue(self.ready.wait(30), "server.main did not become ready")

    def stop(self):
        """Cleanup is part of the test: a thread, port or runtime file left fails it."""
        if self.ready.is_set():
            request = urllib.request.Request(
                f"http://127.0.0.1:{self.port}/api/system/shutdown", data=b"", method="POST",
                headers={"X-Control-Token": self.server.CONTROL_TOKEN})
            urllib.request.urlopen(request, timeout=10).close()
        self.server.stop_event.set()
        self.thread.join(timeout=20)
        if self.thread.is_alive():
            raise AssertionError("server.main did not stop")
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", self.port))
        if (self.data / ".runtime.json").exists():
            raise AssertionError("the runtime file was left behind")

    def get(self, path):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=15) as response:
            return json.loads(response.read())

    def test_no_result_is_accepted_into_stats_alone(self):
        server = self.server
        self.assertIsNone(server.history, "precondition: the store could not start a session")
        self.assertEqual(self.get("/api/dashboard/summary")["history_health"]["status"], "degraded")
        for result in ("win", "loss", "win"):
            self.assertFalse(server.record_result(result, "manual"),
                             f"{result} accepted with no history store")
        on_disk = json.loads((self.data / "stats.json").read_text(encoding="utf-8"))
        self.assertEqual((on_disk["wins"], on_disk["losses"], on_disk["streak"]), (0, 0, 0))
        live = self.get("/stats")
        self.assertEqual((live["wins"], live["losses"], live["streak"]), (0, 0, 0))
        connection = sqlite3.connect(self.data / "history.db")
        try:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM matches").fetchone()[0], 0)
        finally:
            connection.close()
        self.assertEqual(server.history_event_ids, [])
        self.assertIsNone(server.result_gate.last_accepted_at, "a refusal must not use the gate")
        for _ in range(2):
            server.invalidate_dashboard_summary()
            self.assertEqual(self.get("/api/dashboard/summary")["history_health"]["status"],
                             "degraded")
        snapshot = dict(server.safe_snapshot_bundle())
        self.assertEqual((snapshot["stats"]["wins"], snapshot["stats"]["losses"]), (0, 0))


if __name__ == "__main__":
    unittest.main(verbosity=2)
