"""Launcher-owned settings UI; no Tracker processes are started or stopped.

Everything here runs inside the existing Launcher process. Read-only work
(analytics, CSV export, integrity checks) goes straight to history.db through
``history_analytics`` on a read-only connection. Destructive history
maintenance is delegated to the already-running Tracker over its existing
localhost control API, so server.py stays the only writer and no second
server, detector or overlay is ever spawned.
"""
import json
import os
import queue
import re
import tempfile
import threading
import unicodedata
import urllib.error
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path

import config_utils
import history_analytics
from app_paths import DISPLAY_NAME, VERSION

LATEST_RELEASE_URL = "https://api.github.com/repos/TullysAC6/ac6-winloss-tracker/releases/latest"
RELEASES_PAGE_URL = "https://github.com/TullysAC6/ac6-winloss-tracker/releases/latest"
RUNTIME_NAME = ".runtime.json"
PURGE_ENDPOINT = "/api/history/purge"
DIAGNOSTICS_FLUSH_ENDPOINT = "/api/diagnostics/flush"
CONTROL_TIMEOUT_SECONDS = 20.0
EDITABLE_KEYS = ("effect_enabled", "effect_screenshot_enabled", "overlay_stats_scope")

DIAGNOSTIC_STEPS = (
    "1. 問題が起きてもTrackerを終了・再起動しない\n"
    "2. この画面で「Diagnostic Reportを作成」を実行する\n"
    "3. 作成されたZIPをそのまま保存する\n"
    "4. 開発者から依頼された場合に提出する"
)
DIAGNOSTIC_PRIVACY = (
    "ZIPに入るもの:\n"
    "・勝敗検出のテレメトリログ（detector.jsonl / detector.previous.jsonl / frame-buffer.jsonl）\n"
    "・スクリーンショットの保存結果ログ（effect-screenshot.jsonl）\n"
    "・config.json / stats.json / installed-version.json（インストール方法の確認用）\n"
    "・起動ログ startup.log（ローカルのファイルパスとPythonエラーを含みます）\n"
    "・環境情報 manifest.json（アプリ版・OS・Python・依存パッケージの有無とバージョン・"
    "SQLite版・履歴スキーマ版・画面解像度/DPI/モニタ数）\n"
    "・勝敗判定に使う画面の一部（ROI）のPNG画像\n"
    "含まれないもの: フルスクリーン画像、勝敗履歴データベース(history.db)"
)

_save_lock = threading.Lock()


class TrackerUnavailable(RuntimeError):
    """The running Tracker could not be reached over its control API."""


def data_root() -> Path:
    """User-data root, derived from CONFIG_PATH so tests redirect one place."""
    return config_utils.CONFIG_PATH.parent


def read_settings():
    raw = json.loads(config_utils.CONFIG_PATH.read_text(encoding="utf-8"))
    valid = config_utils.validate_config(raw)
    return {key: valid[key] for key in EDITABLE_KEYS}


def _checked(values):
    if not isinstance(values, dict) or not values:
        raise ValueError("保存する設定がありません。")
    unknown = set(values) - set(EDITABLE_KEYS)
    if unknown:
        raise ValueError("この画面では変更できない設定です: " + ", ".join(sorted(unknown)))
    for key, value in values.items():
        if key == "overlay_stats_scope":
            if value not in config_utils.OVERLAY_STATS_SCOPES:
                raise ValueError("表示モードはセッション/累計で指定してください。")
        elif type(value) is not bool:
            raise ValueError("設定値はON/OFFで指定してください。")
    return dict(values)


def save_settings(values):
    """Atomically merge settings into config.json without losing other keys."""
    values = _checked(values)
    with _save_lock:
        path = config_utils.CONFIG_PATH
        original = path.read_bytes()
        raw = json.loads(original.decode("utf-8"))
        config_utils.validate_config(raw)  # Never overwrite an invalid/future config.
        raw.update(values)
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


def read_screenshot_setting():
    return read_settings()["effect_screenshot_enabled"]


def save_screenshot_setting(enabled):
    if type(enabled) is not bool:
        raise ValueError("設定値はON/OFFで指定してください。")
    save_settings({"effect_screenshot_enabled": enabled})


def read_runtime():
    """Locate the running Tracker's control endpoint, or raise."""
    path = data_root() / RUNTIME_NAME
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        port, token = raw["port"], raw["token"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        raise TrackerUnavailable(
            "Trackerが起動していないため実行できません。Trackerを起動してからお試しください。"
        ) from error
    if type(port) is not int or not 1024 <= port <= 65535:
        raise TrackerUnavailable("Trackerの接続情報が不正です。")
    if not isinstance(token, str) or len(token) < 32:
        raise TrackerUnavailable("Trackerの接続情報が不正です。")
    return port, token


def control_request(endpoint, payload, timeout=CONTROL_TIMEOUT_SECONDS):
    """POST to the running Tracker. Never starts or stops any process."""
    port, token = read_runtime()
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{endpoint}", data=body, method="POST",
        headers={"X-Control-Token": token, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read(1024 * 1024).decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read(4096).decode("utf-8", errors="replace")
        error.close()
        try:  # Prefer the Tracker's own explanation over the raw JSON body.
            detail = json.loads(detail).get("error") or detail
        except (ValueError, TypeError, AttributeError):
            pass
        raise TrackerUnavailable(
            f"Trackerが要求を拒否しました（HTTP {error.code}）: {detail}"
        ) from error
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise TrackerUnavailable(f"Trackerと通信できませんでした: {error}") from error


def purge_all_history():
    return control_request(PURGE_ENDPOINT, {"mode": "all"})


def purge_history_before(cutoff):
    return control_request(PURGE_ENDPOINT, {"mode": "before", "cutoff": float(cutoff)})


def cutoff_for_date(text, now=None):
    """Local midnight of ``YYYY-MM-DD``. Matches before that instant are removed.

    Today is the newest accepted date. A future date would delete matches the
    live session still counts in stats.json; server.py refuses the same range.
    """
    try:
        day = datetime.strptime(str(text).strip(), "%Y-%m-%d")
    except (TypeError, ValueError) as error:
        raise ValueError("日付は YYYY-MM-DD の形式で入力してください。") from error
    cutoff = day.timestamp()
    limit = history_analytics.latest_allowed_cutoff(now)
    if cutoff > limit:
        raise ValueError(
            "指定できるのは今日までです（今日: "
            f"{datetime.fromtimestamp(limit).strftime('%Y-%m-%d')}）。"
        )
    return cutoff


def flush_live_diagnostics():
    """Ask a running Tracker to persist its in-memory detector telemetry.

    Best effort: the report is still produced when no Tracker is running.
    """
    try:
        return control_request(DIAGNOSTICS_FLUSH_ENDPOINT, {}, timeout=10.0)
    except TrackerUnavailable:
        return None


def create_diagnostic_report():
    """Reuse the exporter behind Create-Diagnostic-Report.bat.

    ``diagnostics.RECORDER.export()`` is the same single source that
    ``app.py --diagnostics`` calls. It only reads diagnostics files that have
    already been written, so no server, detector or overlay process starts.
    The running Tracker is asked to write out its recent-frame ring first,
    because that buffer only exists inside the Tracker process.
    """
    from diagnostics import RECORDER
    flush_live_diagnostics()
    return Path(RECORDER.export())


def open_folder(path):
    folder = Path(path)
    folder = folder if folder.is_dir() else folder.parent
    if os.name == "nt":
        os.startfile(str(folder))
        return True
    return webbrowser.open(folder.as_uri(), new=2)


def open_releases_page():
    """Open the official Releases page. No download, no self-replacement."""
    return webbrowser.open(RELEASES_PAGE_URL, new=2)


def _version(value):
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", value)
    if not match:
        raise ValueError("バージョン番号を比較できませんでした。")
    return tuple(int(part) for part in match.groups())


def latest_release_status(current=VERSION):
    """Fetch release metadata only. No assets, browser launch, or installation."""
    request = urllib.request.Request(LATEST_RELEASE_URL, headers={
        "Accept": "application/vnd.github+json", "User-Agent": "AC6-WinLoss-Tracker",
    })
    result = {"ok": False, "update_available": False, "current": current, "latest": None}
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
        result.update(ok=True, latest=latest)
        if available > installed:
            result.update(update_available=True,
                          message=f"新しいバージョン {latest} があります（現在 {current}）。")
        elif available == installed:
            result["message"] = f"最新の公開バージョンです（{current}）。"
        else:
            result["message"] = (
                f"現在 {current} は最新の公開Release {latest} より新しいバージョンです。"
            )
        return result
    except urllib.error.HTTPError as error:
        error.close()
        if error.code == 404:
            result["message"] = "公開Releaseが見つかりませんでした。"
        elif error.code in (403, 429):
            result["message"] = "GitHubの利用制限などにより確認できません。時間をおいてお試しください。"
        else:
            result["message"] = f"更新を確認できませんでした（HTTP {error.code}）。"
        return result
    except (OSError, ValueError, TypeError) as error:
        result["message"] = f"更新を確認できませんでした: {error}"
        return result


def check_latest_release(current=VERSION):
    return latest_release_status(current)["message"]


def cell_width(text):
    """Monospace columns must count full-width Japanese glyphs as two cells."""
    return sum(2 if unicodedata.east_asian_width(character) in "WF" else 1
               for character in str(text))


def _row(cells, widths):
    parts = []
    for index, (cell, width) in enumerate(zip(cells, widths)):
        pad = " " * max(0, width - cell_width(cell))
        parts.append(f"{cell}{pad}" if index == 0 else f"{pad}{cell}")
    return "".join(parts)


PERIOD_WIDTHS = (18, 6, 7, 7, 7, 9, 10)
RECENT_WIDTHS = (18, 6, 7, 7, 7, 9)


def format_summary(summary):
    """Human-readable analytics report for the settings window."""
    lines = [
        f"履歴DB: {summary['database']}",
        f"記録済み試合数: {summary['total_matches']}",
    ]
    if summary["first_match_at"] is not None:
        lines.append(
            f"記録期間: {history_analytics.format_local(summary['first_match_at'])}"
            f" 〜 {history_analytics.format_local(summary['last_match_at'])}"
        )
    lines.append("")
    lines.append("■ 期間別（日付境界はWindowsのローカル時刻、週は月曜開始）")
    lines.append(_row(("期間", "WIN", "LOSE", "DRAW", "試合", "勝率", "最高連勝"),
                      PERIOD_WIDTHS))
    for entry in summary["periods"]:
        lines.append(_row((entry["label"], entry["wins"], entry["losses"], entry["draws"],
                           entry["matches"], f"{entry['win_rate']:.1f}%", entry["best_streak"]),
                          PERIOD_WIDTHS))
    lines.append("")
    for entry in summary["periods"]:
        lines.append(f"  {entry['label']}: 開始 {entry['start_text']} / 対象 {entry['matches']} 試合")
    lines.append("")
    lines.append("■ 直近成績（履歴が足りない場合は存在する分だけで計算）")
    lines.append(_row(("対象", "WIN", "LOSE", "DRAW", "件数", "勝率"), RECENT_WIDTHS))
    for entry in summary["recent"]:
        lines.append(_row((f"直近{entry['size']}戦", entry["wins"], entry["losses"],
                           entry["draws"], entry["available"], f"{entry['win_rate']:.1f}%"),
                          RECENT_WIDTHS))
    lines.append("")
    lines.append("勝率 = WIN ÷ (WIN + LOSE) × 100")
    lines.append("Trackerの既存定義どおり、DRAWは勝率の分母に含みません。")
    return "\n".join(lines)


def format_integrity(result):
    lines = [
        f"判定: {'正常' if result['ok'] else '異常'}（PRAGMA {result['pragma']}）",
        f"DB: {result['database']}",
        f"サイズ: {result['size_bytes']:,} バイト / スキーマ版 {result['schema_version']}",
    ]
    if not result["ok"]:
        lines.append("検出内容: " + " / ".join(result["messages"][:5]))
        lines.append(
            "このツールは修復も書き換えも行いません。上記のDBパスを控え、"
            "「Diagnostic Reportを作成」を実行して開発者に報告してください。"
        )
    return "\n".join(lines)


class SettingsWindow:
    def __init__(self, parent):
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk

        self._filedialog = filedialog
        self._messagebox = messagebox
        self.window = tk.Toplevel(parent)
        self.window.withdraw()
        self.window.title(f"設定 — {DISPLAY_NAME}")
        self.window.resizable(False, False)
        notebook = ttk.Notebook(self.window)
        notebook.pack(fill="both", expand=True, padx=12, pady=12)

        self.enabled = tk.BooleanVar(master=self.window)
        self.effect_enabled = tk.BooleanVar(master=self.window)
        self.scope = tk.StringVar(master=self.window, value="session")
        self.cutoff_date = tk.StringVar(master=self.window)

        self._build_display_tab(ttk, notebook)
        self._build_analytics_tab(tk, ttk, notebook)
        self._build_maintenance_tab(ttk, notebook)
        self._build_support_tab(ttk, notebook)

        self.window.protocol("WM_DELETE_WINDOW", self.window.withdraw)
        self.window.bind("<Destroy>", self._destroyed)
        self.results = queue.Queue(maxsize=1)
        self.checking = False
        self.poll_id = None
        self._tasks = queue.Queue()
        self._task_poll_id = None
        self._busy = set()
        self._latest_report = None
        self._task_handlers = {
            "analytics": self._analytics_done,
            "export": self._export_done,
            "integrity": self._integrity_done,
            "purge_preview": self._purge_preview_done,
            "purge": self._purge_done,
            "diagnostics": self._diagnostics_done,
        }

    # ----------------------------------------------------------------- layout

    def _build_display_tab(self, ttk, notebook):
        frame = ttk.Frame(notebook, padding=16)
        notebook.add(frame, text="表示・演出")
        ttk.Label(frame, text="連勝演出", font=("Segoe UI", 9, "bold")).pack(anchor="w")
        ttk.Checkbutton(frame, text="連勝演出（5/10/…連勝バナー）を表示する",
                        variable=self.effect_enabled).pack(anchor="w", pady=(2, 0))
        self.toggle = ttk.Checkbutton(frame, text="連勝演出時のスクリーンショットを保存する",
                                      variable=self.enabled)
        self.toggle.pack(anchor="w")
        ttk.Label(frame, text="演出をOFFにしても勝敗のカウントには影響しません。",
                  wraplength=440).pack(anchor="w", pady=(2, 12))
        ttk.Label(frame, text="オーバーレイの成績表示",
                  font=("Segoe UI", 9, "bold")).pack(anchor="w")
        ttk.Radiobutton(frame, text="現在のセッション成績を表示", value="session",
                        variable=self.scope).pack(anchor="w", pady=(2, 0))
        ttk.Radiobutton(frame, text="累計成績（全履歴）を表示", value="lifetime",
                        variable=self.scope).pack(anchor="w")
        ttk.Label(frame, text="連勝数と演出は、どちらを選んでも現在のセッション基準のままです。",
                  wraplength=440).pack(anchor="w", pady=(2, 12))
        self.save_button = ttk.Button(frame, text="保存", command=self.save)
        self.save_button.pack(anchor="e")
        self.status = ttk.Label(frame, text="", wraplength=440)
        self.status.pack(anchor="w", pady=(8, 0))

    def _build_analytics_tab(self, tk, ttk, notebook):
        frame = ttk.Frame(notebook, padding=16)
        notebook.add(frame, text="成績")
        row = ttk.Frame(frame)
        row.pack(fill="x")
        self.analytics_button = ttk.Button(row, text="成績を集計", command=self.refresh_analytics)
        self.analytics_button.pack(side="left")
        self.export_button = ttk.Button(row, text="CSVエクスポート", command=self.export_csv)
        self.export_button.pack(side="left", padx=(8, 0))
        body = ttk.Frame(frame)
        body.pack(fill="both", expand=True, pady=(10, 0))
        self.analytics_text = tk.Text(body, height=18, width=66, wrap="none",
                                      font=("Consolas", 9), state="disabled")
        vertical = ttk.Scrollbar(body, orient="vertical", command=self.analytics_text.yview)
        horizontal = ttk.Scrollbar(body, orient="horizontal", command=self.analytics_text.xview)
        self.analytics_text.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.analytics_text.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)
        self.analytics_status = ttk.Label(frame, text="", wraplength=440, justify="left")
        self.analytics_status.pack(anchor="w", pady=(8, 0))

    def _build_maintenance_tab(self, ttk, notebook):
        frame = ttk.Frame(notebook, padding=16)
        notebook.add(frame, text="メンテナンス")
        ttk.Label(frame, text="データベース整合性",
                  font=("Segoe UI", 9, "bold")).pack(anchor="w")
        row = ttk.Frame(frame)
        row.pack(anchor="w", pady=(2, 0))
        self.integrity_button = ttk.Button(row, text="DB整合性を確認",
                                           command=lambda: self.check_integrity(False))
        self.integrity_button.pack(side="left")
        self.integrity_full_button = ttk.Button(row, text="詳細検査",
                                                command=lambda: self.check_integrity(True))
        self.integrity_full_button.pack(side="left", padx=(8, 0))
        ttk.Label(frame, text="読み取り専用で検査します。DBの修復や書き換えは行わず、"
                              "勝敗の自動検出も停止しません。",
                  wraplength=440).pack(anchor="w", pady=(2, 0))
        self.integrity_status = ttk.Label(frame, text="", wraplength=440, justify="left")
        self.integrity_status.pack(anchor="w", pady=(6, 14))

        ttk.Separator(frame).pack(fill="x")
        ttk.Label(frame, text="履歴の削除（取り消せません）",
                  font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(12, 0))
        self.purge_all_button = ttk.Button(frame, text="全勝敗履歴をリセット",
                                           command=self.confirm_purge_all)
        self.purge_all_button.pack(anchor="w", pady=(4, 0))
        ttk.Label(frame, text="全試合の履歴と現在のセッション成績を消去します。"
                              "設定と診断ログは消えません。",
                  wraplength=440).pack(anchor="w")
        row = ttk.Frame(frame)
        row.pack(anchor="w", pady=(10, 0))
        ttk.Label(row, text="この日より前を削除:").pack(side="left")
        self.cutoff_entry = ttk.Entry(row, textvariable=self.cutoff_date, width=12)
        self.cutoff_entry.pack(side="left", padx=(6, 6))
        self.purge_before_button = ttk.Button(row, text="削除", command=self.confirm_purge_before)
        self.purge_before_button.pack(side="left")
        ttk.Label(frame, text="YYYY-MM-DD 形式。指定できるのは今日までです。指定日の 00:00"
                              "（お使いのPCのローカル時刻）より前の試合を削除し、"
                              "指定日当日の試合は残します。",
                  wraplength=440).pack(anchor="w", pady=(2, 0))
        self.purge_status = ttk.Label(frame, text="", wraplength=440, justify="left")
        self.purge_status.pack(anchor="w", pady=(8, 0))

    def _build_support_tab(self, ttk, notebook):
        frame = ttk.Frame(notebook, padding=16)
        notebook.add(frame, text="サポート")
        ttk.Label(frame, text=f"現在のバージョン: {VERSION}").pack(anchor="w")
        row = ttk.Frame(frame)
        row.pack(anchor="w", pady=(6, 0))
        self.check_button = ttk.Button(row, text="新しいバージョンを調べる", command=self.check)
        self.check_button.pack(side="left")
        self.install_button = ttk.Button(row, text="新しいバージョンをインストール",
                                         command=self.open_release_page, state="disabled")
        self.install_button.pack(side="left", padx=(8, 0))
        self.update_status = ttk.Label(frame, text="確認のみ行います。自動更新はしません。",
                                       wraplength=440)
        self.update_status.pack(anchor="w", pady=(6, 0))
        ttk.Label(frame, text="更新は自動ダウンロードせず、GitHubの公開Releaseページを開くだけです。"
                              "現在と同じ正式インストーラー（install.ps1）を使って更新してください。",
                  wraplength=440).pack(anchor="w", pady=(2, 12))

        ttk.Separator(frame).pack(fill="x")
        ttk.Label(frame, text="Diagnostic Report",
                  font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(12, 0))
        ttk.Label(frame, text="勝敗が認識されない、演出やスクリーンショットが動作しないなど、"
                              "不具合報告時に使用する診断ZIPを作成します。",
                  wraplength=440).pack(anchor="w", pady=(2, 0))
        ttk.Label(frame, text="問題が発生した直後、できればTrackerを終了・再起動する前に"
                              "作成してください。",
                  wraplength=440).pack(anchor="w", pady=(2, 6))
        row = ttk.Frame(frame)
        row.pack(anchor="w")
        self.diagnostics_button = ttk.Button(row, text="Diagnostic Reportを作成",
                                             command=self.create_report)
        self.diagnostics_button.pack(side="left")
        self.open_report_button = ttk.Button(row, text="保存先を開く",
                                             command=self.open_report_folder, state="disabled")
        self.open_report_button.pack(side="left", padx=(8, 0))
        self.diagnostics_status = ttk.Label(frame, text="", wraplength=440, justify="left")
        self.diagnostics_status.pack(anchor="w", pady=(6, 6))
        ttk.Label(frame, text=DIAGNOSTIC_STEPS, justify="left",
                  wraplength=440).pack(anchor="w")
        ttk.Label(frame, text=DIAGNOSTIC_PRIVACY, justify="left",
                  wraplength=440).pack(anchor="w", pady=(6, 0))

    # ------------------------------------------------------------- async work

    def _start_task(self, name, work):
        """Run blocking work off the Tk thread; one job per name at a time."""
        if name in self._busy:
            return False
        self._busy.add(name)

        def runner():
            try:
                self._tasks.put((name, True, work()))
            except Exception as error:  # Surfaced in the UI, never raised into Tk.
                self._tasks.put((name, False, error))

        try:
            threading.Thread(target=runner, name=f"settings-{name}", daemon=True).start()
        except (OSError, RuntimeError):
            self._busy.discard(name)
            return False
        if self._task_poll_id is None:
            self._task_poll_id = self.window.after(100, self._poll_tasks)
        return True

    def _poll_tasks(self):
        self._task_poll_id = None
        try:
            while True:
                name, ok, payload = self._tasks.get_nowait()
                self._busy.discard(name)
                self._task_handlers[name](ok, payload)
        except queue.Empty:
            pass
        if self._busy:
            self._task_poll_id = self.window.after(100, self._poll_tasks)

    # --------------------------------------------------------------- settings

    def show(self):
        if self.window.state() == "withdrawn":
            try:
                values = read_settings()
                self.enabled.set(values["effect_screenshot_enabled"])
                self.effect_enabled.set(values["effect_enabled"])
                self.scope.set(values["overlay_stats_scope"])
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
            save_settings({
                "effect_screenshot_enabled": self.enabled.get(),
                "effect_enabled": self.effect_enabled.get(),
                "overlay_stats_scope": self.scope.get(),
            })
            self.status.config(
                text="保存しました。Trackerの再起動は不要です。試合中でも安全に反映されます。"
            )
        except (OSError, ValueError) as error:
            self.status.config(text=f"保存できませんでした: {error}")

    # -------------------------------------------------------------- analytics

    def refresh_analytics(self):
        root = data_root()
        started = self._start_task("analytics", lambda: history_analytics.summarize(root))
        self.analytics_status.config(
            text="集計しています..." if started else "集計中です。完了までお待ちください。")

    def _analytics_done(self, ok, payload):
        if ok:
            self._set_analytics_text(format_summary(payload))
            self.analytics_status.config(text=f"集計しました（{payload['total_matches']} 試合）。")
        else:
            self.analytics_status.config(text=f"集計できませんでした: {payload}")

    def _set_analytics_text(self, text):
        self.analytics_text.config(state="normal")
        self.analytics_text.delete("1.0", "end")
        self.analytics_text.insert("1.0", text)
        self.analytics_text.config(state="disabled")

    def export_csv(self):
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        destination = self._filedialog.asksaveasfilename(
            parent=self.window, title="CSVの保存先", defaultextension=".csv",
            initialfile=f"AC6-history-{stamp}.csv",
            filetypes=[("CSV (UTF-8 BOM)", "*.csv"), ("すべてのファイル", "*.*")],
        )
        if not destination:
            self.analytics_status.config(text="CSVエクスポートを中止しました。")
            return
        root = data_root()
        started = self._start_task(
            "export", lambda: history_analytics.export_csv(root, destination))
        self.analytics_status.config(
            text="CSVを書き出しています..." if started else "書き出し中です。完了までお待ちください。")

    def _export_done(self, ok, payload):
        if ok:
            self.analytics_status.config(
                text=f"{payload['rows']} 件を書き出しました（{payload['encoding']}、"
                     f"並び順: {payload['order']}）。\n{payload['path']}"
            )
        else:
            self.analytics_status.config(text=f"CSVを書き出せませんでした: {payload}")

    # ------------------------------------------------------------ maintenance

    def check_integrity(self, thorough):
        root = data_root()
        started = self._start_task(
            "integrity", lambda: history_analytics.integrity_check(root, thorough))
        self.integrity_status.config(
            text="検査しています..." if started else "検査中です。完了までお待ちください。")

    def _integrity_done(self, ok, payload):
        if ok:
            self.integrity_status.config(text=format_integrity(payload))
        else:
            self.integrity_status.config(
                text=f"検査できませんでした: {payload}\n"
                     f"DB: {history_analytics.database_path(data_root())}\n"
                     "「Diagnostic Reportを作成」で診断ZIPを作成して報告してください。"
            )

    def confirm_purge_all(self):
        root = data_root()
        started = self._start_task(
            "purge_preview", lambda: ("all", history_analytics.summarize(root)))
        self.purge_status.config(
            text="削除対象を確認しています..." if started else "処理中です。完了までお待ちください。")

    def confirm_purge_before(self):
        try:
            cutoff = cutoff_for_date(self.cutoff_date.get())
        except ValueError as error:
            self.purge_status.config(text=str(error))
            return
        root = data_root()
        started = self._start_task(
            "purge_preview", lambda: ("before", history_analytics.count_before(root, cutoff)))
        self.purge_status.config(
            text="削除対象を確認しています..." if started else "処理中です。完了までお待ちください。")

    def _purge_preview_done(self, ok, payload):
        if not ok:
            self.purge_status.config(text=f"削除対象を確認できませんでした: {payload}")
            return
        mode, preview = payload
        if mode == "all":
            if preview["total_matches"] == 0:
                self.purge_status.config(text="削除できる履歴がありません。")
                return
            question = (
                f"全勝敗履歴 {preview['total_matches']} 件と、現在のセッション成績を削除します。\n"
                "この操作は取り消せません。実行しますか？"
            )
            work = purge_all_history
        else:
            if preview["removable"] == 0:
                self.purge_status.config(
                    text=f"{preview['cutoff_text']} より前の履歴はありません"
                         f"（全 {preview['total']} 件）。"
                )
                return
            # server.py refuses the same range; stop here so the confirmation
            # dialog never offers an operation the Tracker will reject.
            if preview.get("active_session_removable", 0):
                self.purge_status.config(
                    text=f"{history_analytics.ACTIVE_SESSION_PURGE_MESSAGE}\n"
                         f"（対象 {preview['active_session_removable']} 件）"
                )
                return
            question = (
                f"{preview['cutoff_text']} より前の履歴 {preview['removable']} 件を削除します。\n"
                f"指定日当日を含む {preview['kept']} 件は残ります。\n"
                "この操作は取り消せません。実行しますか？"
            )
            cutoff = preview["cutoff"]

            def work():
                return purge_history_before(cutoff)

        if not self._messagebox.askyesno(
            f"履歴の削除 — {DISPLAY_NAME}", question,
            icon="warning", default="no", parent=self.window,
        ):
            self.purge_status.config(text="削除を中止しました。履歴は変更していません。")
            return
        started = self._start_task("purge", work)
        self.purge_status.config(
            text="Trackerに削除を依頼しています..." if started
            else "処理中です。完了までお待ちください。")

    def _purge_done(self, ok, payload):
        if not ok:
            self.purge_status.config(text=f"削除できませんでした: {payload}")
            return
        lines = [f"履歴 {payload.get('removed_matches', 0)} 件を削除しました。"]
        if payload.get("removed_sessions"):
            lines.append(f"空になったセッション {payload['removed_sessions']} 件も削除しました。")
        if payload.get("mode") == "all":
            # A reported success now always means both stores were cleared
            # together. A partial purge comes back as an error instead, and the
            # message there says which state the Tracker was left in.
            lines.append("セッション成績もリセットしました。")
        self.purge_status.config(text="\n".join(lines))

    # ---------------------------------------------------------------- support

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
        self.install_button.config(
            state="normal" if message.startswith("新しいバージョン ") else "disabled"
        )

    def open_release_page(self):
        opened = open_releases_page()
        self.update_status.config(
            text="GitHubの公開Releaseページを開きました。"
                 "現在と同じ正式インストーラーで更新してください。"
            if opened else
            "ブラウザを開けませんでした。次のURLを開いてください:\n" + RELEASES_PAGE_URL
        )

    def create_report(self):
        started = self._start_task("diagnostics", create_diagnostic_report)
        self.diagnostics_status.config(
            text="診断ZIPを作成しています..." if started else "作成中です。完了までお待ちください。")

    def _diagnostics_done(self, ok, payload):
        if ok:
            self._latest_report = Path(payload)
            self.open_report_button.config(state="normal")
            self.diagnostics_status.config(
                text=f"作成しました。\nファイル名: {self._latest_report.name}\n"
                     f"保存場所: {self._latest_report.parent}"
            )
        else:
            self.diagnostics_status.config(
                text=f"作成できませんでした: {payload}\nTrackerは動作したままです。"
            )

    def open_report_folder(self):
        if self._latest_report is None:
            return
        try:
            open_folder(self._latest_report)
        except OSError as error:
            self.diagnostics_status.config(text=f"保存先を開けませんでした: {error}")

    def _destroyed(self, event):
        if event.widget is not self.window:
            return
        for attribute in ("poll_id", "_task_poll_id"):
            pending = getattr(self, attribute)
            if pending is not None:
                self.window.after_cancel(pending)
                setattr(self, attribute, None)


def open_settings(parent):
    window = getattr(parent, "_tracker_settings", None)
    if window is None or not window.window.winfo_exists():
        window = parent._tracker_settings = SettingsWindow(parent)
    window.show()
    return window
