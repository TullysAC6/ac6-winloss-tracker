"""T2: the diagnostic runner (maintenance issue #59) keeps subprocess.run verdicts.

These start real child processes, including ones that must be killed at their
watchdog, so they are T2. Each case proves the verdict is unchanged (success,
CalledProcessError or TimeoutExpired with the caller's timeout), that the
evidence names the stage reached and the process state, that nothing secret is
echoed, and that no child, probe or trace directory is left behind.
"""
import glob
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

import timing_diagnostics as td  # noqa: E402

SECRET = "ac6-diag-secret-7f3c9e"
TRACE_THEN_SLEEP = (
    "import json, os, sys, time\n"
    "with open(os.environ['AC6_DIAG_TRACE'], 'a') as trace:\n"
    "    trace.write(json.dumps({'stage': 'entry', 't_ms': 0, 'wall_ms': time.time() * 1000,"
    " 'uptime_ms': 3, 'secret': sys.argv[1]}) + '\\n')\n"
    "time.sleep(60)\n")


def live_children():
    table, state = td.process_table()
    assert state == "complete", state
    return sorted(pid for pid, (_, parent, _) in table.items() if parent == os.getpid())


def trace_dirs():
    return set(glob.glob(os.path.join(tempfile.gettempdir(), "ac6-diag-*")))


@unittest.skipUnless(os.name == "nt", "the diagnostic runner targets the Windows CI")
class RunWatchedTests(unittest.TestCase):
    def setUp(self):
        self.before_dirs = trace_dirs()
        self.before_children = live_children()
        self.out = io.StringIO()

    def tearDown(self):
        self.assertEqual(trace_dirs() - self.before_dirs, set(), "a trace directory was left behind")
        self.assertEqual(live_children(), self.before_children, "a child process was left running")
        self.assertNotIn(SECRET, self.out.getvalue())

    def run_child(self, code, *args, timeout=15, **kwargs):
        environment = dict(os.environ, AC6_TEST_SECRET=SECRET)
        return td.run_watched([sys.executable, "-c", code, *args], timeout=timeout, label="child",
                              env=environment, out=self.out, **kwargs)

    def test_success_keeps_its_result_and_prints_one_line(self):
        result = self.run_child("import os, sys; sys.exit(0 if os.environ.get('AC6_DIAG_TRACE') else 9)")
        self.assertIsInstance(result, subprocess.CompletedProcess)
        self.assertEqual(result.returncode, 0)
        lines = self.out.getvalue().splitlines()
        self.assertEqual(len(lines), 1, lines)
        self.assertTrue(lines[0].startswith("[diag] child: ok in "))

    def test_nonzero_exit_is_still_called_process_error(self):
        argv_tail = ("import sys; sys.exit(3)",)
        with self.assertRaises(subprocess.CalledProcessError) as caught:
            self.run_child(*argv_tail)
        self.assertEqual(caught.exception.returncode, 3)
        self.assertEqual(caught.exception.cmd, [sys.executable, "-c", *argv_tail])
        self.assertIn("[diag] child: FAILED rc=3", self.out.getvalue())

    def test_timeout_is_still_timeout_with_evidence_taken_before_the_kill(self):
        with self.assertRaises(subprocess.TimeoutExpired) as caught:
            self.run_child(TRACE_THEN_SLEEP, SECRET, timeout=1, expected_stages=("entry", "done"))
        self.assertEqual(caught.exception.timeout, 1)
        self.assertEqual(caught.exception.cmd, [sys.executable, "-c", TRACE_THEN_SLEEP, SECRET])
        report = self.out.getvalue()
        self.assertRegex(report, r"\[diag\] child: TIMEOUT after [\d.]+s \(watchdog 1s\)")
        self.assertIn("stages: reached=entry@+0ms last=entry missing=done", report)
        self.assertRegex(report, r"child before kill: pid=\d+ state=running cpu_s=")
        self.assertIn("cpu_pct_of_one_cpu=", report)
        self.assertIn("[diag] child system: sample_ms=500", report)
        self.assertRegex(report, r"cleanup: exit observed \d+ms after kill")

    def test_a_hung_probe_is_bounded_and_killed(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            self.run_child("import time; time.sleep(60)", timeout=1,
                           probe_argv=[sys.executable, "-c", "import time; time.sleep(60)"])
        self.assertIn(f"TIMEOUT>{td.PROBE_TIMEOUT_SECONDS:.0f}s (killed)", self.out.getvalue())

    def test_a_quick_probe_reports_its_launch_time(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            self.run_child("import time; time.sleep(60)", timeout=1, probe_argv=[sys.executable, "--version"])
        self.assertRegex(self.out.getvalue(), r"probe python\S*--version: rc=0 in \d+ms out=Python_3")

    def test_the_real_overlay_script_reaches_every_stage(self):
        node = os.environ.get("AC6_TEST_NODE") or shutil.which("node")
        if node is None:
            self.assertFalse(os.environ.get("CI"), "Node.js is required on CI")
            self.skipTest("Node.js not found")
        from test_issue4_effect_replay import OVERLAY_JS_STAGES
        td.run_watched([node, str(ROOT / "tests" / "test_issue4_overlay.js")], timeout=15, label="overlay.js",
                       expected_stages=OVERLAY_JS_STAGES, out=self.out)
        line = self.out.getvalue().strip()
        self.assertIn("last=exit missing=none", line)
        self.assertRegex(line, r"launch->entry=-?\d+ms child_boot=\d+ms node=v\d+")

    def test_unavailable_diagnostics_do_not_change_any_verdict(self):
        class BrokenStream:
            def write(self, _):
                raise OSError("diagnostic output unavailable")

        with patch.object(td.tempfile, "mkdtemp", side_effect=OSError("trace unavailable")):
            argv = [sys.executable, "-c", "import sys; sys.exit(0)"]
            result = td.run_watched(argv, timeout=1, label="child", out=BrokenStream())
            self.assertEqual(result.returncode, 0)
            argv[-1] = "import sys; sys.exit(3)"
            with self.assertRaises(subprocess.CalledProcessError) as failed:
                td.run_watched(argv, timeout=1, label="child", out=BrokenStream())
            self.assertEqual(failed.exception.returncode, 3)
            argv[-1] = "import time; time.sleep(60)"
            with self.assertRaises(subprocess.TimeoutExpired) as timed_out:
                td.run_watched(argv, timeout=1, label="child", out=BrokenStream())
            self.assertEqual(timed_out.exception.timeout, 1)


if __name__ == "__main__":
    unittest.main()
