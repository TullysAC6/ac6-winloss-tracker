from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main():
    start = (ROOT / "app.py").read_text(encoding="utf-8", errors="replace").lower()
    overlay = (ROOT / "game_overlay.py").read_text(encoding="utf-8", errors="replace")
    assert '--overlay' in start
    assert '_launch_overlay' in start
    assert 'import server' in start
    assert '_acquire_single_instance_mutex' in overlay
    assert 'OVERLAY_MUTEX_NAME' in overlay
    print('game overlay autostart wiring: OK')


if __name__ == '__main__':
    main()
