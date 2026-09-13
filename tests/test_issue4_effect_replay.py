"""Issue #4: short reconnect recovery without historical effect resurrection."""
import json
import http.client
import os
import queue
import shutil
import subprocess
import socket
import sys
import time
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from event_bus import EventBus


def effect(n, created=100000):
    return {"effect": "milestone", "effect_id": f"effect-{n}", "milestone": n,
            "created_at_ms": created}


def recovered(bus, last=""):
    client, replay, snapshots = bus.register_with_snapshots(last, lambda: [("stats", {"wins": 10})])
    bus.unregister(client)
    return [p for kind, p in snapshots if kind == "effect"], replay


class ReplayTests(unittest.TestCase):
    @patch("time.time", return_value=100)
    def test_disconnected_five_and_ten_recover_latest_once(self, clock):
        bus = EventBus()
        stats = bus.publish("stats", {"wins": 4})
        bus.publish("effect", effect(5), remember=False)
        self.assertEqual(recovered(bus)[0], [effect(5)])
        bus.publish("stats", {"wins": 10})
        clock.return_value = 100.002
        bus.publish("effect", effect(10, 100001), remember=False)
        effects, replay = recovered(bus, stats["id"])
        self.assertEqual(effects, [effect(10, 100001)])
        self.assertEqual([r["event"] for r in replay], ["stats"])
        self.assertTrue(all(r["event"] != "effect" for r in bus.history))

    @patch("time.time", return_value=100)
    def test_expiry_live_delivery_and_no_restart_history(self, clock):
        bus = EventBus()
        client, _, _ = bus.register_with_snapshots("", lambda: [])
        bus.publish("effect", effect(5), remember=False)
        self.assertEqual(json.loads(client.queue.get_nowait()["data"]), effect(5))
        clock.return_value = 102.9
        self.assertEqual(recovered(bus)[0], [effect(5)])
        clock.return_value = 103
        self.assertEqual(recovered(bus)[0], [])
        self.assertEqual(recovered(EventBus())[0], [])
        bus.unregister(client)

    @patch("time.time", return_value=100)
    def test_native_freshness_dedup_order_and_restart(self, clock):
        import game_overlay
        overlay = game_overlay.GameOverlay.__new__(game_overlay.GameOverlay)
        overlay._overlay_started_at = 99
        overlay._effect_ids = set()
        overlay._effect_queue = queue.Queue()
        for payload in (effect(5), effect(5), effect(10, 100001), effect(5)):
            clock.return_value = 100.01
            overlay._queue_sse_event("effect", json.dumps(payload))
        self.assertEqual([overlay._effect_queue.get_nowait()["milestone"] for _ in range(2)], [5, 10])
        self.assertTrue(overlay._effect_queue.empty())
        clock.return_value = 110
        overlay._queue_sse_event("effect", json.dumps(effect(15)))
        self.assertTrue(overlay._effect_queue.empty(), "expired effect was queued")
        overlay._effect_ids.clear()
        overlay._overlay_started_at = 110
        overlay._queue_sse_event("effect", json.dumps(effect(20, 109500)))
        self.assertTrue(overlay._effect_queue.empty(), "pre-restart effect resurrected")

    def test_browser_handler_executes_freshness_and_dedup(self):
        node = os.environ.get("AC6_TEST_NODE") or shutil.which("node")
        if node is None:
            # CI (windows-latest) ships Node.js, so the browser handler stays
            # enforced there. A developer box without it still gets a green
            # suite instead of an unrelated hard failure.
            self.assertFalse(os.environ.get("CI"), "Node.js is required to execute overlay.html regression")
            self.skipTest("Node.js not found; set AC6_TEST_NODE to run the overlay.html regression")
        subprocess.run([node, str(Path(__file__).with_name("test_issue4_overlay.js"))], check=True, timeout=15)

    @patch("time.monotonic", return_value=100)
    @patch("time.time", return_value=100)
    def test_monotonic_expiry_and_no_effect_in_stats_history(self, wall, mono):
        bus = EventBus()
        bus.publish("effect", effect(5))  # Even default remember=True stays out of stats history.
        self.assertEqual(list(bus.history), [])
        mono.return_value = 104
        wall.return_value = 101  # Wall-clock rollback cannot lengthen retention.
        self.assertEqual(recovered(bus)[0], [])

    @patch("time.time", return_value=110)
    def test_native_queue_delay_does_not_resurrect_expired_effect(self, clock):
        import game_overlay
        from unittest.mock import Mock
        overlay = game_overlay.GameOverlay.__new__(game_overlay.GameOverlay)
        overlay._stats_queue = queue.Queue()
        overlay._effect_queue = queue.Queue()
        overlay._effect_queue.put(effect(5))
        overlay._active_effect = None
        overlay._finish_effect = Mock()
        overlay._drain_effects()
        self.assertIsNone(overlay._active_effect)


class LiveServerTests(unittest.TestCase):
    def test_real_disconnect_and_reconnect_at_five_and_ten(self):
        # Real server.main, stats/history files, sockets and five-second gate.
        # No production API or gate bypass is introduced for this fixture.
        with tempfile.TemporaryDirectory(prefix="ac6-issue4-") as temporary, \
             patch.dict(os.environ, {"LOCALAPPDATA": temporary}):
            data = Path(temporary) / "AC6WinLossTracker"
            data.mkdir()
            with socket.socket() as reservation:
                reservation.bind(("127.0.0.1", 0))
                port = reservation.getsockname()[1]
            (data / "config.json").write_text(json.dumps({"config_version": 18, "port": port,
                "stats_enabled": True, "result_detector_enabled": False, "effect_screenshot_enabled": False}))
            import server
            ready = threading.Event()
            thread = threading.Thread(target=server.main, kwargs={"on_ready": ready.set}, daemon=True)
            thread.start()
            connections = []
            def disconnect(connection):
                connection.shutdown(socket.SHUT_RDWR)
                connection.close()
            def connect():
                connection = socket.create_connection(("127.0.0.1", port), timeout=5)
                connections.append(connection)
                connection.sendall(f"GET /events HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: keep-alive\r\n\r\n".encode())
                response = connection.makefile("rb")
                self.assertIn(b" 200 ", response.readline())
                while response.readline().strip():
                    pass
                return connection, response
            def receive(response):
                kind, payload = None, None
                while True:
                    line = response.readline().decode("utf-8").strip()
                    if not line:
                        return kind, payload
                    if line.startswith("event: "):
                        kind = line[7:]
                    elif line.startswith("data: "):
                        payload = json.loads(line[6:])
            def snapshot_effects():
                connection, response = connect()
                connection.settimeout(.5)
                found = []
                deadline = time.monotonic() + 2.5
                while time.monotonic() < deadline:
                    try:
                        kind, payload = receive(response)
                    except TimeoutError:
                        break
                    if kind is None:
                        break
                    if kind == "effect":
                        found.append(payload)
                disconnect(connection)
                response.close()
                return found
            try:
                self.assertTrue(ready.wait(10), "server.main did not become ready")
                connection, response = connect()
                for _ in range(4):  # stats, stats_health, config_health, detector
                    receive(response)
                disconnect(connection)
                response.close()
                last_win = 0
                for n in range(1, 11):
                    time.sleep(max(0, last_win + 5.05 - time.monotonic()))
                    self.assertTrue(server.record_result("win", "manual"))
                    last_win = time.monotonic()
                    if n in (5, 10):
                        recovered_effects = snapshot_effects()
                        self.assertEqual([p["milestone"] for p in recovered_effects], [n])
                        # Both events were emitted while no SSE socket existed.
                        print(f"Issue #4 real socket reconnect: {n} wins -> one effect", flush=True)
                self.assertEqual(server.stats.snapshot()["wins"], 10)
                time.sleep(3.05)
                self.assertEqual(snapshot_effects(), [], "late reconnect resurrected effect")
            finally:
                for connection in connections:
                    connection.close()
                if ready.is_set():
                    shutdown = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                    shutdown.request("POST", "/api/system/shutdown", body=b"",
                                     headers={"X-Control-Token": server.CONTROL_TOKEN})
                    shutdown.getresponse().read()
                    shutdown.close()
                thread.join(8)
                self.assertFalse(thread.is_alive(), "test server remained alive")


if __name__ == "__main__":
    unittest.main()
