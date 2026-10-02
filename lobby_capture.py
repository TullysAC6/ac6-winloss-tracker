"""Bounded optional WGC header job. No server import, DB access or image archive.

Reuse the existing owned_entry/Job/stop pattern with python_spawn's actual
interpreter handle. The standalone bootstrap avoids multiprocessing re-importing
server.py and initializing Tracker data before the containment gate.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import time

from owned_worker import KillOnCloseJob, stop_process
from python_spawn import spawn_python, matches_launch

ATTEMPT_SECONDS = 5.0
CAPTURE_SECONDS = 2.0
MAX_MESSAGE = 4096


def process_birth(pid):
    if os.name != "nt":
        return None
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    try:
        stamps = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(s) for s in stamps)):
            return None
        return (stamps[0].dwHighDateTime << 32) | stamps[0].dwLowDateTime
    finally:
        kernel.CloseHandle(handle)


def current_target(*, foreground=False):
    if os.name != "nt":
        return None
    from result_detector import WinApi
    api = WinApi()
    target = api.game_target()
    if (target is None or target["client"]["width"] != 1920
            or target["client"]["height"] != 1080
            or (foreground and int(api.user32.GetForegroundWindow() or 0) != target["hwnd"])):
        return None
    birth = process_birth(target["pid"])
    if birth is None:
        return None
    return {**target, "bounds": list(target["bounds"]), "birth": birth}


def header_geometry(target, width, height):
    """Absolute accepted client ROI translated into a native WGC frame, never scaled."""
    client = target["client"]
    if (client["width"], client["height"]) != (1920, 1080):
        return None
    left, top, right, bottom = target["bounds"]
    if (width, height) == (1920, 1080):
        left, top = client["left"], client["top"]
    elif (width, height) != (right - left, bottom - top):
        return None
    x, y = client["left"] - left + 80, client["top"] - top + 40
    if x < 0 or y < 0 or x + 480 > width or y + 60 > height:
        return None
    return x, y, 480, 60


class PipeReader:
    """Peek anonymous Windows pipe before reading. Partial/overlarge IPC stays bounded."""
    def __init__(self, stream):
        import msvcrt
        self.stream = stream
        self.handle = msvcrt.get_osfhandle(stream.fileno())
        self.buffer = bytearray()
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.PeekNamedPipe.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                                            ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]

    def read(self):
        available = wintypes.DWORD()
        if not self.kernel.PeekNamedPipe(self.handle, None, 0, None, ctypes.byref(available), None):
            raise EOFError("closed worker pipe")
        if available.value:
            self.buffer.extend(os.read(self.stream.fileno(), min(available.value, MAX_MESSAGE + 1)))
        if len(self.buffer) > MAX_MESSAGE:
            raise ValueError("oversized worker message")
        if b"\n" not in self.buffer:
            return None
        line, _, rest = self.buffer.partition(b"\n")
        self.buffer = bytearray(rest)
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError("invalid worker message")
        return value


class ProcessAdapter:
    """Use stop_process's exact bounded escalation on an owned Popen handle."""
    def __init__(self, process):
        self.process = process
    def join(self, timeout):
        try:
            self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            pass
    def is_alive(self):
        return self.process.poll() is None
    def terminate(self):
        self.process.terminate()
    def kill(self):
        self.process.kill()
    def close(self):
        for stream in (self.process.stdin, self.process.stdout):
            if stream:
                stream.close()


# Keep ownership on an unreapable worker; no later acquisition may replace it.
_unreaped = []


def acquire(target, cancel, *, script=None):
    if os.name != "nt" or _unreaped or cancel.is_set():
        return {"status": "failed", "cleaned": not _unreaped}
    deadline = time.monotonic() + ATTEMPT_SECONDS
    process = job = None
    response = {"status": "failed"}
    cleaned = False
    completed_normally = False
    try:
        script = Path(script) if script else Path(__file__).with_name("lobby_worker.py")
        birth = process_birth(os.getpid())
        process = spawn_python([script, str(os.getpid()), str(birth)], windowless=False,
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               bufsize=0, creationflags=subprocess.CREATE_NO_WINDOW)
        adapter = ProcessAdapter(process)
        # Actual interpreter PID is owned by python_spawn; assign before GO.
        job = KillOnCloseJob(process.pid)
        reader = PipeReader(process.stdout)
        hello = None
        while time.monotonic() < deadline and not cancel.is_set():
            hello = reader.read()
            if hello is not None:
                break
            cancel.wait(.01)
        if not matches_launch(hello, process):
            return response
        payload = json.dumps({"go": True, "target": target, "deadline": deadline},
                             separators=(",", ":")).encode() + b"\n"
        if len(payload) > MAX_MESSAGE or cancel.is_set():
            return response
        process.stdin.write(payload)
        process.stdin.flush()
        while time.monotonic() < deadline and not cancel.is_set():
            message = reader.read()
            if message is not None:
                allowed = {"status", "match_type", "match_format", "version", "source", "captured_at"}
                if set(message) <= allowed:
                    response = message
                break
            cancel.wait(.01)
    except Exception:
        pass
    finally:
        if process is not None:
            # EOF requests graceful native stop. Whole tree kill precedes the
            # final direct-handle escalation, including any descendants.
            try:
                process.stdin.close()
                adapter.join(.3)
                completed_normally = process.poll() == 0
            finally:
                if job:
                    job.close()
                try:
                    stop_process(adapter)
                    cleaned = not adapter.is_alive()
                    # A structured response is evidence only when its owned
                    # producer also completed normally. Dead after crash/kill
                    # is cleanup success, not recognition success.
                    if not cleaned or not completed_normally:
                        response.clear()
                        response["status"] = "failed"
                except Exception:
                    _unreaped.append((process, job))
        response["cleaned"] = cleaned
    return response


def capture_header(target, deadline):
    """Called only after owned_entry's ready gate. No pixels leave this child."""
    import threading
    from game_capture import native_frame_time
    from windows_capture import WindowsCapture
    import match_header
    if current_target(foreground=True) != target:
        return {"status": "failed"}
    stop = threading.Event()
    lock = threading.Lock()
    latest = None
    previous = None
    count = 0
    broken = False
    control = None
    limit = min(deadline, time.monotonic() + CAPTURE_SECONDS)
    capture = WindowsCapture(window_hwnd=target["hwnd"], cursor_capture=False,
                             draw_border=False, secondary_window=False, minimum_update_interval=100)

    @capture.event
    def on_frame_arrived(frame, capture_control):
        nonlocal latest, previous, count, broken
        with lock:
            if stop.is_set() or count >= 3 or time.monotonic() >= limit:
                return
            stamp = native_frame_time(frame.timespan, time.monotonic())
            geometry = header_geometry(target, frame.width, frame.height)
            if stamp is None or geometry is None or (previous is not None and frame.timespan <= previous):
                broken = True
                stop.set()
                return
            previous = frame.timespan
            x, y, w, h = geometry
            roi = frame.frame_buffer[y:y+h, x:x+w, :].copy()
            count += 1
            latest = (stamp, roi)

    @capture.event
    def on_closed():
        nonlocal broken
        broken = True
        stop.set()

    try:
        control = capture.start_free_threaded()
        while not stop.is_set() and time.monotonic() < limit:
            if current_target(foreground=True) != target or control.is_finished():
                return {"status": "failed"}
            with lock:
                sample, latest = latest, None
            if sample is not None:
                stamp, roi = sample
                if native_frame_time(int(stamp * 10_000_000), time.monotonic()) is None:
                    return {"status": "failed"}
                result = match_header.recognize_header(roi)
                if result.status == "recognized" and not broken and current_target(foreground=True) == target:
                    return {"status": result.status, "match_type": result.match_type,
                            "match_format": result.match_format, "version": result.version,
                            "source": result.source, "captured_at": stamp}
            stop.wait(.01)
        return {"status": "failed"}
    finally:
        stop.set()
        if control is not None:
            control.stop()  # Parent kills a hung native stop; output is not adopted before reap.
