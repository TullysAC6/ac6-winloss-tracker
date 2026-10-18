"""Stdlib-only standalone bootstrap. No Tracker/native capture import before GO."""
import ctypes
from ctypes import wintypes
import json
import os
import sys
import time

from owned_worker import owned_entry


class Parent:
    def __init__(self, pid, birth):
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)]
        self.handle = self.kernel.OpenProcess(0x101000, False, pid)
        stamps = [wintypes.FILETIME() for _ in range(4)]
        self.valid = bool(self.handle and self.kernel.GetProcessTimes(
            self.handle, *(ctypes.byref(s) for s in stamps)))
        self.valid = self.valid and ((stamps[0].dwHighDateTime << 32) | stamps[0].dwLowDateTime) == birth
        self.expires = time.monotonic() + 5.0
    def is_alive(self):
        return bool(self.valid and time.monotonic() < self.expires
                    and self.kernel.WaitForSingleObject(self.handle, 0) == 258)
    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)


class Ready:
    def __init__(self):
        # This module imports only stdlib/ownership until GO; no WinApi/WGC/numpy.
        from lobby_capture import PipeReader
        self.reader = PipeReader(sys.stdin.buffer)
        self.payload = None
    def wait(self, seconds):
        self.payload = self.reader.read()
        if self.payload is None:
            time.sleep(seconds)
            return False
        if (set(self.payload) != {"go", "target", "deadline"}
                or self.payload["go"] is not True
                or not isinstance(self.payload["target"], dict)
                or type(self.payload["deadline"]) not in (float, int)
                or not time.monotonic() < self.payload["deadline"] <= time.monotonic() + 5.0):
            raise ValueError("invalid containment release")
        return True


def native_work(ready):
    from lobby_capture import capture_header
    response = capture_header(ready.payload["target"], ready.payload["deadline"])
    line = json.dumps(response, separators=(",", ":"))
    if len(line.encode()) <= 4096:
        print(line, flush=True)


def main():
    if os.name != "nt" or len(sys.argv) != 3:
        return
    owner = Parent(int(sys.argv[1]), int(sys.argv[2]))
    try:
        ready = Ready()
        print(json.dumps({"pid": os.getpid(), "launch_nonce": os.environ.get("AC6_LAUNCH_NONCE", "")}), flush=True)
        owned_entry(ready, native_work, (ready,), owner=owner)
    finally:
        owner.close()


if __name__ == "__main__":
    main()
