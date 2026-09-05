"""Windows integration: launcher exits; real server/overlay stay healthy."""
import json
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
loader = importlib.machinery.SourceFileLoader("lifetime_launcher", str(ROOT / "launcher.pyw"))
spec = importlib.util.spec_from_loader(loader.name, loader)
launcher = importlib.util.module_from_spec(spec)
loader.exec_module(launcher)

if os.name != "nt":
    raise SystemExit(0)

with tempfile.TemporaryDirectory(prefix="ac6-lifetime-") as temporary:
    data = Path(temporary) / "AC6WinLossTracker"
    data.mkdir()
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    (data / "config.json").write_text(json.dumps({
        "config_version": 17, "port": port, "stats_enabled": True, "result_detector_enabled": False,
    }), encoding="utf-8")
    env = dict(os.environ, LOCALAPPDATA=temporary, PYTHONNOUSERSITE="1")
    code = (
        "import importlib.machinery,importlib.util;"
        "l=importlib.machinery.SourceFileLoader('l','launcher.pyw');"
        "s=importlib.util.spec_from_loader(l.name,l);"
        "m=importlib.util.module_from_spec(s);l.exec_module(m);"
        "r=m.launch_once();print(r);raise SystemExit(0 if r=='started' else 1)"
    )
    runtime = None
    try:
        # Match the installed Windows shortcut (venv pythonw, not console Python).
        pythonw = str(Path(sys.executable).with_name("pythonw.exe"))
        result = subprocess.run([pythonw, "-c", code], cwd=ROOT,
                                env=env, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        runtime = json.loads((data / ".runtime.json").read_text())
        assert runtime["port"] == port
        # Longer than the original erroneous ten-second timeout.
        for _ in range(13):
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health") as response:
                health = json.load(response)
            assert health["ok"] and health["overlay"]["ok"]
            time.sleep(1)
        print("real launcher exited; server/overlay healthy beyond timeout: OK")
    finally:
        path = data / ".runtime.json"
        if runtime is None and path.exists():
            runtime = json.loads(path.read_text())
        if runtime:
            overlay_path = data / ".overlay-runtime.json"
            overlay_pid = launcher.read_overlay_pid(overlay_path)
            request = urllib.request.Request(
                f"http://127.0.0.1:{runtime['port']}/api/system/shutdown", data=b"", method="POST",
                headers={"X-Control-Token": runtime["token"]})
            with urllib.request.urlopen(request, timeout=3):
                pass
            assert launcher.wait_for_complete_shutdown(
                runtime, overlay_pid, timeout=15, runtime_path=path,
                overlay_path=overlay_path, dashboard_path=data / ".dashboard-runtime.json")
    print("real server/overlay authenticated cleanup: OK")
