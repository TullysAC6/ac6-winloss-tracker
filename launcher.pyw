from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path


DISPLAY_NAME = "AC6 Win/Loss Tracker"
APP_DIR = Path(__file__).resolve().parent
APP_PATH = APP_DIR / "app.py"
DATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "AC6WinLossTracker"
RUNTIME_PATH = DATA_DIR / ".runtime.json"
STARTUP_LOG = DATA_DIR / "startup.log"
MAX_LOG_BYTES = 1024 * 1024
STARTUP_TIMEOUT_SECONDS = 10.0


def read_runtime(path: Path = RUNTIME_PATH) -> dict[str, int] | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        pid = raw["pid"]
        port = raw["port"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None
    if type(pid) is not int or pid <= 0:
        return None
    if type(port) is not int or not 1 <= port <= 65535:
        return None
    return {"pid": pid, "port": port}


def process_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        process_query_limited_information = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(
            process_query_limited_information, False, pid
        )
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except (OSError, ValueError):
        return False
    return True


def stats_server_is_ready(port: int, timeout: float = 0.75) -> bool:
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/stats", timeout=timeout
        ) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError, ValueError):
        return False


def running_instance(path: Path = RUNTIME_PATH) -> dict[str, int] | None:
    runtime = read_runtime(path)
    if runtime is None:
        return None
    if not process_is_alive(runtime["pid"]):
        return None
    if not stats_server_is_ready(runtime["port"]):
        return None
    return runtime


def rotate_startup_log(path: Path = STARTUP_LOG) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if path.stat().st_size < MAX_LOG_BYTES:
            return
    except FileNotFoundError:
        return
    rotated = path.with_name(path.name + ".1")
    try:
        rotated.unlink()
    except FileNotFoundError:
        pass
    os.replace(path, rotated)


def start_application(
    app_path: Path = APP_PATH,
    app_dir: Path = APP_DIR,
    log_path: Path = STARTUP_LOG,
) -> subprocess.Popen:
    rotate_startup_log(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    with log_path.open("a", encoding="utf-8", buffering=1) as startup_log:
        startup_log.write(
            f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] "
            f"launch: {sys.executable} {app_path}\n"
        )
        startup_log.flush()
        return subprocess.Popen(
            [sys.executable, str(app_path)],
            cwd=str(app_dir),
            stdin=subprocess.DEVNULL,
            stdout=startup_log,
            stderr=subprocess.STDOUT,
            shell=False,
            creationflags=creationflags,
        )


def wait_for_application(
    process: subprocess.Popen,
    runtime_path: Path = RUNTIME_PATH,
    timeout: float = STARTUP_TIMEOUT_SECONDS,
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        runtime = read_runtime(runtime_path)
        if (
            runtime is not None
            and runtime["pid"] == process.pid
            and process_is_alive(runtime["pid"])
            and stats_server_is_ready(runtime["port"])
        ):
            return True
        time.sleep(0.2)
    return False


def log_launcher_error(path: Path = STARTUP_LOG) -> None:
    try:
        rotate_startup_log(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as startup_log:
            startup_log.write(
                f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] launcher error\n"
            )
            traceback.print_exc(file=startup_log)
    except Exception:
        pass


def launch_once(
    runtime_path: Path = RUNTIME_PATH,
    app_path: Path = APP_PATH,
    app_dir: Path = APP_DIR,
    log_path: Path = STARTUP_LOG,
    timeout: float = STARTUP_TIMEOUT_SECONDS,
) -> str:
    if running_instance(runtime_path) is not None:
        return "already_running"
    try:
        process = start_application(app_path, app_dir, log_path)
    except Exception:
        log_launcher_error(log_path)
        return "failed"
    if wait_for_application(process, runtime_path, timeout):
        return "started"
    if process.poll() is None:
        try:
            process.terminate()
        except OSError:
            pass
    try:
        with log_path.open("a", encoding="utf-8") as startup_log:
            startup_log.write(
                f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] "
                "startup verification failed\n"
            )
    except OSError:
        pass
    return "failed"


def main() -> None:
    import tkinter as tk

    root = tk.Tk()
    root.title(DISPLAY_NAME)
    root.resizable(False, False)
    root.geometry("380x150")
    root.update_idletasks()
    root.geometry(
        f"380x150+{max(0, (root.winfo_screenwidth() - 380) // 2)}"
        f"+{max(0, (root.winfo_screenheight() - 150) // 2)}"
    )

    title = tk.Label(root, text=DISPLAY_NAME, font=("Segoe UI", 13, "bold"))
    title.pack(pady=(24, 12))
    status = tk.Label(root, text="起動しています...", font=("Segoe UI", 11))
    status.pack(padx=20)

    def finish(result: str) -> None:
        if result == "started":
            status.config(text="AC6 Win/Loss Trackerを起動しました。")
            root.after(2000, root.destroy)
        elif result == "already_running":
            status.config(text="AC6 Win/Loss Trackerは\n既に起動しています。")
            root.after(2500, root.destroy)
        else:
            status.config(
                text="AC6 Win/Loss Trackerを起動できませんでした。\n"
                "診断ログを確認してください。"
            )

    def worker() -> None:
        result = launch_once()
        root.after(0, finish, result)

    root.after(100, lambda: threading.Thread(target=worker, daemon=True).start())
    root.mainloop()


if __name__ == "__main__":
    main()
