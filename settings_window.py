"""Launcher-owned settings UI; no Tracker processes or control API mutations."""
import json
import os
import queue
import re
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path

import config_utils
from app_paths import DISPLAY_NAME, VERSION

LATEST_RELEASE_URL = "https://api.github.com/repos/TullysAC6/ac6-winloss-tracker/releases/latest"
_save_lock = threading.Lock()


def read_screenshot_setting():
    raw = json.loads(config_utils.CONFIG_PATH.read_text(encoding="utf-8"))
    return config_utils.validate_config(raw)["effect_screenshot_enabled"]


def save_screenshot_setting(enabled):
    if type(enabled) is not bool:
        raise ValueError("設定値はON/OFFで指定してください。")
    with _save_lock:
        path = config_utils.CONFIG_PATH
        original = path.read_bytes()
        raw = json.loads(original.decode("utf-8"))
        config_utils.validate_config(raw)  # Never overwrite an invalid/future config.
        raw["effect_screenshot_enabled"] = enabled
        config_utils.validate_config(raw)
        descriptor, name = tempfile.mkstemp(prefix=".config-", suffix=".tmp", dir=path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(raw, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            if path.read_bytes() != original:
                raise OSError("保存中に設定が変更されました。設定を開き直してください。")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def _version(value):
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", value)
    if not match:
        raise ValueError("バージョン番号を比較できませんでした。")
    return tuple(int(part) for part in match.groups())


def check_latest_release(current=VERSION):
    """Fetch release metadata only. No assets, browser launch, or installation."""
    request = urllib.request.Request(LATEST_RELEASE_URL, headers={
        "Accept": "application/vnd.github+json", "User-Agent": "AC6-WinLoss-Tracker",
    })
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            body = response.read(1024 * 1024 + 1)
        if len(body) > 1024 * 1024:
            raise ValueError("Release情報が大きすぎます。")
        release = json.loads(body)
        if not isinstance(release, dict) or release.get("draft") or release.get("prerelease"):
            raise ValueError("正式Release情報を確認できませんでした。")
        latest = release.get("tag_name")
        if not isinstance(latest, str):
            raise ValueError("Releaseにバージョン番号がありません。")
        available, installed = _version(latest), _version(current)
        if available > installed:
            return f"新しいバージョン {latest} があります（現在 {current}）。"
        if available == installed:
            return f"最新の公開バージョンです（{current}）。"
        return f"現在 {current} は最新の公開Release {latest} より新しいバージョンです。"
    except urllib.error.HTTPError as error:
        error.close()
        if error.code == 404:
            return "公開Releaseが見つかりませんでした。"
        if error.code in (403, 429):
            return "GitHubの利用制限などにより確認できません。時間をおいてお試しください。"
        return f"更新を確認できませんでした（HTTP {error.code}）。"
    except (OSError, ValueError, TypeError) as error:
        return f"更新を確認できませんでした: {error}"


class SettingsWindow:
    def __init__(self, parent):
        import tkinter as tk
        from tkinter import ttk

        self.window = tk.Toplevel(parent)
        self.window.withdraw()
        self.window.title(f"設定 — {DISPLAY_NAME}")
        self.window.resizable(False, False)
        frame = ttk.Frame(self.window, padding=20)
        frame.pack(fill="both", expand=True)
        self.enabled = tk.BooleanVar(master=self.window)
        self.toggle = ttk.Checkbutton(frame, text="連勝演出時のスクリーンショットを保存する",
                                      variable=self.enabled)
        self.toggle.pack(anchor="w")
        self.save_button = ttk.Button(frame, text="保存", command=self.save)
        self.save_button.pack(anchor="e", pady=(12, 0))
        self.status = ttk.Label(frame, text="", wraplength=420)
        self.status.pack(anchor="w", pady=(8, 16))
        ttk.Separator(frame).pack(fill="x")
        ttk.Label(frame, text=f"現在のバージョン: {VERSION}").pack(anchor="w", pady=(12, 8))
        self.check_button = ttk.Button(frame, text="新しいバージョンを調べる", command=self.check)
        self.check_button.pack(anchor="w")
        self.update_status = ttk.Label(frame, text="確認のみ行います。自動更新はしません。", wraplength=420)
        self.update_status.pack(anchor="w", pady=(8, 12))
        ttk.Button(frame, text="閉じる", command=self.window.withdraw).pack(anchor="e")
        self.window.protocol("WM_DELETE_WINDOW", self.window.withdraw)
        self.window.bind("<Destroy>", self._destroyed)
        self.results = queue.Queue(maxsize=1)
        self.checking = False
        self.poll_id = None

    def show(self):
        if self.window.state() == "withdrawn":
            try:
                self.enabled.set(read_screenshot_setting())
                self.toggle.config(state="normal")
                self.save_button.config(state="normal")
                self.status.config(text="保存すると、Trackerの再起動なしで反映されます。")
            except (OSError, ValueError) as error:
                self.toggle.config(state="disabled")
                self.save_button.config(state="disabled")
                self.status.config(text=f"設定を読み込めませんでした: {error}")
        self.window.deiconify()
        self.window.lift()

    def save(self):
        try:
            save_screenshot_setting(self.enabled.get())
            self.status.config(text="保存しました。Trackerの再起動は不要です。")
        except (OSError, ValueError) as error:
            self.status.config(text=f"保存できませんでした: {error}")

    def check(self):
        if self.checking:
            return
        self.checking = True
        self.check_button.config(state="disabled")
        self.update_status.config(text="GitHubの公開Releaseを確認しています...")
        # The worker never touches Tk; closing Launcher need not wait for I/O.
        try:
            threading.Thread(target=lambda: self.results.put(check_latest_release()),
                             name="release-check", daemon=True).start()
        except (OSError, RuntimeError):
            self.checking = False
            self.check_button.config(state="normal")
            self.update_status.config(text="更新確認を開始できませんでした。もう一度お試しください。")
            return
        self.poll_id = self.window.after(100, self._poll)

    def _poll(self):
        self.poll_id = None
        try:
            message = self.results.get_nowait()
        except queue.Empty:
            self.poll_id = self.window.after(100, self._poll)
            return
        self.checking = False
        self.check_button.config(state="normal")
        self.update_status.config(text=message)

    def _destroyed(self, event):
        if event.widget is self.window and self.poll_id is not None:
            self.window.after_cancel(self.poll_id)
            self.poll_id = None


def open_settings(parent):
    window = getattr(parent, "_tracker_settings", None)
    if window is None or not window.window.winfo_exists():
        window = parent._tracker_settings = SettingsWindow(parent)
    window.show()
    return window
