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
DASHBOARD_PATH = APP_DIR / "dashboard.py"
DATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "AC6WinLossTracker"
RUNTIME_PATH = DATA_DIR / ".runtime.json"
OVERLAY_RUNTIME_PATH = DATA_DIR / ".overlay-runtime.json"
DASHBOARD_RUNTIME_PATH = DATA_DIR / ".dashboard-runtime.json"
STARTUP_LOG = DATA_DIR / "startup.log"
MAX_LOG_BYTES = 1024 * 1024
STARTUP_TIMEOUT_SECONDS = 10.0


def read_runtime(path: Path = RUNTIME_PATH) -> dict | None:
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
    token = raw.get("token")
    return {"pid": pid, "port": port, "token": token if isinstance(token, str) else ""}


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


def tracker_health(port: int, timeout: float = 0.75) -> dict | None:
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/health", timeout=timeout
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return payload if response.status == 200 and payload.get("ok") is True else None
    except (OSError, urllib.error.URLError, ValueError, json.JSONDecodeError):
        return None


def running_instance(path: Path = RUNTIME_PATH) -> dict[str, int] | None:
    runtime = read_runtime(path)
    if runtime is None:
        return None
    if not process_is_alive(runtime["pid"]):
        return None
    if tracker_health(runtime["port"]) is None:
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


def open_dashboard(
    dashboard_path: Path = DASHBOARD_PATH,
    app_dir: Path = APP_DIR,
    log_path: Path = STARTUP_LOG,
) -> bool:
    """Launch the optional dashboard with this verified Python executable."""
    try:
        rotate_startup_log(log_path)
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        with log_path.open("a", encoding="utf-8", buffering=1) as startup_log:
            subprocess.Popen(
                [sys.executable, str(dashboard_path)], cwd=str(app_dir),
                stdin=subprocess.DEVNULL, stdout=startup_log,
                stderr=subprocess.STDOUT, shell=False, creationflags=creationflags,
            )
        return True
    except Exception:
        log_launcher_error(log_path)
        return False


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
            and tracker_health(runtime["port"]) is not None
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
        try:
            runtime = read_runtime(runtime_path)
            health = tracker_health(runtime["port"]) if runtime else None
            with log_path.open("a", encoding="utf-8") as log:
                if health:
                    log.write(
                        f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] server ready; "
                        f"detector={health['detector'].get('status')} "
                        f"overlay PID={health['overlay'].get('pid')} overlay ready; "
                        "overall health ready\n"
                    )
        except (OSError, KeyError, TypeError):
            pass
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


def read_overlay_pid(path: Path = OVERLAY_RUNTIME_PATH) -> int:
    try:
        value = json.loads(path.read_text(encoding="utf-8")).get("pid")
        return value if type(value) is int and value > 0 else 0
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return 0


def read_dashboard_pid(path: Path = DASHBOARD_RUNTIME_PATH) -> int:
    try:
        value = json.loads(path.read_text(encoding="utf-8")).get("pid")
        return value if type(value) is int and value > 0 else 0
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return 0


def request_shutdown(runtime_path: Path = RUNTIME_PATH) -> tuple[dict | None, int]:
    runtime = read_runtime(runtime_path)
    overlay_pid = read_overlay_pid()
    dashboard_pid = read_dashboard_pid()
    if runtime is None or not runtime.get("token"):
        return None, overlay_pid
    request = urllib.request.Request(
        f"http://127.0.0.1:{runtime['port']}/api/system/shutdown",
        data=b"", method="POST",
        headers={"X-Control-Token": runtime["token"]},
    )
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            if response.status != 200:
                return None, overlay_pid
    except (OSError, urllib.error.URLError):
        return None, overlay_pid
    runtime["dashboard_pid"] = dashboard_pid
    return runtime, overlay_pid


def wait_for_complete_shutdown(
    runtime: dict, overlay_pid: int, timeout: float = 12.0,
    runtime_path: Path = RUNTIME_PATH, overlay_path: Path = OVERLAY_RUNTIME_PATH,
    dashboard_path: Path = DASHBOARD_RUNTIME_PATH,
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        server_gone = not process_is_alive(runtime["pid"])
        overlay_gone = overlay_pid <= 0 or not process_is_alive(overlay_pid)
        dashboard_pid = int(runtime.get("dashboard_pid", 0))
        dashboard_gone = dashboard_pid <= 0 or not process_is_alive(dashboard_pid)
        dashboard_file_gone = dashboard_pid <= 0 or not dashboard_path.exists()
        files_gone = (
            not runtime_path.exists() and not overlay_path.exists()
            and dashboard_file_gone
        )
        endpoints_gone = (
            not stats_server_is_ready(runtime["port"], 0.3)
            and tracker_health(runtime["port"], 0.3) is None
        )
        if server_gone and overlay_gone and dashboard_gone and files_gone and endpoints_gone:
            return True
        time.sleep(0.2)
    return False


def shutdown_tracker(log_path: Path = STARTUP_LOG) -> bool:
    runtime, overlay_pid = request_shutdown()
    if runtime is None:
        return False
    result = wait_for_complete_shutdown(runtime, overlay_pid)
    try:
        with log_path.open("a", encoding="utf-8") as log:
            log.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] shutdown requested\n")
            log.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] overall shutdown {'complete' if result else 'failed'}\n")
    except OSError:
        pass
    return result


def main() -> None:
    import tkinter as tk

    root = tk.Tk()
    root.title(DISPLAY_NAME)
    root.resizable(False, False)
    root.geometry("420x230")
    root.update_idletasks()
    root.geometry(
        f"420x230+{max(0, (root.winfo_screenwidth() - 420) // 2)}"
        f"+{max(0, (root.winfo_screenheight() - 230) // 2)}"
    )

    title = tk.Label(root, text=DISPLAY_NAME, font=("Segoe UI", 13, "bold"))
    title.pack(pady=(24, 12))
    status = tk.Label(root, text="起動しています...", font=("Segoe UI", 11))
    status.pack(padx=20)
    actions = tk.Frame(root)
    button_row = tk.Frame(actions)
    dashboard_button = tk.Button(actions, text="ダッシュボードを開く", width=22)
    close_button = tk.Button(button_row, text="閉じる", width=12, command=root.destroy)
    shutdown_button = tk.Button(button_row, text="Trackerを終了", width=16)
    dashboard_button.pack(pady=(0, 8))
    shutdown_button.pack(side="left", padx=6)
    close_button.pack(side="left", padx=6)
    button_row.pack()

    def begin_dashboard() -> None:
        if open_dashboard():
            status.config(text="ダッシュボードを開きました。")
        else:
            status.config(text="ダッシュボードを開けませんでした。\n診断ログを確認してください。")

    dashboard_button.config(command=begin_dashboard)

    def shutdown_finish(ok: bool) -> None:
        shutdown_button.config(state="disabled")
        if ok:
            status.config(text="AC6 Win/Loss Trackerを\n完全に終了しました。")
            root.after(2000, root.destroy)
        else:
            status.config(text="AC6 Win/Loss Trackerを\n完全に終了できませんでした。\n診断ログを確認してください。")

    def begin_shutdown() -> None:
        actions.pack_forget()
        status.config(text="終了しています...")
        threading.Thread(
            target=lambda: root.after(0, shutdown_finish, shutdown_tracker()), daemon=True
        ).start()

    shutdown_button.config(command=begin_shutdown)

    def finish(result: str) -> None:
        if result == "started":
            status.config(text="AC6 Win/Loss Trackerを\n正常に起動しました。")
            root.after(2000, root.destroy)
        elif result == "already_running":
            status.config(text="AC6 Win/Loss Trackerは\n正常に起動中です。")
            actions.pack(pady=16)
        else:
            status.config(
                text="AC6 Win/Loss Trackerを\n完全に起動できませんでした。\n"
                "ゲームオーバーレイまたは診断ログを確認してください。"
            )

    def worker() -> None:
        result = launch_once()
        root.after(0, finish, result)

    root.after(100, lambda: threading.Thread(target=worker, daemon=True).start())
    root.mainloop()


if __name__ == "__main__":
    main()
