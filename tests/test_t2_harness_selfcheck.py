"""The T2 harness must fail when cleanup fails.

Independent review of 1819f748 found that ``t2_settings_analytics_e2e`` computed
its exit status from RESULTS *before* the ``finally`` block, so a server thread
left alive, a bound port or a surviving temporary root could accompany
``RESULT: PASS``. Cleanup is now part of the gate; these tests inject each
cleanup failure and require it to be recorded as a failing check.

Review 5188346877 found two ways process ownership could still pass falsely,
and both are exercised here:

* a Tool Help walk that ended part-way with an error other than
  ERROR_NO_MORE_FILES was treated as the complete table;
* a grandchild whose parent exited first was nobody's descendant any more, so
  a still-running orphan passed the gate. Ownership is now a Job Object, proven
  with real processes.

Every case reclaims whatever it deliberately held on to, so this file leaves no
thread, socket, handle, process or directory behind.
"""
import ctypes
import importlib.util
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "t2_harness", ROOT / "tests" / "t2_settings_analytics_e2e.py")
t2 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(t2)

ERROR_ACCESS_DENIED = 5
ERROR_NO_MORE_FILES = 18
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


def setUpModule():
    # Exactly as main() does: ownership before anything is started.
    t2.establish_process_ownership()


class GateCase(unittest.TestCase):
    """Shared scaffolding: an owned temporary root and the teardown state."""

    def setUp(self):
        t2.RESULTS.clear()
        self.addCleanup(t2.RESULTS.clear)
        # Keep the injected-stall case fast without changing the shipped bound.
        join = patch.object(t2, "THREAD_JOIN_SECONDS", 1)
        join.start()
        self.addCleanup(join.stop)
        attempts = patch.object(t2, "ROOT_REMOVE_ATTEMPTS", 2)
        attempts.start()
        self.addCleanup(attempts.stop)

        self.directory = tempfile.mkdtemp(prefix="ac6-t2-selfcheck-")
        self.addCleanup(self._force_remove)

    def _force_remove(self):
        import shutil
        for _ in range(10):
            shutil.rmtree(self.directory, ignore_errors=True)
            if not Path(self.directory).exists():
                return
            time.sleep(0.2)

    def state(self, **overrides):
        base = {
            "server": None, "thread": None, "port": t2.free_port(),
            "temporary": self.directory, "baseline_local": os.environ.get("LOCALAPPDATA"),
        }
        base.update(overrides)
        # Only scan when the caller did not supply a baseline: these tests
        # deliberately patch the scanner to fail, and setdefault would still
        # evaluate the scan.
        if "children_before" not in base:
            base["children_before"] = t2.owned_descendants()
        return base

    def failing_checks(self):
        return [f"{section} {name}" for section, name, ok in t2.RESULTS if not ok]

    def passing_checks(self):
        return [f"{section} {name}" for section, name, ok in t2.RESULTS if ok]

    def assert_gate_failed(self, expected_fragment):
        failures = self.failing_checks()
        self.assertTrue(failures, "cleanup failure was not recorded as a check")
        self.assertTrue(
            any(expected_fragment in failure for failure in failures),
            f"expected a failing check mentioning {expected_fragment!r}, got {failures}")
        # This is what main() evaluates; it must not be able to return 0.
        self.assertFalse(all(ok for _, _, ok in t2.RESULTS))
        self.assertEqual(0 if all(ok for _, _, ok in t2.RESULTS) else 1, 1,
                         "main() would have exited 0")

    def spawn_bounded(self, code, extra=()):
        """A real child that exits on its own, and is reaped whatever happens."""
        child = subprocess.Popen([sys.executable, "-c", code, *extra])
        self.addCleanup(self.reap, child)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if child.pid in t2.owned_descendants():
                owner = t2.process_owner()
                if owner is not None:
                    self.assertTrue(owner.is_owned(child.pid),
                                    "a spawned child must be created inside the ownership job")
                return child
            time.sleep(0.1)
        self.fail("the spawned child never appeared as a descendant")

    def reap(self, child):
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=10)

    @staticmethod
    def pid_is_alive(pid):
        """Direct liveness, independent of the descendant relation."""
        return pid in t2.process_parents()

    @staticmethod
    def wait_until(predicate, timeout=15.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            time.sleep(0.05)
        return predicate()


class TeardownGateTests(GateCase):
    """Each injected cleanup failure must show up as a failing check."""

    # -------------------------------------------------------------- clean case
    def test_a_clean_teardown_records_only_passes(self):
        self.assertTrue(t2.teardown(self.state()))
        self.assertEqual(self.failing_checks(), [])
        self.assertTrue(all(ok for _, _, ok in t2.RESULTS))

    # -------------------------------------------------------- shutdown failure
    def test_shutdown_request_failure_fails_the_gate(self):
        class Unreachable:
            CONTROL_TOKEN = "token"
            stop_event = threading.Event()

        # No listener on this port, so the shutdown POST cannot be delivered.
        t2.teardown(self.state(server=Unreachable()))
        self.assert_gate_failed("graceful shutdown accepted")

    # ------------------------------------------------------------ thread stall
    def test_a_thread_that_refuses_to_stop_fails_the_gate(self):
        release = threading.Event()
        stalled = threading.Thread(target=lambda: release.wait(60), daemon=True)
        stalled.start()
        # Reclaim the thread whatever the assertions do.
        self.addCleanup(lambda: (release.set(), stalled.join(timeout=10)))

        t2.teardown(self.state(thread=stalled))
        self.assert_gate_failed("server thread stopped")

        release.set()
        stalled.join(timeout=10)
        self.assertFalse(stalled.is_alive())

    # ------------------------------------------------------------- bound port
    def test_a_port_still_bound_fails_the_gate(self):
        holder = socket.socket()
        holder.bind(("127.0.0.1", 0))
        holder.listen(1)
        port = holder.getsockname()[1]
        self.addCleanup(holder.close)

        t2.teardown(self.state(port=port))
        self.assert_gate_failed("isolated port released")

        holder.close()
        self.assertTrue(t2.port_is_free(port), "the probe port must be reclaimed")

    # ------------------------------------------------------ root removal fails
    @unittest.skipUnless(os.name == "nt", "an open handle only blocks removal on Windows")
    def test_a_temporary_root_that_cannot_be_removed_fails_the_gate(self):
        locked = Path(self.directory) / "held-open.bin"
        handle = open(locked, "wb")
        handle.write(b"held")
        handle.flush()
        self.addCleanup(handle.close)

        t2.teardown(self.state())
        self.assert_gate_failed("isolated TEMP root removed")

        handle.close()
        self._force_remove()
        self.assertFalse(Path(self.directory).exists(), "the root must be reclaimed")

    # -------------------------------------------------------- cleanup raises
    def test_an_exception_during_cleanup_fails_the_gate(self):
        with patch.object(t2, "port_is_free", side_effect=RuntimeError("probe exploded")):
            t2.teardown(self.state())
        self.assert_gate_failed("no exception during cleanup")

    # ------------------------------------------------- real process ownership
    def test_a_real_leaked_child_fails_the_gate(self):
        child = self.spawn_bounded("import time; time.sleep(30)")
        t2.teardown(self.state(children_before=set()))
        self.assert_gate_failed("no owned child or grandchild process left")
        failures = " ".join(self.failing_checks())
        self.assertNotIn("process table inspected successfully", failures,
                         "the scan itself succeeded; only the leak should fail")
        self.reap(child)
        self.assertNotIn(child.pid, t2.owned_descendants())

    def test_a_real_leaked_grandchild_fails_the_gate(self):
        """Depth matters: a worker's worker is still this harness's problem."""
        code = ("import subprocess, sys, time\n"
                "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
                "time.sleep(30)\n")
        child = self.spawn_bounded(code)
        grandchildren = self.wait_until(lambda: t2.owned_descendants() - {child.pid}, 10)
        self.assertTrue(grandchildren, "no grandchild appeared to test with")
        owned = {child.pid} | set(grandchildren)

        # Detected while everything is still running, before teardown acts.
        detected = t2.owned_descendants()
        self.assertTrue(set(grandchildren) <= detected,
                        f"the grandchild must be detected, saw {sorted(detected)}")

        t2.teardown(self.state(children_before=set()))
        self.assert_gate_failed("no owned child or grandchild process left")
        self.assertIn("Z leaked owned processes reclaimed", self.passing_checks())

        self.reap(child)
        self.assertEqual(self.wait_until(lambda: not (t2.owned_descendants() & owned)), True,
                         "the fault test must leave no owned process behind")
        self.assertFalse(any(self.pid_is_alive(pid) for pid in owned),
                         "every process this test started must be reaped")

    def test_an_unreadable_process_table_fails_the_gate(self):
        """Inability to inspect must never be reported as zero children."""
        with patch.object(t2, "process_parents",
                          side_effect=t2.ProcessScanError("scanner unavailable")):
            t2.teardown(self.state(children_before=set()))
        self.assert_gate_failed("process table inspected successfully")
        self.assert_gate_failed("no owned child or grandchild process left")

    def test_a_malformed_process_table_fails_the_gate(self):
        """A table that cannot see this process is not evidence of anything."""
        with patch.object(t2, "process_parents", return_value={1: 0, 2: 1}):
            t2.teardown(self.state(children_before=set()))
        self.assert_gate_failed("process table inspected successfully")

    def test_a_missing_baseline_scan_fails_the_gate(self):
        """If the pre-run scan failed, the post-run comparison is meaningless."""
        t2.teardown(self.state(children_before=None))
        self.assert_gate_failed("process table inspected successfully")
        self.assert_gate_failed("no owned child or grandchild process left")

    def test_the_overlay_mutex_claim_requires_proven_ownership(self):
        """The old check was `server is None or True`; it must mean something."""
        with patch.object(t2, "process_parents",
                          side_effect=t2.ProcessScanError("scanner unavailable")):
            t2.teardown(self.state(children_before=set()))
        self.assert_gate_failed("overlay mutex scope is N/A")

    def test_process_enumeration_uses_no_external_command(self):
        source = (ROOT / "tests" / "t2_settings_analytics_e2e.py").read_text(encoding="utf-8")
        code = "\n".join(line for line in source.splitlines()
                         if not line.strip().startswith("#"))
        # The word may appear in prose explaining why it was removed; what must
        # not exist is an invocation or the import that would enable one.
        self.assertNotIn("import subprocess", code, "the scanner must not shell out")
        self.assertNotIn("subprocess.run", code)
        self.assertNotIn('"wmic"', code)
        self.assertIn("CreateToolhelp32Snapshot", code)
        self.assertIn("class ProcessScanError", code)


class FakeCall:
    """A stand-in for one kernel32 export, accepting argtypes and restype."""

    def __init__(self, function):
        self.function = function
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        return self.function(*args)


class FakeToolhelpKernel:
    """Tool Help over a fixed table; the walk can fail part-way through.

    Stands in for ``ctypes.WinDLL("kernel32")`` so the harness's own walk is
    what runs. Errors are reported the way the real calls report them, through
    the thread's last-error value that ``ctypes.get_last_error()`` returns.
    """

    SNAPSHOT = 0x4242

    def __init__(self, table, *, snapshot_ok=True, first_ok=True,
                 stop_after=None, stop_error=ERROR_NO_MORE_FILES):
        self.table = list(table)
        self.snapshot_ok = snapshot_ok
        self.first_ok = first_ok
        self.stop_after = stop_after
        self.stop_error = stop_error
        self.read = 0
        self.closed = []
        self.CreateToolhelp32Snapshot = FakeCall(self._snapshot)
        self.Process32FirstW = FakeCall(self._first)
        self.Process32NextW = FakeCall(self._next)
        self.CloseHandle = FakeCall(self._close)

    def _fill(self, reference):
        entry = reference._obj if hasattr(reference, "_obj") else reference.contents
        entry.th32ProcessID, entry.th32ParentProcessID = self.table[self.read]
        self.read += 1

    def _snapshot(self, flags, pid):
        if not self.snapshot_ok:
            ctypes.set_last_error(ERROR_ACCESS_DENIED)
            return INVALID_HANDLE_VALUE
        return self.SNAPSHOT

    def _first(self, handle, reference):
        if not self.first_ok:
            ctypes.set_last_error(ERROR_ACCESS_DENIED)
            return 0
        self.read = 0
        self._fill(reference)
        return 1

    def _next(self, handle, reference):
        if self.stop_after is not None and self.read >= self.stop_after:
            ctypes.set_last_error(self.stop_error)
            return 0
        if self.read >= len(self.table):
            ctypes.set_last_error(ERROR_NO_MORE_FILES)
            return 0
        self._fill(reference)
        return 1

    def _close(self, handle):
        self.closed.append(handle)
        return 1


@unittest.skipUnless(os.name == "nt", "the Tool Help walk is the Windows scanner")
class ToolhelpWalkTests(GateCase):
    """Review 5188346877: only ERROR_NO_MORE_FILES ends a complete walk."""

    def table(self, *, include_self=True):
        rows = [(0, 0), (4, 0)]
        if include_self:
            rows.append((os.getpid(), os.getppid()))
        rows += [(70001, 4), (70002, 70001)]
        return rows

    def kernel(self, fake):
        return patch("ctypes.WinDLL", lambda *args, **kwargs: fake)

    def test_access_denied_part_way_through_is_an_incomplete_table(self):
        """Valid entries, this process included, then ERROR_ACCESS_DENIED."""
        fake = FakeToolhelpKernel(self.table(), stop_after=3, stop_error=ERROR_ACCESS_DENIED)
        with self.kernel(fake):
            with self.assertRaises(t2.ProcessScanError) as caught:
                t2.process_parents()
        self.assertIn("incomplete", str(caught.exception))
        self.assertEqual(fake.read, 3, "the failure must come after valid entries")
        self.assertEqual(fake.closed, [FakeToolhelpKernel.SNAPSHOT],
                         "the snapshot handle must be released")

    def test_false_without_an_error_code_is_not_the_end_of_the_table(self):
        fake = FakeToolhelpKernel(self.table(), stop_after=3, stop_error=0)
        with self.kernel(fake):
            with self.assertRaises(t2.ProcessScanError):
                t2.process_parents()

    def test_only_error_no_more_files_ends_a_complete_walk(self):
        fake = FakeToolhelpKernel(self.table())
        with self.kernel(fake):
            parents = t2.process_parents()
        self.assertEqual(parents, dict(self.table()))
        self.assertEqual(fake.closed, [FakeToolhelpKernel.SNAPSHOT])

    def test_a_snapshot_that_cannot_be_taken_raises(self):
        fake = FakeToolhelpKernel(self.table(), snapshot_ok=False)
        with self.kernel(fake):
            with self.assertRaises(t2.ProcessScanError):
                t2.process_parents()
        self.assertEqual(fake.closed, [], "there was no handle to release")

    def test_a_first_entry_that_cannot_be_read_raises_and_releases_the_snapshot(self):
        fake = FakeToolhelpKernel(self.table(), first_ok=False)
        with self.kernel(fake):
            with self.assertRaises(t2.ProcessScanError):
                t2.process_parents()
        self.assertEqual(fake.closed, [FakeToolhelpKernel.SNAPSHOT])

    def test_a_complete_walk_that_misses_this_process_raises(self):
        fake = FakeToolhelpKernel(self.table(include_self=False))
        with self.kernel(fake):
            with self.assertRaises(t2.ProcessScanError):
                t2.process_parents()

    def test_access_denied_part_way_through_fails_the_cleanup_gate(self):
        fake = FakeToolhelpKernel(self.table(), stop_after=3, stop_error=ERROR_ACCESS_DENIED)
        state = self.state(children_before=set())
        with self.kernel(fake):
            t2.teardown(state)
        self.assert_gate_failed("process table inspected successfully")
        self.assert_gate_failed("no owned child or grandchild process left")
        self.assert_gate_failed("overlay mutex scope is N/A")


class PinnedProcess:
    """A handle opened on a pid while it is known to be ours.

    The open handle keeps the process object alive, so the pid cannot be
    recycled; the test uses it only to reclaim its own grandchild whatever the
    assertions do. The harness's ownership is what is under test, not this.
    """

    SYNCHRONIZE = 0x00100000
    PROCESS_TERMINATE = 0x0001
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    WAIT_TIMEOUT = 0x102

    def __init__(self, pid):
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel32.TerminateProcess.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        self._kernel32 = kernel32
        self.pid = pid
        self.handle = kernel32.OpenProcess(
            self.SYNCHRONIZE | self.PROCESS_TERMINATE | self.PROCESS_QUERY_LIMITED_INFORMATION,
            False, pid)
        if not self.handle:
            raise OSError(f"could not open process {pid}: error {ctypes.get_last_error()}")

    def alive(self):
        return self._kernel32.WaitForSingleObject(self.handle, 0) == self.WAIT_TIMEOUT

    def reclaim(self):
        if not self.handle:
            return
        try:
            if self.alive():
                self._kernel32.TerminateProcess(self.handle, 1)
                self._kernel32.WaitForSingleObject(self.handle, 10000)
            if self.alive():
                raise AssertionError(f"process {self.pid} could not be reclaimed")
        finally:
            self._kernel32.CloseHandle(self.handle)
            self.handle = None


@unittest.skipUnless(os.name == "nt", "Job Object ownership is Windows-specific")
class OrphanOwnershipTests(GateCase):
    """Review 5188346877: the parent exits first and its child keeps running."""

    # The child starts a long-lived grandchild, reports its pid, and exits as
    # soon as it is told to -- leaving the grandchild orphaned but running.
    CHILD = (
        "import pathlib, subprocess, sys, time\n"
        "work = pathlib.Path(sys.argv[1])\n"
        "grandchild = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "(work / 'grandchild.tmp').write_text(str(grandchild.pid))\n"
        "(work / 'grandchild.tmp').replace(work / 'grandchild.pid')\n"
        "deadline = time.monotonic() + 60\n"
        "while not (work / 'release').exists() and time.monotonic() < deadline:\n"
        "    time.sleep(0.05)\n"
    )

    @staticmethod
    def ancestors(parents, pid):
        chain, seen = [], {pid}
        while pid in parents and parents[pid] not in seen:
            pid = parents[pid]
            seen.add(pid)
            chain.append(pid)
        return chain

    def test_an_orphaned_grandchild_stays_owned_fails_the_gate_and_is_reclaimed(self):
        work = Path(self.directory) / "orphan"
        work.mkdir()
        baseline = t2.owned_descendants()
        child = self.spawn_bounded(self.CHILD, [str(work)])
        pid_file = work / "grandchild.pid"
        self.assertTrue(self.wait_until(pid_file.exists), "the child never reported a grandchild")
        grandchild = int(pid_file.read_text())
        pinned = PinnedProcess(grandchild)
        self.addCleanup(pinned.reclaim)

        # 1. Everything alive: child and grandchild are both owned.
        self.assertTrue(self.wait_until(lambda: {child.pid, grandchild} <= t2.owned_descendants()),
                        "a running grandchild must be owned")

        # 2. The parent exits first.
        (work / "release").write_text("go")
        child.wait(timeout=20)
        self.assertIsNotNone(child.returncode)
        self.assertTrue(pinned.alive(), "the grandchild must outlive its parent for this test")

        # 3. Only the grandchild is alive, and ancestry can no longer reach it.
        parents = t2.process_parents()
        self.assertIn(grandchild, parents)
        self.assertNotIn(os.getpid(), self.ancestors(parents, grandchild),
                         "precondition: the orphan is not anyone's descendant of ours")
        self.assertNotIn(grandchild, t2.ancestry_descendants(parents, os.getpid()))

        # 4. Ownership still holds.
        self.assertIn(grandchild, t2.owned_descendants(),
                      "an orphaned grandchild is still owned and must still be seen")

        # 5. The cleanup gate fails on it ...
        t2.teardown(self.state(children_before=baseline))
        self.assert_gate_failed("no owned child or grandchild process left")
        self.assertNotIn("Z process table inspected successfully", self.failing_checks())

        # 6. ... and reclaims it, leaving nothing owned.
        self.assertIn("Z leaked owned processes reclaimed", self.passing_checks())
        self.assertFalse(pinned.alive(), "teardown must reclaim the orphaned grandchild")
        self.assertEqual(t2.owned_descendants() - baseline, set())
        self.assertEqual(t2.process_owner().owned(), set())

    def test_an_unrelated_process_is_never_owned_or_reclaimed(self):
        owner = t2.process_owner()
        unrelated = os.getppid()  # started this test run; not started by it
        self.assertIn(unrelated, t2.process_parents())
        self.assertFalse(owner.is_owned(unrelated))
        self.assertNotIn(unrelated, t2.owned_descendants())
        self.assertFalse(owner.reclaim(unrelated), "a process outside the job must not be touched")
        self.assertIn(unrelated, t2.process_parents(), "the unrelated process is still running")

    def test_a_pid_the_job_does_not_vouch_for_is_never_terminated(self):
        """Stands in for pid reuse: the pid is live, but not verified as ours."""
        child = self.spawn_bounded("import time; time.sleep(30)")
        owner = t2.process_owner()
        with patch.object(owner, "is_member_handle", return_value=False):
            self.assertFalse(owner.is_owned(child.pid))
            self.assertFalse(owner.reclaim(child.pid))
        self.assertIsNone(child.poll(), "an unverified process must not be terminated")
        self.assertTrue(owner.reclaim(child.pid))
        child.wait(timeout=10)

    def test_ownership_that_was_never_established_fails_closed(self):
        with patch.object(t2, "_PROCESS_OWNER", None):
            with self.assertRaises(t2.ProcessScanError):
                t2.owned_descendants()
            t2.teardown(self.state(children_before=set()))
        self.assert_gate_failed("process table inspected successfully")
        self.assert_gate_failed("leaked owned processes reclaimed")


class HarnessStructureTests(unittest.TestCase):
    """The control flow the review asked for, asserted rather than assumed."""

    def test_exit_status_is_computed_after_teardown(self):
        source = (ROOT / "tests" / "t2_settings_analytics_e2e.py").read_text(encoding="utf-8")
        main_body = source[source.index("def main() -> int:"):source.index("class ProcessScanError")]
        finally_at = main_body.rindex("finally:")
        return_at = main_body.rindex("return 0 if all(")
        self.assertLess(finally_at, return_at,
                        "the exit status must be computed after the finally block")
        self.assertIn("teardown(", main_body)
        self.assertLess(main_body.index("establish_process_ownership()"),
                        main_body.index("server.main"),
                        "ownership must exist before anything is started")

    def test_a_watchdog_bounds_the_whole_run(self):
        source = (ROOT / "tests" / "t2_settings_analytics_e2e.py").read_text(encoding="utf-8")
        self.assertIn("WATCHDOG_SECONDS", source)
        self.assertIn("threading.Timer", source)
        self.assertIn("os._exit(3)", source, "a hang must still produce an exit code")

    def test_every_cleanup_outcome_is_a_recorded_check(self):
        source = (ROOT / "tests" / "t2_settings_analytics_e2e.py").read_text(encoding="utf-8")
        teardown = source[source.index("def teardown(state):"):source.index('if __name__ == "__main__"')]
        for required in ("graceful shutdown accepted", "server thread stopped",
                         "isolated port released", "no owned child or grandchild",
                         "leaked owned processes reclaimed",
                         "runtime files removed", "isolated TEMP root removed",
                         "no exception during cleanup"):
            with self.subTest(check=required):
                self.assertIn(required, teardown)
        self.assertEqual(teardown.count("print(f\"isolated root removed"), 0,
                         "cleanup outcomes must be checks, not prints")

    def test_ownership_is_a_job_not_only_ancestry(self):
        source = (ROOT / "tests" / "t2_settings_analytics_e2e.py").read_text(encoding="utf-8")
        for required in ("CreateJobObjectW", "AssignProcessToJobObject", "IsProcessInJob",
                         "JOB_OBJECT_BASIC_PROCESS_ID_LIST", "ERROR_NO_MORE_FILES"):
            with self.subTest(api=required):
                self.assertIn(required, source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
