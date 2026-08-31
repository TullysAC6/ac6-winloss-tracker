from __future__ import annotations

import ctypes
import json
import os
import queue
import threading
import time
import traceback
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from app_paths import DISPLAY_NAME, data_dir


DATA_ROOT = data_dir()
RUNTIME_PATH = DATA_ROOT / ".runtime.json"
DASHBOARD_RUNTIME_PATH = DATA_ROOT / ".dashboard-runtime.json"
DASHBOARD_LOG_PATH = DATA_ROOT / "dashboard.log"
POLL_SECONDS = 1.0
HEARTBEAT_SECONDS = 1.5
STALE_HEARTBEAT_SECONDS = 5.0
MUTEX_NAME = "Local\\AC6WinLossTrackerDashboard"


def process_is_alive(pid: int) -> bool:
    if type(pid) is not int or pid <= 0:
        return False
    if os.name == "nt":
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def read_server_runtime(path: Path = RUNTIME_PATH) -> dict[str, Any] | None:
    raw = read_json(path)
    try:
        pid, port = int(raw["pid"]), int(raw["port"])
        token = raw.get("token", "")
    except (TypeError, KeyError, ValueError):
        return None
    if pid <= 0 or not 1 <= port <= 65535 or not isinstance(token, str):
        return None
    return {"pid": pid, "port": port, "token": token}


def write_runtime(
    server_pid: int, hwnd: int, path: Path = DASHBOARD_RUNTIME_PATH,
    durable: bool = False,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "pid": os.getpid(), "server_pid": int(server_pid),
        "started_at": getattr(write_runtime, "started_at", time.time()),
        "heartbeat_at": time.time(), "hwnd": int(hwnd),
    }
    write_runtime.started_at = payload["started_at"]
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.flush()
        if durable:
            os.fsync(stream.fileno())
    os.replace(temporary, path)


def remove_owned_runtime(path: Path = DASHBOARD_RUNTIME_PATH) -> None:
    raw = read_json(path)
    if raw is not None and raw.get("pid") == os.getpid():
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def existing_dashboard(path: Path = DASHBOARD_RUNTIME_PATH) -> dict[str, Any] | None:
    raw = read_json(path)
    try:
        pid = int(raw["pid"])
        heartbeat = float(raw["heartbeat_at"])
        hwnd = int(raw.get("hwnd", 0))
    except (TypeError, KeyError, ValueError):
        return None
    if process_is_alive(pid) and time.time() - heartbeat <= STALE_HEARTBEAT_SECONDS:
        return {"pid": pid, "hwnd": hwnd}
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    return None


def focus_existing_dashboard(runtime: dict[str, Any]) -> None:
    if os.name != "nt" or int(runtime.get("hwnd", 0)) <= 0:
        return
    hwnd = int(runtime["hwnd"])
    ctypes.windll.user32.ShowWindow(hwnd, 9)
    ctypes.windll.user32.SetForegroundWindow(hwnd)


def acquire_single_instance():
    if os.name != "nt":
        return None
    handle = ctypes.windll.kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if not handle:
        return None
    if ctypes.windll.kernel32.GetLastError() == 183:
        ctypes.windll.kernel32.CloseHandle(handle)
        return False
    return handle


def fetch_summary(port: int, timeout: float = 0.8) -> dict[str, Any]:
    with urllib.request.urlopen(
        f"http://127.0.0.1:{port}/api/dashboard/summary", timeout=timeout
    ) as response:
        if response.status != 200:
            raise OSError("dashboard API unavailable")
        payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, dict) or not payload.get("system"):
            raise ValueError("invalid dashboard response")
        return payload


def request_session_reset(runtime: dict[str, Any], timeout: float = 2.0) -> None:
    request = urllib.request.Request(
        f"http://127.0.0.1:{runtime['port']}/api/stats/reset",
        data=b"", method="POST",
        headers={"X-Control-Token": runtime["token"]},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if response.status != 200:
            raise OSError("session reset failed")


class OverviewFrame:
    def __init__(self, parent, ttk):
        self.frame = ttk.Frame(parent, padding=24)
        self.session_values = self._card(ttk, "CURRENT SESSION", 0, "STREAK")
        self.lifetime_values = self._card(ttk, "LIFETIME", 1, "MATCHES")

    def _card(self, ttk, title: str, column: int, fourth_label: str):
        card = ttk.Labelframe(self.frame, text=title, padding=22)
        card.grid(row=0, column=column, sticky="nsew", padx=(0, 12) if column == 0 else (12, 0))
        self.frame.columnconfigure(column, weight=1)
        names = ["WIN", "LOSE", "WIN RATE", fourth_label, "BEST STREAK"]
        values = {}
        for row, name in enumerate(names):
            ttk.Label(card, text=name, font=("Segoe UI", 10)).grid(row=row, column=0, sticky="w", pady=8)
            label = ttk.Label(card, text="—", font=("Segoe UI", 18, "bold"))
            label.grid(row=row, column=1, sticky="e", padx=(50, 0), pady=8)
            card.columnconfigure(1, weight=1)
            values[name] = label
        return values

    @staticmethod
    def _set(values, data, lifetime=False):
        values["WIN"].configure(text=str(data.get("wins", 0)), bootstyle="success")
        values["LOSE"].configure(text=str(data.get("losses", 0)), bootstyle="danger")
        values["WIN RATE"].configure(text=f"{float(data.get('win_rate', 0)):.1f}%")
        values["MATCHES" if lifetime else "STREAK"].configure(
            text=str(data.get("matches" if lifetime else "streak", 0))
        )
        values["BEST STREAK"].configure(text=str(data.get("best_streak", 0)))

    def update(self, payload):
        self._set(self.session_values, payload["session"])
        self._set(self.lifetime_values, payload["lifetime"], lifetime=True)


class HistoryFrame:
    def __init__(self, parent, ttk):
        self.frame = ttk.Frame(parent, padding=24)
        self.tree = ttk.Treeview(
            self.frame, columns=("time", "result", "streak"), show="headings", height=16
        )
        for key, label, width in (("time", "Time", 220), ("result", "Result", 180), ("streak", "Streak", 140)):
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, anchor="center")
        self.tree.pack(fill="both", expand=True)

    def update(self, matches):
        self.tree.delete(*self.tree.get_children())
        for match in matches:
            stamp = datetime.fromtimestamp(float(match["created_at"])).strftime("%Y-%m-%d %H:%M:%S")
            self.tree.insert("", "end", values=(stamp, str(match["result"]).upper(), match["streak_after"]))


class DashboardApp:
    def __init__(self, runtime: dict[str, Any]):
        import ttkbootstrap as ttk
        import tkinter.font as tkfont
        from ttkbootstrap.dialogs import Messagebox

        self.ttk = ttk
        self.Messagebox = Messagebox
        self.runtime = runtime
        self.root = ttk.Window(themename="darkly")
        self.root.title(f"{DISPLAY_NAME} - Dashboard")
        self.root.geometry("950x650")
        self.root.minsize(800, 540)
        # Configure Tk named fonts structurally.  A raw option database string
        # such as "Segoe UI 10" is tokenized by Tcl and treats "UI" as size.
        for font_name in ("TkDefaultFont", "TkTextFont"):
            tkfont.nametofont(font_name).configure(family="Segoe UI", size=10)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self._closing = False
        self._responses: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._poll_in_flight = False
        self._last_heartbeat_at = 0.0

        header = ttk.Frame(self.root, padding=(24, 18))
        header.pack(fill="x")
        ttk.Label(header, text="AC6 WIN/LOSS TRACKER", font=("Segoe UI", 18, "bold")).pack(side="left")
        self.connection = ttk.Label(header, text="● CONNECTING", bootstyle="warning")
        self.connection.pack(side="right")

        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True, padx=18)
        self.overview = OverviewFrame(notebook, ttk)
        self.history = HistoryFrame(notebook, ttk)
        notebook.add(self.overview.frame, text="概要")
        notebook.add(self.history.frame, text="履歴")

        footer = ttk.Frame(self.root, padding=(24, 16))
        footer.pack(fill="x")
        self.health_label = ttk.Label(footer, text="履歴を準備しています…")
        self.health_label.pack(side="left")
        ttk.Button(
            footer, text="セッションをリセット", bootstyle="secondary-outline",
            command=self.confirm_reset,
        ).pack(side="right")

    def hwnd(self) -> int:
        self.root.update_idletasks()
        return int(self.root.winfo_id())

    def start(self):
        write_runtime(self.runtime["pid"], self.hwnd(), durable=True)
        self._last_heartbeat_at = time.monotonic()
        self.root.after(100, self._tick)
        self.root.mainloop()

    def _worker(self):
        try:
            self._responses.put(("online", fetch_summary(self.runtime["port"])))
        except Exception:
            self._responses.put(("offline", None))

    def _tick(self):
        if self._closing:
            return
        current = read_server_runtime()
        if current is None or current["pid"] != self.runtime["pid"] or not process_is_alive(self.runtime["pid"]):
            self.close()
            return
        now = time.monotonic()
        if now - self._last_heartbeat_at >= HEARTBEAT_SECONDS:
            write_runtime(self.runtime["pid"], self.hwnd())
            self._last_heartbeat_at = now
        try:
            while True:
                state, payload = self._responses.get_nowait()
                self._poll_in_flight = False
                if state == "online":
                    self.connection.configure(text="● RUNNING", bootstyle="success")
                    self.overview.update(payload)
                    self.history.update(payload.get("recent_matches", []))
                    health = payload.get("history_health", {})
                    if health.get("status") == "active":
                        self.health_label.configure(text="履歴: 正常", bootstyle="secondary")
                    else:
                        self.health_label.configure(text="履歴の保存に問題があります", bootstyle="warning")
                else:
                    self.connection.configure(text="● OFFLINE", bootstyle="danger")
        except queue.Empty:
            pass
        if not self._poll_in_flight:
            self._poll_in_flight = True
            threading.Thread(target=self._worker, name="dashboard-poll", daemon=True).start()
        self.root.after(int(POLL_SECONDS * 1000), self._tick)

    def confirm_reset(self):
        answer = self.Messagebox.yesno(
            "現在のセッション戦績をリセットしますか？\n\n累計戦績と過去履歴は削除されません。",
            title="セッションをリセット",
            parent=self.root, alert=True,
            buttons=["キャンセル:secondary", "リセット:danger"],
        )
        if answer != "リセット":
            return
        threading.Thread(target=self._reset_worker, name="dashboard-reset", daemon=True).start()

    def _reset_worker(self):
        try:
            current = read_server_runtime()
            if current is None or current["pid"] != self.runtime["pid"]:
                raise OSError("server offline")
            request_session_reset(current)
            self._responses.put(("online", fetch_summary(current["port"])))
        except Exception:
            self._responses.put(("offline", None))

    def close(self):
        if self._closing:
            return
        self._closing = True
        remove_owned_runtime()
        self.root.destroy()


def main() -> int:
    runtime = read_server_runtime()
    if runtime is None or not process_is_alive(runtime["pid"]):
        return 2
    mutex = acquire_single_instance()
    if mutex is False:
        existing = existing_dashboard()
        if existing:
            focus_existing_dashboard(existing)
        return 0
    existing = existing_dashboard()
    if existing and existing["pid"] != os.getpid():
        focus_existing_dashboard(existing)
        return 0
    try:
        DashboardApp(runtime).start()
        return 0
    finally:
        remove_owned_runtime()
        if mutex not in (None, False):
            ctypes.windll.kernel32.CloseHandle(mutex)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        try:
            DASHBOARD_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with DASHBOARD_LOG_PATH.open("a", encoding="utf-8") as log:
                log.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] dashboard fatal error\n")
                traceback.print_exc(file=log)
        except Exception:
            pass
        raise
