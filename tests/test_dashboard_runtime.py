import importlib
import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


with tempfile.TemporaryDirectory() as temporary:
    old = os.environ.get("LOCALAPPDATA")
    os.environ["LOCALAPPDATA"] = temporary
    try:
        dashboard = importlib.import_module("dashboard")
        runtime_path = Path(temporary) / "AC6WinLossTracker" / ".dashboard-runtime.json"
        dashboard.write_runtime(os.getpid(), 123, runtime_path)
        payload = json.loads(runtime_path.read_text(encoding="utf-8"))
        assert payload["pid"] == os.getpid()
        assert payload["server_pid"] == os.getpid()
        assert payload["hwnd"] == 123
        assert dashboard.existing_dashboard(runtime_path)["pid"] == os.getpid()

        payload["pid"] = 2147483647
        payload["heartbeat_at"] = time.time() - 30
        runtime_path.write_text(json.dumps(payload), encoding="utf-8")
        assert dashboard.existing_dashboard(runtime_path) is None
        assert not runtime_path.exists()

        dashboard.write_runtime(os.getpid(), 456, runtime_path)
        dashboard.remove_owned_runtime(runtime_path)
        assert not runtime_path.exists()

        class Handler(BaseHTTPRequestHandler):
            reset_seen = False

            def log_message(self, *args):
                pass

            def do_GET(self):
                body = json.dumps({
                    "system": True, "session": {}, "lifetime": {},
                    "recent_matches": [],
                    "history_health": {"status": "active", "error": None},
                }).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                Handler.reset_seen = self.headers.get("X-Control-Token") == "secret"
                self.send_response(200 if Handler.reset_seen else 403)
                self.end_headers()

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_address[1]
        assert dashboard.fetch_summary(port)["history_health"]["status"] == "active"
        dashboard.request_session_reset({"port": port, "token": "secret"})
        assert Handler.reset_seen
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()
    finally:
        if old is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = old

print("dashboard runtime ownership/stale cleanup/API/reset: OK")
