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

        time.sleep(game_overlay.HEARTBEAT_SECONDS + 0.1)
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
assert "self._stats_queue.put(stats_payload)" in game_source
assert "if not self._sse_connected.is_set()" in game_source
assert "fallback_stats = read_stats()" in game_source
assert "\n        s = read_stats()\n" not in game_source[game_source.index("def _tick(self)"):]
assert 'self.canvas.delete("milestone")' not in game_source
assert 'self.canvas.delete("all")' in game_source
assert 'self._show_at_game(left, top)' in game_source
assert 'int(height * 0.62)' in game_source
print("server-sourced 5..50 effect rendering coverage: OK")


class FakeCanvas:
    def __init__(self):
        self.deleted = []
        self.rectangles = 0
        self.texts = 0

    def configure(self, **kwargs):
        pass

    def delete(self, tag):
        self.deleted.append(tag)

    def create_rectangle(self, *args, **kwargs):
        self.rectangles += 1

    def create_text(self, *args, **kwargs):
        self.texts += 1


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


# Identical animation frames reuse the existing canvas; only the four exact
# 50-win visual stage transitions redraw it.
names = ("user32", "HWND_TOPMOST", "SWP_NOACTIVATE", "SWP_SHOWWINDOW", "SW_SHOWNOACTIVATE")
saved = {name: getattr(game_overlay, name, missing) for name in names}
original_monotonic = game_overlay.time.monotonic
try:
    fake_user32 = FakeUser32()
    game_overlay.user32 = fake_user32
    game_overlay.HWND_TOPMOST = -1
    game_overlay.SWP_NOACTIVATE = 0x10
    game_overlay.SWP_SHOWWINDOW = 0x40
    game_overlay.SW_SHOWNOACTIVATE = 4
    overlay = game_overlay.GameOverlay.__new__(game_overlay.GameOverlay)
    overlay.effect_canvas = FakeCanvas()
    overlay.effect_hwnd = 303
    overlay._effect_visible = False
    overlay._active_effect = {
        "effect_id": "five", "milestone": 5, "started": 0.0,
        "duration": 3.5, "render_key": None,
    }
    game_overlay.time.monotonic = lambda: 0.5
    overlay._render_milestone_effect((1, 0, 0, 1920, 1080))
    overlay._render_milestone_effect((1, 0, 0, 1920, 1080))
    assert overlay.effect_canvas.deleted == ["milestone"]

    overlay.effect_canvas = FakeCanvas()
    overlay._active_effect = {
        "effect_id": "fifty", "milestone": 50, "started": 0.0,
        "duration": 6.0, "render_key": None,
    }
    for moment in (0.1, 0.5, 1.0, 2.0, 4.0):
        game_overlay.time.monotonic = lambda value=moment: value
        overlay._render_milestone_effect((1, 0, 0, 1920, 1080))
    assert len(overlay.effect_canvas.deleted) == 4
finally:
    game_overlay.time.monotonic = original_monotonic
    for name, value in saved.items():
        if value is missing:
            try:
                delattr(game_overlay, name)
            except AttributeError:
                pass
        else:
            setattr(game_overlay, name, value)

print("milestone visual stages redraw only on transitions: OK")
