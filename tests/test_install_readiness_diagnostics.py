"""T2: installer readiness diagnostics (maintenance issue #59) change no verdict.

The real Wait-AppRuntimeReady and its helpers are loaded from install.ps1 through
the PowerShell AST and run beside the pre-#59 function pinned in
tests/test_timing_diagnostics.py, against the same local scenarios: a missing,
unparsable or foreign runtime file, a dead PID, a closed port, a 503 health
answer and a ready one. Both must return the same single Boolean; only the
instrumented log may differ, and it must name the stage and
outcome while never echoing the control token or a nonce. Windows PowerShell 5.1
always runs; PowerShell 7 runs where installed (always on CI). A second class
runs the source-install harness's failure dump and proves it still rethrows the
original error. This binds local sockets and starts PowerShell processes, so it
is T2.
"""
import http.server
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

import timing_diagnostics as td  # noqa: E402
from test_timing_diagnostics import BASELINE_WAIT_APP_RUNTIME_READY  # noqa: E402

TOKEN = "ctl-token-5d8e0a1b"
LAUNCH_NONCE = "launch-nonce-99aa"
NONCE = "install-nonce-" + "c" * 50
OTHER_NONCE = "install-nonce-" + "d" * 50
DRIVER_TIMEOUT_SECONDS = 180
PREFIX = "application readiness diagnostics:"

DRIVER = r"""
param([string]$Installer, [string]$Baseline, [string]$Variant, [string]$ScenarioFile)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseInput([IO.File]::ReadAllText($Installer), [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'install.ps1 does not parse' }
$definitions = @($ast.FindAll({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $false))
foreach ($definition in $definitions) {
    $wanted = $definition.Name -eq 'Write-InstallLog'
    if ($Variant -eq 'instrumented') { $wanted = $wanted -or $definition.Name -eq 'Wait-AppRuntimeReady' -or $definition.Name -like '*-Readiness*' }
    if ($wanted) { . ([scriptblock]::Create($definition.Extent.Text)) }
}
if ($Variant -eq 'baseline') { . ([scriptblock]::Create([IO.File]::ReadAllText($Baseline))) }
$results = @()
# Windows PowerShell 5.1 emits a parsed JSON array as one object: assign, then iterate.
$scenarios = Get-Content -LiteralPath $ScenarioFile -Raw | ConvertFrom-Json
foreach ($scenario in $scenarios) {
    $dataPath = $scenario.data
    $script:logPath = $scenario.log
    $script:installNonce = $scenario.nonce
    $script:launcherProcess = $null
    $value = @(Wait-AppRuntimeReady -TimeoutSeconds ([int]$scenario.timeout))
    $types = @($value | ForEach-Object { if ($null -eq $_) { 'null' } else { $_.GetType().Name } }) -join ','
    $text = ''
    if ($value.Count -eq 1) { $text = [string]$value[0] }
    $results += [pscustomobject]@{ name = $scenario.name; count = $value.Count; types = $types; value = $text }
}
ConvertTo-Json -InputObject @($results) -Compress
"""


class Health(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        status, body = self.server.answer
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


def free_port():
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        return reservation.getsockname()[1]


# A valid 32-bit, multiple-of-four PID far above any live one: a just-exited
# child's PID could be reused within the test and make this case flaky.
ABSENT_PID = 2147483644


def health(ok, **overlay):
    return {"ok": ok, "server": {"ok": True, "pid": os.getpid()}, "http": {"ok": True},
            "detector": {"ok": True, "status": "disabled"},
            "overlay": {"ok": ok, "pid": 4200, "server_pid": os.getpid(), **overlay},
            "dashboard": {"open": False}}


@unittest.skipUnless(os.name == "nt", "the installer is Windows-only")
class ReadinessDiagnosticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.children_before = sorted(p for p, (_, parent, _) in td.process_table()[0].items() if parent == os.getpid())
        cls.root = Path(tempfile.mkdtemp(prefix="ac6-readiness-"))
        cls.servers = []
        cls.scenarios = {}
        injected = "starting\n2026-01-01 00:00:00 forged line token=" + TOKEN
        cls.add("no-runtime", None)
        cls.add("unparsable-runtime", "{")
        cls.add("nonce-mismatch", {"install_nonce": OTHER_NONCE}, answer=(200, health(True)))
        cls.add("nonce-empty", {"install_nonce": ""}, answer=(200, health(True)))
        cls.add("nonce-missing", {}, answer=(200, health(True)))
        cls.add("health-503", {"install_nonce": NONCE},
                answer=(503, dict(health(False, state=injected, heartbeat_age=7.5),
                                  detector={"ok": True, "status": "x" * 200})))
        cls.add("connection-refused", {"install_nonce": NONCE, "port": free_port()})
        cls.add("dead-pid", {"install_nonce": NONCE, "pid": ABSENT_PID, "port": free_port()})
        cls.add("ready", {"install_nonce": NONCE}, answer=(200, health(True, state="ready", heartbeat_age=0.4)),
                timeout=5)
        cls.shells = [("powershell", shutil.which("powershell.exe") or "powershell.exe")]
        pwsh = shutil.which("pwsh")
        if pwsh:
            cls.shells.append(("pwsh", pwsh))
        elif os.environ.get("CI"):
            raise AssertionError("PowerShell 7 is required on CI: the source-install flow runs the installer in it")
        cls.results = {}
        driver = cls.root / "driver.ps1"
        driver.write_text(DRIVER, encoding="utf-8-sig")
        baseline = cls.root / "baseline.ps1"
        baseline.write_text(BASELINE_WAIT_APP_RUNTIME_READY, encoding="utf-8-sig")
        for shell, executable in cls.shells:
            for variant in ("baseline", "instrumented"):
                listing = [{"name": name, "data": str(item["data"]), "log": str(cls.log(shell, variant, name)),
                            "nonce": NONCE, "timeout": item["timeout"]} for name, item in cls.scenarios.items()]
                scenario_file = cls.root / f"{shell}-{variant}.json"
                scenario_file.write_text(json.dumps(listing), encoding="utf-8")
                completed = subprocess.run(
                    [executable, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(driver),
                     "-Installer", str(ROOT / "install.ps1"), "-Baseline", str(baseline), "-Variant", variant,
                     "-ScenarioFile", str(scenario_file)],
                    capture_output=True, text=True, encoding="utf-8", timeout=DRIVER_TIMEOUT_SECONDS)
                if completed.returncode:
                    raise AssertionError(f"{shell} {variant} driver failed:\n{completed.stdout}\n{completed.stderr}")
                output = [line for line in completed.stdout.splitlines() if line.strip()]
                cls.results[(shell, variant)] = {row["name"]: row for row in json.loads(output[-1])}

    @classmethod
    def add(cls, name, runtime, answer=None, timeout=1):
        data = cls.root / name
        data.mkdir()
        port = None
        if answer is not None:
            server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Health)
            server.answer = answer
            threading.Thread(target=server.serve_forever, daemon=True).start()
            cls.servers.append(server)
            port = server.server_address[1]
        if isinstance(runtime, str):
            (data / ".runtime.json").write_text(runtime, encoding="utf-8")
        elif runtime is not None:
            payload = {"port": port, "token": TOKEN, "pid": os.getpid(), "started_at": 1.0,
                       "launch_nonce": LAUNCH_NONCE, **runtime}
            (data / ".runtime.json").write_text(json.dumps(payload), encoding="utf-8")
        cls.scenarios[name] = {"data": data, "timeout": timeout}

    @classmethod
    def log(cls, shell, variant, name):
        return cls.root / f"{shell}-{variant}-{name}.log"

    @classmethod
    def tearDownClass(cls):
        for server in cls.servers:
            server.shutdown()
            server.server_close()
        shutil.rmtree(cls.root, ignore_errors=True)
        assert not cls.root.exists(), "readiness fixture root was left behind"
        after = sorted(p for p, (_, parent, _) in td.process_table()[0].items() if parent == os.getpid())
        assert after == cls.children_before, f"child processes left running: {after}"

    def diagnostics(self, shell, name):
        text = self.log(shell, "instrumented", name).read_text(encoding="utf-8-sig")
        return text, [line for line in text.splitlines() if PREFIX in line]

    def test_every_verdict_matches_the_previous_function(self):
        expected = {name: "False" for name in self.scenarios}
        expected["ready"] = "True"
        for shell, _ in self.shells:
            for name in self.scenarios:
                with self.subTest(shell=shell, scenario=name):
                    old = self.results[(shell, "baseline")][name]
                    new = self.results[(shell, "instrumented")][name]
                    for row in (old, new):
                        # Exactly one Boolean: a helper that leaked pipeline output would
                        # turn $false into an array, which `-not (...)` reads as ready.
                        self.assertEqual((row["count"], row["types"], row["value"]), (1, "Boolean", expected[name]))

    def test_baseline_logs_are_unchanged_lines_only(self):
        for shell, _ in self.shells:
            for name in self.scenarios:
                with self.subTest(shell=shell, scenario=name):
                    text = self.log(shell, "baseline", name).read_text(encoding="utf-8-sig")
                    self.assertNotIn(PREFIX, text)

    def test_timeouts_name_the_stage_and_the_outcome(self):
        cases = {
            "no-runtime": ["result=timeout", "best_stage=none", "runtime-file=-", "outcomes none"],
            "unparsable-runtime": ["best_stage=runtime-file", "runtime-file-error:"],
            "nonce-mismatch": ["best_stage=runtime-parsed", "last_outcome=nonce-mismatch"],
            "nonce-empty": ["last_outcome=nonce-empty"],
            "nonce-missing": ["last_outcome=nonce-missing"],
            "health-503": ["best_stage=http-response", "last_outcome=http-503", "overlay.ok=False",
                           "overlay.heartbeat_age=7.5", "detector.status=" + "x" * 24 + " ",
                           f"pids runtime={os.getpid()} ", f"health.server={os.getpid()} ", "overlay=4200",
                           f"process server pid={os.getpid()} alive cpu_s=", "launcher=none"],
            "dead-pid": ["best_stage=pid-port-valid", "last_outcome=process-not-found"],
        }
        for shell, _ in self.shells:
            for name, needles in cases.items():
                with self.subTest(shell=shell, scenario=name):
                    _, lines = self.diagnostics(shell, name)
                    self.assertEqual(len(lines), 5, lines)
                    joined = "\n".join(lines)
                    for needle in needles:
                        self.assertIn(needle, joined)
            with self.subTest(shell=shell, scenario="connection-refused"):
                _, lines = self.diagnostics(shell, "connection-refused")
                # No HTTP answer at all. How a closed loopback port fails is the OS's
                # choice: refused at once, or (seen with Windows PowerShell 5.1) held
                # until Invoke-WebRequest's own 2 s timeout. Each is named, not hidden.
                self.assertIn("best_stage=process-alive", lines[0])
                self.assertIn("http-response=-", lines[0])
                self.assertRegex(lines[0], r"last_outcome=(socket-ConnectionRefused|web-ConnectFailure"
                                           r"|web-Timeout|http-timeout) ")

    def test_ready_adds_one_baseline_line(self):
        for shell, _ in self.shells:
            with self.subTest(shell=shell):
                text, lines = self.diagnostics(shell, "ready")
                self.assertEqual(len(lines), 1)
                self.assertRegex(lines[0], r"result=ready elapsed_ms=\d+ .* best_stage=health-ok .*health-ok=\d+")
                self.assertIn("application HTTP /health status: 200; overall health ready", text)

    def test_no_secret_nonce_or_injected_line_reaches_the_log(self):
        stamp = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} ")
        for shell, _ in self.shells:
            for name in self.scenarios:
                with self.subTest(shell=shell, scenario=name):
                    text, _ = self.diagnostics(shell, name)
                    for secret in (TOKEN, LAUNCH_NONCE, NONCE, OTHER_NONCE, "forged line"):
                        self.assertNotIn(secret, text)
                    self.assertTrue(all(stamp.match(line) for line in text.splitlines()), text)


HARNESS_DRIVER = r"""
param([string]$Harness, [string]$Data, [string]$Root, [string]$PythonPath)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseInput([IO.File]::ReadAllText($Harness), [ref]$tokens, [ref]$errors)
$definition = @($ast.FindAll({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Write-FailureDiagnostics' }, $true))[0]
. ([scriptblock]::Create($definition.Extent.Text))
$data = $Data
$root = $Root
$installed = Join-Path $Data 'no-such-installed-app'
$legacy = Join-Path $Data 'no-such-legacy-app'
try {
    try { throw 'original install failure' } catch { Write-FailureDiagnostics; throw }
} catch {
    Write-Host ('rethrown: ' + $_.Exception.Message)
}
"""


@unittest.skipUnless(os.name == "nt", "the source-install harness is Windows-only")
class HarnessFailureDumpTests(unittest.TestCase):
    def test_failure_dump_prints_evidence_and_the_original_error_is_rethrown(self):
        with tempfile.TemporaryDirectory(prefix="ac6-harness-dump-") as temporary:
            root = Path(temporary)
            lines = [f"2026-09-29 09:06:0{i} application readiness diagnostics: result=ready elapsed_ms={i}" for i in range(5)]
            lines += ["2026-09-29 09:06:07 application runtime readiness timed out after 15 seconds",
                      "2026-09-29 09:06:07 application readiness diagnostics: result=timeout best_stage=http-response",
                      "2026-09-29 09:06:07 some other installer line"]
            (root / "source-install.log").write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
            driver = root / "driver.ps1"
            driver.write_text(HARNESS_DRIVER, encoding="utf-8-sig")
            completed = subprocess.run(
                ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(driver),
                 "-Harness", str(ROOT / "tests" / "test_source_install_flow.ps1"), "-Data", str(root),
                 "-Root", str(ROOT), "-PythonPath", sys.executable],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        output = completed.stdout
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("rethrown: original install failure", output)
        self.assertIn("result=timeout best_stage=http-response", output)
        self.assertEqual(output.count("result=ready"), 3, "only the last three baselines")
        self.assertNotIn("some other installer line", output)
        self.assertIn("[diag] source-install system: sample_ms=500", output)


if __name__ == "__main__":
    unittest.main()
