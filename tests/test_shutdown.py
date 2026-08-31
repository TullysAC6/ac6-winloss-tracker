import json
import os
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

with tempfile.TemporaryDirectory() as temporary:
    old_local_app_data = os.environ.get("LOCALAPPDATA")
    os.environ["LOCALAPPDATA"] = temporary
    try:
        import server

        overlay_runtime = Path(temporary) / "AC6WinLossTracker" / ".overlay-runtime.json"
        overlay_runtime.parent.mkdir(parents=True, exist_ok=True)
        overlay_runtime.write_text(json.dumps({
            "pid": os.getpid(), "server_pid": os.getpid(),
            "heartbeat_at": __import__("time").time(), "state": "ready",
            "panel_hwnd": 1, "text_hwnd": 2,
        }), encoding="utf-8")
        server.OVERLAY_RUNTIME_PATH = overlay_runtime
        server.detector_fallback.update(status="waiting", error=None)
        assert server.lifecycle_health()["ok"] is True
        server.detector_fallback.update(status="disabled", error=None)
        assert server.lifecycle_health()["ok"] is True
        server.detector_fallback.update(status="error", error="fatal")
        assert server.lifecycle_health()["ok"] is False
        server.detector_fallback.update(status="waiting", error=None)
        stale = json.loads(overlay_runtime.read_text(encoding="utf-8"))
        stale["heartbeat_at"] -= 10
        overlay_runtime.write_text(json.dumps(stale), encoding="utf-8")
        assert server.lifecycle_health()["ok"] is False
        stale["heartbeat_at"] = __import__("time").time()
        stale["server_pid"] = os.getpid() + 1
        overlay_runtime.write_text(json.dumps(stale), encoding="utf-8")
        assert server.lifecycle_health()["ok"] is False
        stale["server_pid"] = os.getpid()
        stale["pid"] = 2147483647
        overlay_runtime.write_text(json.dumps(stale), encoding="utf-8")
        assert server.lifecycle_health()["ok"] is False
        stale["pid"] = os.getpid()
        overlay_runtime.write_text(json.dumps(stale), encoding="utf-8")

        server.CONTROL_TOKEN = "shutdown-test-token"
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{httpd.server_address[1]}/api/system/shutdown"

        unauthorized = urllib.request.Request(url, data=b"", method="POST")
        try:
            urllib.request.urlopen(unauthorized, timeout=2)
            raise AssertionError("shutdown accepted without a control token")
        except urllib.error.HTTPError as error:
            assert error.code == 403
        assert thread.is_alive()

        authorized = urllib.request.Request(
            url,
            data=b"",
            method="POST",
            headers={"X-Control-Token": server.CONTROL_TOKEN},
        )
        with urllib.request.urlopen(authorized, timeout=2) as response:
            assert response.status == 200
            payload = json.loads(response.read().decode("utf-8"))
            assert payload == {"ok": True, "status": "shutting_down"}
        thread.join(timeout=3)
        assert not thread.is_alive()
        httpd.server_close()
    finally:
        if old_local_app_data is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = old_local_app_data

print("authenticated graceful shutdown endpoint: OK")
