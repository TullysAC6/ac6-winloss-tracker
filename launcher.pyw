from __future__ import annotations

import ctypes
import json
import os
import secrets
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
DASHBOARD_LOG = DATA_DIR / "dashboard.log"
MAX_LOG_BYTES = 1024 * 1024
STARTUP_TIMEOUT_SECONDS = 10.0
LAUNCH_ID_ENV = "AC6_TRACKER_LAUNCH_ID"


def utf8_python_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment["PYTHONNOUSERSITE"] = "1"
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    return environment


def new_launch_id() -> str:
    return secrets.token_urlsafe(24)


def read_requested_launch_id(arguments: list[str] | None = None) -> str | None:
    arguments = sys.argv[1:] if arguments is None else arguments
    try:
        index = arguments.index("--launch-id")
        value = arguments[index + 1]
    except (ValueError, IndexError):
        return None
    if not 16 <= len(value) <= 128 or any(
        character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
        for character in value
    ):
        return None
    return value


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
    launch_id = raw.get("launch_id")
    return {
        "pid": pid,
        "port": port,
        "token": token if isinstance(token, str) else "",
        "launch_id": launch_id if isinstance(launch_id, str) else "",
    }


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
        try:
            exit_code = ctypes.c_ulong(0)
            if not ctypes.windll.kernel32.GetExitCodeProcess(
                handle, ctypes.byref(exit_code)
            ):
                return False
            return exit_code.value == 259  # STILL_ACTIVE
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
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
    launch_id: str | None = None,
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
        environment = utf8_python_environment()
        if launch_id:
            environment[LAUNCH_ID_ENV] = launch_id
        return subprocess.Popen(
            [sys.executable, str(app_path)],
            cwd=str(app_dir),
            stdin=subprocess.DEVNULL,
            stdout=startup_log,
            stderr=subprocess.STDOUT,
            shell=False,
            creationflags=creationflags,
            env=environment,
        )


def open_dashboard(
    dashboard_path: Path = DASHBOARD_PATH,
    app_dir: Path = APP_DIR,
    log_path: Path = DASHBOARD_LOG,
    runtime_path: Path = DASHBOARD_RUNTIME_PATH,
    timeout: float = 7.0,
) -> bool:
    """Return True only after a live dashboard HWND publishes a fresh heartbeat."""
    existing = read_dashboard_runtime(runtime_path)
    if existing is not None and process_is_alive(existing["pid"]):
        focus_dashboard_window(existing["hwnd"])
        return True
    try:
        rotate_startup_log(log_path)
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        with log_path.open("a", encoding="utf-8", buffering=1) as startup_log:
            process = subprocess.Popen(
                [sys.executable, str(dashboard_path)], cwd=str(app_dir),
                stdin=subprocess.DEVNULL, stdout=startup_log,
                stderr=subprocess.STDOUT, shell=False, creationflags=creationflags,
                env=utf8_python_environment(),
            )
            startup_log.write(f"dashboard launch PID: {process.pid}\n")
            startup_log.flush()
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                exit_code = process.poll()
                if exit_code is not None:
                    startup_log.write(f"dashboard exit code: {exit_code}\n")
                    startup_log.write("dashboard runtime verification: failed\n")
                    return False
                runtime = read_dashboard_runtime(runtime_path)
                if runtime is not None and runtime["pid"] == process.pid:
                    startup_log.write("dashboard runtime verification: success\n")
                    return True
                time.sleep(0.1)
            startup_log.write("dashboard runtime verification: failed (timeout)\n")
            try:
                process.terminate()
            except OSError:
                pass
            return False
    except Exception:
        log_launcher_error(log_path)
        return False


def read_dashboard_runtime(
    path: Path = DASHBOARD_RUNTIME_PATH,
    max_heartbeat_age: float = 4.0,
) -> dict | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        pid = raw["pid"]
        server_pid = raw["server_pid"]
        heartbeat_at = float(raw["heartbeat_at"])
        hwnd = raw["hwnd"]
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None
    if type(pid) is not int or pid <= 0 or type(server_pid) is not int or server_pid <= 0:
        return None
    if type(hwnd) is not int or hwnd <= 0:
        return None
    if time.time() - heartbeat_at > max_heartbeat_age:
        return None
    return {"pid": pid, "server_pid": server_pid, "heartbeat_at": heartbeat_at, "hwnd": hwnd}


def focus_dashboard_window(hwnd: int) -> None:
    if os.name != "nt" or hwnd <= 0:
        return
    ctypes.windll.user32.ShowWindow(hwnd, 9)
    ctypes.windll.user32.SetForegroundWindow(hwnd)


def wait_for_application(
    process: subprocess.Popen,
    runtime_path: Path = RUNTIME_PATH,
    timeout: float = STARTUP_TIMEOUT_SECONDS,
    expected_launch_id: str | None = None,
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        runtime = read_runtime(runtime_path)
        if (
            runtime is not None
            and expected_launch_id
            and runtime["launch_id"] == expected_launch_id
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
    launch_id: str | None = None,
) -> str:
    if running_instance(runtime_path) is not None:
        return "already_running"
    launch_id = launch_id or new_launch_id()
    try:
        process = start_application(app_path, app_dir, log_path, launch_id)
    except Exception:
        log_launcher_error(log_path)
        return "failed"
    if wait_for_application(process, runtime_path, timeout, launch_id):
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
    runtime = read_runtime(runtime_path)
    if runtime is not None and runtime.get("launch_id") == launch_id:
        cleanup_failed_launch(runtime, launch_id, runtime_path)
    elif process.poll() is None:
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


def request_shutdown_for_runtime(runtime: dict) -> bool:
    if not runtime.get("token"):
        return False
    request = urllib.request.Request(
        f"http://127.0.0.1:{runtime['port']}/api/system/shutdown",
        data=b"", method="POST",
        headers={"X-Control-Token": runtime["token"]},
    )
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def terminate_process_by_pid(pid: int) -> bool:
    if not process_is_alive(pid):
        return True
    if os.name == "nt":
        process_terminate = 0x0001
        handle = ctypes.windll.kernel32.OpenProcess(process_terminate, False, pid)
        if not handle:
            return False
        try:
            return bool(ctypes.windll.kernel32.TerminateProcess(handle, 1))
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 15)
        return True
    except OSError:
        return False


def cleanup_failed_launch(runtime: dict, expected_launch_id: str, runtime_path: Path) -> None:
    """Stop only the runtime carrying this launch's unguessable identity."""
    if runtime.get("launch_id") != expected_launch_id:
        return
    request_shutdown_for_runtime(runtime)
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and process_is_alive(runtime["pid"]):
        time.sleep(0.1)
    if process_is_alive(runtime["pid"]):
        terminate_process_by_pid(runtime["pid"])
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and process_is_alive(runtime["pid"]):
        time.sleep(0.05)
    current = read_runtime(runtime_path)
    if (
        not process_is_alive(runtime["pid"])
        and current is not None
        and current.get("launch_id") == expected_launch_id
    ):
        try:
            runtime_path.unlink()
        except OSError:
            pass


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
    if not request_shutdown_for_runtime(runtime):
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

    def dashboard_finish(ok: bool) -> None:
        dashboard_button.config(state="normal")
        if ok:
            status.config(text="ダッシュボードを開きました。")
        else:
            status.config(text="ダッシュボードを開けませんでした。\n診断ログを確認してください。")

    def begin_dashboard() -> None:
        dashboard_button.config(state="disabled")
        status.config(text="ダッシュボードを開いています...")
        threading.Thread(
            target=lambda: root.after(0, dashboard_finish, open_dashboard()),
            daemon=True,
        ).start()

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
        result = launch_once(launch_id=read_requested_launch_id())
        root.after(0, finish, result)

    root.after(100, lambda: threading.Thread(target=worker, daemon=True).start())
    root.mainloop()


if __name__ == "__main__":
    main()
