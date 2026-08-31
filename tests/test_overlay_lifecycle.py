import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

with tempfile.TemporaryDirectory() as temporary:
    old = os.environ.get("LOCALAPPDATA")
    os.environ["LOCALAPPDATA"] = temporary
    try:
        import game_overlay

        path = Path(temporary) / "AC6WinLossTracker" / ".overlay-runtime.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"pid": 2147483647, "state": "ready"}), encoding="utf-8")
        game_overlay.OVERLAY_RUNTIME_PATH = path
        game_overlay.remove_stale_overlay_runtime()
        assert not path.exists()

        overlay = game_overlay.GameOverlay.__new__(game_overlay.GameOverlay)
        overlay._server_linked = True
        overlay._server_state = "alive"
        overlay._server_pid = os.getpid()
        overlay._overlay_started_at = time.time()
        overlay._last_heartbeat_at = 0.0
        overlay.panel_hwnd = 101
        overlay.text_hwnd = 202
        overlay.process_name = "armoredcore6.exe"
        overlay._publish_ready_heartbeat()
        first = json.loads(path.read_text(encoding="utf-8"))
        assert first["pid"] == os.getpid()
        assert first["server_pid"] == os.getpid()
        assert first["state"] == "ready"
        assert first["panel_hwnd"] == 101 and first["text_hwnd"] == 202

        time.sleep(0.8)
        overlay._publish_ready_heartbeat()
        second = json.loads(path.read_text(encoding="utf-8"))
        assert second["heartbeat_at"] > first["heartbeat_at"]
        game_overlay.remove_owned_overlay_runtime()
        assert not path.exists()
    finally:
        if old is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = old

print("overlay ready runtime / heartbeat / owner cleanup: OK")
