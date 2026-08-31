import importlib
import json
import os
import sys
import tempfile
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


with tempfile.TemporaryDirectory() as temporary:
    old = os.environ.get("LOCALAPPDATA")
    os.environ["LOCALAPPDATA"] = temporary
    try:
        server = importlib.import_module("server")
        from history_store import HistoryStore

        server.ensure_first_run_config()
        server.stats.reset()
        server.history = HistoryStore(server.DATA_ROOT)
        first_session = server.history.start_session()
        server.set_history_health("active")

        for result in ("win", "win", "win", "loss", "loss"):
            server.result_gate.clear_for_manual_correction()
            assert server.record_result(result, "test") is True

        summary = server.dashboard_summary()
        assert summary["session"]["wins"] == 3
        assert summary["session"]["losses"] == 2
        assert summary["lifetime"]["wins"] == 3
        assert summary["lifetime"]["losses"] == 2
        assert len(summary["recent_matches"]) == 5

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        with urllib.request.urlopen(
            f"http://127.0.0.1:{httpd.server_address[1]}/api/dashboard/summary",
            timeout=2,
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
            assert response.status == 200 and payload["lifetime"]["matches"] == 5

        # A second bind failure is before all startup mutation boundaries.
        sessions_before = len(server.history.recent_matches(100))
        try:
            ThreadingHTTPServer(("127.0.0.1", httpd.server_address[1]), server.Handler)
            raise AssertionError("duplicate server unexpectedly bound")
        except OSError:
            pass
        assert len(server.history.recent_matches(100)) == sessions_before
        assert server.stats.snapshot()["wins"] == 3

        server.reset_stats()
        reset_summary = server.dashboard_summary()
        assert reset_summary["session"]["wins"] == 0
        assert reset_summary["session"]["losses"] == 0
        assert reset_summary["session"]["id"] != first_session
        assert reset_summary["lifetime"]["wins"] == 3
        assert reset_summary["lifetime"]["losses"] == 2
        assert len(reset_summary["recent_matches"]) == 5

        class FailingHistory:
            def record_result(self, *args, **kwargs):
                raise OSError("intentional history failure")

            def session_metadata(self):
                raise OSError("intentional history failure")

        healthy_history = server.history
        server.history = FailingHistory()
        server.result_gate.clear_for_manual_correction()
        assert server.record_result("win", "history-failure-test") is True
        assert server.stats.snapshot()["wins"] == 1
        assert server.history_health["status"] == "degraded"
        failure_summary = server.dashboard_summary()
        assert failure_summary["session"]["wins"] == 1
        assert failure_summary["history_health"]["status"] == "degraded"
        server.history = healthy_history
        server.undo_result()
        assert server.stats.snapshot()["wins"] == 0
        assert server.dashboard_summary()["session"]["wins"] == 0
        assert len(healthy_history.recent_matches(10)) == 5

        httpd.shutdown()
        thread.join(timeout=3)
        httpd.server_close()
    finally:
        if old is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = old

print("server history flow/API/startup boundary/manual reset/lifetime persistence: OK")
