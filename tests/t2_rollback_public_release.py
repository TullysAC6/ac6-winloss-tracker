"""Real public rollback: this build (app-local) -> v1.2.0 (public, shared Python) -> this build.

Manual T2, like t2_settings_analytics_e2e.py: it needs the network (GitHub and
PyPI), takes several minutes and is not run by CI.  It exercises exactly the
command in README "1つ前の公開版に戻す（ロールバック）":

1. install this build with its own verified install.ps1 (pushed commit, from
   raw.githubusercontent.com), seed history, save the new Player setting OFF,
   exit normally;
2. run the README rollback command: download bootstrap.ps1 from the README URL
   and compare it with the README SHA-256, then run that bootstrap's real
   release verification (GitHub Release API, asset digest, .sha256 sidecar)
   and hand the verified v1.2.0 install.ps1 to a child PowerShell;
3. check v1.2.0 starts from its legacy source with the shared Python, and that
   history, config.json and preferences.json are unchanged;
4. install this build again and check the setting and history survived.

Everything runs under a fresh LOCALAPPDATA / APPDATA, so v1.2.0's
``pip install --user`` and both installers' data stay out of the real user
profile.  The one seam: both installers put their shortcut on
[Environment]::GetFolderPath('Desktop'), which cannot be redirected per
process, so the child copies of install.ps1 write it to a temporary Desktop
instead.  The seam is two exact textual replacements, asserted, and the
unmodified asset's SHA-256 is recorded.  Only processes whose command line
names the temporary root are ever stopped.

    python tests/t2_rollback_public_release.py [--candidate SHA] [--keep]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "TullysAC6/ac6-winloss-tracker"
PREVIOUS_RELEASE = "v1.2.0"
PREVIOUS_RELEASE_COMMIT = "c64b241c6b14b54bf7a4ac7897d3be42c8d7d9f4"
PREVIOUS_BOOTSTRAP_SHA256 = "82B223413A44BF9FDBBF399E7EED2AF6983794151DD25C9EE939B569BCD5881B"
ROLLBACK_HEADING = "## 1つ前の公開版に戻す（ロールバック）"
DESKTOP_CALL = "[Environment]::GetFolderPath('Desktop')"
DESKTOP_SEAM = "$env:AC6_ROLLBACK_DESKTOP"
KEY = "player_streak_status_enabled"
USER_AGENT = "AC6-WinLoss-Tracker-rollback-T2"

CHAIN = r'''param([string]$Bootstrap, [string]$Evidence)
$ErrorActionPreference = 'Stop'
# Bind exactly what the README passes to the downloaded bootstrap.
. $Bootstrap -LibraryOnly -ReleaseTag v1.2.0
$web = {
    param($Uri, $OutFile)
    Invoke-WebRequest -Uri $Uri -OutFile $OutFile -UseBasicParsing -TimeoutSec 60 `
        -Headers @{ 'User-Agent' = 'AC6-WinLoss-Tracker-Bootstrap/1.2.0' } -ErrorAction Stop
}
$child = {
    param($Path, $SelectedMode, $VerifiedReleaseTag)
    $bytes = [IO.File]::ReadAllBytes($Path)
    $hash = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash
    $hasBom = $bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF
    $text = (New-Object Text.UTF8Encoding($false)).GetString($bytes, $(if ($hasBom) { 3 } else { 0 }),
        $bytes.Length - $(if ($hasBom) { 3 } else { 0 }))
    $call = "[Environment]::GetFolderPath('Desktop')"
    $count = ([regex]::Matches($text, [regex]::Escape($call))).Count
    if ($count -ne 2) { throw "expected 2 Desktop lookups in the verified installer, found $count" }
    $fixture = Join-Path (Split-Path -Parent $Evidence) 'v120-install-desktop-seam.ps1'
    [IO.File]::WriteAllText($fixture, $text.Replace($call, '$env:AC6_ROLLBACK_DESKTOP'),
        (New-Object Text.UTF8Encoding($hasBom)))
    $arguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $fixture)
    if ($SelectedMode -eq 'Install') { $arguments += @('-SourceTag', $VerifiedReleaseTag) }
    $process = Start-Process -FilePath 'powershell.exe' -ArgumentList $arguments -PassThru `
        -RedirectStandardOutput "$Evidence.stdout" -RedirectStandardError "$Evidence.stderr"
    try { $process.WaitForExit(); $code = [int]$process.ExitCode } finally { $process.Dispose() }
    [IO.File]::WriteAllText($Evidence, (@{ verified_installer_sha256 = $hash; mode = $SelectedMode;
        tag = $VerifiedReleaseTag; exit_code = $code; bom = $hasBom } | ConvertTo-Json))
    return $code
}
$result = Invoke-VerifiedReleaseScript -Mode $Mode -Repository $Repository -ReleaseTag $ReleaseTag `
    -WebRequestInvoker $web -ChildInvoker $child
exit $result
'''

SEED = r'''
import sys, time
sys.path.insert(0, sys.argv[1])
from app_paths import data_dir
from history_store import HistoryStore
store = HistoryStore(data_dir())
store.start_session()
now = time.time() - 3600
for index, result in enumerate(("win", "win", "loss", "win")):
    store.record_result(f"rollback-seed-{index}", result, "test",
                        {"streak": 0, "wins": 0, "losses": 0}, created_at=now + index)
'''


class Failure(AssertionError):
    pass


def check(condition, message):
    if not condition:
        raise Failure(message)


def fetch(url, destination=None, timeout=60):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = response.read()
    if destination is not None:
        Path(destination).write_bytes(data)
    return data


def git(*args):
    return subprocess.run(["git", "-C", str(ROOT), "-c", f"safe.directory={ROOT}", *args],
                          capture_output=True, text=True, timeout=60, check=True).stdout.strip()


def readme_rollback_command():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    section = readme.split(ROLLBACK_HEADING, 1)
    check(len(section) == 2, "README rollback section is missing")
    body = section[1].split("\n## ", 1)[0]
    blocks = re.findall(r"```powershell\s*\n([^\n]+)\n```", body)
    command = next((block for block in blocks if "bootstrap.ps1" in block), None)
    check(command is not None, "README rollback section has no bootstrap command")
    url = re.search(r"\$u='([^']+)'", command).group(1)
    expected = re.search(r"-ne '([0-9A-F]{64})'", command).group(1)
    check(f"refs/tags/{PREVIOUS_RELEASE}/bootstrap.ps1" in url, f"README rollback URL is {url}")
    check(expected == PREVIOUS_BOOTSTRAP_SHA256, "README rollback bootstrap SHA-256 changed")
    check(f"-ReleaseTag {PREVIOUS_RELEASE};" in command, "README rollback command is not pinned")
    return command, url, expected


def processes_under(root):
    script = ("Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and "
              "$_.CommandLine.IndexOf($env:AC6_ROLLBACK_ROOT, [StringComparison]::OrdinalIgnoreCase) -ge 0 "
              "-and $_.ProcessId -ne $PID } | ForEach-Object { \"{0}`t{1}\" -f $_.ProcessId, $_.CommandLine }")
    env = dict(os.environ, AC6_ROLLBACK_ROOT=str(root))
    out = subprocess.run(["powershell.exe", "-NoProfile", "-Command", script], capture_output=True,
                         text=True, timeout=60, env=env).stdout
    rows = []
    for line in out.splitlines():
        if "\t" in line:
            pid, command = line.split("\t", 1)
            rows.append((int(pid), command))
    return rows


class Run:
    def __init__(self, candidate, keep):
        self.candidate = candidate
        self.keep = keep
        self.root = Path(tempfile.mkdtemp(prefix="ac6-public-rollback-"))
        self.local = self.root / "local"
        self.roaming = self.root / "roaming"
        self.desktop = self.root / "desktop"
        for path in (self.local, self.roaming, self.desktop):
            path.mkdir()
        self.data = self.local / "AC6WinLossTracker"
        self.data.mkdir()
        self.evidence = {"candidate": candidate, "root": str(self.root)}
        self.env = dict(os.environ, LOCALAPPDATA=str(self.local), APPDATA=str(self.roaming),
                        AC6_ROLLBACK_DESKTOP=str(self.desktop), PYTHONDONTWRITEBYTECODE="1")
        for name in ("PYTHONPATH", "PYTHONHOME", "PYTHONUSERBASE", "VIRTUAL_ENV", "PIP_USER"):
            self.env.pop(name, None)

    # ------------------------------------------------------------- helpers
    def log(self, message):
        print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)

    def runtime(self):
        path = self.data / ".runtime.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def http(self, path, method="GET", token=None, timeout=5):
        runtime = self.runtime()
        check(runtime is not None, "no .runtime.json")
        headers = {"X-Control-Token": token} if token else {}
        request = urllib.request.Request(f"http://127.0.0.1:{runtime['port']}{path}", method=method,
                                         data=b"" if method == "POST" else None, headers=headers)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read() or b"{}")

    def wait_healthy(self, label, timeout=90):
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            try:
                status, body = self.http("/health", timeout=3)
                if status == 200 and body.get("ok"):
                    return body
                last = body
            except Exception as error:  # still starting
                last = repr(error)
            time.sleep(0.5)
        raise Failure(f"{label}: Tracker did not become healthy: {last}")

    def exit_normally(self, label):
        """What 「Trackerを終了」 does: authenticated shutdown, then nothing left."""
        runtime = self.runtime()
        check(runtime is not None, f"{label}: no runtime to stop")
        status, _ = self.http("/api/system/shutdown", method="POST", token=runtime["token"], timeout=15)
        check(status == 200, f"{label}: shutdown returned {status}")
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if not processes_under(self.root) and not (self.data / ".runtime.json").exists():
                break
            time.sleep(0.5)
        left = processes_under(self.root)
        check(not left, f"{label}: processes left after normal exit: {left}")
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", runtime["port"]))

    def lifetime(self):
        _, summary = self.http("/api/dashboard/summary")
        life = summary["lifetime"]
        return life["wins"], life["losses"]

    def history_ids(self):
        connection = sqlite3.connect(f"file:{self.data / 'history.db'}?mode=ro", uri=True)
        try:
            return [row[0] for row in connection.execute("SELECT event_id FROM matches ORDER BY id")]
        finally:
            connection.close()

    def digest(self, name):
        path = self.data / name
        return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None

    def installer_with_seam(self, text, name):
        count = text.count(DESKTOP_CALL)
        check(count == 2, f"{name}: expected 2 Desktop lookups, found {count}")
        fixture = self.root / name
        fixture.write_bytes(b"\xef\xbb\xbf" + text.replace(DESKTOP_CALL, DESKTOP_SEAM).encode("utf-8"))
        return fixture

    def powershell(self, args, log_name, timeout=1800):
        with (self.root / log_name).open("w", encoding="utf-8", errors="replace") as log:
            child = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", *args],
                                   env=self.env, stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
        return child.returncode

    def app_python(self):
        metadata = json.loads((self.data / "installed-version.json").read_text(encoding="utf-8"))
        environment = Path(metadata["environment_path"])
        return environment / "Scripts" / "python.exe", self.local / "Programs" / "AC6WinLossTracker" / "app"

    def in_app(self, code):
        python, app = self.app_python()
        result = subprocess.run([str(python), "-c", code], cwd=app, env=self.env, capture_output=True,
                                text=True, timeout=120)
        check(result.returncode == 0, f"app-side command failed: {result.stdout}{result.stderr}")
        return result.stdout.strip()

    def shortcut(self):
        link = self.desktop / "AC6 WinLoss Tracker.lnk"
        check(link.exists(), "no shortcut on the temporary Desktop")
        script = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:AC6_LINK);"
                  "\"{0}`t{1}\" -f $s.TargetPath, $s.Arguments")
        out = subprocess.run(["powershell.exe", "-NoProfile", "-Command", script], capture_output=True,
                             text=True, timeout=60, env=dict(os.environ, AC6_LINK=str(link))).stdout.strip()
        target, arguments = out.split("\t", 1)
        return target, arguments

    # --------------------------------------------------------------- phases
    def install_candidate(self, label):
        self.log(f"{label}: installing candidate {self.candidate}")
        code = self.powershell(["-File", str(self.new_installer), "-SourceCommit", self.candidate],
                               f"{label}.log")
        check(code == 0, f"{label}: candidate installer exited {code}; see {self.root / (label + '.log')}")
        metadata = json.loads((self.data / "installed-version.json").read_text(encoding="utf-8"))
        check(metadata.get("channel") == "candidate" and metadata.get("resolved_commit") == self.candidate,
              f"{label}: metadata {metadata}")
        self.wait_healthy(label)
        return metadata

    def main(self):
        # Both layouts live under Programs\AC6WinLossTracker* (app-local and legacy).
        live = processes_under(Path(os.environ["LOCALAPPDATA"]) / "Programs" / "AC6WinLossTracker")
        check(not live, f"a live Tracker is running; exit it first: {live}")
        command, url, expected = readme_rollback_command()
        self.evidence["readme_rollback_command"] = command

        # This build's installer, exactly as GitHub serves it for the candidate.
        blob = subprocess.run(["git", "-C", str(ROOT), "show", f"{self.candidate}:install.ps1"],
                              capture_output=True, timeout=60, check=True).stdout
        served = fetch(f"https://raw.githubusercontent.com/{REPOSITORY}/{self.candidate}/install.ps1")
        check(served == blob, "raw install.ps1 differs from the committed blob")
        self.evidence["candidate_installer_sha256"] = hashlib.sha256(served).hexdigest().upper()
        self.new_installer = self.installer_with_seam(served.decode("utf-8-sig"), "new-install-desktop-seam.ps1")

        port = socket.socket()
        port.bind(("127.0.0.1", 0))
        free_port = port.getsockname()[1]
        port.close()
        (self.data / "config.json").write_text(json.dumps({
            "config_version": 18, "port": free_port, "stats_enabled": True,
            "result_detector_enabled": False, "effect_screenshot_enabled": False,
        }, indent=2), encoding="utf-8")
        subprocess.run([sys.executable, "-c", SEED, str(ROOT)], env=self.env, check=True, timeout=60)
        seeded = self.history_ids()
        check(len(seeded) == 4, f"seed wrote {seeded}")

        # 1. This build, the setting saved OFF, normal exit.
        self.evidence["install_1"] = self.install_candidate("install-1")
        self.in_app(f"import settings_window; settings_window.save_settings({{'{KEY}': False}})")
        check(json.loads((self.data / "preferences.json").read_text(encoding="utf-8"))[KEY] is False,
              "setting not saved")
        lifetime_before = self.lifetime()
        self.exit_normally("install-1")
        before = {name: self.digest(name) for name in ("config.json", "preferences.json")}
        check(KEY not in (self.data / "config.json").read_text(encoding="utf-8"), "key leaked into config.json")
        ids_before = self.history_ids()
        data_before = {str(p.relative_to(self.data)) for p in self.data.rglob("*")}
        real_user_site = Path(os.environ["APPDATA"]) / "Python"
        real_before = sorted(str(p.relative_to(real_user_site)) + f"@{p.stat().st_mtime_ns}"
                             for p in real_user_site.rglob("*")) if real_user_site.exists() else []

        # 2. The README rollback command.
        self.log("rollback: README command -> public v1.2.0")
        bootstrap = self.root / "bootstrap-v1.2.0.ps1"
        fetch(url, bootstrap)
        actual = hashlib.sha256(bootstrap.read_bytes()).hexdigest().upper()
        check(actual == expected, f"bootstrap SHA-256 mismatch: {actual}")
        chain = self.root / "rollback-chain.ps1"
        chain.write_text(CHAIN, encoding="ascii")
        evidence = self.root / "rollback-child.json"
        code = self.powershell(["-File", str(chain), "-Bootstrap", str(bootstrap), "-Evidence", str(evidence)],
                               "rollback-chain.log")
        child = json.loads(evidence.read_text(encoding="utf-8-sig")) if evidence.exists() else None
        self.evidence["rollback_child"] = child
        check(code == 0 and child and child["exit_code"] == 0,
              f"README rollback command exited {code}; child {child}; see {self.root}")
        check(child["tag"] == PREVIOUS_RELEASE and child["mode"] == "Install", f"child {child}")
        metadata = json.loads((self.data / "installed-version.json").read_text(encoding="utf-8"))
        self.evidence["rolled_back_metadata"] = metadata
        check(metadata.get("channel") == "stable" and metadata.get("resolved_commit") == PREVIOUS_RELEASE_COMMIT,
              f"rolled-back metadata {metadata}")
        legacy = self.local / "Programs" / "AC6WinLossTrackerSource"
        check((legacy / "launcher.pyw").exists() and not (legacy / "python_spawn.py").exists(),
              "v1.2.0 legacy source not installed")
        target, arguments = self.shortcut()
        self.evidence["rolled_back_shortcut"] = {"target": target, "arguments": arguments}
        check(str(legacy) in arguments and "venv" not in target.lower(), "shortcut is not the v1.2.0 layout")
        self.wait_healthy("v1.2.0")
        running = processes_under(self.root)
        check(any(str(legacy) in command for _, command in running), f"v1.2.0 is not running: {running}")
        check(not any(str(self.local / "Programs" / "AC6WinLossTracker" / "venv") in command
                      for _, command in running), "the app-local venv is still in use")
        check(self.lifetime() == lifetime_before, "lifetime changed by the rollback")
        check(self.history_ids()[:len(ids_before)] == ids_before, "history changed by the rollback")
        after = {name: self.digest(name) for name in ("config.json", "preferences.json")}
        check(after == before, f"config/preferences changed by the rollback: {before} -> {after}")
        data_after = {str(p.relative_to(self.data)) for p in self.data.rglob("*")}
        # Runtime files come and go with the process; nothing else may disappear.
        removed = {path for path in data_before - data_after if not Path(path).name.startswith(".")}
        check(not removed, f"the rollback deleted user data: {sorted(removed)}")
        user_site = list((self.roaming / "Python").glob("Python3*/site-packages/ttkbootstrap"))
        check(user_site, "v1.2.0 did not use the isolated user site")
        real_after = sorted(str(p.relative_to(real_user_site)) + f"@{p.stat().st_mtime_ns}"
                            for p in real_user_site.rglob("*")) if real_user_site.exists() else []
        check(real_after == real_before, "the real %APPDATA%\\Python was modified")
        check((self.local / "Programs" / "AC6WinLossTracker" / "app").exists(),
              "the newer app-local install was removed by the rollback")
        self.exit_normally("v1.2.0")

        # 3. This build again: the setting and all history survive.
        self.evidence["install_2"] = self.install_candidate("install-2")
        check(self.evidence["install_2"].get("environment_path") == self.evidence["install_1"].get("environment_path"),
              "re-upgrade did not reuse the app-local environment")
        settings = json.loads(self.in_app("import json, settings_window; print(json.dumps(settings_window.read_settings()))"))
        check(settings[KEY] is False, f"setting lost across the round trip: {settings}")
        check(self.lifetime() == lifetime_before, "lifetime changed across the round trip")
        check(not legacy.exists(), "legacy source left after re-upgrade")
        self.exit_normally("install-2")
        self.evidence["lifetime"] = lifetime_before
        self.evidence["history_ids"] = self.history_ids()
        self.evidence["result"] = "PASS"

    def cleanup(self):
        left = processes_under(self.root)
        for pid, _ in left:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=30)
        self.evidence["residue_processes"] = [pid for pid, _ in left]
        if self.keep:
            return
        for _ in range(10):
            try:
                shutil.rmtree(self.root)
                return
            except OSError:
                time.sleep(1)
        self.evidence["cleanup"] = f"could not remove {self.root}"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--candidate", default=None, help="pushed commit to install (default: HEAD)")
    parser.add_argument("--keep", action="store_true", help="keep the temporary root for inspection")
    parser.add_argument("--report", default=None, help="write the evidence JSON here")
    arguments = parser.parse_args(argv)
    candidate = arguments.candidate or git("rev-parse", "HEAD")
    run = Run(candidate, arguments.keep)
    try:
        run.main()
    except Exception as error:
        run.evidence["result"] = f"FAIL: {error}"
        raise
    finally:
        run.cleanup()
        text = json.dumps(run.evidence, indent=2, ensure_ascii=False)
        if arguments.report:
            Path(arguments.report).write_text(text, encoding="utf-8")
        print(text)
    print("public rollback round trip: PASS")


if __name__ == "__main__":
    main()
