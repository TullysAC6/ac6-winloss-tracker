"""Save the visible desktop composition, never synthesize a game/overlay image."""
from __future__ import annotations

import ctypes
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import multiprocessing as mp
import os
import time
import uuid
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

from owned_worker import KillOnCloseJob, owned_entry, stop_process


def _record(effect, status, **details):
    """Optional bounded log independent of pythonw/spawn standard handles.

    Only event metadata is recorded, never desktop pixels or window titles.
    Logging failures must not affect capture, counting, or worker cleanup.
    """
    try:
        from app_paths import diagnostics_dir
        message = json.dumps({"ts": time.time(), "pid": os.getpid(),
                              "effect_id": effect.get("effect_id"),
                              "milestone": effect.get("milestone"),
                              "status": status, **details}, ensure_ascii=False)
        handler = RotatingFileHandler(diagnostics_dir() / "effect-screenshot.jsonl",
                                      maxBytes=256 * 1024, backupCount=1, encoding="utf-8")
        try:
            handler.handle(logging.LogRecord("screenshot", logging.INFO, "", 0, message, (), None))
        finally:
            handler.close()
    except Exception:
        pass


def _blocking_window(api, game_hwnd, client, allowed):
    """Explain an already rejected capture; never changes the safety decision."""
    from result_detector import RECT
    found = {}
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def inspect(hwnd, _):
        try:
            if int(hwnd) == game_hwnd:
                return False
            # Same filter as region_unobscured(), so the reported blocker is
            # the window that actually rejected the capture.
            if (int(hwnd) in allowed or not api.user32.IsWindowVisible(hwnd)
                    or api.user32.IsIconic(hwnd) or api.is_cloaked(hwnd)):
                return True
            rect = RECT()
            if not api.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return False
            if (rect.left < client["left"] + client["width"] and rect.right > client["left"]
                    and rect.top < client["top"] + client["height"] and rect.bottom > client["top"]):
                process, _ = api.process_and_client(hwnd)
                found.update(hwnd=int(hwnd), process=process,
                             rect=[rect.left, rect.top, rect.right, rect.bottom])
                return False
            return True
        except Exception:
            return False
    try:
        api.user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
        api.user32.EnumWindows(callback_type(inspect), 0)
    except Exception:
        pass
    return found


def desktop_directory():
    # Known Folder API follows OneDrive and user-redirected Desktop locations.
    folder_id = (ctypes.c_ubyte * 16).from_buffer_copy(
        uuid.UUID("b4bfcc3a-db2c-424c-b029-7fe99a87c641").bytes_le)
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    ole = ctypes.WinDLL("ole32")
    shell.SHGetKnownFolderPath.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.HANDLE,
                                         ctypes.POINTER(ctypes.c_void_p)]
    shell.SHGetKnownFolderPath.restype = ctypes.c_long
    ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    pointer = ctypes.c_void_p()
    try:
        if shell.SHGetKnownFolderPath(ctypes.byref(folder_id), 0, None, ctypes.byref(pointer)) != 0:
            raise OSError("Windows Desktop folder could not be resolved")
        return Path(ctypes.wstring_at(pointer))
    finally:
        if pointer.value:
            ole.CoTaskMemFree(pointer)


def screenshot_name(effect):
    stamp = datetime.fromtimestamp(effect["created_at_ms"] / 1000).strftime("%Y-%m-%d_%H-%M-%S")
    identity = hashlib.sha256(effect["effect_id"].encode("utf-8")).hexdigest()[:20]
    return f"AC6_{stamp}_{int(effect['milestone']):02d}-WIN-STREAK_{identity}.png"


def contains_effect_banner(image, milestone):
    """Require actual compositor pixels from the opaque Tk banner.

    IsWindowVisible alone cannot prove a layered window appears over an
    exclusive-fullscreen swap chain. Verify its flat fill and top border;
    fail closed for unavailable surfaces/HDR color transforms instead of
    manufacturing an overlay or saving an unrelated desktop image.
    """
    width, height = image.size
    effect_width = max(600, min(width, 1000))
    x = (width - effect_width) // 2
    y = int(height * .62) - 120
    fill = (33, 20, 46) if milestone >= 30 else (43, 33, 8)
    borders = {(255, 216, 74)} if milestone < 30 else {(214, 140, 255)}
    if milestone == 50:
        borders.add((255, 216, 74))
    try:
        # Far from banner text, keyed transparency and antialiased glyphs.
        for offset in (85, 95, 105):
            if image.getpixel((x + offset, y + 75)) != fill:
                return False
            if not any(image.getpixel((x + offset, y + row)) in borders for row in (64, 65, 66)):
                return False
    except IndexError:
        return False
    return True


def save_once(image, destination):
    # An exclusive pending file reserves the event across process restarts.
    # Atomic promotion keeps a killed/timed-out encoder from exposing a partial
    # PNG. A hard kill may leave a .pending file, which never triggers a retry.
    pending = destination.with_name("." + destination.name + ".pending")
    if destination.exists():
        return False
    try:
        stream = pending.open("xb")
    except FileExistsError:
        return False
    try:
        with stream:
            if destination.exists():
                return False
            image.save(stream, format="PNG")
        os.replace(pending, destination)
    finally:
        pending.unlink(missing_ok=True)
    return True


def _save_visible_effect(effect, game, allowed, deadline):
    from PIL import Image
    import mss
    from result_detector import WinApi
    api = WinApi()
    target = api.game_target()
    if target is None or target["hwnd"] != game[0]:
        _record(effect, "skipped", reason="target_changed")
        return
    client = target["client"]
    if (client["left"], client["top"], client["width"], client["height"]) != tuple(game[1:]):
        _record(effect, "skipped", reason="client_changed")
        return

    def visible(phase):
        reason, details = None, {}
        if time.monotonic() >= deadline:
            reason = "deadline"
        elif api.game_target() != target:
            reason = "target_changed"
        elif not all(api.user32.IsWindowVisible(hwnd) for hwnd in allowed):
            reason = "overlay_hidden"
        elif not api.region_unobscured(target["hwnd"], client, allowed):
            if int(api.user32.GetForegroundWindow() or 0) != target["hwnd"]:
                reason = "not_foreground"
            else:
                reason = "occluded"
                details["blocker"] = _blocking_window(api, target["hwnd"], client, allowed)
        if reason:
            _record(effect, "skipped", reason=reason, phase=phase, **details)
            return False
        return True

    if not visible("before_capture"):
        return
    with mss.mss() as desktop:
        virtual = desktop.monitors[0]
        if (client["left"] < virtual["left"] or client["top"] < virtual["top"]
                or client["left"] + client["width"] > virtual["left"] + virtual["width"]
                or client["top"] + client["height"] > virtual["top"] + virtual["height"]):
            _record(effect, "skipped", reason="outside_desktop")
            return
        shot = desktop.grab(client)  # Windows MSS includes layered windows (CAPTUREBLT).
        if not visible("after_capture"):
            return
        image = Image.frombytes("RGB", shot.size, shot.rgb)
    # A black/unavailable exclusive-fullscreen surface is not a useful screenshot.
    if not contains_effect_banner(image, int(effect["milestone"])):
        _record(effect, "skipped", reason="banner_pixels_missing")
        print("[screenshot] skipped: overlay banner is not present in desktop capture", flush=True)
        return
    destination = desktop_directory() / screenshot_name(effect)
    if save_once(image, destination):
        _record(effect, "saved", filename=destination.name)
        print(f"[screenshot] saved: {destination}", flush=True)
    else:
        _record(effect, "skipped", reason="duplicate_or_pending")


def _screenshot_worker(effect, game, allowed, deadline):
    try:
        _record(effect, "worker_started")
        _save_visible_effect(effect, game, allowed, deadline)
    except Exception as error:
        _record(effect, "failed", error_type=type(error).__name__, error=str(error))
        print(f"[screenshot] save failed: {type(error).__name__}: {error}", flush=True)


class EffectScreenshots:
    """One optional worker, owned exclusively by the Overlay Tk thread."""
    def __init__(self, context=None):
        self.context = context or mp.get_context("spawn")
        self.process = None
        self.job = None
        self.worker_started = 0.0
        self.worker_effect_id = None
        self.visible_key = None
        self.visible_since = 0.0

    def close(self):
        if self.job is not None:
            self.job.close()
            self.job = None
        if self.process is not None:
            stop_process(self.process)
            self.process = None

    def tick(self, effect, game, allowed, enabled):
        now = time.monotonic()
        if effect and not effect.get("screenshot_observed"):
            effect["screenshot_observed"] = True
            _record(effect, "observed", enabled=bool(enabled), game_present=bool(game))
        if self.process is not None:
            if (not self.process.is_alive() or now - self.worker_started > 8.0 or not enabled
                    or not effect or effect["effect_id"] != self.worker_effect_id):
                self.close()
        key = (effect["effect_id"], tuple(game), effect.get("render_key")) if effect and game else None
        if key != self.visible_key:
            self.visible_key, self.visible_since = key, now
        if not enabled or not key or effect.get("screenshot_attempted") or self.process is not None:
            return
        elapsed = now - effect["started"]
        # Wait a full tick for Tk/DWM composition; avoid 50-win white/black flash.
        if now - self.visible_since < .25 or elapsed < (2.0 if effect["milestone"] == 50 else .5):
            return
        if elapsed >= effect["duration"] - .3:
            return
        # Set before spawn/save: errors can never enqueue the same event again.
        effect["screenshot_attempted"] = True
        _record(effect, "spawn_requested")
        ready = self.context.Event()
        arguments = (dict(effect), tuple(game), tuple(allowed), effect["started"] + effect["duration"] - .1)
        process = self.context.Process(target=owned_entry,
            args=(ready, _screenshot_worker, arguments),
            name="ac6-effect-screenshot", daemon=True)
        self.process = process
        try:
            process.start()
            self.job = KillOnCloseJob(process.pid)
            ready.set()
            self.worker_started = now
            self.worker_effect_id = effect["effect_id"]
        except Exception as error:
            _record(effect, "spawn_failed", error_type=type(error).__name__)
            if process.pid is not None:
                self.close()
            else:
                self.process = None
            raise
