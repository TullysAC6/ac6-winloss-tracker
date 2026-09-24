"""Lightweight Windows game HUD for AC6 Win/Loss Tracker.

This module is intentionally independent from server.py and the result detector:
- no DLL injection
- no DirectX hook
- no game-process memory access
- no OBS / browser source dependency

It reads the already-persisted stats.json and draws a click-through, non-activating
Win32/Tk overlay over the foreground Armored Core VI client area.

Run the normal start.bat so automatic result detection and this overlay start
together. The drawing/foreground behavior is unchanged; a lightweight lifecycle
check only closes this overlay after the linked server.py process stops.
Borderless/windowed display mode is recommended.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import queue
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, NamedTuple

from app_paths import data_dir
from config_utils import load_config
from effect_screenshot import EffectScreenshots
from event_bus import EFFECT_TTL_MS

ROOT = data_dir()
STATS_PATH = ROOT / "stats.json"
CONFIG_PATH = ROOT / "config.json"
RUNTIME_PATH = ROOT / ".runtime.json"
OVERLAY_RUNTIME_PATH = ROOT / ".overlay-runtime.json"

# These settings are local to the independent in-game overlay.
DEFAULT_PROCESS = "armoredcore6.exe"
DEFAULT_FONT_SIZE = 22
DEFAULT_POLL_MS = 250
DEFAULT_PANEL_OPACITY = 15
DEFAULT_SERVER_CHECK_MS = 500
DEFAULT_SERVER_STARTUP_GRACE_SEC = 20.0
HEARTBEAT_SECONDS = 1.5
STATS_FALLBACK_SECONDS = 3.0
TRANSPARENT_KEY = "#010203"

# UI-1A Player Overlay tokens (docs/UI0_DESIGN_SPEC.md §5 and §6).
PANEL_BG = "#0B1116"      # color.base
TEXT_FG = "#F2F7F8"       # color.text.primary: metric values
LABEL_FG = "#AFC0C7"      # color.text.secondary: quieter metric labels
ACCENT = "#4BD9E8"        # color.system.cyan: leading rule, corner ticks, caption
ACK_ACCENT = "#8BE9F3"    # brighter cyan for the one-shot result acknowledgement
SHADOW = "#000000"
VALUE_FONTS = ("Segoe UI Variable Display", "Yu Gothic UI", "Meiryo")
LABEL_FONTS = ("Segoe UI Variable Text", "Yu Gothic UI", "Meiryo")
# Column minimum widths come from these samples, so a count going 9 -> 10
# does not shift every metric sideways during play.
METRIC_SAMPLES = ("000", "000", "100.0%", "00")
SAFE_INSET_LOGICAL = 24
SAFE_INSET_FRACTION = 0.0125
GAP_REGULAR = 24
GAP_COMPACT = 8
PAD_X = 16
PAD_Y = 8
ACK_MS = 160


def status_for_streak(streak: int) -> tuple[str, int]:
    if streak >= 20:
        return "RUSH継続中", 5
    if streak >= 15:
        return "覚醒ゾーン", 4
    if streak >= 10:
        return "超激アツ", 3
    if streak >= 5:
        return "激アツ", 2
    if streak >= 3:
        return "アツい", 1
    return "", 0


def normalize_stats(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    try:
        wins = int(raw["wins"])
        losses = int(raw["losses"])
        streak = int(raw["streak"])
        best = int(raw["best_streak"])
    except (KeyError, TypeError, ValueError):
        return None
    if min(wins, losses, streak, best) < 0 or streak > wins or best < streak:
        return None

    total = wins + losses
    rate = (wins / total * 100.0) if total else 0.0
    status, level = status_for_streak(streak)
    return {
        "wins": wins,
        "losses": losses,
        "streak": streak,
        "best_streak": best,
        "win_rate": rate,
        "status": status,
        "status_level": level,
    }


def normalize_lifetime(raw: Any) -> dict[str, Any] | None:
    """Accept only a complete, non-negative lifetime total block."""
    if not isinstance(raw, dict):
        return None
    try:
        wins = int(raw["wins"])
        losses = int(raw["losses"])
        best = int(raw["best_streak"])
        rate = float(raw["win_rate"])
    except (KeyError, TypeError, ValueError):
        return None
    if min(wins, losses, best) < 0 or not 0.0 <= rate <= 100.0:
        return None
    return {"wins": wins, "losses": losses, "best_streak": best, "win_rate": rate}


def player_metrics(wins: int, losses: int, rate: float, streak: int) -> tuple[tuple[str, str], ...]:
    """The four always-on Player metrics as (label, value). BEST is not one of them."""
    return (
        ("WIN", str(wins)),
        ("LOSS", str(losses)),
        ("RATE", f"{rate:.1f}%"),
        ("STREAK", str(streak)),
    )


def pick_font_family(available: Any, preferred: tuple[str, ...]) -> str:
    names = set(available)
    for name in preferred:
        if name in names:
            return name
    return preferred[-1]


class PanelLayout(NamedTuple):
    """Physical-pixel geometry of the Player panel; see layout_panel()."""

    width: int
    height: int
    gap: int  # logical px token actually used
    centers: tuple[int, ...]
    caption_y: int
    value_y: int
    label_y: int
    pad_x: int
    rule_width: int
    tick: int


def layout_panel(
    column_widths: tuple[int, ...],
    caption_width: int,
    caption_height: int,
    value_ascent: int,
    label_height: int,
    scale: float,
    available_width: int | None = None,
) -> PanelLayout:
    """Place value-first metric columns; spacing tokens are logical px.

    When the regular 24 px gaps do not fit the AC6 client's safe width, gaps
    fall back to the 8 px token. Type is never reduced.
    """
    pad_x = round(PAD_X * scale)
    pad_y = round(PAD_Y * scale)
    rule_width = max(1, round(2 * scale))
    tick = max(4, round(8 * scale))
    content = sum(column_widths)
    gaps = len(column_widths) - 1

    def width_for(gap_logical: int) -> int:
        metrics = content + round(gap_logical * scale) * gaps
        return rule_width + pad_x + max(metrics, caption_width) + pad_x

    gap_logical = GAP_REGULAR
    if available_width is not None and width_for(GAP_REGULAR) > available_width:
        gap_logical = GAP_COMPACT
    gap = round(gap_logical * scale)
    width = width_for(gap_logical)

    centers = []
    x = rule_width + pad_x
    for column in column_widths:
        centers.append(x + column // 2)
        x += column + gap
    caption_y = pad_y
    value_y = caption_y + caption_height + round(2 * scale)
    label_y = value_y + value_ascent + round(4 * scale)
    height = label_y + label_height + pad_y
    return PanelLayout(width, height, gap_logical, tuple(centers), caption_y, value_y,
                       label_y, pad_x, rule_width, tick)


def safe_inset(client_width: int, client_height: int, scale: float) -> tuple[int, int]:
    """max(24 logical px, 1.25% of the client dimension) on each axis."""
    minimum = SAFE_INSET_LOGICAL * scale
    return (round(max(minimum, client_width * SAFE_INSET_FRACTION)),
            round(max(minimum, client_height * SAFE_INSET_FRACTION)))


def place_panel(
    left: int, top: int, client_width: int, client_height: int,
    panel_width: int, panel_height: int, scale: float,
) -> tuple[int, int]:
    """Top-left of the panel inside the AC6 client's safe zone.

    The panel keeps the same clearance from the opposite edges; when it cannot,
    it stays anchored to the client's top-left rather than leaving the client.
    """
    inset_x, inset_y = safe_inset(client_width, client_height, scale)
    x = max(left, min(left + inset_x, left + client_width - inset_x - panel_width))
    y = max(top, min(top + inset_y, top + client_height - inset_y - panel_height))
    return x, y


def read_stats(path: Path = STATS_PATH) -> dict[str, Any] | None:
    try:
        with path.open("r", encoding="utf-8") as f:
            return normalize_stats(json.load(f))
    except (OSError, json.JSONDecodeError):
        return None


def configured_process(path: Path = CONFIG_PATH) -> str:
    return DEFAULT_PROCESS


def read_server_runtime(path: Path = RUNTIME_PATH) -> dict[str, Any] | None:
    """Read server.py's runtime identity without affecting overlay visibility."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        pid = int(raw["pid"])
        port = int(raw["port"])
        started_at = float(raw.get("started_at", 0.0))
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None
    if pid <= 0 or not 1 <= port <= 65535 or started_at < 0:
        return None
    return {"pid": pid, "port": port, "started_at": started_at}


def write_overlay_runtime(
    payload: dict[str, Any], path: Path | None = None, durable: bool = False,
) -> None:
    path = OVERLAY_RUNTIME_PATH if path is None else path
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)
        handle.flush()
        if durable:
            os.fsync(handle.fileno())
    os.replace(tmp, path)


def remove_owned_overlay_runtime(path: Path | None = None) -> None:
    path = OVERLAY_RUNTIME_PATH if path is None else path
    try:
        current = json.loads(path.read_text(encoding="utf-8"))
        if int(current.get("pid", 0)) == os.getpid():
            path.unlink()
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass


if os.name == "nt":
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    ERROR_ALREADY_EXISTS = 183
    OVERLAY_MUTEX_NAME = "Local\\AC6StatsOverlayV22"
    GWL_EXSTYLE = -20
    WS_EX_TRANSPARENT = 0x00000020
    WS_EX_TOOLWINDOW = 0x00000080
    WS_EX_LAYERED = 0x00080000
    WS_EX_NOACTIVATE = 0x08000000
    HWND_TOPMOST = -1
    SW_HIDE = 0
    SW_SHOWNOACTIVATE = 4
    SWP_NOSIZE = 0x0001
    SWP_NOMOVE = 0x0002
    SWP_NOACTIVATE = 0x0010
    SWP_SHOWWINDOW = 0x0040

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long),
            ("top", ctypes.c_long),
            ("right", ctypes.c_long),
            ("bottom", ctypes.c_long),
        ]

    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetParent.argtypes = [wintypes.HWND]
    user32.GetParent.restype = wintypes.HWND
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
    user32.GetClientRect.restype = wintypes.BOOL
    user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(POINT)]
    user32.ClientToScreen.restype = wintypes.BOOL
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.IsIconic.restype = wintypes.BOOL
    # Tk uses a wrapper HWND around the widget HWND. Apply extended styles to
    # the wrapper and use pointer-sized APIs on 64-bit Windows.
    if hasattr(user32, "GetWindowLongPtrW"):
        user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    else:
        user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.GetWindowLongW.restype = ctypes.c_long
        user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
        user32.SetWindowLongW.restype = ctypes.c_long
    user32.SetWindowPos.argtypes = [
        wintypes.HWND,
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
    ]
    user32.SetWindowPos.restype = wintypes.BOOL
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow.restype = wintypes.BOOL
    # Windows 10 1607+. The safe-zone inset uses the AC6 window's own DPI.
    if hasattr(user32, "GetDpiForWindow"):
        user32.GetDpiForWindow.argtypes = [wintypes.HWND]
        user32.GetDpiForWindow.restype = wintypes.UINT

    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE


def _acquire_single_instance_mutex():
    """Keep only one game overlay instance per Windows login session."""
    if os.name != "nt":
        return None
    handle = kernel32.CreateMutexW(None, False, OVERLAY_MUTEX_NAME)
    if not handle:
        return None
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        return False
    return handle


def _get_exstyle(hwnd: int) -> int:
    if hasattr(user32, "GetWindowLongPtrW"):
        return int(user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE))
    return int(user32.GetWindowLongW(hwnd, GWL_EXSTYLE))


def _set_exstyle(hwnd: int, value: int) -> None:
    if hasattr(user32, "SetWindowLongPtrW"):
        user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, value)
    else:
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, value)


def _tk_toplevel_hwnd(widget_hwnd: int) -> int:
    """Return the real Windows top-level wrapper HWND used by Tk."""
    if os.name != "nt":
        return widget_hwnd
    parent = user32.GetParent(widget_hwnd)
    return int(parent) if parent else int(widget_hwnd)


def _enable_dpi_awareness() -> None:
    if os.name != "nt":
        return
    # Per-monitor-v2 when available; older Windows falls back gracefully.
    try:
        set_ctx = user32.SetProcessDpiAwarenessContext
        set_ctx.argtypes = [ctypes.c_void_p]
        set_ctx.restype = wintypes.BOOL
        if set_ctx(ctypes.c_void_p(-4)):
            return
    except (AttributeError, OSError):
        pass
    try:
        user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass


def _process_is_alive(pid: int) -> bool:
    """Read-only liveness check for the exact server PID from .runtime.json."""
    if os.name != "nt" or pid <= 0:
        return False
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return False
    try:
        code = wintypes.DWORD(0)
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return int(code.value) == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def remove_stale_overlay_runtime(path: Path | None = None) -> None:
    path = OVERLAY_RUNTIME_PATH if path is None else path
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        pid = int(raw.get("pid", 0))
        heartbeat = float(raw.get("heartbeat_at", 0.0))
        if not _process_is_alive(pid) or time.time() - heartbeat > 3.0:
            path.unlink()
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass


def foreground_game_client(process_name: str) -> tuple[int, int, int, int, int] | None:
    """Return hwnd,left,top,width,height only when the target game is foreground."""
    if os.name != "nt":
        return None

    hwnd = user32.GetForegroundWindow()
    if not hwnd or user32.IsIconic(hwnd):
        return None

    pid = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return None

    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not handle:
        return None
    try:
        size = wintypes.DWORD(32768)
        buf = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return None
        exe = os.path.basename(buf.value).lower()
    finally:
        kernel32.CloseHandle(handle)

    if exe != process_name.lower():
        return None

    rect = RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        return None
    origin = POINT(0, 0)
    if not user32.ClientToScreen(hwnd, ctypes.byref(origin)):
        return None

    width = int(rect.right - rect.left)
    height = int(rect.bottom - rect.top)
    if width <= 1 or height <= 1:
        return None
    return int(hwnd), int(origin.x), int(origin.y), width, height


class GameOverlay:
    """Two-window HUD so panel alpha and text alpha are independent.

    Tk applies ``-alpha`` to an entire toplevel.  A single-window design cannot
    make only the panel 10% opaque while leaving text at 100%.  The previous
    stipple workaround was only a dot pattern, not real alpha blending, and can
    therefore look almost unchanged on a moving game background.

    This implementation uses:
      * panel_root: solid dark panel with true window alpha (default 15%)
      * text_window: color-key transparent window with fully opaque text

    The text window is owned by panel_root, so it stays above the panel.  Both
    windows are click-through, non-activating and topmost.

    x_offset / y_offset of None place the panel in the responsive safe zone;
    an explicit value keeps the original fixed offset from the client origin.
    """

    def __init__(
        self,
        process_name: str,
        x_offset: int | None,
        y_offset: int | None,
        font_size: int,
        poll_ms: int,
        panel_opacity: int = DEFAULT_PANEL_OPACITY,
        always_show: bool = False,
        debug: bool = False,
    ) -> None:
        if os.name != "nt":
            raise RuntimeError("game_overlay.py is Windows-only")

        import tkinter as tk
        import tkinter.font as tkfont

        self.tk = tk
        self.process_name = process_name.lower()
        self.x_offset = x_offset
        self.y_offset = y_offset
        self.poll_ms = poll_ms
        self.panel_opacity = panel_opacity
        self.always_show = always_show
        self.debug = debug
        self._last_debug_at = 0.0

        # Lifecycle tracking is intentionally independent from rendering.
        # Unlike the previous linked attempt, startup/waiting NEVER hides the HUD.
        self._server_pid: int | None = None
        self._server_linked = False
        self._server_state = "waiting"
        self._server_startup_deadline = time.monotonic() + DEFAULT_SERVER_STARTUP_GRACE_SEC
        self._next_server_check_at = 0.0
        self._closing = False
        self._overlay_started_at = time.time()
        self._last_heartbeat_at = 0.0
        self._effect_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self._stats_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self._lifetime_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self._lifetime: dict[str, Any] | None = None
        self._stats_scope = "session"
        self._sse_connected = threading.Event()
        self._next_stats_fallback_at = time.monotonic() + STATS_FALLBACK_SECONDS
        self._effect_ids: set[str] = set()
        self._last_effect_created_at = 0
        self._active_effect: dict[str, Any] | None = None
        self._effect_visible = False
        self._effect_stop = threading.Event()
        self._effect_thread_started = False
        self._screenshots = EffectScreenshots()

        self.last_stats: dict[str, Any] = {
            "wins": 0,
            "losses": 0,
            "streak": 0,
            "best_streak": 0,
            "win_rate": 0.0,
            "status": "",
            "status_level": 0,
        }
        startup_stats = read_stats()
        if startup_stats is not None:
            self.last_stats = startup_stats
        # A result acknowledgement needs a known previous count; the first
        # reading after an unreadable stats.json is a baseline, not a result.
        self._stats_baseline = startup_stats is not None
        self._ack_active = False
        self._ack_after: str | None = None
        self.visible = False
        self._last_render_key: tuple[Any, ...] | None = None
        self._measured: tuple[Any, ...] | None = None
        self._available_width: int | None = None
        self._panel_size = (1, 1)

        # Window 1: the actual translucent panel.  This window contains no
        # text, so changing its alpha cannot reduce text readability.
        panel_root = tk.Tk()
        self.root = panel_root
        self.panel_root = panel_root
        panel_root.title("AC6 Stats Overlay Panel")
        panel_root.overrideredirect(True)
        panel_root.configure(bg=PANEL_BG)
        panel_root.attributes("-topmost", True)
        self._set_panel_alpha()
        panel_root.geometry("+32000+32000")

        # Window 2: fully opaque text on a color-key transparent background.
        # Because this Toplevel is owned by panel_root it remains above it.
        text_window = tk.Toplevel(panel_root)
        self.text_window = text_window
        text_window.title("AC6 Stats Overlay Text")
        text_window.overrideredirect(True)
        text_window.configure(bg=TRANSPARENT_KEY)
        text_window.attributes("-topmost", True)
        text_window.attributes("-transparentcolor", TRANSPARENT_KEY)
        try:
            text_window.attributes("-alpha", 1.0)
        except tk.TclError:
            pass
        text_window.geometry("+32000+32000")

        try:
            self._ui_scale = float(panel_root.winfo_fpixels("1i")) / 96.0
        except (tk.TclError, ValueError):
            self._ui_scale = 1.0
        self._build_fonts(font_size)

        self.canvas = tk.Canvas(
            text_window,
            bg=TRANSPARENT_KEY,
            highlightthickness=0,
            bd=0,
            relief="flat",
        )
        self.canvas.pack()

        # Window 3 is exclusively for transient milestone effects.  It never
        # owns or clears persistent HUD items, so stats continue rendering
        # while an effect is visible and need no restoration afterwards.
        effect_window = tk.Toplevel(panel_root)
        self.effect_window = effect_window
        effect_window.title("AC6 Milestone Effect")
        effect_window.overrideredirect(True)
        effect_window.configure(bg=TRANSPARENT_KEY)
        effect_window.attributes("-topmost", True)
        effect_window.attributes("-transparentcolor", TRANSPARENT_KEY)
        try:
            effect_window.attributes("-alpha", 1.0)
        except tk.TclError:
            pass
        effect_window.geometry("+32000+32000")
        self.effect_canvas = tk.Canvas(
            effect_window,
            width=1000,
            height=240,
            bg=TRANSPARENT_KEY,
            highlightthickness=0,
            bd=0,
            relief="flat",
        )
        self.effect_canvas.pack()

        # Map both once off-screen so Tk creates the real Win32 wrapper HWNDs.
        panel_root.update_idletasks()
        panel_root.update()
        self.panel_widget_hwnd = int(panel_root.winfo_id())
        self.panel_hwnd = _tk_toplevel_hwnd(self.panel_widget_hwnd)
        self.text_widget_hwnd = int(text_window.winfo_id())
        self.text_hwnd = _tk_toplevel_hwnd(self.text_widget_hwnd)
        self.effect_widget_hwnd = int(effect_window.winfo_id())
        self.effect_hwnd = _tk_toplevel_hwnd(self.effect_widget_hwnd)

        self._apply_clickthrough_style(self.panel_hwnd)
        self._apply_clickthrough_style(self.text_hwnd)
        self._apply_clickthrough_style(self.effect_hwnd)
        user32.ShowWindow(self.effect_hwnd, SW_HIDE)
        user32.ShowWindow(self.text_hwnd, SW_HIDE)
        user32.ShowWindow(self.panel_hwnd, SW_HIDE)
        self.visible = False
        panel_root.after(0, self._tick)

    def _set_panel_alpha(self) -> None:
        # Real alpha blending.  10 means 10% opaque / 90% transparent.
        alpha = max(0.0, min(1.0, float(self.panel_opacity) / 100.0))
        try:
            self.panel_root.attributes("-alpha", alpha)
        except self.tk.TclError:
            # Very old Tk builds may not expose alpha; the panel is hidden for
            # 0%, otherwise it falls back to opaque rather than affecting text.
            pass

    def _apply_clickthrough_style(self, hwnd: int) -> None:
        ex = _get_exstyle(hwnd)
        ex |= WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
        _set_exstyle(hwnd, ex)
        user32.SetWindowPos(
            hwnd,
            HWND_TOPMOST,
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
        )

    def _display_values(self) -> tuple[int, int, float, int, str]:
        """Session or lifetime totals. Streak and status stay session-scoped."""
        s = self.last_stats
        if self._stats_scope == "lifetime" and self._lifetime is not None:
            life = self._lifetime
            return (life["wins"], life["losses"], float(life["win_rate"]),
                    life["best_streak"], "累計 ")
        return (s["wins"], s["losses"], float(s["win_rate"]), s["best_streak"], "")

    def _build_fonts(self, font_size: int) -> None:
        import tkinter.font as tkfont
        families = tkfont.families(self.root)
        self.main_font = tkfont.Font(
            root=self.root, family=pick_font_family(families, VALUE_FONTS),
            size=font_size, weight="bold",
        )
        self.label_font = tkfont.Font(
            root=self.root, family=pick_font_family(families, LABEL_FONTS),
            size=max(9, round(font_size * 0.45)), weight="bold",
        )
        self._measured = None
        self._last_render_key = None

    def _measure(self, metrics: tuple[tuple[str, str], ...], caption: str) -> tuple[Any, ...]:
        content = (metrics, caption)
        if self._measured is None or self._measured[0] != content:
            columns = tuple(
                max(int(self.main_font.measure(value)), int(self.main_font.measure(sample)),
                    int(self.label_font.measure(label)))
                for (label, value), sample in zip(metrics, METRIC_SAMPLES)
            )
            self._measured = (
                content, columns, int(self.label_font.measure(caption)),
                int(self.label_font.metrics("linespace")),
                int(self.main_font.metrics("ascent")),
            )
        return self._measured

    def _render(self) -> None:
        """Value-first WIN / LOSS / RATE / STREAK panel.

        BEST and the pachinko streak status are deliberately not drawn: BEST
        stays in Dashboard Overview and the loud moment is the milestone effect.
        """
        s = self.last_stats
        wins, losses, rate, _best_streak, scope_label = self._display_values()
        metrics = player_metrics(wins, losses, rate, s["streak"])
        caption = "LIFETIME TOTALS" if scope_label else "SESSION"
        _content, columns, caption_w, label_h, value_ascent = self._measure(metrics, caption)
        layout = layout_panel(columns, caption_w, label_h, value_ascent, label_h,
                              self._ui_scale, self._available_width)
        key = (metrics, caption, layout.gap)
        if key == self._last_render_key:
            return
        self._last_render_key = key

        width, height = layout.width, layout.height
        self._panel_size = (width, height)
        self.canvas.configure(width=width, height=height)
        self.canvas.delete("all")

        # Only text and hairlines are drawn in this window.  The background
        # panel lives in a separate true-alpha toplevel, so text stays 100%
        # opaque.
        accent = ACK_ACCENT if self._ack_active else ACCENT
        rule_x = layout.rule_width / 2
        self.canvas.create_line(
            rule_x, 0, rule_x, height, width=layout.rule_width,
            capstyle="butt", fill=accent, tags="accent",
        )
        tick, right, bottom = layout.tick, width - 1, height - 1
        for points in ((right - tick, 0, right, 0, right, tick),
                       (right - tick, bottom, right, bottom, right, bottom - tick)):
            self.canvas.create_line(*points, fill=accent, tags="accent")

        shadow = max(1, round(2 * self._ui_scale))
        label_shadow = max(1, round(self._ui_scale))
        text_x = layout.rule_width + layout.pad_x
        self.canvas.create_text(
            text_x + label_shadow, layout.caption_y + label_shadow,
            anchor="nw", text=caption, font=self.label_font, fill=SHADOW,
        )
        self.canvas.create_text(
            text_x, layout.caption_y, anchor="nw", text=caption,
            font=self.label_font, fill=ACCENT,
        )
        for (label, value), center in zip(metrics, layout.centers):
            self.canvas.create_text(
                center + shadow, layout.value_y + shadow,
                anchor="n", text=value, font=self.main_font, fill=SHADOW,
            )
            self.canvas.create_text(
                center, layout.value_y, anchor="n", text=value,
                font=self.main_font, fill=TEXT_FG,
            )
            self.canvas.create_text(
                center + label_shadow, layout.label_y + label_shadow,
                anchor="n", text=label, font=self.label_font, fill=SHADOW,
            )
            self.canvas.create_text(
                center, layout.label_y, anchor="n", text=label,
                font=self.label_font, fill=LABEL_FG,
            )
        self.text_window.update_idletasks()

    def _accept_stats(self, stats: dict[str, Any]) -> None:
        previous = self.last_stats
        self.last_stats = stats
        counted = stats["wins"] + stats["losses"]
        if self._stats_baseline and counted > previous["wins"] + previous["losses"]:
            self._acknowledge_result()
        self._stats_baseline = True
        self._render()

    def _acknowledge_result(self) -> None:
        """One finite leading-rule pulse for a newly counted WIN or LOSE.

        Undo, reset and DRAW never raise a count, so they never pulse.  Only
        the tagged hairlines change colour; nothing is re-laid out.
        """
        if self._ack_after is not None:
            try:
                self.root.after_cancel(self._ack_after)
            except self.tk.TclError:
                pass
        self._ack_active = True
        self._paint_accent()
        self._ack_after = self.root.after(ACK_MS, self._end_acknowledgement)

    def _end_acknowledgement(self) -> None:
        self._ack_after = None
        self._ack_active = False
        self._paint_accent()

    def _paint_accent(self) -> None:
        try:
            self.canvas.itemconfigure("accent", fill=ACK_ACCENT if self._ack_active else ACCENT)
        except self.tk.TclError:
            pass

    def _show_absolute(self, x: int, y: int) -> None:
        self.text_window.update_idletasks()
        w = max(1, self.text_window.winfo_reqwidth())
        h = max(1, self.text_window.winfo_reqheight())

        # Keep Tk's own geometry models synchronized with the native HWNDs.
        self.panel_root.geometry(f"{w}x{h}+{x}+{y}")
        self.text_window.geometry(f"{w}x{h}+{x}+{y}")
        self.panel_root.update_idletasks()
        self.text_window.update_idletasks()

        # Show the panel first, then the text window.  The text Toplevel is
        # owned by the panel and is also explicitly raised last.
        if self.panel_opacity > 0:
            user32.SetWindowPos(
                self.panel_hwnd,
                HWND_TOPMOST,
                x,
                y,
                w,
                h,
                SWP_NOACTIVATE | SWP_SHOWWINDOW,
            )
            user32.ShowWindow(self.panel_hwnd, SW_SHOWNOACTIVATE)
        else:
            user32.ShowWindow(self.panel_hwnd, SW_HIDE)

        user32.SetWindowPos(
            self.text_hwnd,
            HWND_TOPMOST,
            x,
            y,
            w,
            h,
            SWP_NOACTIVATE | SWP_SHOWWINDOW,
        )
        user32.ShowWindow(self.text_hwnd, SW_SHOWNOACTIVATE)
        self.visible = bool(user32.IsWindowVisible(self.text_hwnd))

    def _client_scale(self, hwnd: int) -> float:
        """Logical-to-physical factor of the AC6 window's monitor."""
        dpi = 0
        if hwnd:
            try:
                dpi = int(user32.GetDpiForWindow(hwnd))
            except (AttributeError, OSError):
                dpi = 0
        return dpi / 96.0 if dpi > 0 else self._ui_scale

    def _show_at_game(
        self, left: int, top: int, width: int = 0, height: int = 0, hwnd: int = 0,
    ) -> None:
        safe_x = safe_y = 0
        if self.x_offset is None or self.y_offset is None:
            scale = self._client_scale(hwnd)
            inset_x, _inset_y = safe_inset(width, height, scale)
            available = width - 2 * inset_x if self.x_offset is None else None
            if available != self._available_width:
                # Only a change of gap token re-lays out; _render is key-guarded.
                self._available_width = available
                self._render()
            safe_x, safe_y = place_panel(left, top, width, height, *self._panel_size, scale)
        x = left + self.x_offset if self.x_offset is not None else safe_x
        y = top + self.y_offset if self.y_offset is not None else safe_y
        self._show_absolute(x, y)

    def _preview_origin(self) -> tuple[int, int]:
        """--always-show without a foreground AC6 client: absolute screen origin."""
        inset = round(SAFE_INSET_LOGICAL * self._ui_scale)
        return (inset if self.x_offset is None else self.x_offset,
                inset if self.y_offset is None else self.y_offset)

    def _hide(self) -> None:
        if self.visible or user32.IsWindowVisible(self.panel_hwnd):
            user32.ShowWindow(self.text_hwnd, SW_HIDE)
            user32.ShowWindow(self.panel_hwnd, SW_HIDE)
            self.visible = False

    def _hide_effect(self) -> None:
        self.effect_canvas.delete("milestone")
        user32.ShowWindow(self.effect_hwnd, SW_HIDE)
        self._effect_visible = False

    def _finish_effect(self) -> None:
        """Remove all transient state without touching the persistent HUD."""
        self._hide_effect()
        self._active_effect = None
        # Reassert the independent HUD z-order; no HUD redraw is required.
        if self.visible:
            user32.SetWindowPos(
                self.text_hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
            )

    def _shutdown_for_server_stop(self, reason: str) -> None:
        if self._closing:
            return
        self._closing = True
        self._finish_effect()
        self._hide()
        print(f"[game-overlay] {reason}; closing overlay")
        try:
            self.root.destroy()
        except self.tk.TclError:
            pass

    def _poll_server_lifecycle(self) -> bool:
        """Return False only when this overlay must terminate.

        Important: while server.py is still starting, this function does not
        hide, move, or otherwise gate the HUD.  Rendering stays byte-for-byte
        on the same path as the known-good unlinked build.
        """
        now = time.monotonic()
        if now < self._next_server_check_at:
            return True
        self._next_server_check_at = now + DEFAULT_SERVER_CHECK_MS / 1000.0

        runtime = read_server_runtime()

        if not self._server_linked:
            if runtime is not None and _process_is_alive(int(runtime["pid"])):
                self._server_pid = int(runtime["pid"])
                self._server_linked = True
                self._server_state = "alive"
                print(f"[game-overlay] linked to server.py PID {self._server_pid}")
                self._start_effect_listener(int(runtime["port"]))
                return True
            if now >= self._server_startup_deadline:
                self._server_state = "stopped"
                self._shutdown_for_server_stop("server.py startup timed out")
                return False
            self._server_state = "waiting"
            return True

        if (
            runtime is None
            or int(runtime["pid"]) != int(self._server_pid or 0)
            or not _process_is_alive(int(self._server_pid or 0))
        ):
            self._server_state = "stopped"
            self._shutdown_for_server_stop("server.py stopped")
            return False

        self._server_state = "alive"
        return True

    def _queue_sse_event(self, event_name: str, data: str) -> None:
        payload = json.loads(data)
        if event_name == "stats":
            stats_payload = normalize_stats(payload)
            if stats_payload is not None:
                self._stats_queue.put(stats_payload)
            return
        if event_name == "lifetime":
            lifetime_payload = normalize_lifetime(payload)
            if lifetime_payload is not None:
                self._lifetime_queue.put(lifetime_payload)
            return
        if event_name != "effect":
            return
        effect_id = str(payload.get("effect_id", ""))
        created = int(payload.get("created_at_ms", 0))
        if (
            payload.get("effect") == "milestone"
            and effect_id
            and effect_id not in self._effect_ids
            and created >= int(self._overlay_started_at * 1000)
            and 0 <= time.time() * 1000 - created < EFFECT_TTL_MS
            and created >= getattr(self, "_last_effect_created_at", 0)
        ):
            self._effect_ids.add(effect_id)
            self._last_effect_created_at = created
            self._effect_queue.put(payload)

    def _start_effect_listener(self, port: int) -> None:
        if self._effect_thread_started:
            return
        self._effect_thread_started = True

        def listen() -> None:
            while not self._effect_stop.is_set():
                try:
                    with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/events", timeout=5
                    ) as response:
                        self._sse_connected.set()
                        event_name = ""
                        data = None
                        for raw_line in response:
                            if self._effect_stop.is_set():
                                return
                            line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
                            if line.startswith("event: "):
                                event_name = line[7:]
                            elif line.startswith("data: "):
                                data = line[6:]
                            elif not line and event_name and data:
                                try:
                                    self._queue_sse_event(event_name, data)
                                except (TypeError, ValueError, json.JSONDecodeError):
                                    pass
                                event_name = ""
                                data = None
                except (OSError, urllib.error.URLError, ValueError):
                    self._effect_stop.wait(1.0)
                finally:
                    self._sse_connected.clear()

        threading.Thread(target=listen, name="overlay-effects", daemon=True).start()

    def _render_milestone_effect(self, game: tuple[int, int, int, int, int]) -> None:
        effect = self._active_effect
        if effect is None:
            return
        elapsed = time.monotonic() - effect["started"]
        milestone = int(effect["milestone"])
        if elapsed > effect["duration"]:
            self._finish_effect()
            return
        width, height = max(1, game[3]), max(1, game[4])
        effect_width = max(600, min(width, 1000))
        effect_height = 240
        if milestone == 50 and elapsed < 1.6:
            flash = "#ffffff" if elapsed < 0.8 else "#111111"
            stage = "flash-white" if elapsed < 0.8 else "flash-dark"
            text, color, size = "", flash, 1
        else:
            stage = "legend-gold" if milestone == 50 and elapsed < 3.2 else "banner"
            text = {
                5: "5連勝　激アツ!!", 10: "10連勝　超激アツ!!",
                15: "15連勝　覚醒ゾーン突入", 20: "20連勝　RUSH突入!!",
                25: "25連勝　RUSH継続!!", 30: "30連勝!!", 35: "35連勝!!",
                40: "40連勝!!", 45: "45連勝!!", 50: "50連勝　LEGEND",
            }.get(milestone, f"{milestone}連勝!!")
            color = "#ffd84a" if milestone < 30 else "#d68cff"
            size = 42 + min(28, milestone // 2)
            if milestone == 50 and elapsed < 3.2:
                color, size = "#ffd84a", 76
        render_key = (milestone, stage, effect_width)
        if effect.get("render_key") != render_key:
            effect["render_key"] = render_key
            self.effect_canvas.configure(width=effect_width, height=effect_height)
            self.effect_canvas.delete("milestone")
            if not text:
                self.effect_canvas.create_rectangle(
                    0, 0, effect_width, effect_height,
                    fill=color, outline=color, tags="milestone",
                )
            else:
                # A compact banner sits slightly below center, away from game UI.
                banner_fill = "#21142e" if milestone >= 30 else "#2b2108"
                self.effect_canvas.create_rectangle(
                    70, 65, effect_width - 70, 175,
                    fill=banner_fill, outline=color, width=3, tags="milestone",
                )
                self.effect_canvas.create_text(
                    effect_width // 2, 120,
                    anchor="center", text=text, fill=color,
                    font=("Yu Gothic UI", size, "bold"),
                    tags="milestone",
                )
        left, top = game[1], game[2]
        x = left + (width - effect_width) // 2
        y = top + int(height * 0.62) - effect_height // 2
        user32.SetWindowPos(
            self.effect_hwnd, HWND_TOPMOST, x, y, effect_width, effect_height,
            SWP_NOACTIVATE | SWP_SHOWWINDOW,
        )
        user32.ShowWindow(self.effect_hwnd, SW_SHOWNOACTIVATE)
        self._effect_visible = True

    def _drain_display_scope(self) -> None:
        """Apply a mid-match scope change or new lifetime totals.

        _render() is key-guarded, so this repaints without a new stats event
        and costs nothing when neither the scope nor the totals moved.
        """
        try:
            scope = load_config().get("overlay_stats_scope", "session")
            self._stats_scope = "lifetime" if scope == "lifetime" else "session"
        except Exception:
            pass
        try:
            while True:
                self._lifetime = self._lifetime_queue.get_nowait()
        except queue.Empty:
            pass
        self._render()

    def _drain_effects(self) -> None:
        try:
            while True:
                self._accept_stats(self._stats_queue.get_nowait())
        except queue.Empty:
            pass
        try:
            while True:
                payload = self._effect_queue.get_nowait()
                if not 0 <= time.time() * 1000 - int(payload["created_at_ms"]) < EFFECT_TTL_MS:
                    continue
                if self._active_effect is not None:
                    self._finish_effect()
                self._active_effect = {
                    "effect_id": str(payload["effect_id"]),
                    "milestone": int(payload["milestone"]),
                    "started": time.monotonic(),
                    "duration": 6.0 if int(payload["milestone"]) == 50 else 3.5,
                    "render_key": None,
                    "created_at_ms": int(payload["created_at_ms"]),
                }
        except queue.Empty:
            pass

    def _publish_ready_heartbeat(self) -> None:
        now = time.time()
        if not self._server_linked or self._server_state != "alive" or not self._server_pid:
            return
        if now - self._last_heartbeat_at < HEARTBEAT_SECONDS:
            return
        first_heartbeat = self._last_heartbeat_at == 0.0
        from python_spawn import launch_nonce
        write_overlay_runtime({
            "pid": os.getpid(),
            "launch_nonce": launch_nonce(),
            "server_pid": self._server_pid,
            "started_at": self._overlay_started_at,
            "heartbeat_at": now,
            "state": "ready",
            "panel_hwnd": self.panel_hwnd,
            "text_hwnd": self.text_hwnd,
            "target_process": self.process_name,
        }, durable=first_heartbeat)
        if first_heartbeat:
            print(f"[lifecycle] overlay ready: PID {os.getpid()}, server PID {self._server_pid}")
        self._last_heartbeat_at = now

    def _tick(self) -> None:
        self._drain_effects()
        self._drain_display_scope()
        now = time.monotonic()
        if not self._sse_connected.is_set() and now >= self._next_stats_fallback_at:
            self._next_stats_fallback_at = now + STATS_FALLBACK_SECONDS
            fallback_stats = read_stats()
            if fallback_stats is not None:
                self._accept_stats(fallback_stats)

        game = foreground_game_client(self.process_name)
        if game is not None:
            hwnd, left, top, width, height = game
            # The persistent HUD remains positioned and updated throughout the
            # effect.  The transient window is simply layered over the game.
            self._show_at_game(left, top, width, height, hwnd)
            if self._active_effect is not None:
                self._render_milestone_effect(game)
            elif self._effect_visible:
                self._hide_effect()
        elif self.always_show:
            preview_x, preview_y = self._preview_origin()
            self._show_absolute(preview_x, preview_y)
            if self._active_effect is not None:
                preview = (0, preview_x, preview_y, 1920, 1080)
                self._render_milestone_effect(preview)
        else:
            if self._effect_visible:
                self._hide_effect()
            self._hide()

        try:
            self._screenshots.tick(
                self._active_effect if self._effect_visible else None, game,
                # A 0% panel is intentionally hidden; text and the actual
                # effect remain mandatory visible windows for capture.
                ((self.panel_hwnd,) if self.panel_opacity > 0 else ())
                + (self.text_hwnd, self.effect_hwnd),
                load_config().get("effect_screenshot_enabled", False),
            )
        except Exception as error:
            print(f"[screenshot] optional capture failed: {type(error).__name__}: {error}")

        if self.debug:
            now = time.monotonic()
            if now - self._last_debug_at >= 1.0:
                self._last_debug_at = now
                print(
                    "[game-overlay] debug "
                    f"always_show={self.always_show} game={'yes' if game else 'no'} "
                    f"panel_hwnd=0x{self.panel_hwnd:x} text_hwnd=0x{self.text_hwnd:x} "
                    f"panel_visible={int(bool(user32.IsWindowVisible(self.panel_hwnd)))} "
                    f"text_visible={int(bool(user32.IsWindowVisible(self.text_hwnd)))} "
                    f"panel_alpha={self.panel_opacity}% "
                    f"server_pid={self._server_pid or 0} server_state={self._server_state} "
                    f"stats={self.last_stats['wins']}/{self.last_stats['losses']}"
                )

        # Lifecycle check is deliberately LAST: it never participates in the
        # known-good foreground/visibility decision above.
        if not self._poll_server_lifecycle():
            return
        self._publish_ready_heartbeat()
        self.root.after(self.poll_ms, self._tick)

    def run(self) -> None:
        try:
            self.root.mainloop()
        finally:
            self._effect_stop.set()
            try:
                self._screenshots.close()
            except Exception as error:
                print(f"[screenshot] cleanup failed: {error}")
            if not self._closing:
                self._finish_effect()

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Click-through AC6 stats HUD (no OBS / no injection)."
    )
    p.add_argument(
        "--process",
        default=configured_process(),
        help="target foreground executable (default: config.json value)",
    )
    p.add_argument(
        "--x", type=int, default=None,
        help="fixed left offset in pixels from the game client (default: responsive safe zone)",
    )
    p.add_argument(
        "--y", type=int, default=None,
        help="fixed top offset in pixels from the game client (default: responsive safe zone)",
    )
    p.add_argument(
        "--font-size",
        type=int,
        default=DEFAULT_FONT_SIZE,
        help="main font size (12..48)",
    )
    p.add_argument(
        "--poll-ms",
        type=int,
        default=DEFAULT_POLL_MS,
        help="refresh interval in milliseconds (100..2000)",
    )
    p.add_argument(
        "--panel-opacity",
        type=int,
        default=DEFAULT_PANEL_OPACITY,
        help=f"HUD panel opacity in percent (0..100; default: {DEFAULT_PANEL_OPACITY})",
    )
    p.add_argument(
        "--always-show",
        action="store_true",
        help="preview overlay even when the game is not foreground",
    )
    p.add_argument(
        "--debug",
        action="store_true",
        help="print foreground/HWND/visibility diagnostics once per second",
    )
    args = p.parse_args(argv)
    if not args.process.lower().endswith(".exe"):
        p.error("--process must be an .exe filename")
    if not 12 <= args.font_size <= 48:
        p.error("--font-size must be 12..48")
    if not 100 <= args.poll_ms <= 2000:
        p.error("--poll-ms must be 100..2000")
    if not 0 <= args.panel_opacity <= 100:
        p.error("--panel-opacity must be 0..100")
    if any(value is not None and not -5000 <= value <= 5000 for value in (args.x, args.y)):
        p.error("--x/--y must be -5000..5000")
    return args


def main(argv: list[str] | None = None) -> int:
    if os.name != "nt":
        print("[game-overlay] Windows only.", file=sys.stderr)
        return 2

    mutex = _acquire_single_instance_mutex()
    if mutex is False:
        print("[game-overlay] another overlay instance is already running")
        return 0

    _enable_dpi_awareness()
    remove_stale_overlay_runtime()
    args = parse_args(argv)
    print("[game-overlay] started")
    print(f"[game-overlay] target: {args.process}")
    print("[game-overlay] click-through / non-activating / no injection")
    print(f"[game-overlay] panel opacity: {args.panel_opacity}%")
    print("[game-overlay] lifecycle: follows server.py; rendering is not startup-gated")
    if args.always_show:
        print("[game-overlay] preview mode: ALWAYS SHOW enabled")
    else:
        print("[game-overlay] visible only while the target game is foreground")
    print("[game-overlay] Ctrl+C in this console closes the overlay")

    overlay = GameOverlay(
        process_name=args.process,
        x_offset=args.x,
        y_offset=args.y,
        font_size=args.font_size,
        poll_ms=args.poll_ms,
        panel_opacity=args.panel_opacity,
        always_show=args.always_show,
        debug=args.debug,
    )
    try:
        overlay.run()
    except KeyboardInterrupt:
        return 0
    finally:
        remove_owned_overlay_runtime()
        if mutex not in (None, False):
            try:
                kernel32.CloseHandle(mutex)
            except Exception:
                pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
