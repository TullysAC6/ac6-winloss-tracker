from __future__ import annotations

import subprocess
import sys
import time
import traceback

from app_paths import DISPLAY_NAME


def _launch_overlay():
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, "--overlay"]
    else:
        cmd = [sys.executable, __file__, "--overlay"]
    flags = 0
    if sys.platform == "win32":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen(cmd, creationflags=flags)
    print(f"[lifecycle] overlay command: {cmd!r}")
    print(f"[lifecycle] overlay PID: {process.pid}")
    return process


def _stop_overlay(process, timeout=3.0):
    if process is None or process.poll() is not None:
        return
    # The overlay watches .runtime.json and normally exits itself, which lets
    # it remove its owned heartbeat file in Python-level cleanup.
    try:
        process.wait(timeout=2.0)
        print(f"[lifecycle] overlay stopped itself: PID {process.pid}, exit {process.returncode}")
        return
    except subprocess.TimeoutExpired:
        pass
    process.terminate()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=timeout)
    print(f"[lifecycle] overlay stopped: PID {process.pid}, exit {process.returncode}")


def _show_error(message: str):
    try:
        import tkinter.messagebox as mb
        mb.showerror(DISPLAY_NAME, message)
    except Exception:
        pass


def main():
    if "--overlay" in sys.argv:
        import game_overlay
        overlay_args = [arg for arg in sys.argv[1:] if arg != "--overlay"]
        raise SystemExit(game_overlay.main(overlay_args))
    if "--diagnostics" in sys.argv:
        from diagnostics import RECORDER
        path = RECORDER.export()
        try:
            import tkinter.messagebox as mb
            mb.showinfo(DISPLAY_NAME, f"診断ZIPを作成しました。\n\n{path}")
        except Exception:
            print(path)
        return

    overlay = None
    try:
        overlay = _launch_overlay()
        time.sleep(0.25)
        if overlay.poll() is not None:
            print(f"[lifecycle] overlay exited during startup: PID {overlay.pid}, exit {overlay.returncode}")
        import server
        server.main()
    except Exception as e:
        _show_error(f"起動に失敗しました。\n\n{type(e).__name__}: {e}\n\n診断ZIPを作成して報告してください。")
        traceback.print_exc()
    finally:
        try:
            _stop_overlay(overlay)
        except Exception as e:
            print(f"[lifecycle] overlay shutdown failed: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
