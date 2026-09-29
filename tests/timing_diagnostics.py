"""Bounded timing and process evidence for harness timeouts (maintenance issue #59).

Two 15-second CI timeouts on unchanged code could not be explained from their logs.
This module records where the time went when the next one happens. It is
diagnostics only:

* ``run_watched`` keeps ``subprocess.run(argv, check=True, timeout=...)`` verdicts:
  the same watchdog, measured from the same point, the same exceptions. It adds a
  stage trace, and on the timeout path bounded evidence captured before the kill.
* Every other function is bounded, never raises and prints no environment, token
  or file content except the trace records a child chose to write.
* A successful run prints one summary line; a full report is printed only on a
  timeout or a failed exit.

    python tests/timing_diagnostics.py snapshot --label NAME [--pid PID ...]

prints a bounded machine/process snapshot for a failure path in another harness.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

TRACE_ENV = "AC6_DIAG_TRACE"
MAX_TRACE_BYTES = 16384
MAX_TRACE_RECORDS = 64
POST_KILL_WAIT_SECONDS = 5.0
PROBE_TIMEOUT_SECONDS = 5.0
SAMPLE_SECONDS = 0.5
TOP_PROCESSES = 5
MAX_PROCESS_ENTRIES = 4096
ENUMERATION_BUDGET_SECONDS = 1.5
PREFIX = "[diag]"

_TOKEN = re.compile(r"[^A-Za-z0-9_.:+-]")
_TRACE_NUMBERS = ("t_ms", "uptime_ms", "wall_ms", "pid", "n", "code", "bytes")
_TRACE_TOKENS = ("node", "arch", "error")


def token(value, limit=40):
    """A short, single-line, character-whitelisted rendering of an untrusted value."""
    text = _TOKEN.sub("_", str(value))
    return text[:limit] if text else "-"


def read_trace(path):
    """Parse the child's JSON-lines trace; bounded and whitelisted, never raises."""
    records, malformed = [], 0
    try:
        with open(path, "rb") as handle:
            data = handle.read(MAX_TRACE_BYTES + 1)
    except OSError:
        return records, malformed, False
    truncated = len(data) > MAX_TRACE_BYTES
    for line in data[:MAX_TRACE_BYTES].splitlines():
        if len(records) >= MAX_TRACE_RECORDS:
            truncated = True
            break
        try:
            raw = json.loads(line.decode("utf-8"))
            stage = token(raw["stage"])
        except (ValueError, KeyError, TypeError, UnicodeDecodeError, AttributeError):
            malformed += 1
            continue
        record = {"stage": stage}
        for key in _TRACE_NUMBERS:
            value = raw.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                record[key] = float(value)
        for key in _TRACE_TOKENS:
            if key in raw:
                record[key] = token(raw[key])
        records.append(record)
    return records, malformed, truncated


def stage_summary(records, launch_wall_ms, expected=()):
    """One line of reached stages as offsets from the child's own entry stamp."""
    entry = next((r for r in records if r["stage"] == "entry"), None)
    parts = []
    for record in records:
        name = record["stage"] + (f"{int(record['n'])}" if "n" in record else "")
        if entry is not None and "t_ms" in record and "t_ms" in entry:
            name += f"@+{record['t_ms'] - entry['t_ms']:.0f}ms"
        parts.append(name)
    reached = {r["stage"] for r in records}
    missing = [stage for stage in expected if stage not in reached]
    details = []
    if entry is not None:
        if "wall_ms" in entry:
            details.append(f"launch->entry={entry['wall_ms'] - launch_wall_ms:.0f}ms")
        if "uptime_ms" in entry:
            details.append(f"child_boot={entry['uptime_ms']:.0f}ms")
        for key in ("node", "arch"):
            if key in entry:
                details.append(f"{key}={entry[key]}")
    return {
        "reached": ",".join(parts) or "none",
        "last": records[-1]["stage"] if records else "none",
        "missing": ",".join(missing) or "none",
        "details": " ".join(details) or "entry-not-reached",
    }


# --------------------------------------------------------------- Windows process facts
def _kernel32():
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    handle, dword = wintypes.HANDLE, wintypes.DWORD
    lp_filetime = ctypes.POINTER(wintypes.FILETIME)
    k.OpenProcess.argtypes, k.OpenProcess.restype = [dword, wintypes.BOOL, dword], handle
    k.CloseHandle.argtypes, k.CloseHandle.restype = [handle], wintypes.BOOL
    k.GetExitCodeProcess.argtypes = [handle, ctypes.POINTER(dword)]
    k.WaitForSingleObject.argtypes, k.WaitForSingleObject.restype = [handle, dword], dword
    k.GetProcessTimes.argtypes = [handle, lp_filetime, lp_filetime, lp_filetime, lp_filetime]
    k.GetSystemTimes.argtypes = [lp_filetime, lp_filetime, lp_filetime]
    k.GetProcessHandleCount.argtypes = [handle, ctypes.POINTER(dword)]
    k.GetTickCount64.restype = ctypes.c_ulonglong
    k.CreateToolhelp32Snapshot.argtypes, k.CreateToolhelp32Snapshot.restype = [dword, dword], handle
    # Structure arguments go as addresses so every HANDLE keeps its full width.
    k.K32GetProcessMemoryInfo.argtypes = [handle, ctypes.c_void_p, dword]
    k.GetProcessIoCounters.argtypes = [handle, ctypes.c_void_p]
    k.GlobalMemoryStatusEx.argtypes = [ctypes.c_void_p]
    k.Process32FirstW.argtypes = [handle, ctypes.c_void_p]
    k.Process32NextW.argtypes = [handle, ctypes.c_void_p]
    return k


class _Structs:
    """ctypes layouts, built lazily so importing this module never touches Windows APIs."""
    _built = None

    @classmethod
    def get(cls):
        if cls._built is None:
            import ctypes
            from ctypes import wintypes

            class MemoryCounters(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
                    (name, ctypes.c_size_t) for name in (
                        "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                        "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage",
                        "PagefileUsage", "PeakPagefileUsage", "PrivateUsage")]

            class IoCounters(ctypes.Structure):
                _fields_ = [(name, ctypes.c_ulonglong) for name in (
                    "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                    "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

            class MemoryStatus(ctypes.Structure):
                _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD)] + [
                    (name, ctypes.c_ulonglong) for name in (
                        "ullTotalPhys", "ullAvailPhys", "ullTotalPageFile", "ullAvailPageFile",
                        "ullTotalVirtual", "ullAvailVirtual", "ullAvailExtendedVirtual")]

            class ProcessEntry(ctypes.Structure):
                _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                            ("th32ProcessID", wintypes.DWORD),
                            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                            ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                            ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
                            ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]

            cls._built = (MemoryCounters, IoCounters, MemoryStatus, ProcessEntry)
        return cls._built


def _filetime(value):
    return (value.dwHighDateTime << 32) | value.dwLowDateTime


def _process_cpu_100ns(kernel, pid):
    """(kernel+user CPU in 100 ns units, creation FILETIME) or None."""
    import ctypes
    from ctypes import wintypes

    handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *[ctypes.byref(t) for t in times]):
            return None
        return _filetime(times[2]) + _filetime(times[3]), _filetime(times[0])
    finally:
        kernel.CloseHandle(handle)


def process_state(pid):
    """Bounded facts about one process: state, CPU, memory, handles, I/O. Never raises."""
    facts = {"pid": int(pid)}
    kernel = _kernel32()
    if kernel is None:
        facts["supported"] = False
        return facts
    import ctypes
    from ctypes import wintypes

    try:
        memory_counters, io_counters, _, _ = _Structs.get()
        access = 0x1000 | 0x0010 | 0x00100000  # QUERY_LIMITED | VM_READ | SYNCHRONIZE
        handle = kernel.OpenProcess(access, False, int(pid))
        if not handle:
            handle = kernel.OpenProcess(0x1000 | 0x00100000, False, int(pid))
        if not handle:
            error = ctypes.get_last_error()
            facts["state"] = "gone" if error == 87 else f"unopenable(error={error})"
            return facts
        try:
            signalled = kernel.WaitForSingleObject(handle, 0) == 0
            code = wintypes.DWORD()
            if kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
                facts["state"] = f"exited(rc={code.value})" if signalled else "running"
            else:
                facts["state"] = "exited" if signalled else "running"
            times = [wintypes.FILETIME() for _ in range(4)]
            if kernel.GetProcessTimes(handle, *[ctypes.byref(t) for t in times]):
                facts["created_wall_ms"] = (_filetime(times[0]) - 116444736000000000) / 10000.0
                facts["cpu_s"] = round((_filetime(times[2]) + _filetime(times[3])) / 1e7, 3)
                facts["kernel_s"] = round(_filetime(times[2]) / 1e7, 3)
            counters = memory_counters()
            counters.cb = ctypes.sizeof(counters)
            if kernel.K32GetProcessMemoryInfo(handle, ctypes.addressof(counters), counters.cb):
                facts["ws_mb"] = round(counters.WorkingSetSize / 1048576, 1)
                facts["private_mb"] = round(counters.PrivateUsage / 1048576, 1)
                facts["page_faults"] = int(counters.PageFaultCount)
            handles = wintypes.DWORD()
            if kernel.GetProcessHandleCount(handle, ctypes.byref(handles)):
                facts["handles"] = int(handles.value)
            io = io_counters()
            if kernel.GetProcessIoCounters(handle, ctypes.addressof(io)):
                facts["io_read_ops"] = int(io.ReadOperationCount)
                facts["io_write_ops"] = int(io.WriteOperationCount)
                facts["io_read_kb"] = int(io.ReadTransferCount // 1024)
        finally:
            kernel.CloseHandle(handle)
    except Exception as error:  # diagnostics never raise
        facts["error"] = type(error).__name__
    return facts


def process_table():
    """{pid: (threads, parent pid, exe name)}, plus whether the walk ended normally."""
    kernel = _kernel32()
    table = {}
    if kernel is None:
        return table, "unsupported"
    import ctypes

    try:
        entry_type = _Structs.get()[3]
        snapshot = kernel.CreateToolhelp32Snapshot(0x2, 0)
        if not snapshot or snapshot == ctypes.c_void_p(-1).value:
            return table, f"snapshot-error={ctypes.get_last_error()}"
        try:
            entry = entry_type()
            entry.dwSize = ctypes.sizeof(entry)
            if not kernel.Process32FirstW(snapshot, ctypes.addressof(entry)):
                return table, f"first-error={ctypes.get_last_error()}"
            deadline = time.monotonic() + ENUMERATION_BUDGET_SECONDS
            while len(table) < MAX_PROCESS_ENTRIES and time.monotonic() < deadline:
                table[int(entry.th32ProcessID)] = (int(entry.cntThreads), int(entry.th32ParentProcessID),
                                                   entry.szExeFile)
                if not kernel.Process32NextW(snapshot, ctypes.addressof(entry)):
                    error = ctypes.get_last_error()
                    # ERROR_NO_MORE_FILES is the only complete ending; say so if not.
                    return table, "complete" if error == 18 else f"incomplete(error={error})"
            return table, "incomplete(bounded)"
        finally:
            kernel.CloseHandle(snapshot)
    except Exception as error:
        return table, f"error={type(error).__name__}"


def system_snapshot(pids=(), include_names=None):
    """Machine CPU/memory plus CPU deltas for ``pids`` over one short sample. Never raises."""
    if include_names is None:
        include_names = os.environ.get("CI", "").lower() == "true"
    result = {"sample_ms": int(SAMPLE_SECONDS * 1000), "logical_cpus": os.cpu_count() or 0}
    kernel = _kernel32()
    if kernel is None:
        result["supported"] = False
        return result
    import ctypes
    from ctypes import wintypes

    try:
        memory_status = _Structs.get()[2]

        def system_times():
            idle, kern, user = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
            if not kernel.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kern), ctypes.byref(user)):
                return None
            return _filetime(idle), _filetime(kern) + _filetime(user)

        table, table_state = process_table()
        watched = [int(p) for p in pids]
        candidates = list(table) if include_names else watched
        before_cpu = {pid: _process_cpu_100ns(kernel, pid) for pid in dict.fromkeys(candidates + watched)}
        before_sys = system_times()
        start = time.monotonic()
        time.sleep(SAMPLE_SECONDS)
        after_sys = system_times()
        elapsed_100ns = (time.monotonic() - start) * 1e7
        deltas = {}
        for pid, before in before_cpu.items():
            after = _process_cpu_100ns(kernel, pid)
            # Same creation time: the same process, not a reused PID.
            if before and after and before[1] == after[1]:
                deltas[pid] = after[0] - before[0]
        if before_sys and after_sys:
            idle = after_sys[0] - before_sys[0]
            total = after_sys[1] - before_sys[1]  # kernel time includes idle time
            result["cpu_busy_pct"] = round(100.0 * (1 - idle / total), 1) if total > 0 else None
        status = memory_status()
        status.dwLength = ctypes.sizeof(status)
        if kernel.GlobalMemoryStatusEx(ctypes.addressof(status)):
            result["mem_load_pct"] = int(status.dwMemoryLoad)
            result["avail_phys_mb"] = int(status.ullAvailPhys // 1048576)
            result["avail_commit_mb"] = int(status.ullAvailPageFile // 1048576)
        result["uptime_s"] = int(kernel.GetTickCount64() // 1000)
        result["processes"] = len(table)
        result["threads"] = sum(threads for threads, _, _ in table.values())
        result["table"] = table_state
        span = elapsed_100ns * max(1, result["logical_cpus"])
        result["watched"] = {pid: (round(100.0 * deltas[pid] / elapsed_100ns, 1) if pid in deltas else None)
                             for pid in watched}
        if include_names:
            ranked = sorted(((d, pid) for pid, d in deltas.items() if pid != 0), reverse=True)[:TOP_PROCESSES]
            result["top"] = [(token(table.get(pid, (0, 0, "?"))[2]), pid, round(100.0 * d / span, 1))
                             for d, pid in ranked]
    except Exception as error:
        result["error"] = type(error).__name__
    return result


def _fields(mapping):
    return " ".join(f"{key}={token(value, 60)}" for key, value in mapping.items() if value is not None)


def machine_lines(label, snapshot):
    """The system line, and the top-CPU line when names were allowed."""
    snapshot = dict(snapshot)
    snapshot.pop("watched", None)
    top = snapshot.pop("top", None)
    lines = [f"{PREFIX} {label} system: {_fields(snapshot)}"]
    if top is not None:
        ranked = ",".join(f"{name}#{pid}:{pct}%" for name, pid, pct in top) or "none"
        lines.append(f"{PREFIX} {label} top-cpu (share of all CPUs over the sample): {ranked}")
    return lines


def snapshot_lines(label, pids=(), include_names=None):
    """Printable lines for a failure path in another harness. Never raises."""
    try:
        snapshot = system_snapshot(pids, include_names)
        lines = machine_lines(label, snapshot)
        now_ms = time.time() * 1000
        for pid in pids:
            facts = process_state(pid)
            if "created_wall_ms" in facts:
                facts["age_s"] = round((now_ms - facts.pop("created_wall_ms")) / 1000, 1)
            facts["cpu_pct_of_one_cpu"] = snapshot.get("watched", {}).get(int(pid))
            lines.append(f"{PREFIX} {label} process: {_fields(facts)}")
        return lines
    except Exception as error:
        return [f"{PREFIX} {label} snapshot error: {type(error).__name__}"]


# --------------------------------------------------------------------------- the runner
def _probe(argv):
    """Time a second, trivial launch of the same executable; bounded and killed on overrun."""
    start = time.monotonic()
    try:
        child = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                 stdin=subprocess.DEVNULL)
    except OSError as error:
        return f"launch-error={type(error).__name__}"
    try:
        out, _ = child.communicate(timeout=PROBE_TIMEOUT_SECONDS)
        return f"rc={child.returncode} in {1000 * (time.monotonic() - start):.0f}ms out={token(out.decode('utf-8', 'replace').strip())}"
    except subprocess.TimeoutExpired:
        child.kill()
        try:
            child.communicate(timeout=POST_KILL_WAIT_SECONDS)
        except subprocess.TimeoutExpired:
            return f"TIMEOUT>{PROBE_TIMEOUT_SECONDS:.0f}s and not reaped"
        return f"TIMEOUT>{PROBE_TIMEOUT_SECONDS:.0f}s (killed)"


def run_watched(argv, *, timeout, label, expected_stages=(), probe_argv=None, env=None, out=None):
    """``subprocess.run(argv, check=True, timeout=timeout)`` plus bounded evidence.

    The watchdog starts when the child exists, exactly as in ``subprocess.run``,
    and its length is the caller's. A timeout still raises ``TimeoutExpired`` and
    a non-zero exit still raises ``CalledProcessError``. The only addition to the
    timeout path is evidence taken before the kill and a bounded wait after it
    (``subprocess.run`` waits without a bound there).
    """
    stream = sys.stdout if out is None else out

    def emit(line):
        print(line, file=stream, flush=True)

    trace_dir = tempfile.mkdtemp(prefix="ac6-diag-")
    trace_path = os.path.join(trace_dir, "trace.jsonl")
    environment = dict(os.environ if env is None else env)
    environment[TRACE_ENV] = trace_path
    name = " ".join(os.path.basename(str(part)) for part in argv[:2])
    try:
        launch_wall_ms = time.time() * 1000
        launch_begin = time.monotonic()
        child = subprocess.Popen(argv, env=environment)
        launched = time.monotonic()
        popen_ms = 1000 * (launched - launch_begin)
        try:
            returncode = child.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            waited = time.monotonic() - launched
            try:
                state = process_state(child.pid)
                snapshot = system_snapshot([child.pid])
                state["cpu_pct_of_one_cpu"] = snapshot.get("watched", {}).get(child.pid)
                machine = machine_lines(label, snapshot)
                records, malformed, truncated = read_trace(trace_path)
                summary = stage_summary(records, launch_wall_ms, expected_stages)
            except Exception as error:  # evidence is best effort; the verdict is not
                state, machine, summary = {"error": type(error).__name__}, [], None
                malformed = truncated = 0
            child.kill()
            kill_sent = time.monotonic()
            try:
                child.wait(timeout=POST_KILL_WAIT_SECONDS)
                cleanup = f"exit observed {1000 * (time.monotonic() - kill_sent):.0f}ms after kill (rc={child.returncode})"
            except subprocess.TimeoutExpired:
                cleanup = f"NO exit observed within {POST_KILL_WAIT_SECONDS:.0f}s of kill"
            emit(f"{PREFIX} {label}: TIMEOUT after {waited:.1f}s (watchdog {timeout}s) "
                 f"pid={child.pid} popen={popen_ms:.0f}ms cmd={token(name, 80)}")
            if summary is not None:
                emit(f"{PREFIX} {label} stages: reached={summary['reached']} last={summary['last']} "
                     f"missing={summary['missing']} {summary['details']}"
                     + (f" malformed={malformed}" if malformed else "") + (" truncated" if truncated else ""))
            if "created_wall_ms" in state:
                state["created_after_launch_ms"] = round(state.pop("created_wall_ms") - launch_wall_ms)
            emit(f"{PREFIX} {label} child before kill: {_fields(state)}")
            for line in machine:
                emit(line)
            emit(f"{PREFIX} {label} cleanup: {cleanup}")
            if probe_argv:
                emit(f"{PREFIX} {label} probe {token(' '.join(os.path.basename(str(p)) for p in probe_argv), 60)}: "
                     f"{_probe(probe_argv)}")
            raise
        total_ms = 1000 * (time.monotonic() - launch_begin)
        records, malformed, truncated = read_trace(trace_path)
        summary = stage_summary(records, launch_wall_ms, expected_stages)
        if returncode:
            emit(f"{PREFIX} {label}: FAILED rc={returncode} after {total_ms:.0f}ms pid={child.pid} "
                 f"popen={popen_ms:.0f}ms cmd={token(name, 80)}")
            emit(f"{PREFIX} {label} stages: reached={summary['reached']} last={summary['last']} "
                 f"missing={summary['missing']} {summary['details']}")
            raise subprocess.CalledProcessError(returncode, argv)
        emit(f"{PREFIX} {label}: ok in {total_ms:.0f}ms popen={popen_ms:.0f}ms {summary['details']} "
             f"last={summary['last']} missing={summary['missing']}")
        return subprocess.CompletedProcess(argv, returncode)
    finally:
        shutil.rmtree(trace_dir, ignore_errors=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description="bounded failure-path snapshot")
    parser.add_argument("command", choices=["snapshot"])
    parser.add_argument("--label", default="snapshot")
    parser.add_argument("--pid", type=int, action="append", default=[])
    arguments = parser.parse_args(argv)
    for line in snapshot_lines(token(arguments.label), arguments.pid[:8]):
        print(line, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
