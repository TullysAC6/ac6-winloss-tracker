"""T0: timing diagnostics (maintenance issue #59) preserve behaviour and stay bounded.

Static proofs that the instrumentation changed no timeout, sleep, condition or
result, plus the pure parsing, summarising and redaction logic. Nothing here
starts a process; the real child, timeout and installer runs are T2 in
tests/test_timing_diagnostics_process.py and tests/test_install_readiness_diagnostics.py.
"""
import io
import json
import os
import re
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

import timing_diagnostics as td  # noqa: E402

# Wait-AppRuntimeReady exactly as it was before issue #59 (main 9a06656b).
BASELINE_WAIT_APP_RUNTIME_READY = r"""function Wait-AppRuntimeReady {
    param([int]$TimeoutSeconds = 15)

    $runtimePath = Join-Path $dataPath '.runtime.json'
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        try {
            if (Test-Path -LiteralPath $runtimePath -PathType Leaf) {
                $runtime = Get-Content -LiteralPath $runtimePath -Raw -ErrorAction Stop | ConvertFrom-Json
                $runtimePid = [int]$runtime.pid
                $runtimePort = [int]$runtime.port
                if (-not $runtime.PSObject.Properties['install_nonce'] -or $runtime.install_nonce -cne $script:installNonce) {
                    Start-Sleep -Milliseconds 100
                    continue
                }
                if ($runtimePid -gt 0 -and $runtimePort -ge 1 -and $runtimePort -le 65535) {
                    $runtimeProcess = Get-Process -Id $runtimePid -ErrorAction Stop
                    if ($runtimeProcess -and -not $runtimeProcess.HasExited) {
                        $response = Invoke-WebRequest -Uri ("http://127.0.0.1:{0}/health" -f $runtimePort) -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
                        $health = $response.Content | ConvertFrom-Json
                        if ([int]$response.StatusCode -eq 200 -and $health.ok -eq $true) {
                            Write-InstallLog "application runtime path: $runtimePath"
                            Write-InstallLog "application runtime PID: $runtimePid"
                            Write-InstallLog "application runtime port: $runtimePort"
                            Write-InstallLog 'application HTTP /health status: 200; overall health ready'
                            return $true
                        }
                    }
                }
            }
        } catch {
            # Startup is asynchronous. Keep polling until the deadline.
        }
        Start-Sleep -Milliseconds 250
    }
    Write-InstallLog "application runtime readiness timed out after $TimeoutSeconds seconds"
    return $false
}
"""

DIAGNOSTIC_STATEMENT = re.compile(
    r"^\s*(\$readinessDiagnostic = New-ReadinessDiagnostic"
    r"|\$null = (Add|Write)-ReadinessDiagnostic \$readinessDiagnostic '[a-z-]+'( -[A-Za-z]+ \$[A-Za-z_]+)*)\s*$")


def installer_text():
    raw = (ROOT / "install.ps1").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), "install.ps1 lost the BOM Windows PowerShell 5.1 needs"
    return raw[3:].decode("utf-8").replace("\r\n", "\n")


def function_block(text, name):
    start = text.index(f"function {name} {{")
    return text[start:text.index("\n}\n", start) + 3]


def readiness_helpers(text):
    return {m.group(1): function_block(text, m.group(1))
            for m in re.finditer(r"^function ([A-Za-z]+-Readiness[A-Za-z]*) \{", text, re.M)}


class InstallerInstrumentationTests(unittest.TestCase):
    def setUp(self):
        self.text = installer_text()
        self.function = function_block(self.text, "Wait-AppRuntimeReady")

    def test_the_function_minus_its_diagnostics_is_the_previous_function(self):
        lines = self.function.split("\n")
        diagnostic = [line for line in lines if DIAGNOSTIC_STATEMENT.match(line)]
        stripped = "\n".join(line for line in lines if not DIAGNOSTIC_STATEMENT.match(line))
        self.assertEqual(stripped, BASELINE_WAIT_APP_RUNTIME_READY)
        self.assertEqual(len(diagnostic), 12, "one New, nine Add and two Write statements")
        # Every other mention of the helpers is a whole, output-discarding statement.
        self.assertEqual(self.function.count("ReadinessDiagnostic"), 12)

    def test_timeout_call_site_and_rollback_trigger_are_unchanged(self):
        self.assertEqual(self.text.count("Wait-AppRuntimeReady -TimeoutSeconds"), 1)
        self.assertIn("    if (-not (Wait-AppRuntimeReady -TimeoutSeconds 15)) {\n"
                      "        throw 'アプリの起動を確認できませんでした。startup.logを確認してください。'\n    }\n",
                      self.text)

    def test_helpers_sit_before_the_function_and_contain_every_error(self):
        helpers = readiness_helpers(self.text)
        self.assertEqual(len(helpers), 12)
        body_start = self.text.index("function Wait-AppRuntimeReady {")
        for name, block in helpers.items():
            with self.subTest(helper=name):
                self.assertLess(self.text.index(block), body_start)
                statements = [line.strip() for line in block.split("\n")[1:-2] if line.strip()]
                if statements[0].startswith("param("):
                    statements = statements[1:]
                self.assertEqual(statements[0], "try {", "the whole body is guarded")
                self.assertTrue(statements[-1].startswith("} catch {"), "the guard catches everything")
                # break/continue in a function escape into the caller's loop.
                self.assertNotRegex(block, r"(?m)^\s*(break|continue)\b|[;{]\s*(break|continue)\b")
                self.assertNotIn("Write-Output", block)
                self.assertNotIn("Write-Host", block)

    def test_diagnostics_read_no_secret_and_only_their_whitelisted_fields(self):
        region = self.text[self.text.index("# Readiness diagnostics"):self.text.index("function Wait-AppRuntimeReady {")]
        for forbidden in (".token", "'token'", '"token"', "launch_nonce", "installNonce", "ConvertTo-Json"):
            self.assertNotIn(forbidden, region)
        self.assertEqual(re.findall(r"Properties\['([a-z_]+)'\]", region), ["install_nonce"])
        # The nonce is only classified as missing / empty / different, never printed.
        self.assertNotRegex(region, r"Write-InstallLog[^\n]*nonce")

    def test_readiness_lines_are_single_line_prefixed_records(self):
        writer = function_block(self.text, "Write-ReadinessDiagnostic")
        logged = re.findall(r"Write-InstallLog (.+)", writer)
        self.assertEqual(len(logged), 5, "one headline; four more lines on a timeout only")
        self.assertIn("$prefix = 'application readiness diagnostics:'", writer)
        self.assertTrue(all(line.startswith(("$headline", "('{0} ")) for line in logged))


class NodeHarnessInstrumentationTests(unittest.TestCase):
    def test_watchdog_stays_fifteen_seconds_and_uses_the_diagnostic_runner(self):
        source = (ROOT / "tests" / "test_issue4_effect_replay.py").read_text(encoding="utf-8")
        self.assertEqual(len(re.findall(r"timeout=15\b", source)), 1)
        self.assertIn('run_watched([node, str(Path(__file__).with_name("test_issue4_overlay.js"))], timeout=15,',
                      source)
        self.assertNotRegex(source, r"(?m)^\s*subprocess\.run\(")

    def test_overlay_script_marks_are_silent_and_cannot_fail(self):
        script = (ROOT / "tests" / "test_issue4_overlay.js").read_text(encoding="utf-8")
        mark = script[script.index("function mark("):script.index("mark('entry'")]
        self.assertIn("if (!trace) return;", mark)
        self.assertRegex(mark, r"try \{[\s\S]*appendFileSync[\s\S]*\} catch \(_\) \{\}")
        for stage in ("entry", "source_loaded", "vm_start", "vm_done", "assertions_done", "exit", "error"):
            self.assertIn(f"mark('{stage}'", script)
        # The assertions and their order are the pre-#59 regression, unchanged.
        assertions = [line.strip() for line in script.splitlines() if "assert." in line]
        self.assertEqual(assertions, [
            "assert.deepEqual(b.played, ['effect-5'], 'duplicate replay restarted banner');",
            "assert.deepEqual(b.played, ['effect-5', 'effect-10']);",
            "assert.equal(b.played.length, 2, 'expired effect played');",
            "assert.equal(restarted.played.length, 0, 'pre-restart effect resurrected');",
            "assert.deepEqual(reconnect.played, ['effect-5']);",
        ])


class TraceParsingTests(unittest.TestCase):
    def write(self, lines):
        handle = tempfile.NamedTemporaryFile("wb", suffix=".jsonl", delete=False)
        self.addCleanup(os.unlink, handle.name)
        with handle:
            handle.write(b"\n".join(lines))
        return handle.name

    def test_records_are_whitelisted_and_malformed_lines_counted(self):
        path = self.write([
            json.dumps({"stage": "entry", "t_ms": 10.5, "uptime_ms": 900, "wall_ms": 5000, "pid": 7,
                        "node": "v22.1.0", "secret": "s3cr3t", "token": "abc"}).encode(),
            b"not json",
            json.dumps({"stage": "vm start\nforged", "n": 1, "t_ms": 12}).encode(),
            json.dumps({"no_stage": 1}).encode(),
        ])
        records, malformed, truncated = td.read_trace(path)
        self.assertEqual(malformed, 2)
        self.assertFalse(truncated)
        self.assertEqual(records[0], {"stage": "entry", "t_ms": 10.5, "uptime_ms": 900.0, "wall_ms": 5000.0,
                                      "pid": 7.0, "node": "v22.1.0"})
        self.assertEqual(records[1]["stage"], "vm_start_forged")
        self.assertNotIn("s3cr3t", repr(records))

    def test_trace_is_bounded(self):
        many = [json.dumps({"stage": "tick", "t_ms": i}).encode() for i in range(td.MAX_TRACE_RECORDS + 5)]
        records, _, truncated = td.read_trace(self.write(many))
        self.assertEqual(len(records), td.MAX_TRACE_RECORDS)
        self.assertTrue(truncated)
        records, _, truncated = td.read_trace(self.write([b"x" * (td.MAX_TRACE_BYTES + 10)]))
        self.assertTrue(truncated)

    def test_missing_trace_is_not_an_error(self):
        self.assertEqual(td.read_trace(os.path.join(tempfile.gettempdir(), "ac6-no-such-trace.jsonl")), ([], 0, False))

    def test_stage_summary_offsets_boot_and_missing(self):
        records = [{"stage": "entry", "t_ms": 100.0, "uptime_ms": 1800.0, "wall_ms": 11840.0, "node": "v22.1.0"},
                   {"stage": "source_loaded", "t_ms": 103.0},
                   {"stage": "vm_start", "n": 1.0, "t_ms": 104.0}]
        summary = td.stage_summary(records, 10000.0, ("entry", "source_loaded", "vm_start", "vm_done", "exit"))
        self.assertEqual(summary["reached"], "entry@+0ms,source_loaded@+3ms,vm_start1@+4ms")
        self.assertEqual(summary["last"], "vm_start")
        self.assertEqual(summary["missing"], "vm_done,exit")
        self.assertIn("launch->entry=1840ms", summary["details"])
        self.assertIn("child_boot=1800ms", summary["details"])
        self.assertIn("node=v22.1.0", summary["details"])
        empty = td.stage_summary([], 0.0, ("entry",))
        self.assertEqual((empty["reached"], empty["missing"], empty["details"]), ("none", "entry", "entry-not-reached"))

    def test_token_is_single_line_and_bounded(self):
        self.assertEqual(td.token("a b\nc;d=e"), "a_b_c_d_e")
        self.assertEqual(len(td.token("x" * 500)), 40)
        self.assertEqual(td.token(""), "-")


@unittest.skipUnless(os.name == "nt", "process facts use the Windows API")
class ProcessFactTests(unittest.TestCase):
    def test_own_process_facts(self):
        facts = td.process_state(os.getpid())
        self.assertEqual(facts["state"], "running")
        for key in ("cpu_s", "ws_mb", "handles", "io_read_ops"):
            self.assertIn(key, facts)

    def test_absent_process_is_reported_not_raised(self):
        self.assertEqual(td.process_state(0x7FFFFFF0)["state"], "gone")

    def test_snapshot_is_bounded_and_names_are_opt_in(self):
        start = time.monotonic()
        snapshot = td.system_snapshot([os.getpid()], include_names=False)
        self.assertLess(time.monotonic() - start, 3.0)
        self.assertNotIn("top", snapshot)
        self.assertIn("cpu_busy_pct", snapshot)
        self.assertEqual(snapshot["table"], "complete")
        self.assertIn(os.getpid(), snapshot["watched"])
        named = td.system_snapshot([], include_names=True)
        self.assertLessEqual(len(named["top"]), td.TOP_PROCESSES)

    def test_snapshot_command_prints_only_prefixed_lines(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            self.assertEqual(td.main(["snapshot", "--label", "x y", "--pid", str(os.getpid())]), 0)
        lines = buffer.getvalue().splitlines()
        self.assertTrue(lines)
        self.assertTrue(all(line.startswith("[diag] x_y ") for line in lines))
        self.assertIn(f"pid={os.getpid()} state=running", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
