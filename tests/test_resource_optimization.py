import importlib
import json
import os
import queue
import sys
import tempfile
import threading
import time
import urllib.request
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


with tempfile.TemporaryDirectory() as temporary:
    old_local = os.environ.get("LOCALAPPDATA")
    old_home = os.environ.get("HOME")
    os.environ["LOCALAPPDATA"] = temporary
    os.environ["HOME"] = temporary
    try:
        server = importlib.import_module("server")
        server.ensure_first_run_config()
        server.stats.reset()

        # Hold one real idle SSE connection beyond urllib's five-second read
        # timeout. Keepalives must retain one handler/client for a full minute.
        httpd = server.QuietThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        http_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        http_thread.start()
        response = urllib.request.urlopen(
            f"http://127.0.0.1:{httpd.server_address[1]}/events", timeout=5
        )
        lines = []
        reader_stop = threading.Event()

        def read_stream():
            try:
                while not reader_stop.is_set():
                    line = response.readline()
                    if not line:
                        break
                    lines.append(line)
            except OSError:
                pass

        reader = threading.Thread(target=read_stream, daemon=True)
        reader.start()
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            with server.event_bus.lock:
                if len(server.event_bus.clients) == 1:
                    break
            time.sleep(0.02)
        with server.event_bus.lock:
            assert len(server.event_bus.clients) == 1

        server.publish("stats", {"wins": 1, "losses": 0, "streak": 1, "best_streak": 1})
        server.publish("effect", {"effect": "milestone", "effect_id": "test"}, remember=False)
        time.sleep(0.2)
        idle_seq = server.event_bus.seq
        idle_history = len(server.event_bus.history)
        samples = []
        idle_seconds = float(os.environ.get("AC6_SSE_TEST_SECONDS", "60.5"))
        idle_deadline = time.monotonic() + idle_seconds
        while time.monotonic() < idle_deadline:
            with server.event_bus.lock:
                samples.append(len(server.event_bus.clients))
            time.sleep(1.0)

        assert samples and set(samples) == {1}
        assert server.event_bus.seq == idle_seq
        assert len(server.event_bus.history) == idle_history
        assert sum(1 for line in lines if line == b": keepalive\n") >= max(2, int(idle_seconds // 2) - 1)
        assert sum(1 for thread in threading.enumerate() if "process_request_thread" in thread.name) == 1

        reader_stop.set()
        response.close()
        reader.join(timeout=4)
        cleanup_deadline = time.monotonic() + 5.0
        while time.monotonic() < cleanup_deadline:
            with server.event_bus.lock:
                if not server.event_bus.clients:
                    break
            time.sleep(0.1)
        with server.event_bus.lock:
            assert len(server.event_bus.clients) == 0
        httpd.shutdown()
        http_thread.join(timeout=3)
        httpd.server_close()
        print(f"{idle_seconds:.1f}-second SSE keepalive/client/thread stability: OK")

        # The overlay parser queues both stats and effects and ignores comments.
        game_overlay = importlib.import_module("game_overlay")
        overlay = game_overlay.GameOverlay.__new__(game_overlay.GameOverlay)
        overlay._stats_queue = queue.Queue()
        overlay._effect_queue = queue.Queue()
        overlay._effect_ids = set()
        overlay._overlay_started_at = time.time() - 1
        overlay._queue_sse_event("stats", json.dumps({
            "wins": 4, "losses": 2, "streak": 2, "best_streak": 3,
        }))
        overlay._queue_sse_event("effect", json.dumps({
            "effect": "milestone", "effect_id": "effect-5", "milestone": 5,
            "created_at_ms": int(time.time() * 1000),
        }))
        assert overlay._stats_queue.get_nowait()["wins"] == 4
        assert overlay._effect_queue.get_nowait()["milestone"] == 5
        print("overlay SSE stats/effect queue dispatch: OK")

        # Periodic lifecycle heartbeats stay atomic but skip physical fsync;
        # explicit startup boundaries can still request durable writes.
        fsync_calls = []
        original_fsync = game_overlay.os.fsync
        game_overlay.os.fsync = lambda descriptor: fsync_calls.append(descriptor)
        try:
            runtime_payload = {
                "pid": os.getpid(), "server_pid": os.getpid(),
                "started_at": time.time(), "heartbeat_at": time.time(),
                "state": "ready", "panel_hwnd": 1, "text_hwnd": 2,
                "target_process": "armoredcore6.exe",
            }
            runtime_path = Path(temporary) / "overlay-heartbeat.json"
            game_overlay.write_overlay_runtime(runtime_payload, runtime_path)
            assert fsync_calls == []
            game_overlay.write_overlay_runtime(runtime_payload, runtime_path, durable=True)
            assert len(fsync_calls) == 1
        finally:
            game_overlay.os.fsync = original_fsync
        dashboard = importlib.import_module("dashboard")
        dashboard_fsync_calls = []
        original_dashboard_fsync = dashboard.os.fsync
        dashboard.os.fsync = lambda descriptor: dashboard_fsync_calls.append(descriptor)
        try:
            dashboard_path = Path(temporary) / "dashboard-heartbeat.json"
            dashboard.write_runtime(os.getpid(), 3, dashboard_path)
            assert dashboard_fsync_calls == []
            dashboard.write_runtime(os.getpid(), 3, dashboard_path, durable=True)
            assert len(dashboard_fsync_calls) == 1
        finally:
            dashboard.os.fsync = original_dashboard_fsync
        print("atomic heartbeat without periodic fsync: OK")

        # Routine frames stay memory-only; important context and export retain it.
        diagnostics = importlib.import_module("diagnostics")
        recorder = diagnostics.DiagnosticRecorder()
        log_size_before = recorder.log_path.stat().st_size if recorder.log_path.exists() else 0
        for index in range(5):
            recorder.buffer_frame(frame_state="CLEAR", sample=index)
        assert len(recorder.buffered_frames()) == 5
        log_size_after = recorder.log_path.stat().st_size if recorder.log_path.exists() else 0
        assert log_size_after == log_size_before
        assert recorder.flush_frame_context("result_accepted") == 5
        rows = [json.loads(line) for line in recorder.log_path.read_text(encoding="utf-8").splitlines()]
        assert len(rows) >= 5 and all(row["kind"] == "frame_context" for row in rows[-5:])
        recorder.buffer_frame(frame_state="NON_CLEAR", sample=99)
        archive = recorder.export()
        with zipfile.ZipFile(archive) as bundle:
            assert "frame-buffer.jsonl" in bundle.namelist()
            buffered = bundle.read("frame-buffer.jsonl").decode("utf-8")
            assert '"sample":99' in buffered
            diagnostic_manifest = json.loads(bundle.read("manifest.json").decode("utf-8"))
            assert diagnostic_manifest["python_version"]
            assert diagnostic_manifest["python_architecture"].endswith("-bit")
        assert not (recorder.root / "frame-buffer.jsonl").exists()
        print("diagnostics memory ring/context flush/export: OK")

        # Unchanged config uses the validated cache; mtime change reloads.
        config_utils = importlib.import_module("config_utils")
        config_path = Path(temporary) / "cached-config.json"
        config_path.write_text(json.dumps(config_utils.DEFAULT_CONFIG), encoding="utf-8")
        config_utils.CONFIG_PATH = config_path
        config_utils._last_good = None
        config_utils._last_good_signature = None
        original_load = config_utils.json.load
        load_count = {"value": 0}

        def counted_load(stream):
            load_count["value"] += 1
            return original_load(stream)

        config_utils.json.load = counted_load
        try:
            assert config_utils.load_config()["port"] == 8765
            assert config_utils.load_config()["port"] == 8765
            assert load_count["value"] == 1
            changed = dict(config_utils.DEFAULT_CONFIG, port=8766)
            config_path.write_text(json.dumps(changed), encoding="utf-8")
            os.utime(config_path, ns=(time.time_ns() + 2_000_000_000,) * 2)
            assert config_utils.load_config()["port"] == 8766
            assert load_count["value"] == 2
            config_path.write_text("{invalid", encoding="utf-8")
            os.utime(config_path, ns=(time.time_ns() + 4_000_000_000,) * 2)
            assert config_utils.load_config()["port"] == 8766
            assert config_utils.get_config_health()["status"] == "degraded"
        finally:
            config_utils.json.load = original_load
        print("config signature cache/reload/last-good fallback: OK")

        # Consecutive dashboard reads use memory; mutation invalidates the cache.
        class CountingHistory:
            def __init__(self):
                self.calls = 0

            def session_metadata(self):
                self.calls += 1
                return {"id": 1, "started_at": 1.0}

            def lifetime_summary(self):
                self.calls += 1
                return {"wins": 0, "losses": 0, "draws": 0, "matches": 0,
                        "win_rate": 0.0, "best_streak": 0}

            def recent_matches(self, limit):
                self.calls += 1
                return []

        counting = CountingHistory()
        server.history = counting
        server.set_history_health("active")
        server.invalidate_dashboard_summary()
        server.dashboard_summary()
        server.dashboard_summary()
        assert counting.calls == 3
        server.invalidate_dashboard_summary()
        server.dashboard_summary()
        assert counting.calls == 6
        class FailingSummary:
            def __init__(self):
                self.calls = 0

            def session_metadata(self):
                self.calls += 1
                raise OSError("dashboard test failure")

        failing = FailingSummary()
        server.history = failing
        server.set_history_health("active")
        server.invalidate_dashboard_summary()
        assert server.dashboard_summary()["history_health"]["status"] == "degraded"
        assert server.dashboard_summary()["history_health"]["status"] == "degraded"
        assert failing.calls == 1
        print("dashboard summary cache/invalidation: OK")
    finally:
        if old_local is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = old_local
        if old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = old_home
