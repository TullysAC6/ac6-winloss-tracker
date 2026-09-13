"""Review 5190453838: restart, double-failed Undo, and live SSE correction."""
import http.client
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from test_result_persistence_invariant import (
    REFUSE_DELETES, run_sql,
)
from test_purge_history_writability import WritableHistoryHarness


class RecoveryFailures(WritableHistoryHarness):
    def test_undo_double_failure_is_not_http_success(self):
        blocker = self.data / "stats.json.tmp"
        self.addCleanup(lambda: blocker.exists() and blocker.rmdir())
        real_connect = self.history._connect

        def connect():
            connection = real_connect()
            connection.create_function("block_restore", 0, lambda: blocker.mkdir())
            return connection

        run_sql(self.data / "history.db", "CREATE TRIGGER refuse_undo BEFORE DELETE ON matches "
                "BEGIN SELECT block_restore(); SELECT RAISE(ABORT, 'undo refused'); END")
        with patch.object(self.history, "_connect", side_effect=connect):
            status, body = self.post("/api/stats/undo", {})
        self.assertEqual(status, 500)
        self.assertIn("UndoIncomplete", body["error"])
        state = self.observe()
        self.assertEqual(state["stats"], (1, 0))
        self.assertEqual(state["lifetime"], (1, 1))
        self.assertEqual(state["health"], "degraded")
        self.assertEqual(self.server.pending_history.load(), self.server.uncounted_event_ids)
        self.assertEqual(len(self.server.uncounted_event_ids), 1)
        blocker.rmdir()
        run_sql(self.data / "history.db", "DROP TRIGGER refuse_undo")
        self.clock.advance()
        self.assertTrue(self.server.record_result("win", "manual"))
        self.assertEqual(self.observe()["stats"], (2, 0))
        self.assertEqual(self.observe()["lifetime"], (2, 0))
        self.assertEqual(self.server.pending_history.load(), [])

    def test_shutdown_refuses_to_lose_an_unsavable_queue(self):
        blocker = self.data / "stats.json.tmp"
        blocker.mkdir()
        self.addCleanup(lambda: blocker.exists() and blocker.rmdir())
        pending_blocker = self.data / "pending-history.json.tmp"
        pending_blocker.mkdir()
        self.addCleanup(pending_blocker.rmdir)
        run_sql(self.data / "history.db", REFUSE_DELETES)
        self.clock.advance()
        with self.assertRaises(OSError):
            self.server.record_result("win", "manual")
        status, body = self.post("/api/system/shutdown", {})
        self.assertEqual(status, 500)
        self.assertNotIn("ok", body)
        self.assertTrue(self.http_thread.is_alive())
        self.assertEqual(len(self.server.uncounted_event_ids), 1)

    def test_reconnect_during_provisional_commit_gets_live_correction(self):
        blocker = self.data / "stats.json.tmp"
        blocker.mkdir()
        self.addCleanup(blocker.rmdir)
        committed, release = threading.Event(), threading.Event()
        original = self.history.record_result
        errors = []

        def pause(*args, **kwargs):
            value = original(*args, **kwargs)
            committed.set()
            if not release.wait(10):
                raise AssertionError("test failed to release commit")
            return value

        def record():
            try:
                self.server.record_result("win", "manual")
            except Exception as error:
                errors.append(error)

        self.clock.advance()
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=8)
        worker = threading.Thread(target=record, daemon=True)
        try:
            with patch.object(self.history, "record_result", side_effect=pause):
                worker.start()
                self.assertTrue(committed.wait(8))
                connection.request("GET", "/events")
                response = connection.getresponse()
                self.assertEqual(response.status, 200)

                def lifetime():
                    kind = None
                    for _ in range(100):
                        line = response.readline().decode().strip()
                        if line.startswith("event:"):
                            kind = line[6:].strip()
                        if line.startswith("data:") and kind == "lifetime":
                            return json.loads(line[5:])
                    self.fail("no lifetime event on live SSE connection")

                self.assertEqual(lifetime()["wins"], 2, "force the provisional snapshot")
                release.set()
                worker.join(8)
                self.assertFalse(worker.is_alive())
                self.assertEqual(len(errors), 1)
                self.assertIsInstance(errors[0], OSError)
                self.assertEqual(lifetime()["wins"], 1, "same connection must be corrected")
                self.assertEqual(self.observe()["lifetime"], (1, 1))
        finally:
            release.set()
            worker.join(10)
            connection.close()
            self.server.stop_event.set()


def process_phase(root, phase):
    """Fresh interpreter with the real main and authenticated normal shutdown."""
    data = root / "AC6WinLossTracker"
    data.mkdir(exist_ok=True)
    import config_utils
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    assert port != 8765
    (data / "config.json").write_text(json.dumps({
        "config_version": config_utils.CONFIG_VERSION, "port": port,
        "result_detector_enabled": False, "effect_screenshot_enabled": False,
    }), encoding="utf-8")
    import server
    ready = threading.Event()
    worker = threading.Thread(target=server.main, kwargs={"on_ready": ready.set}, daemon=True)
    worker.start()
    assert ready.wait(25), "main did not start"
    try:
        if phase == "fail":
            blocker = data / "stats.json.tmp"
            blocker.mkdir()
            run_sql(data / "history.db", REFUSE_DELETES)
            try:
                server.record_result("win", "manual")
            except OSError:
                pass
            else:
                raise AssertionError("stats write should fail")
            blocker.rmdir()
        state = {
            "stats": server.stats.snapshot()["wins"],
            "history": server.history.lifetime_summary()["wins"],
            "pending": len(server.uncounted_event_ids),
            "health": server.history_health["status"],
        }
        if phase != "fail":
            state["accepted"] = server.record_result("win", "manual")
            state["after_history"] = server.history.lifetime_summary()["wins"]
        (root / (phase + ".json")).write_text(json.dumps(state), encoding="utf-8")
    finally:
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/system/shutdown", data=b"", method="POST",
            headers={"X-Control-Token": server.CONTROL_TOKEN})
        with urllib.request.urlopen(request, timeout=15) as response:
            assert response.status == 200
        worker.join(20)
        assert not worker.is_alive(), "main left running"
        assert not (data / ".runtime.json").exists()
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))


class FreshProcessRecovery(unittest.TestCase):
    def test_pending_failure_survives_normal_shutdown_and_two_fresh_starts(self):
        with tempfile.TemporaryDirectory(prefix="ac6-pending-process-") as directory:
            root = Path(directory)
            env = dict(os.environ, LOCALAPPDATA=str(root), PYTHONDONTWRITEBYTECODE="1")

            def run(phase):
                child = subprocess.run(
                    [sys.executable, "-B", str(Path(__file__).resolve()), "--phase", str(root), phase],
                    env=env, cwd=ROOT, capture_output=True, text=True, timeout=65,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                self.assertEqual(child.returncode, 0, child.stdout + child.stderr)
                return json.loads((root / (phase + ".json")).read_text(encoding="utf-8"))

            failed = run("fail")
            self.assertEqual((failed["stats"], failed["history"], failed["pending"]), (0, 1, 1))
            blocked = run("blocked")
            self.assertEqual((blocked["pending"], blocked["health"], blocked["accepted"]),
                             (1, "degraded", False))
            run_sql(root / "AC6WinLossTracker" / "history.db", "DROP TRIGGER refuse_match_deletes")
            recovered = run("recovered")
            self.assertEqual((recovered["stats"], recovered["history"], recovered["pending"]), (0, 0, 0))
            self.assertEqual((recovered["accepted"], recovered["after_history"]), (True, 1))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--phase":
        process_phase(Path(sys.argv[2]), sys.argv[3])
    else:
        unittest.main(verbosity=2)
