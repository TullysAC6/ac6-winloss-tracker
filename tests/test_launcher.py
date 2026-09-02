import importlib.machinery
import importlib.util
import json
import os
import tempfile
import textwrap
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
LOADER = importlib.machinery.SourceFileLoader("source_launcher", str(ROOT / "launcher.pyw"))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
launcher = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(launcher)


FAKE_SERVER = textwrap.dedent(
    """
    import json
    import os
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from pathlib import Path

    data = Path(os.environ["LOCALAPPDATA"]) / "AC6WinLossTracker"
    data.mkdir(parents=True, exist_ok=True)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/stats":
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"{}")
            elif self.path == "/health":
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"ok": true}')
            else:
                self.send_response(404)
                self.end_headers()
        def log_message(self, *args):
            pass
        def do_POST(self):
            if self.path == "/api/system/shutdown" and self.headers.get("X-Control-Token") == "test-token":
                self.send_response(200)
                self.end_headers()
                threading.Thread(target=server.shutdown, daemon=True).start()
            else:
                self.send_response(403)
                self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    runtime = {"pid": os.getpid(), "port": server.server_address[1], "token": "test-token"}
    (data / ".runtime.json").write_text(json.dumps(runtime), encoding="utf-8")
    server.serve_forever()
    server.server_close()
    (data / ".runtime.json").unlink(missing_ok=True)
    """
)


with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    old_local_app_data = os.environ.get("LOCALAPPDATA")
    os.environ["LOCALAPPDATA"] = str(root)
    try:
        data = root / "AC6WinLossTracker"
        data.mkdir()
        runtime_path = data / ".runtime.json"
        startup_log = data / "startup.log"
        fake_server = root / "fake_server.py"
        fake_server.write_text(FAKE_SERVER, encoding="utf-8")

        runtime_path.write_text(
            json.dumps({"pid": 2147483647, "port": 65534}), encoding="utf-8"
        )
        assert launcher.running_instance(runtime_path) is None

        process = launcher.start_application(fake_server, root, startup_log)
        try:
            assert launcher.wait_for_application(
                process, runtime_path, timeout=5
            ), startup_log.read_text(encoding="utf-8")
            runtime = launcher.running_instance(runtime_path)
            assert runtime is not None
            assert runtime["pid"] == process.pid

            second_result = launcher.launch_once(
                runtime_path, fake_server, root, startup_log, timeout=1
            )
            assert second_result == "already_running"
            assert process.poll() is None

            shutdown_runtime, overlay_pid = launcher.request_shutdown(runtime_path)
            assert shutdown_runtime is not None and overlay_pid == 0
            process.wait(timeout=5)
            assert launcher.wait_for_complete_shutdown(
                shutdown_runtime, overlay_pid, timeout=5,
                runtime_path=runtime_path,
                overlay_path=data / ".overlay-runtime.json",
                dashboard_path=data / ".dashboard-runtime.json",
            )
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)

        runtime_path.unlink(missing_ok=True)
        dashboard_runtime = data / ".dashboard-runtime.json"
        dashboard_log = data / "dashboard.log"
        fake_dashboard = root / "fake_dashboard.py"
        fake_dashboard.write_text(
            textwrap.dedent(
                """
                import json, os, time
                from pathlib import Path
                path = Path(os.environ["LOCALAPPDATA"]) / "AC6WinLossTracker" / ".dashboard-runtime.json"
                path.write_text(json.dumps({
                    "pid": os.getpid(), "server_pid": os.getppid(),
                    "heartbeat_at": time.time(), "hwnd": 123,
                }), encoding="utf-8")
                time.sleep(0.5)
                path.unlink(missing_ok=True)
                """
            ),
            encoding="utf-8",
        )
        assert launcher.open_dashboard(
            fake_dashboard, root, dashboard_log, dashboard_runtime, timeout=2
        ) is True
        assert "dashboard runtime verification: success" in dashboard_log.read_text(encoding="utf-8")
        # open_dashboard intentionally returns while the dashboard owns the
        # log handle. Wait for this short-lived fixture to exit before the
        # TemporaryDirectory cleanup (Windows does not unlink open files).
        fixture_deadline = time.monotonic() + 3.0
        while dashboard_runtime.exists() and time.monotonic() < fixture_deadline:
            time.sleep(0.05)
        assert not dashboard_runtime.exists()

        dashboard_runtime.write_text(json.dumps({
            "pid": os.getpid(), "server_pid": os.getpid(),
            "heartbeat_at": __import__("time").time(), "hwnd": 456,
        }), encoding="utf-8")
        assert launcher.open_dashboard(
            root / "must-not-launch.py", root, dashboard_log, dashboard_runtime, timeout=1
        ) is True
        dashboard_runtime.unlink(missing_ok=True)

        failing_dashboard = root / "failing_dashboard.py"
        failing_dashboard.write_text(
            "raise RuntimeError('intentional dashboard startup failure')\n", encoding="utf-8"
        )
        assert launcher.open_dashboard(
            failing_dashboard, root, dashboard_log, dashboard_runtime, timeout=2
        ) is False
        dashboard_log_text = dashboard_log.read_text(encoding="utf-8")
        assert "intentional dashboard startup failure" in dashboard_log_text
        assert "dashboard exit code: 1" in dashboard_log_text
        assert "dashboard runtime verification: failed" in dashboard_log_text

        failing_app = root / "failing_app.py"
        failing_app.write_text(
            "raise RuntimeError('intentional launcher failure')\n", encoding="utf-8"
        )
        failed_result = launcher.launch_once(
            runtime_path, failing_app, root, startup_log, timeout=2
        )
        assert failed_result == "failed"
        assert "intentional launcher failure" in startup_log.read_text(encoding="utf-8")

        startup_log.write_bytes(b"x" * (launcher.MAX_LOG_BYTES + 1))
        launcher.rotate_startup_log(startup_log)
        assert not startup_log.exists()
        assert startup_log.with_name("startup.log.1").stat().st_size > launcher.MAX_LOG_BYTES
    finally:
        if old_local_app_data is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = old_local_app_data

print("launcher startup / duplicate / stale runtime / failure logging: OK")

launcher_source = (ROOT / "launcher.pyw").read_text(encoding="utf-8")
assert "GetExitCodeProcess" in launcher_source
assert "exit_code.value == 259" in launcher_source

installer = (ROOT / "install.ps1").read_text(encoding="utf-8")
assert "$shortcut.TargetPath = $PythonwPath" in installer
assert "$shortcut.Arguments = '\"{0}\"' -f $LauncherPath" in installer
assert "New-AppShortcut -PythonwPath $python.PythonwPath -LauncherPath $launcherPath" in installer
assert "Start-Process -FilePath $python.PythonwPath -ArgumentList $launcherArguments" in installer
assert "Wait-AppRuntimeReady -TimeoutSeconds 15" in installer
assert '"http://127.0.0.1:{0}/health"' in installer
print("launcher shortcut and installer readiness checks: OK")

assert "Get-CimInstance Win32_Process" in installer
assert "Get-NetTCPConnection -LocalPort $RuntimePort -State Listen" in installer
assert "Test-TrackerCommandLine" in installer
assert "(?i)^pythonw?\\.exe$" in installer
assert "[Regex]::Escape($installPath)" in installer
assert "(app\\.py|launcher\\.pyw|dashboard\\.py)" in installer
assert "Stop-Process -Id $processId" in installer
assert installer.index("Stop-RunningTracker") < installer.index("Install-SourceTree -SourcePath")
print("installer Tracker-only fallback safety checks: OK")
