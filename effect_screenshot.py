"""Save the visible desktop composition, never synthesize a game/overlay image."""
from __future__ import annotations

import ctypes
import hashlib
import multiprocessing as mp
import os
import time
import uuid
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

from owned_worker import KillOnCloseJob, owned_entry, stop_process


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
        return
    client = target["client"]
    if (client["left"], client["top"], client["width"], client["height"]) != tuple(game[1:]):
        return

    def visible():
        return (time.monotonic() < deadline and api.game_target() == target
                and all(api.user32.IsWindowVisible(hwnd) for hwnd in allowed)
                and api.region_unobscured(target["hwnd"], client, allowed))

    if not visible():
        return
    with mss.mss() as desktop:
        virtual = desktop.monitors[0]
        if (client["left"] < virtual["left"] or client["top"] < virtual["top"]
                or client["left"] + client["width"] > virtual["left"] + virtual["width"]
                or client["top"] + client["height"] > virtual["top"] + virtual["height"]):
            return
        shot = desktop.grab(client)  # Windows MSS includes layered windows (CAPTUREBLT).
        if not visible():
            return
        image = Image.frombytes("RGB", shot.size, shot.rgb)
    # A black/unavailable exclusive-fullscreen surface is not a useful screenshot.
    if not contains_effect_banner(image, int(effect["milestone"])):
        print("[screenshot] skipped: overlay banner is not present in desktop capture", flush=True)
        return
    destination = desktop_directory() / screenshot_name(effect)
    if save_once(image, destination):
        print(f"[screenshot] saved: {destination}", flush=True)


def _screenshot_worker(effect, game, allowed, deadline):
    try:
        _save_visible_effect(effect, game, allowed, deadline)
    except Exception as error:
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
        except Exception:
            if process.pid is not None:
                self.close()
            else:
                self.process = None
            raise
