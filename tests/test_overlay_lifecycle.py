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

overlay_source = (ROOT / "overlay.html").read_text(encoding="utf-8")
game_source = (ROOT / "game_overlay.py").read_text(encoding="utf-8")
for milestone in range(5, 51, 5):
    assert f"{milestone}連勝" in overlay_source
assert "_start_effect_listener" in game_source
assert "_render_milestone_effect" in game_source
assert "effect_id" in game_source
assert 'self.effect_canvas.delete("milestone")' in game_source
assert 'self.canvas.delete("milestone")' not in game_source
assert 'self.canvas.delete("all")' in game_source
assert 'self._show_at_game(left, top)' in game_source
assert 'int(height * 0.62)' in game_source
print("server-sourced 5..50 effect rendering coverage: OK")


class FakeCanvas:
    def __init__(self):
        self.deleted = []

    def delete(self, tag):
        self.deleted.append(tag)


class FakeUser32:
    def __init__(self):
        self.hidden = []

    def ShowWindow(self, hwnd, command):
        self.hidden.append((hwnd, command))

    def SetWindowPos(self, *args):
        return True


missing = object()
original_user32 = getattr(game_overlay, "user32", missing)
original_sw_hide = getattr(game_overlay, "SW_HIDE", missing)
try:
    fake_user32 = FakeUser32()
    game_overlay.user32 = fake_user32
    game_overlay.SW_HIDE = 0
    overlay = game_overlay.GameOverlay.__new__(game_overlay.GameOverlay)
    overlay.effect_canvas = FakeCanvas()
    overlay.effect_hwnd = 303
    overlay.text_hwnd = 202
    overlay._effect_visible = True
    overlay._active_effect = {"effect_id": "once", "milestone": 5}
    overlay.visible = False
    overlay._finish_effect()
    assert overlay._active_effect is None
    assert overlay._effect_visible is False
    assert overlay.effect_canvas.deleted == ["milestone"]
    assert fake_user32.hidden == [(303, game_overlay.SW_HIDE)]
finally:
    if original_user32 is missing:
        del game_overlay.user32
    else:
        game_overlay.user32 = original_user32
    if original_sw_hide is missing:
        del game_overlay.SW_HIDE
    else:
        game_overlay.SW_HIDE = original_sw_hide

print("transient effect cleanup leaves persistent HUD untouched: OK")
