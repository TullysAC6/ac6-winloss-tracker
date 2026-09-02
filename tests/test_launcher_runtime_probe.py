"""Temporary Windows CI probe for launcher readiness predicates."""

import ctypes
import importlib.machinery
import importlib.util
import json
import os
import platform
import subprocess
import sys
import tempfile
import textwrap
import time
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
LOADER = importlib.machinery.SourceFileLoader("probe_launcher", str(ROOT / "launcher.pyw"))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
launcher = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(launcher)


def raw_windows_probe(pid):
    if os.name != "nt":
        return {"platform": "not-windows"}
    kernel32 = ctypes.windll.kernel32
    ctypes.set_last_error(0)
    handle = kernel32.OpenProcess(0x1000, False, pid)
    open_error = ctypes.get_last_error()
    result = {
        "handle": handle,
        "handle_type": type(handle).__name__,
        "open_error": open_error,
    }
    if not handle:
        return result
    try:
        exit_code = ctypes.c_ulong(0)
        ctypes.set_last_error(0)
        ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
        result.update({
            "get_exit_ok": bool(ok),
            "get_exit_error": ctypes.get_last_error(),
            "exit_code": exit_code.value,
        })
        return result
    finally:
        kernel32.CloseHandle(handle)


def health_probe(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=0.75) as response:
            body = response.read().decode("utf-8")
            return {"status": response.status, "json": json.loads(body), "exception": None}
    except Exception as error:
        return {"status": None, "json": None, "exception": f"{type(error).__name__}: {error}"}


print(f"python={sys.version}")
print(f"architecture={platform.architecture()[0]} pointer_bits={ctypes.sizeof(ctypes.c_void_p) * 8}")

child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(1.0)"])
try:
    for index in range(6):
        time.sleep(0.15)
        print(json.dumps({
            "sleep_elapsed": round((index + 1) * 0.15, 2),
            "pid": child.pid,
            "poll": child.poll(),
            "process_is_alive": launcher.process_is_alive(child.pid),
            "windows_api": raw_windows_probe(child.pid),
        }, sort_keys=True))
    child.wait(timeout=3)
    print(json.dumps({
        "sleep_after_exit": True,
        "pid": child.pid,
        "poll": child.poll(),
        "process_is_alive": launcher.process_is_alive(child.pid),
        "windows_api": raw_windows_probe(child.pid),
    }, sort_keys=True))
finally:
    if child.poll() is None:
        child.terminate()
        child.wait(timeout=3)


FAKE_SERVER = textwrap.dedent(
    """
    import json, os, threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from pathlib import Path

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path in ("/stats", "/health"):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"ok": true}')
            else:
                self.send_response(404)
                self.end_headers()
        def do_POST(self):
            self.send_response(200)
            self.end_headers()
            threading.Thread(target=server.shutdown, daemon=True).start()
        def log_message(self, *args):
            pass

    data = Path(os.environ["LOCALAPPDATA"]) / "AC6WinLossTracker"
    data.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    path = data / ".runtime.json"
    path.write_text(json.dumps({"pid": os.getpid(), "port": server.server_address[1], "token": "probe"}), encoding="utf-8")
    server.serve_forever()
    server.server_close()
    path.unlink(missing_ok=True)
    """
)

with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    data = root / "AC6WinLossTracker"
    data.mkdir()
    runtime_path = data / ".runtime.json"
    log_path = data / "startup.log"
    script_path = root / "probe_server.py"
    script_path.write_text(FAKE_SERVER, encoding="utf-8")
    old_localappdata = os.environ.get("LOCALAPPDATA")
    os.environ["LOCALAPPDATA"] = str(root)
    process = launcher.start_application(script_path, root, log_path)
    started = time.monotonic()
    ready = False
    try:
        while time.monotonic() - started < 5:
            elapsed = time.monotonic() - started
            poll = process.poll()
            exists = runtime_path.exists()
            runtime = launcher.read_runtime(runtime_path)
            pid_match = runtime is not None and runtime["pid"] == process.pid
            alive = launcher.process_is_alive(runtime["pid"]) if runtime else None
            health = health_probe(runtime["port"]) if runtime else None
            print(json.dumps({
                "elapsed": round(elapsed, 3),
                "expected_pid": process.pid,
                "poll": poll,
                "runtime_exists": exists,
                "runtime": runtime,
                "pid_match": pid_match,
                "process_is_alive": alive,
                "health": health,
                "windows_api": raw_windows_probe(process.pid),
            }, ensure_ascii=False, sort_keys=True))
            if poll is None and pid_match and alive and health and health["status"] == 200 and health["json"].get("ok") is True:
                ready = True
                break
            if poll is not None:
                break
            time.sleep(0.2)
        assert ready, log_path.read_text(encoding="utf-8")
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
        if old_localappdata is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = old_localappdata

print("launcher runtime probe: OK")
