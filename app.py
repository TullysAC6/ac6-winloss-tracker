from __future__ import annotations

import subprocess
import sys
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
    return subprocess.Popen(cmd, creationflags=flags)


def _show_error(message: str):
    try:
        import tkinter.messagebox as mb
        mb.showerror(DISPLAY_NAME, message)
    except Exception:
        pass


def main():
    if "--overlay" in sys.argv:
        import game_overlay
        game_overlay.main()
        return
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
        import server
        server.main()
    except Exception as e:
        _show_error(f"起動に失敗しました。\n\n{type(e).__name__}: {e}\n\n診断ZIPを作成して報告してください。")
        traceback.print_exc()
    finally:
        if overlay is not None and overlay.poll() is None:
            try:
                overlay.terminate()
            except Exception:
                pass


if __name__ == "__main__":
    main()
