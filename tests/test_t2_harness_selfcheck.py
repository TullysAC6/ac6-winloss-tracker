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
            "children_before": t2.owned_python_pids(),
        }
        base.update(overrides)
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

    def test_a_leaked_child_process_fails_the_gate(self):
        # A PID that was not present before teardown looks like a leaked worker.
        with patch.object(t2, "owned_python_pids", return_value={999999}):
            t2.teardown(self.state(children_before=set()))
        self.assert_gate_failed("no owned child or grandchild process left")


class HarnessStructureTests(unittest.TestCase):
    """The control flow the review asked for, asserted rather than assumed."""

    def test_exit_status_is_computed_after_teardown(self):
        source = (ROOT / "tests" / "t2_settings_analytics_e2e.py").read_text(encoding="utf-8")
        main_body = source[source.index("def main() -> int:"):source.index("def owned_python_pids")]
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
