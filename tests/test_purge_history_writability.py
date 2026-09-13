"""A purge that reports success must leave history able to record the next result.

Independent review 5188346877 of 366d9cc reproduced this against the real
handler. ``purge_all`` had already committed a fresh session, and the purge path
then advanced the session a second time with ``store.reset_session()``. That
closes the valid session -- clearing the in-memory owner -- before starting
another, and when the start failed the failure was swallowed. The request still
answered HTTP 200 / ok=true with no active history session, every WIN and LOSE
after it was counted in stats.json and dropped from history (stats 1W/1L,
lifetime 0), and the response named session 1 while session 2 was in use.

What is asserted here, against the real HTTP handler:

* HTTP success means a writable history session exists, it is the session the
  response names, and the next accepted WIN and LOSE are persisted in it.
* When no writable session can be established the purge is never HTTP 200, and
  the Tracker refuses results rather than counting them in stats alone.
* After every outcome: stats.json, in-memory stats, ``/stats``, lifetime
  history, the dashboard summary, the snapshot and replay a reconnecting SSE
  client receives, the live stats and lifetime events, and the undo identities
  all agree -- through result, undo and restart.

The assertion that matters most is ``assert_no_stats_only_result``: whatever a
result does to session stats, it must do the same to history.

Every test owns a fresh temporary LOCALAPPDATA and an ephemeral loopback port.
Port 8765 and the user's data are never touched. The ResultGate is honoured by
advancing a test clock past its cooldown, never by clearing it.
"""
import importlib
import json
import os
import queue
import socket
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

GATE_STEP_SECONDS = 6.0  # past the shared 5-second ResultGate cooldown
SESSION_UNAVAILABLE = "作成できるまで勝敗は記録しません"


class GateClock:
    """``time`` with a monotonic clock the test moves forward.

    The gate still enforces its cooldown; the test simply lets that time pass
    without sleeping. Every other ``time`` function is the real one.
    """

    def __init__(self):
        self.offset = 0.0

    def monotonic(self):
        return time.monotonic() + self.offset

    def advance(self, seconds=GATE_STEP_SECONDS):
        self.offset += seconds

    def __getattr__(self, name):
        return getattr(time, name)


class SessionInsertFault:
    """Make ``INSERT INTO sessions`` fail at the SQLite connection, once armed.

    The fault sits underneath HistoryStore, so it hits whichever method tries
    to open a session -- ``start_session``, ``reset_session`` or anything newer
    -- without the test depending on which one the implementation calls.
    """

    def __init__(self, store):
        self.armed = False
        self.hits = 0
        real_connect = store._connect
        fault = self

        class Connection:
            def __init__(self, real):
                self._real = real

            def execute(self, sql, *args):
                if fault.armed and "INSERT INTO sessions" in " ".join(str(sql).split()):
                    fault.hits += 1
                    raise sqlite3.OperationalError("injected: cannot insert a session row")
                return self._real.execute(sql, *args)

            def __enter__(self):
                self._real.__enter__()
                return self

            def __exit__(self, *exc):
                return self._real.__exit__(*exc)

            def __getattr__(self, name):
                return getattr(self._real, name)

        self.connect = lambda: Connection(real_connect())


class WritableHistoryHarness(unittest.TestCase):
    """A Tracker with one WIN and one LOSE already recorded through the real path."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="ac6-purge-writable-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.data = self.root / "AC6WinLossTracker"
        self.data.mkdir()
        environment = patch.dict(os.environ, {"LOCALAPPDATA": str(self.root)})
        environment.start()
        self.addCleanup(environment.stop)

        import app_paths
        import config_utils
        importlib.reload(app_paths)
        importlib.reload(config_utils)
        self.assertEqual(config_utils.CONFIG_PATH.parent.resolve(), self.data.resolve())

        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            config_port = reservation.getsockname()[1]
        (self.data / "config.json").write_text(json.dumps({
            "config_version": config_utils.CONFIG_VERSION, "port": config_port,
            "stats_enabled": True, "result_detector_enabled": False,
            "effect_screenshot_enabled": False, "effect_enabled": True,
            "overlay_stats_scope": "session"}), encoding="utf-8")

        import history_store
        import stats_manager
        importlib.reload(history_store)
        importlib.reload(stats_manager)
        self.history = history_store.HistoryStore(self.data)
        self.first_session = self.history.start_session()
        self.stats = stats_manager.StatsManager(self.data)
        self.stats.reset()  # as server.main does after binding

        import server
        importlib.reload(server)
        self.server = server
        server.history = self.history
        server.stats = self.stats
        server.detector = None
        server.history_event_ids.clear()
        server.set_history_health("active")
        for name, replacement in (("RECORDER", Mock()),):
            patcher = patch.object(server, name, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

        self.clock = GateClock()
        gate_module = sys.modules[type(server.result_gate).__module__]
        for module in (server, gate_module):
            patcher = patch.object(module, "time", self.clock)
            patcher.start()
            self.addCleanup(patcher.stop)

        # A connected overlay: every live event the Tracker publishes.
        self.client, _, _ = server.event_bus.register_with_snapshots(None, lambda: [])
        self.addCleanup(server.event_bus.unregister, self.client)

        # The real HTTP handler on an ephemeral loopback port.
        self.httpd = server.QuietThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.port = self.httpd.server_address[1]
        self.assertNotEqual(self.port, 8765)
        self.http_thread = threading.Thread(
            target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.http_thread.start()
        self.addCleanup(self.stop_http)

        self.assertTrue(self.record("win"))
        self.assertTrue(self.record("loss"))
        seeded = self.observe()
        self.assertEqual(seeded["stats"], (1, 1))
        self.assertEqual(seeded["lifetime"], (1, 1))
        self.assertEqual(len(seeded["undo"]), 2)
        self.drain()

    def stop_http(self):
        """Cleanup is part of the test: a thread or port left behind fails it."""
        self.httpd.shutdown()
        self.httpd.server_close()
        self.http_thread.join(timeout=10)
        if self.http_thread.is_alive():
            raise AssertionError("the HTTP server thread did not stop")
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", self.port))

    # ------------------------------------------------------------ transport
    def post(self, path, body=None):
        data = b"" if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=data, method="POST",
            headers={"Content-Type": "application/json",
                     "X-Control-Token": self.server.CONTROL_TOKEN})
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            with error:
                return error.code, json.loads(error.read())

    def get(self, path):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=15) as response:
            return json.loads(response.read())

    def drain(self):
        """Live events received by the connected client since the last drain."""
        events = []
        while True:
            try:
                record = self.client.queue.get_nowait()
            except queue.Empty:
                return events
            events.append((record["event"], json.loads(record["data"])))

    def event_marker(self):
        bus = self.server.event_bus
        with bus.lock:
            return f"{bus.boot_id}:{bus.seq}"

    def reconnect(self, last_event_id):
        """What a reconnecting SSE client receives: the handler's own call."""
        client, replay, snapshots = self.server.event_bus.register_with_snapshots(
            last_event_id, self.server.safe_snapshot_bundle)
        self.server.event_bus.unregister(client)
        return [(r["event"], json.loads(r["data"])) for r in replay], dict(snapshots)

    # ---------------------------------------------------------- observation
    def observe(self):
        """Every place a user or client can see session, lifetime and undo state."""
        on_disk = json.loads((self.data / "stats.json").read_text(encoding="utf-8"))
        memory = self.stats.snapshot()
        lifetime = self.server.history.lifetime_summary()
        connection = sqlite3.connect(self.data / "history.db")
        try:
            open_sessions = [row[0] for row in connection.execute(
                "SELECT id FROM sessions WHERE ended_at IS NULL ORDER BY id")]
            total, wins, losses = connection.execute(
                "SELECT COUNT(*), COALESCE(SUM(result='win'),0), "
                "COALESCE(SUM(result='loss'),0) FROM matches").fetchone()
            newest = connection.execute(
                "SELECT event_id, session_id, result FROM matches "
                "ORDER BY id DESC LIMIT 1").fetchone()
        finally:
            connection.close()
        _, snapshot = self.reconnect(None)
        summary = self.get("/api/dashboard/summary")
        http_stats = self.get("/stats")
        return {
            "stats": (memory["wins"], memory["losses"]),
            "stats_json": (on_disk["wins"], on_disk["losses"]),
            "http_stats": (http_stats["wins"], http_stats["losses"]),
            "lifetime": (lifetime["wins"], lifetime["losses"]),
            "rows": (total, wins, losses),
            "newest": tuple(newest) if newest else None,
            "active": self.server.history.current_session_id,
            "open_sessions": open_sessions,
            "dashboard_session": summary["session"]["id"],
            "dashboard_stats": (summary["session"]["wins"], summary["session"]["losses"]),
            "dashboard_lifetime": (summary["lifetime"]["wins"], summary["lifetime"]["losses"]),
            "health": summary["history_health"]["status"],
            "sse_stats": (snapshot["stats"]["wins"], snapshot["stats"]["losses"]),
            "sse_lifetime": (snapshot["lifetime"]["wins"], snapshot["lifetime"]["losses"]),
            "undo": list(self.server.history_event_ids),
        }

    @staticmethod
    def last(events, kind):
        matching = [payload for event, payload in events if event == kind]
        return matching[-1] if matching else None

    # ----------------------------------------------------------- invariants
    def assert_converged(self, state, where):
        """Every surface shows the same session and the same lifetime."""
        stats = state["stats"]
        for surface in ("stats_json", "http_stats", "dashboard_stats", "sse_stats"):
            self.assertEqual(state[surface], stats, f"{where}: {surface} disagrees with stats")
        lifetime = state["lifetime"]
        for surface in ("dashboard_lifetime", "sse_lifetime"):
            self.assertEqual(state[surface], lifetime, f"{where}: {surface} disagrees with lifetime")
        self.assertEqual(state["rows"][1:], lifetime,
                         f"{where}: lifetime aggregates disagree with the stored rows")
        self.assertEqual(state["dashboard_session"], state["active"],
                         f"{where}: the dashboard names a different session")

    def assert_writable(self, state, where):
        self.assertIsNotNone(state["active"], f"{where}: no active history session")
        self.assertEqual(state["open_sessions"], [state["active"]],
                         f"{where}: the active session must be the one open session")

    def assert_no_stats_only_result(self, before, after, result, accepted):
        """A result may change stats only by exactly what it changes in history."""
        for surface in ("stats", "stats_json"):
            stats_delta = (after[surface][0] - before[surface][0],
                           after[surface][1] - before[surface][1])
            rows_delta = (after["rows"][1] - before["rows"][1],
                          after["rows"][2] - before["rows"][2])
            self.assertEqual(
                stats_delta, rows_delta,
                f"{result.upper()} changed {surface} by {stats_delta} but history by "
                f"{rows_delta}: a result was accepted into stats but not into history")
        if not accepted:
            self.assertEqual(after["stats"], before["stats"], "a refused result changed stats")
            self.assertEqual(after["undo"], before["undo"], "a refused result took an undo identity")
            return
        self.assertEqual(stats_delta, (1, 0) if result == "win" else (0, 1))
        self.assertIsNotNone(after["newest"], "an accepted result left no history row")
        event_id, session_id, stored = after["newest"]
        self.assertEqual(stored, result)
        self.assertEqual(session_id, after["active"],
                         "the result must be persisted in the active session")
        self.assert_writable(after, f"after {result}")
        self.assertEqual(after["undo"][-1:], [event_id],
                         "the undo identity must be the persisted row")
        self.assert_converged(after, f"after {result}")

    # -------------------------------------------------------------- actions
    def record(self, result, *, advance=True):
        if advance:
            self.clock.advance()
        before = self.observe()
        self.drain()
        accepted = self.server.record_result(result, "manual")
        events = self.drain()
        after = self.observe()
        self.assert_no_stats_only_result(before, after, result, accepted)
        if accepted:
            self.assertEqual((self.last(events, "stats")["wins"],
                              self.last(events, "stats")["losses"]), after["stats"],
                             "the live stats event must match the session")
            self.assertEqual((self.last(events, "lifetime")["wins"],
                              self.last(events, "lifetime")["losses"]), after["lifetime"],
                             "the live lifetime event must match history")
        else:
            self.assertIsNone(self.last(events, "stats"),
                              "a refused result must not publish a session change")
        return accepted

    def undo(self):
        before = self.observe()
        status, body = self.post("/api/stats/undo")
        after = self.observe()
        self.assertEqual(status, 200, body)
        stats_delta = (after["stats"][0] - before["stats"][0],
                       after["stats"][1] - before["stats"][1])
        rows_delta = (after["rows"][1] - before["rows"][1],
                      after["rows"][2] - before["rows"][2])
        self.assertEqual(stats_delta, rows_delta,
                         f"undo changed stats by {stats_delta} but history by {rows_delta}")
        self.assert_converged(after, "after undo")
        return body, before, after

    def purge(self):
        marker = self.event_marker()
        self.drain()
        status, body = self.post("/api/history/purge", {"mode": "all"})
        events = self.drain()
        state = self.observe()
        replay, snapshot = self.reconnect(marker)
        return status, body, state, events, replay, snapshot

    def restart(self):
        """A restarted Tracker, as ``server.main`` builds it, over the same files."""
        import history_store
        import stats_manager
        store = history_store.HistoryStore(self.data)
        store.start_session()
        manager = stats_manager.StatsManager(self.data)
        manager.reset()
        self.history = store
        self.stats = manager
        self.server.history = store
        self.server.stats = manager
        self.server.history_event_ids.clear()
        self.server.invalidate_dashboard_summary()
        self.server.set_history_health("active")
        state = self.observe()
        self.assert_writable(state, "after restart")
        self.assert_converged(state, "after restart")
        return state

    # ------------------------------------------------------------ contracts
    def assert_reset_session_published(self, events, replay, snapshot, where):
        self.assertEqual(self.last(events, "stats")["wins"], 0,
                         f"{where}: connected clients must be told the session is zero")
        self.assertEqual(self.last(events, "lifetime")["wins"],
                         self.observe()["lifetime"][0], f"{where}: live lifetime event")
        self.assertEqual(self.last(replay, "stats")["wins"], 0,
                         f"{where}: a reconnecting client must replay the reset")
        self.assertEqual(snapshot["stats"]["wins"], 0, f"{where}: SSE snapshot")

    def assert_success_is_writable(self, status, body, state, where):
        self.assertEqual(status, 200, f"{where}: {body}")
        self.assertIs(body.get("ok"), True)
        self.assertIs(body.get("session_reset"), True)
        self.assert_writable(state, where)
        self.assertEqual(
            body.get("session_id"), state["active"],
            f"{where}: the response names session {body.get('session_id')} "
            f"but session {state['active']} is the one recording")
        self.assertEqual(state["stats"], (0, 0))
        self.assertEqual(state["lifetime"], (0, 0))
        self.assertEqual(state["undo"], [])
        self.assertEqual(state["health"], "active")
        self.assert_converged(state, where)

    def assert_recording_survives_result_undo_and_restart(self, where):
        """Record a WIN and a LOSE, undo the LOSE, restart, record again."""
        wins, losses = self.observe()["lifetime"]
        self.assertTrue(self.record("win"), f"{where}: WIN refused")
        win_row = self.observe()["newest"]
        self.assertTrue(self.record("loss"), f"{where}: LOSE refused")
        self.assertEqual(self.observe()["lifetime"], (wins + 1, losses + 1))

        body, before, after = self.undo()
        self.assertIsNotNone(body["removed"], f"{where}: the LOSE must be undoable")
        self.assertEqual(after["stats"], (before["stats"][0], before["stats"][1] - 1))
        self.assertEqual(after["lifetime"], (wins + 1, losses))
        self.assertEqual(after["newest"], win_row, f"{where}: undo removed the wrong row")
        self.assertEqual(after["undo"][-1:], [win_row[0]])

        restarted = self.restart()
        self.assertEqual(restarted["lifetime"], (wins + 1, losses),
                         f"{where}: history lost on restart")
        self.assertEqual(restarted["stats"], (0, 0))
        self.assertTrue(self.record("win"), f"{where}: WIN refused after restart")
        self.assertEqual(self.observe()["lifetime"], (wins + 2, losses))

    def lose_session_after_purge(self, *, arm_fault=None, close=False, forget=False):
        """Run the real purge_all, then take its session away before the caller sees it."""
        real_purge = self.history.purge_all

        def purge_then_lose_session():
            outcome = real_purge()
            if close:
                connection = sqlite3.connect(self.data / "history.db")
                try:
                    connection.execute(
                        "UPDATE sessions SET ended_at=?, ended_reason=? WHERE ended_at IS NULL",
                        (time.time(), "closed-out-of-band"))
                    connection.commit()
                finally:
                    connection.close()
            if forget:
                self.history._current_session_id = None
            if arm_fault is not None:
                arm_fault.armed = True
            return outcome

        return patch.object(self.history, "purge_all", purge_then_lose_session)


class SuccessfulPurgeLeavesAWritableSession(WritableHistoryHarness):

    def test_a_success_names_the_session_that_records_the_next_results(self):
        status, body, state, events, replay, snapshot = self.purge()
        self.assert_success_is_writable(status, body, state, "purge success")
        self.assertEqual(body["removed_matches"], 2)
        self.assert_reset_session_published(events, replay, snapshot, "purge success")
        self.assert_recording_survives_result_undo_and_restart("purge success")

    def test_b_reviewer_reproduction_start_session_failure_during_purge(self):
        """The exact reproduction, step by step.

        start_session fails during the purge; the fault is then removed, an
        empty undo is performed, and a WIN and a LOSE are accepted. 366d9cc
        answered 200 with no active session and ended at stats 1W/1L against
        lifetime 0; undoing the LOSE and restarting still left lifetime 0.
        """
        with patch.object(self.history, "start_session",
                          side_effect=OSError("injected start_session failure")):
            status, body, state, events, replay, snapshot = self.purge()
        self.assert_success_is_writable(status, body, state, "start_session broken")
        self.assert_reset_session_published(events, replay, snapshot, "start_session broken")

        body, _, after = self.undo()
        self.assertIsNone(body["removed"], "the purged session has nothing to undo")
        self.assertEqual(after["lifetime"], (0, 0))

        self.assertTrue(self.record("win"))
        self.assertTrue(self.record("loss"))
        after = self.observe()
        self.assertEqual(after["stats"], (1, 1))
        self.assertEqual(after["lifetime"], (1, 1),
                         "the reviewer saw lifetime 0 here: results in stats only")

        body, _, after = self.undo()
        self.assertIsNotNone(body["removed"])
        self.assertEqual((after["stats"], after["lifetime"]), ((1, 0), (1, 0)),
                         "the reviewer saw stats 1W/0L against lifetime 0 here")
        self.assertEqual(self.restart()["lifetime"], (1, 0),
                         "the reviewer saw lifetime 0 after a real restart")
        self.assert_recording_survives_result_undo_and_restart("start_session broken")

    def test_b2_results_after_the_reproduction_reach_history_whatever_http_said(self):
        """The assertion that matters most, judged before anything else.

        Whatever the purge answered, a WIN and a LOSE recorded afterwards must
        change history exactly as they change stats -- the reviewer measured
        stats 1W/1L against lifetime 0 here.
        """
        with patch.object(self.history, "start_session",
                          side_effect=OSError("injected start_session failure")):
            self.purge()
            for result in ("win", "loss"):
                self.record(result)  # asserts no stats-only result first

    def test_c_close_succeeds_and_the_next_session_insert_fails(self):
        """Close committed, start failed: the store must not be left without a session."""
        fault = SessionInsertFault(self.history)
        with patch.object(self.history, "_connect", fault.connect), \
             self.lose_session_after_purge(arm_fault=fault):
            status, body, state, _, _, _ = self.purge()
            self.assert_success_is_writable(status, body, state, "session insert broken")
            self.assertTrue(self.record("win"))
            self.assertTrue(self.record("loss"))
            self.assertEqual(self.observe()["lifetime"], (1, 1))
        self.assert_recording_survives_result_undo_and_restart("session insert broken")

    def test_d_session_lost_after_the_purge_is_re_established_before_success(self):
        """The fresh session is gone by the time success would be reported."""
        with self.lose_session_after_purge(close=True):
            status, body, state, events, replay, snapshot = self.purge()
        self.assert_success_is_writable(status, body, state, "session closed after purge")
        self.assert_reset_session_published(events, replay, snapshot, "session closed after purge")
        self.assert_recording_survives_result_undo_and_restart("session closed after purge")

    def test_d2_owner_forgotten_after_the_purge_is_re_established_before_success(self):
        with self.lose_session_after_purge(forget=True):
            status, body, state, _, _, _ = self.purge()
        self.assert_success_is_writable(status, body, state, "owner forgotten after purge")
        self.assert_recording_survives_result_undo_and_restart("owner forgotten after purge")


class UnrecoverableSessionIsNeverSuccess(WritableHistoryHarness):

    def fail_purge(self):
        self.fault = SessionInsertFault(self.history)
        connect = patch.object(self.history, "_connect", self.fault.connect)
        connect.start()
        self.addCleanup(connect.stop)
        with self.lose_session_after_purge(arm_fault=self.fault, close=True):
            return self.purge()

    def assert_failed_without_a_session(self, status, body, state, where):
        self.assertEqual(status, 500, f"{where}: {body}")
        self.assertIs(body.get("ok"), False)
        self.assertEqual(body.get("stage"), "history session")
        self.assertIs(body.get("history_cleared"), True)
        self.assertIn(SESSION_UNAVAILABLE, body.get("error", ""))
        self.assertIsNone(state["active"], f"{where}: no session may be claimed")
        self.assertEqual(state["open_sessions"], [])
        self.assertEqual(state["stats"], (0, 0))
        self.assertEqual(state["lifetime"], (0, 0))
        self.assertEqual(state["undo"], [])
        self.assertEqual(state["health"], "degraded",
                         f"{where}: the dashboard must show history cannot record")
        self.assert_converged(state, where)

    def test_e_no_session_means_no_http_success_and_no_stats_only_results(self):
        status, body, state, events, replay, snapshot = self.fail_purge()
        self.assert_failed_without_a_session(status, body, state, "session unrecoverable")
        self.assert_reset_session_published(events, replay, snapshot, "session unrecoverable")

        # While a session still cannot be created, results are refused outright.
        self.assertFalse(self.record("win"), "a WIN was accepted with nowhere to store it")
        self.assertFalse(self.record("loss"), "a LOSE was accepted with nowhere to store it")
        body, _, after = self.undo()
        self.assertIsNone(body["removed"])
        self.assertEqual(after["lifetime"], (0, 0))
        self.assertEqual(self.observe()["health"], "degraded")

        # Once the database accepts a session again, recording resumes by
        # itself. No clock advance: a refused result must not have used the gate.
        self.fault.armed = False
        self.assertTrue(self.record("win", advance=False),
                        "a refused result consumed the gate cooldown")
        healed = self.observe()
        self.assertEqual(healed["health"], "active")
        self.assertEqual(healed["lifetime"], (1, 0))
        self.assertTrue(self.record("loss"))
        _, _, after = self.undo()
        self.assertEqual(after["lifetime"], (1, 0))
        self.assertEqual(self.restart()["lifetime"], (1, 0))
        self.assertTrue(self.record("loss"))
        self.assertEqual(self.observe()["lifetime"], (1, 1))

    def test_f_restart_after_the_failure_starts_consistent_and_records(self):
        status, body, state, _, _, _ = self.fail_purge()
        self.assert_failed_without_a_session(status, body, state, "before restart")
        self.assertFalse(self.record("win"))

        restarted = self.restart()
        self.assertEqual(restarted["stats"], (0, 0))
        self.assertEqual(restarted["lifetime"], (0, 0))
        self.assertEqual(restarted["health"], "active")
        self.assert_recording_survives_result_undo_and_restart("restart after failure")


class FailedPurgeKeepsRecordingInHistory(WritableHistoryHarness):
    """Case C: the purge and the stats restore both fail, so history is retained."""

    def fail_before_delete(self):
        failing_purge = patch.object(self.history, "purge_all",
                                     side_effect=OSError("injected purge failure"))
        failing_restore = patch.object(self.stats, "restore",
                                       side_effect=OSError("injected restore failure"))
        with failing_purge, failing_restore:
            return self.purge()

    def test_g_session_advance_failure_keeps_the_old_session_writable(self):
        """Close succeeded, start failed, on the failure path this time.

        366d9cc closed the session and then failed to start one, leaving no
        session; the next WIN reached stats only.
        """
        fault = SessionInsertFault(self.history)
        fault.armed = True
        with patch.object(self.history, "_connect", fault.connect):
            status, body, state, events, replay, snapshot = self.fail_before_delete()
            self.assertEqual(status, 500, body)
            self.assertEqual(body["stage"], "history purge")
            self.assertIs(body["stats_restored"], False)
            self.assertEqual(state["stats"], (0, 0))
            self.assertEqual(state["lifetime"], (1, 1), "history was retained")
            self.assertEqual(state["undo"], [])
            self.assertEqual(state["active"], self.first_session,
                             "the session that could not be advanced stays the active one")
            self.assert_writable(state, "session advance failed")
            self.assert_converged(state, "session advance failed")
            self.assert_reset_session_published(events, replay, snapshot, "session advance failed")

            self.assertTrue(self.record("win"))
            self.assertEqual(self.observe()["lifetime"], (2, 1))
            body, _, after = self.undo()
            self.assertIsNotNone(body["removed"])
            self.assertEqual(after["lifetime"], (1, 1), "undo removed only the new WIN")

    def test_h_session_advance_success_moves_recording_to_a_new_session(self):
        status, body, state, _, _, _ = self.fail_before_delete()
        self.assertEqual(status, 500, body)
        self.assertNotEqual(state["active"], self.first_session)
        self.assert_writable(state, "session advanced")
        self.assert_converged(state, "session advanced")
        self.assertTrue(self.record("win"))
        self.assertEqual(self.observe()["newest"][1], state["active"])
        self.assertEqual(self.observe()["lifetime"], (2, 1))


class SessionResetCannotProduceStatsOnlyResults(WritableHistoryHarness):
    """The same close-then-start hazard exists in the ordinary session reset.

    ``reset_stats`` is unchanged; what is asserted is that a session lost there
    can no longer turn the next results into stats-only results.
    """

    def test_i_reset_that_loses_its_session_refuses_until_one_can_be_created(self):
        fault = SessionInsertFault(self.history)
        with patch.object(self.history, "_connect", fault.connect):
            fault.armed = True
            status, body = self.post("/api/stats/reset")
            self.assertEqual(status, 200, body)

            # Judged first: no result may reach stats without reaching history.
            self.assertFalse(self.record("win"), "a WIN was accepted with nowhere to store it")
            state = self.observe()
            self.assertIsNone(state["active"])
            self.assertEqual(state["health"], "degraded")

            fault.armed = False
            self.assertTrue(self.record("win", advance=False))
            self.assertTrue(self.record("loss"))
            self.assertEqual(self.observe()["stats"], (1, 1))
            self.assertEqual(self.observe()["lifetime"], (2, 2))


class NormalPathIsUnchanged(WritableHistoryHarness):

    def test_j_a_normal_result_neither_queries_nor_reopens_the_session(self):
        with patch.object(self.history, "establish_session",
                          wraps=self.history.establish_session) as establish, \
             patch.object(self.history, "confirm_active_session",
                          wraps=self.history.confirm_active_session) as confirm:
            self.assertTrue(self.record("win"))
        establish.assert_not_called()
        confirm.assert_not_called()


class HistoryStoreSessionTests(unittest.TestCase):
    """The store primitives the purge relies on."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="ac6-session-store-")
        self.addCleanup(self.directory.cleanup)
        import history_store
        self.store = history_store.HistoryStore(Path(self.directory.name))

    def open_sessions(self):
        connection = sqlite3.connect(self.store.path)
        try:
            return [row[0] for row in connection.execute(
                "SELECT id FROM sessions WHERE ended_at IS NULL ORDER BY id")]
        finally:
            connection.close()

    def test_establish_session_is_one_transaction_and_moves_ownership_after_commit(self):
        first = self.store.start_session()
        fault = SessionInsertFault(self.store)
        with patch.object(self.store, "_connect", fault.connect):
            fault.armed = True
            with self.assertRaises(sqlite3.OperationalError):
                self.store.establish_session("manual_reset")
        self.assertEqual(fault.hits, 1)
        self.assertEqual(self.store.current_session_id, first,
                         "a failed establish must leave the previous session active")
        self.assertEqual(self.open_sessions(), [first],
                         "the close must have rolled back with the failed insert")
        self.assertEqual(self.store.confirm_active_session(), first)

        second = self.store.establish_session("manual_reset")
        self.assertNotEqual(second, first)
        self.assertEqual(self.store.current_session_id, second)
        self.assertEqual(self.open_sessions(), [second])

    def test_confirm_active_session_drops_an_owner_that_names_a_closed_session(self):
        first = self.store.start_session()
        connection = sqlite3.connect(self.store.path)
        try:
            connection.execute("UPDATE sessions SET ended_at=1 WHERE id=?", (first,))
            connection.commit()
        finally:
            connection.close()
        self.assertIsNone(self.store.confirm_active_session())
        self.assertIsNone(self.store.current_session_id)

    def test_confirm_active_session_drops_an_owner_it_cannot_check(self):
        self.store.start_session()
        with patch.object(self.store, "_connect", side_effect=sqlite3.OperationalError("gone")):
            with self.assertRaises(sqlite3.OperationalError):
                self.store.confirm_active_session()
        self.assertIsNone(self.store.current_session_id)


if __name__ == "__main__":
    unittest.main(verbosity=2)
