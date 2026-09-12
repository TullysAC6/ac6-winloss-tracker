"""The T2 harness must fail when cleanup fails.

Independent review of 1819f748 found that ``t2_settings_analytics_e2e`` computed
its exit status from RESULTS *before* the ``finally`` block, so a server thread
left alive, a bound port or a surviving temporary root could accompany
``RESULT: PASS``. Cleanup is now part of the gate; these tests inject each
cleanup failure and require it to be recorded as a failing check.

Every case reclaims whatever it deliberately held on to, so this file leaves no
thread, socket, handle or directory behind.
"""
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


class TeardownGateTests(unittest.TestCase):
    """Each injected cleanup failure must show up as a failing check."""

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

    def assert_gate_failed(self, expected_fragment):
        failures = self.failing_checks()
        self.assertTrue(failures, "cleanup failure was not recorded as a check")
        self.assertTrue(
            any(expected_fragment in failure for failure in failures),
            f"expected a failing check mentioning {expected_fragment!r}, got {failures}")
        # This is what main() evaluates; it must not be able to return 0.
        self.assertFalse(all(ok for _, _, ok in t2.RESULTS))

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
    def spawn_bounded(self, code, extra=()):
        """A real child that exits on its own, and is reaped whatever happens."""
        child = subprocess.Popen([sys.executable, "-c", code, *extra])
        self.addCleanup(self.reap, child)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if child.pid in t2.owned_descendants():
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
        deadline = time.monotonic() + 10
        grandchildren = set()
        while time.monotonic() < deadline:
            grandchildren = t2.owned_descendants() - {child.pid}
            if grandchildren:
                break
            time.sleep(0.1)
        self.assertTrue(grandchildren, "no grandchild appeared to test with")

        owned = {child.pid} | grandchildren
        t2.teardown(self.state(children_before=set()))
        self.assert_gate_failed("no owned child or grandchild process left")
        detected = t2.owned_descendants()
        self.assertTrue(grandchildren & detected,
                        f"the grandchild must be detected, saw {sorted(detected)}")

        # Reclaim only what this test owns, grandchild first. Windows does not
        # re-parent an orphan, so killing the child first would make the
        # grandchild stop resolving as a descendant while still running.
        for pid in sorted(grandchildren):
            try:
                os.kill(pid, 9)
            except OSError:
                pass
        self.reap(child)

        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and (t2.owned_descendants() & owned):
            time.sleep(0.1)
        self.assertEqual(t2.owned_descendants() & owned, set(),
                         "the fault test must leave no owned process behind")
        self.assertFalse(any(self.pid_is_alive(pid) for pid in owned),
                         "every process this test started must be reaped")

    @staticmethod
    def pid_is_alive(pid):
        """Direct liveness, independent of the descendant relation."""
        return pid in t2.process_parents()

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
                         "runtime files removed", "isolated TEMP root removed",
                         "no exception during cleanup"):
            with self.subTest(check=required):
                self.assertIn(required, teardown)
        self.assertEqual(teardown.count("print(f\"isolated root removed"), 0,
                         "cleanup outcomes must be checks, not prints")


if __name__ == "__main__":
    unittest.main(verbosity=2)
