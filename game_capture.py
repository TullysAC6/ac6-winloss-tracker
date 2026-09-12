"""AC6-only WGC capture, isolated from counting and bounded on shutdown.

Only the detector thread owns GameCapture. The child owns the native capture
thread; its callback copies a small ROI into one replaceable slot. Request /
response IPC allows at most one outstanding frame, never a frame backlog.
"""
from __future__ import annotations

import multiprocessing as mp
import threading
import time

from owned_worker import KillOnCloseJob, owned_entry, stop_process

MAX_FRAME_AGE = 1.6


def native_frame_time(timespan, now):
    # WGC SystemRelativeTime is QPC in 100 ns units. CPython on Windows
    # uses the same QPC clock for monotonic(); callback delivery is NOT capture.
    captured = timespan / 10_000_000
    # DWM may stamp the next presentation (~one refresh ahead). Clamp a small
    # lead to arrival time so it cannot extend gameplay proof into the future.
    return min(captured, now) if -.05 <= now - captured <= MAX_FRAME_AGE else None


def result_region(client):
    return {
        "left": client["left"] + int(client["width"] * .20),
        "top": client["top"] + int(client["height"] * .43),
        "width": max(100, int(client["width"] * .60)),
        "height": max(40, int(client["height"] * .07)),
    }


def crop_geometry(target, width, height):
    client = target["client"]
    left, top, right, bottom = target["bounds"]
    # Borderless/fullscreen WGC frames equal the client; decorated windows
    # include their visible non-client frame. Unknown geometry fails closed.
    if (width, height) == (client["width"], client["height"]):
        left, top = client["left"], client["top"]
    elif (width, height) != (right - left, bottom - top):
        return None
    region = result_region(client)
    x, y = region["left"] - left, region["top"] - top
    w, h = region["width"], region["height"]
    if x < 0 or y < 0 or x + w > width or y + h > height:
        return None
    return x, y, w, h


def _capture_worker(connection, target):
    """Spawn entry; never imports server, opens stats, or launches an overlay."""
    control = None
    lock = threading.Lock()
    latest = None
    last_timestamp = None
    last_copy = 0.0
    closed = threading.Event()
    try:
        from result_detector import WinApi
        api = WinApi()
        from windows_capture import WindowsCapture
        if api.game_target() != target:
            return
        capture = WindowsCapture(window_hwnd=target["hwnd"], cursor_capture=False,
                                 draw_border=False, secondary_window=False,
                                 minimum_update_interval=100)

        @capture.event
        def on_frame_arrived(frame, capture_control):
            nonlocal latest, last_timestamp, last_copy
            now = time.monotonic()
            if closed.is_set():
                capture_control.stop()
                return
            captured = native_frame_time(frame.timespan, now)
            if captured is None:
                return
            # Unique native presentation times, not repeated polls of a cached
            # frame, provide confirmation hits. Throttle only the ROI copy.
            if ((last_timestamp is not None and frame.timespan <= last_timestamp)
                    or now - last_copy < .15):
                return
            geometry = crop_geometry(target, frame.width, frame.height)
            if geometry is None:
                return
            x, y, w, h = geometry
            raw = frame.frame_buffer[y:y+h, x:x+w, :].tobytes()
            if len(raw) != w * h * 4:
                return
            with lock:
                latest = (captured, frame.timespan, w, h, raw)
            last_timestamp, last_copy = frame.timespan, now

        @capture.event
        def on_closed():
            closed.set()

        control = capture.start_free_threaded()
        owner = mp.parent_process()
        while not closed.is_set() and owner is not None and owner.is_alive():
            if control.is_finished():
                break
            if not connection.poll(.1):
                continue
            if connection.recv() != "frame":
                break
            # Revalidate identity, minimization and geometry before releasing
            # any cached frame. Parent independently does the same on receipt.
            valid = api.game_target() == target
            with lock:
                sample, latest = latest, None
            connection.send(sample if valid else None)
    except (EOFError, BrokenPipeError, OSError):
        pass
    except Exception as error:
        print(f"[capture] WGC worker failed: {type(error).__name__}: {error}", flush=True)
        # The parent exposes unavailable capture and uses a guarded foreground
        # fallback. Native failures cannot kill the tracker process.
        pass
    finally:
        closed.set()
        connection.close()
        if control is not None:
            control.stop()


class GameCapture:
    MAX_FRAME_AGE = MAX_FRAME_AGE
    REQUEST_TIMEOUT = 5.0
    RETRY_SECONDS = 10.0

    def __init__(self, api, context=None):
        self.api = api
        self.context = context or mp.get_context("spawn")
        self.process = None
        self.job = None
        self.connection = None
        self.target = None
        self.pending_at = None
        self.retry_at = 0.0
        self.last_timestamp = None
        self.source = None
        self.discontinuity = True
        self.status = "AC6 capture unavailable"
        self.identity = None
        self.identity_changed = False
        self.captured_at = None
        self.last_frame_at = None

    def close(self):
        process, connection = self.process, self.connection
        self.process = self.connection = None
        self.pending_at = None
        self.target = None
        self.last_timestamp = None
        self.last_frame_at = None
        self.source = None
        if connection is not None:
            connection.close()
        if process is not None:
            process.join(timeout=.3)
        if self.job is not None:
            self.job.close()
            self.job = None
        if process is not None:
            try:
                stop_process(process)
            except Exception:
                self.process = process
                raise

    def _start(self, target):
        parent, child = self.context.Pipe()
        ready = self.context.Event()
        process = self.context.Process(target=owned_entry, args=(ready, _capture_worker, (child, target)),
                                       name="ac6-window-capture", daemon=True)
        try:
            process.start()
        except Exception:
            parent.close()
            child.close()
            raise
        self.process, self.connection, self.target = process, parent, target
        try:
            self.job = KillOnCloseJob(process.pid)
            self.last_frame_at = time.monotonic()
            ready.set()
        except Exception:
            self.close()
            raise
        finally:
            child.close()

    def _mark_source(self, source):
        self.discontinuity = self.discontinuity or self.source != source
        self.source = source

    def grab(self, desktop):
        from mss.screenshot import ScreenShot
        now = time.monotonic()
        target = self.api.game_target()
        self.discontinuity = False
        self.identity_changed = False
        if target is not None:
            identity = (target["hwnd"], target["pid"])
            self.identity_changed = self.identity is not None and identity != self.identity
            self.identity = identity
        if target != self.target and self.process is not None:
            self.close()
            self.retry_at = 0.0
        if target is None:
            self.status = "AC6 window unavailable or minimized"
            return None
        try:
            if self.process is None and now >= self.retry_at:
                self._start(target)
            if self.process is not None:
                if not self.process.is_alive():
                    raise RuntimeError("WGC worker stopped")
                if self.pending_at is not None and now - self.pending_at > self.REQUEST_TIMEOUT:
                    raise RuntimeError("WGC worker timed out")
                sample = None
                if self.pending_at is not None and self.connection.poll():
                    sample = self.connection.recv()
                    self.pending_at = None
                if self.pending_at is None:
                    self.connection.send("frame")
                    self.pending_at = now
                now = time.monotonic()
                if sample is not None:
                    captured, timestamp, width, height, raw = sample
                    expected = result_region(target["client"])
                    if (0 <= now - captured <= self.MAX_FRAME_AGE
                            and (self.last_timestamp is None or timestamp > self.last_timestamp)
                            and (width, height) == (expected["width"], expected["height"])
                            and len(raw) == width * height * 4
                            and self.api.game_target() == target):
                        self.last_timestamp = timestamp
                        self.last_frame_at = now
                        self.captured_at = captured
                        self._mark_source((target["hwnd"], target["pid"], "wgc"))
                        self.status = "WGC window capture"
                        return ScreenShot.from_size(bytearray(raw), width, height)
                    self.discontinuity = True
                    self.status = "WGC frame rejected: stale, repeated, or target/geometry mismatch"
                if self.last_frame_at is not None and now - self.last_frame_at > self.REQUEST_TIMEOUT:
                    raise RuntimeError("WGC produced no fresh frame before timeout")
        except Exception as error:
            self.close()
            self.retry_at = now + self.RETRY_SECONDS
            self.status = f"WGC unavailable: {type(error).__name__}: {error}"
            print(f"[capture] {self.status}; retry in {self.RETRY_SECONDS}s", flush=True)

        # Desktop pixels are permitted ONLY with AC6 foreground, identical
        # identity/geometry before and after, and no overlapping window in ROI.
        region = result_region(target["client"])
        if self.api.region_unobscured(target["hwnd"], region):
            shot = desktop.grab(region)
            if self.api.game_target() == target and self.api.region_unobscured(target["hwnd"], region):
                self._mark_source((target["hwnd"], target["pid"], "desktop",
                                   tuple(target["client"][k] for k in ("left", "top", "width", "height"))))
                self.captured_at = now
                self.status = "guarded foreground desktop capture"
                return shot
        return None
