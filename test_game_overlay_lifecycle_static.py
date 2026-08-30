from pathlib import Path

ROOT = Path(__file__).resolve().parent
s = (ROOT / "game_overlay.py").read_text(encoding="utf-8")
app = (ROOT / "app.py").read_text(encoding="utf-8", errors="replace")

assert "linked to server.py PID" in s
assert "server.py stopped" in s
assert "rendering is not startup-gated" in s
# The normal visibility decision must occur before lifecycle termination logic.
tick = s[s.index("    def _tick(self) -> None:"):s.index("    def run(self) -> None:")]
assert tick.index("foreground_game_client") < tick.index("_poll_server_lifecycle()")
assert 'server_state != "alive"' not in tick
assert '_launch_overlay()' in app
assert 'import server' in app
assert '--overlay' in app
print("game overlay lifecycle follows server without startup visibility gating: OK")
