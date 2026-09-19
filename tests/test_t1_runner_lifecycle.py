"""T2: the T1 runner owns its workers - timeout, leaks, exits, residue, verdicts.

These start real worker processes, so they are T2. Each run uses a small
non-canonical corpus copied into an owned temporary directory and, where a
failure mode must be forced, a stand-in worker script. No network, port, live
data or native capture is used.
"""
import ctypes
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
sys.path.insert(0, str(ROOT / "tests"))

from t1 import runner  # noqa: E402


def process_alive(pid):
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = ctypes.c_void_p
    handle = kernel.OpenProcess(0x1000 | 0x00100000, False, pid)  # QUERY_LIMITED | SYNCHRONIZE
    if not handle:
        return False
    try:
        return kernel.WaitForSingleObject(ctypes.c_void_p(handle), 0) != 0
    finally:
        kernel.CloseHandle(ctypes.c_void_p(handle))


@unittest.skipUnless(os.name == "nt", "T1 worker ownership uses Windows job objects")
class RunnerLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="ac6-t1-lifecycle-")
        self.addCleanup(self.directory.cleanup)
        self.base = Path(self.directory.name)
        self.corpus = self.base / "fixtures"
        self.corpus.mkdir()
        shutil.copy(FIXTURES / "families.json", self.corpus)
        shutil.copy(FIXTURES / "final_win_01.ppm", self.corpus)
        (self.corpus / "results" / "win").mkdir(parents=True)
        self.record_path = self.corpus / "results" / "win" / "final-win.01.json"
        shutil.copy(FIXTURES / "results" / "win" / "final-win.01.json", self.record_path)
        (self.corpus / "required-coverage.json").write_text(json.dumps(
            {"schema_version": 1, "required": {"result.win.positive": "a WIN final classifies as FINAL_WIN"}}),
            encoding="utf-8")
        self.markers = self.base / "markers"
        self.markers.mkdir()
        self.suites_before = self.suite_roots()

    def suite_roots(self):
        return set(Path(tempfile.gettempdir()).glob("ac6-t1-suite-*"))

    def run_t1(self, worker_script=None, case_timeout=10.0, expect_clean=True):
        reports = self.base / "reports"
        with patch.dict(os.environ, {"AC6_T1_TEST_MARKERS": str(self.markers)}), \
                open(os.devnull, "w", encoding="utf-8") as sink:
            started = time.monotonic()
            code = runner.run_t1(ROOT, reports, case_timeout=case_timeout, suite_timeout=60.0,
                                 fixtures_root=self.corpus, stream=sink,
                                 worker_script=worker_script or runner.WORKER_SCRIPT, forbidden_roots=[])
            elapsed = time.monotonic() - started
        report = json.loads((reports / "t1-report.json").read_text(encoding="utf-8"))
        if expect_clean:
            self.assertEqual(self.suite_roots() - self.suites_before, set(), "a T1 suite root was left behind")
        return code, report, elapsed

    def stand_in(self, body):
        path = self.base / "stand_in_worker.py"
        path.write_text("import os, subprocess, sys, time\nmarkers = os.environ['AC6_T1_TEST_MARKERS']\n" + body,
                        encoding="utf-8")
        return path

    def failure_stages(self, report):
        return [item["stage"] for case in report["cases"] for item in case["failures"]]

    def test_a_real_worker_on_a_non_canonical_corpus_is_never_a_t1_pass(self):
        code, report, _ = self.run_t1()
        self.assertEqual(report["cases"][0]["status"], "PASS")
        self.assertEqual(report["status"], "PASS-NONCANONICAL")
        self.assertFalse(report["canonical_corpus"])
        self.assertNotEqual(code, 0)

    def test_a_wrong_expectation_fails_the_case(self):
        record = json.loads(self.record_path.read_text(encoding="utf-8"))
        record["checks"]["frame_class"] = "CLEAR"
        record["provenance"]["legacy"] = None
        self.record_path.write_text(json.dumps(record), encoding="utf-8")
        code, report, _ = self.run_t1()
        self.assertEqual(code, 1)
        self.assertEqual(report["status"], "FAIL")
        failure = report["cases"][0]["failures"][0]
        self.assertEqual((failure["stage"], failure["check"], failure["expected"], failure["actual"]),
                         ("classify", "frame_class", "CLEAR", "FINAL_WIN"))

    def test_a_hung_worker_is_killed_at_its_deadline(self):
        worker = self.stand_in("open(os.path.join(markers, 'pid'), 'w').write(str(os.getpid()))\ntime.sleep(60)\n")
        code, report, elapsed = self.run_t1(worker, case_timeout=1.5)
        self.assertEqual(code, 1)
        self.assertIn("timeout", self.failure_stages(report))
        self.assertLess(elapsed, 30)
        self.assertFalse(process_alive(int((self.markers / "pid").read_text())))

    def test_a_leaked_grandchild_fails_and_is_reaped(self):
        worker = self.stand_in(
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            "open(os.path.join(markers, 'grandchild'), 'w').write(str(child.pid))\n"
            "sys.exit(0)\n")
        code, report, _ = self.run_t1(worker)
        self.assertEqual(code, 1)
        leaks = [item for case in report["cases"] for item in case["failures"] if item["check"] == "active_processes"]
        self.assertEqual(len(leaks), 1)
        self.assertIn("python", json.dumps(leaks[0]["actual"]).lower())
        self.assertFalse(process_alive(int((self.markers / "grandchild").read_text())))

    def test_an_abnormal_exit_without_a_result_fails(self):
        code, report, _ = self.run_t1(self.stand_in("sys.exit(7)\n"))
        self.assertEqual(code, 1)
        exits = [item for case in report["cases"] for item in case["failures"] if item["check"] == "exit_code"]
        self.assertEqual(exits[0]["actual"], 7)

    def test_a_worker_that_skips_isolation_proof_fails(self):
        worker = self.stand_in(
            "import json\nroot = os.environ['AC6_T1_CASE_ROOT']\n"
            "json.dump({'worker_version': 1, 'status': 'completed', 'case_id': 'result.final-win.01', 'kind': 'image',"
            " 'actual': {'frame_class': 'FINAL_WIN', 'debug': {}}}, open(os.path.join(root, 'result.json'), 'w'))\n")
        code, report, _ = self.run_t1(worker)
        self.assertEqual(code, 1)
        self.assertIn("isolation", self.failure_stages(report))

    def test_residue_that_cannot_be_removed_fails(self):
        # The forced failure really leaves this run's roots behind; remove them
        # whatever the assertions below conclude.
        self.addCleanup(lambda: [shutil.rmtree(leftover, ignore_errors=True)
                                 for leftover in self.suite_roots() - self.suites_before])
        with patch.object(runner, "_remove_tree", return_value=False):
            code, report, _ = self.run_t1(expect_clean=False)
        self.assertEqual(code, 1)
        self.assertIn("cleanup", self.failure_stages(report))
        self.assertTrue(any(item["stage"] == "cleanup" for item in report["infrastructure_failures"]))
        self.assertEqual(len(self.suite_roots() - self.suites_before), 1, "the forced residue is observable")

    def test_an_empty_or_uncovered_corpus_fails_before_any_worker(self):
        (self.corpus / "required-coverage.json").write_text(json.dumps(
            {"schema_version": 1, "required": {"result.loss.positive": "a LOSS final classifies as FINAL_LOSS"}}),
            encoding="utf-8")
        record = json.loads(self.record_path.read_text(encoding="utf-8"))
        record["coverage"] = ["result.loss.positive"]
        record["truth"]["result"] = "win"
        self.record_path.write_text(json.dumps(record), encoding="utf-8")
        shutil.rmtree(self.corpus / "results")
        (self.corpus / "final_win_01.ppm").unlink()
        code, report, _ = self.run_t1()
        self.assertEqual(code, 1)
        self.assertEqual(report["cases"], [])
        self.assertEqual(report["infrastructure_failures"][0]["stage"], "corpus")


if __name__ == "__main__":
    unittest.main(verbosity=2)
