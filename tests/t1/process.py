"""Ownership of T1 worker processes through a Windows job object.

Every worker is assigned to its own kill-on-close job. The job, not ancestry,
answers whether anything the worker started is still alive, and closing or
terminating it reaps the whole tree even if the runner dies.
"""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
JOB_BASIC_ACCOUNTING = 1
JOB_PROCESS_ID_LIST = 3
JOB_EXTENDED_LIMITS = 9
PROCESS_SET_QUOTA = 0x0100
PROCESS_TERMINATE = 0x0001
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
MAX_LISTED_PROCESSES = 64


class _ProcessIds(ctypes.Structure):
    _fields_ = [("assigned", wintypes.DWORD), ("listed", wintypes.DWORD),
                ("ids", ctypes.c_size_t * MAX_LISTED_PROCESSES)]


class _Basic(ctypes.Structure):
    _fields_ = [("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
                ("flags", wintypes.DWORD), ("minimum", ctypes.c_size_t), ("maximum", ctypes.c_size_t),
                ("active_limit", wintypes.DWORD), ("affinity", ctypes.c_size_t),
                ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD)]


class _IO(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in
                ("read_ops", "write_ops", "other_ops", "read", "write", "other")]


class _Extended(ctypes.Structure):
    _fields_ = [("basic", _Basic), ("io", _IO), ("process_memory", ctypes.c_size_t),
                ("job_memory", ctypes.c_size_t), ("peak_process", ctypes.c_size_t),
                ("peak_job", ctypes.c_size_t)]


class _Accounting(ctypes.Structure):
    _fields_ = [("user", ctypes.c_longlong), ("kernel", ctypes.c_longlong),
                ("period_user", ctypes.c_longlong), ("period_kernel", ctypes.c_longlong),
                ("page_faults", wintypes.DWORD), ("total_processes", wintypes.DWORD),
                ("active_processes", wintypes.DWORD), ("terminated_processes", wintypes.DWORD)]


class WorkerJob:
    def __init__(self):
        if os.name != "nt":
            raise OSError("T1 worker ownership requires Windows job objects")
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                                     wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                      ctypes.POINTER(wintypes.DWORD)]
        kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
        self.kernel = kernel
        self.handle = kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = _Extended()
        limits.basic.flags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel.SetInformationJobObject(self.handle, JOB_EXTENDED_LIMITS, ctypes.byref(limits),
                                              ctypes.sizeof(limits)):
            error = ctypes.get_last_error()
            self.close()
            raise ctypes.WinError(error)

    def assign(self, pid):
        process = self.kernel.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, pid)
        if not process:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not self.kernel.AssignProcessToJobObject(self.handle, process):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            self.kernel.CloseHandle(process)

    def _query(self, kind, structure):
        returned = wintypes.DWORD()
        if not self.kernel.QueryInformationJobObject(self.handle, kind, ctypes.byref(structure),
                                                     ctypes.sizeof(structure), ctypes.byref(returned)):
            raise ctypes.WinError(ctypes.get_last_error())
        return structure

    def active_processes(self):
        return int(self._query(JOB_BASIC_ACCOUNTING, _Accounting()).active_processes)

    def total_processes(self):
        return int(self._query(JOB_BASIC_ACCOUNTING, _Accounting()).total_processes)

    def peak_process_memory(self):
        return int(self._query(JOB_EXTENDED_LIMITS, _Extended()).peak_process)

    def members(self):
        """(pid, image name) for each process still in the job, for leak reports."""
        listing = self._query(JOB_PROCESS_ID_LIST, _ProcessIds())
        result = []
        for pid in listing.ids[:min(listing.listed, MAX_LISTED_PROCESSES)]:
            name = None
            handle = self.kernel.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
            if handle:
                try:
                    buffer = ctypes.create_unicode_buffer(1024)
                    size = wintypes.DWORD(len(buffer))
                    if self.kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                        name = os.path.basename(buffer.value)
                finally:
                    self.kernel.CloseHandle(handle)
            result.append({"pid": int(pid), "image": name})
        return result

    def wait_empty(self, grace_seconds):
        """Poll until no process remains in the job or the grace period ends."""
        import time
        deadline = time.monotonic() + grace_seconds
        while True:
            active = self.active_processes()
            if active == 0 or time.monotonic() >= deadline:
                return active
            time.sleep(0.02)

    def terminate(self):
        if self.handle and not self.kernel.TerminateJobObject(self.handle, 1):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None
