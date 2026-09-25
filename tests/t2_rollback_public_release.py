"""Real public rollback: this build (app-local) -> v1.2.0 (public, shared Python) -> this build.

Manual T2, like t2_settings_analytics_e2e.py: it needs the network (GitHub and
PyPI), takes a few minutes and is not run by CI.  It follows README
"1つ前の公開版に戻す（ロールバック）" twice:

A. As written.  Install this build with its own verified install.ps1 (pushed
   commit, from raw.githubusercontent.com), seed history, save the new Player
   setting OFF and exit normally (step 1); run the README one-liner (step 2);
   check it reports 「セットアップが完了しました。」 and v1.2.0 runs from its
   legacy source with the shared Python (step 3),
   that the README version check prints exactly the README's text, and that
   history, config.json and preferences.json are unchanged.  Then install this
   build again and check the setting and history survived.
B. Step 1 skipped, as the README's IMPORTANT note describes.  The one-liner
   still succeeds and reports completion, but this build keeps running.  v1.2.0's own Launcher code
   (what the shortcut opens) reports it running, and its 「Trackerを終了」
   worker stops it.  Opening the shortcut then starts v1.2.0.  Finally this
   build is installed a third time.

The README one-liner runs verbatim in Windows PowerShell 5.1.  It downloads
bootstrap.ps1 from the README URL, checks the README SHA-256 and calls
``& powershell.exe ... -File $p -ReleaseTag v1.2.0``.  A function named
powershell.exe (functions take precedence over applications) receives that
call and asserts its arguments.  It then dot-sources the downloaded bootstrap
with -LibraryOnly in a real child and runs the bootstrap's real release
verification: GitHub Release API, asset digest and .sha256 sidecar.  The
verified v1.2.0 install.ps1 then runs in its own child PowerShell.

Seams, all asserted:
- Everything runs under a fresh LOCALAPPDATA / APPDATA, so v1.2.0's
  ``pip install --user`` and both installers' data stay out of the real user
  profile.
- Both installers put their shortcut on [Environment]::GetFolderPath('Desktop'),
  which cannot be redirected per process.  The child copies of install.ps1
  therefore write it to a temporary Desktop: two exact textual replacements,
  with the unmodified asset's SHA-256 recorded.

Not exercised:
- the bootstrap's own top-level block (its default invokers, catch and exit);
- the installer's new console window (output is redirected here);
- a real Tk click (B calls the button's worker, ``shutdown_tracker``);
- Explorer opening the .lnk (its target runs with its own arguments and
  working directory instead).

Only processes whose command line names the temporary root are ever stopped.
The run ends, cleaned up, before tests/run_t2.py's 900 s entry timeout.

    python tests/t2_rollback_public_release.py [--candidate SHA] [--keep] [--report PATH]
"""
from __future__ import annotations

import argparse
import ctypes
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
import uuid
from ctypes import wintypes
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
SETUP_COMPLETE = "セットアップが完了しました。"  # README step 3; v1.2.0 prints it with Write-Host only
USER_AGENT = "AC6-WinLoss-Tracker-rollback-T2"
# tests/run_t2.py kills an entry after 900 s, which would skip cleanup().
DEADLINE_SECONDS = 840
TRACKER_MUTEXES = ("Local\\AC6StatsOverlayV22", "Local\\AC6WinLossTrackerDashboard")
FOLDERID_LOCAL_APP_DATA = "F1B32785-6FBA-4FCF-9D55-7B8E7F157091"

# Prepended to the README one-liner, which follows verbatim.
ONE_LINER = r'''param([string]$Chain, [string]$Evidence)
function powershell.exe {
    # The one-liner's only call out of this session. It must pass exactly what
    # the v1.2.0 README passes to the bootstrap it downloaded and hash-checked.
    $bootstrap = [string]$args[4]
    $shape = (@($args | Select-Object -First 4) + '<bootstrap>' + @($args | Select-Object -Skip 5)) -join ' '
    if ($shape -cne '-NoProfile -ExecutionPolicy Bypass -File <bootstrap> -ReleaseTag v1.2.0') {
        throw "unexpected bootstrap invocation: $shape"
    }
    [IO.File]::WriteAllText("$Evidence.bootstrap",
        (Get-FileHash -LiteralPath $bootstrap -Algorithm SHA256).Hash + "`t" + $bootstrap)
    & (Join-Path $PSHOME 'powershell.exe') -NoProfile -ExecutionPolicy Bypass -File $Chain `
        -Bootstrap $bootstrap -Evidence $Evidence
}
'''

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
    $fixture = [IO.Path]::ChangeExtension($Evidence, '.install-desktop-seam.ps1')
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

# What the shortcut's Launcher does when a Tracker already runs, then its
# shutdown button's worker; run with v1.2.0's shared Python in its legacy tree.
V120_LAUNCHER = r'''
import importlib.machinery, importlib.util, json, sys
loader = importlib.machinery.SourceFileLoader("v120_launcher", sys.argv[1])
launcher = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
loader.exec_module(launcher)
state = launcher.launch_once()
stopped = launcher.shutdown_tracker() if state == "already_running" else None
print(json.dumps({"state": state, "stopped": stopped}))
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


def readme_rollback_section():
    """(one-liner, its URL, its SHA-256, version-check command, expected output lines)."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    section = readme.split(ROLLBACK_HEADING, 1)
    check(len(section) == 2, "README rollback section is missing")
    body = section[1].split("\n## ", 1)[0]
    blocks = re.findall(r"```(powershell|text)[ \t]*\n(.*?)\n```", body, re.S)
    commands = [text for kind, text in blocks if kind == "powershell" and "bootstrap.ps1" in text]
    check(len(commands) == 1 and "\n" not in commands[0], "README rollback section needs one one-line command")
    command = commands[0]
    url = re.search(r"\$u='([^']+)'", command).group(1)
    expected = re.search(r"-ne '([0-9A-F]{64})'", command).group(1)
    check(f"refs/tags/{PREVIOUS_RELEASE}/bootstrap.ps1" in url, f"README rollback URL is {url}")
    check(expected == PREVIOUS_BOOTSTRAP_SHA256, "README rollback bootstrap SHA-256 changed")
    check(f"-ReleaseTag {PREVIOUS_RELEASE};" in command, "README rollback command is not pinned")
    kinds = [kind for kind, _ in blocks]
    index = next((i for i, (kind, text) in enumerate(blocks)
                  if kind == "powershell" and "installed-version.json" in text), None)
    check(index is not None and kinds[index + 1:index + 2] == ["text"],
          "README rollback section needs a version check followed by its expected output")
    expected_lines = [line.rstrip() for line in blocks[index + 1][1].splitlines() if line.strip()]
    return command, url, expected, blocks[index][1], expected_lines


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


def console_shows(path, phrase):
    """Whether redirected Windows PowerShell 5.1 output shows phrase.

    The child writes in its console (OEM) code page; None when that code page
    cannot represent the phrase, so the check is skipped rather than guessed.
    """
    try:
        phrase.encode("oem")
    except UnicodeEncodeError:
        return None
    return path.exists() and phrase in path.read_bytes().decode("oem", errors="replace")


def real_local_appdata():
    """The signed-in user's LocalAppData, even when run_t2.py has redirected %LOCALAPPDATA%."""
    guid = (ctypes.c_ubyte * 16).from_buffer_copy(uuid.UUID(FOLDERID_LOCAL_APP_DATA).bytes_le)
    shell32 = ctypes.WinDLL("shell32")
    shell32.SHGetKnownFolderPath.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.HANDLE,
                                             ctypes.POINTER(ctypes.c_void_p)]
    shell32.SHGetKnownFolderPath.restype = ctypes.c_long
    path = ctypes.c_void_p()
    result = shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(path))
    try:
        check(result == 0 and path.value, f"SHGetKnownFolderPath failed: {result:#x}")
        return Path(ctypes.wstring_at(path.value))
    finally:
        ctypes.windll.ole32.CoTaskMemFree(path)


def held_tracker_mutexes():
    """Named mutexes a Tracker from any install or checkout holds in this session."""
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.OpenMutexW.restype = wintypes.HANDLE
    held = []
    for name in TRACKER_MUTEXES:
        handle = kernel32.OpenMutexW(0x00100000, False, name)  # SYNCHRONIZE
        if handle:
            held.append(name)
            kernel32.CloseHandle(handle)
    return held


class Run:
    def __init__(self, candidate, keep):
        self.candidate = candidate
        self.keep = keep
        self.deadline = time.monotonic() + DEADLINE_SECONDS
        self.root = Path(tempfile.mkdtemp(prefix="ac6-public-rollback-"))
        self.local = self.root / "local"
        self.roaming = self.root / "roaming"
        self.desktop = self.root / "desktop"
        for path in (self.local, self.roaming, self.desktop):
            path.mkdir()
        self.data = self.local / "AC6WinLossTracker"
        self.data.mkdir()
        self.app_local = self.local / "Programs" / "AC6WinLossTracker"
        self.legacy = self.local / "Programs" / "AC6WinLossTrackerSource"
        self.opened = []
        self.evidence = {"candidate": candidate, "root": str(self.root)}
        self.env = dict(os.environ, LOCALAPPDATA=str(self.local), APPDATA=str(self.roaming),
                        AC6_ROLLBACK_DESKTOP=str(self.desktop), PYTHONDONTWRITEBYTECODE="1")
        for name in ("PYTHONPATH", "PYTHONHOME", "PYTHONUSERBASE", "VIRTUAL_ENV", "PIP_USER"):
            self.env.pop(name, None)

    # ------------------------------------------------------------- helpers
    def log(self, message):
        print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)

    def budget(self, seconds):
        left = self.deadline - time.monotonic()
        check(left > 5, f"overall {DEADLINE_SECONDS} s deadline reached")
        return min(seconds, left)

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
        deadline = time.monotonic() + self.budget(timeout)
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

    def wait_no_processes(self, label, timeout=45):
        deadline = time.monotonic() + self.budget(timeout)
        while time.monotonic() < deadline:
            if not processes_under(self.root) and not (self.data / ".runtime.json").exists():
                return
            time.sleep(0.5)
        left = processes_under(self.root)
        check(not left, f"{label}: processes left after exit: {left}")
        check(not (self.data / ".runtime.json").exists(), f"{label}: .runtime.json left after exit")

    def exit_normally(self, label):
        """What 「Trackerを終了」 does: authenticated shutdown, then nothing left."""
        runtime = self.runtime()
        check(runtime is not None, f"{label}: no runtime to stop")
        status, _ = self.http("/api/system/shutdown", method="POST", token=runtime["token"], timeout=15)
        check(status == 200, f"{label}: shutdown returned {status}")
        self.wait_no_processes(label)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", runtime["port"]))

    def server_command(self):
        runtime = self.runtime()
        check(runtime is not None, "no .runtime.json")
        return dict(processes_under(self.root)).get(runtime["pid"], "")

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

    def digests(self):
        return {name: hashlib.sha256((self.data / name).read_bytes()).hexdigest()
                if (self.data / name).exists() else None for name in ("config.json", "preferences.json")}

    def installer_with_seam(self, text, name):
        count = text.count(DESKTOP_CALL)
        check(count == 2, f"{name}: expected 2 Desktop lookups, found {count}")
        fixture = self.root / name
        fixture.write_bytes(b"\xef\xbb\xbf" + text.replace(DESKTOP_CALL, DESKTOP_SEAM).encode("utf-8"))
        return fixture

    def powershell(self, args, log_name, timeout=600):
        with (self.root / log_name).open("w", encoding="utf-8", errors="replace") as log:
            child = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", *args],
                                   env=self.env, stdout=log, stderr=subprocess.STDOUT,
                                   timeout=self.budget(timeout))
        return child.returncode

    def app_python(self):
        metadata = json.loads((self.data / "installed-version.json").read_text(encoding="utf-8"))
        environment = Path(metadata["environment_path"])
        return environment / "Scripts" / "python.exe", self.app_local / "app"

    def in_app(self, code):
        python, app = self.app_python()
        result = subprocess.run([str(python), "-c", code], cwd=app, env=self.env, capture_output=True,
                                text=True, timeout=self.budget(120))
        check(result.returncode == 0, f"app-side command failed: {result.stdout}{result.stderr}")
        return result.stdout.strip()

    def shortcut(self):
        link = self.desktop / "AC6 WinLoss Tracker.lnk"
        check(link.exists(), "no shortcut on the temporary Desktop")
        script = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:AC6_LINK);"
                  "\"{0}`t{1}`t{2}\" -f $s.TargetPath, $s.Arguments, $s.WorkingDirectory")
        out = subprocess.run(["powershell.exe", "-NoProfile", "-Command", script], capture_output=True,
                             text=True, timeout=60, env=dict(os.environ, AC6_LINK=str(link))).stdout.strip()
        target, arguments, workdir = out.split("\t", 2)
        return target, arguments, workdir

    def check_v120_shortcut(self, label):
        target, arguments, workdir = self.shortcut()
        self.evidence[label] = {"target": target, "arguments": arguments, "working_directory": workdir}
        check(str(self.legacy) in arguments and "venv" not in target.lower(),
              f"{label}: shortcut is not the v1.2.0 layout")
        return target, arguments, workdir

    def open_shortcut(self):
        """Double-clicking the shortcut: its target, arguments and working directory."""
        target, arguments, workdir = self.shortcut()
        self.opened.append(subprocess.Popen(f'"{target}" {arguments}', cwd=workdir or None, env=self.env))

    def readme_version_check(self, label):
        """The README's version check, run as written; it must print the README's text."""
        script = self.root / "version-check.ps1"
        script.write_text(self.version_command + "\n", encoding="ascii")
        result = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                                env=self.env, capture_output=True, text=True, timeout=self.budget(60))
        lines = [line.rstrip() for line in result.stdout.splitlines() if line.strip()]
        self.evidence[f"{label}_version_check"] = lines
        check(result.returncode == 0 and lines == self.version_expected,
              f"{label}: README version check printed {lines}{result.stderr}")

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
        check(str(self.app_local / "app") in self.server_command(), f"{label}: not running from the app-local tree")
        return metadata

    def rollback(self, label):
        """README step 2: the one-liner, verbatim."""
        self.log(f"{label}: README one-liner -> public {PREVIOUS_RELEASE}")
        chain = self.root / "rollback-chain.ps1"
        chain.write_text(CHAIN, encoding="ascii")
        script = self.root / f"{label}-one-liner.ps1"
        script.write_text(ONE_LINER + self.command + "\n", encoding="ascii")
        evidence = self.root / f"{label}-child.json"
        code = self.powershell(["-File", str(script), "-Chain", str(chain), "-Evidence", str(evidence)],
                               f"{label}.log")
        child = json.loads(evidence.read_text(encoding="utf-8-sig")) if evidence.exists() else None
        received = Path(f"{evidence}.bootstrap")
        bootstrap_hash, _, bootstrap_path = (received.read_text(encoding="utf-8").partition("\t")
                                             if received.exists() else ("", "", ""))
        shown = console_shows(Path(f"{evidence}.stdout"), SETUP_COMPLETE)
        self.evidence[label] = dict(child or {}, one_liner_exit_code=code, bootstrap_sha256=bootstrap_hash,
                                    setup_complete_shown=shown)
        check(bootstrap_hash == PREVIOUS_BOOTSTRAP_SHA256, f"{label}: bootstrap handed on was {bootstrap_hash!r}")
        check(code == 0 and child and child["exit_code"] == 0,
              f"{label}: README one-liner exited {code}; child {child}; see {self.root / (label + '.log')}")
        check(shown is not False, f"{label}: the v1.2.0 installer did not print {SETUP_COMPLETE}")
        check(child["tag"] == PREVIOUS_RELEASE and child["mode"] == "Install", f"{label}: child {child}")
        check(not Path(bootstrap_path).exists(), f"{label}: the one-liner left {bootstrap_path} behind")
        metadata = json.loads((self.data / "installed-version.json").read_text(encoding="utf-8"))
        self.evidence[f"{label}_metadata"] = metadata
        check(metadata.get("channel") == "stable" and metadata.get("resolved_commit") == PREVIOUS_RELEASE_COMMIT,
              f"{label}: metadata {metadata}")
        check((self.legacy / "launcher.pyw").exists() and not (self.legacy / "python_spawn.py").exists(),
              f"{label}: v1.2.0 legacy source not installed")
        check((self.app_local / "app").exists(), f"{label}: the newer app-local install was removed")

    def check_data_kept(self, label, lifetime, ids, digests):
        check(self.lifetime() == lifetime, f"{label}: lifetime changed")
        check(self.history_ids()[:len(ids)] == ids, f"{label}: history changed")
        after = self.digests()
        check(after == digests, f"{label}: config/preferences changed: {digests} -> {after}")

    def main(self):
        # Both layouts live under Programs\AC6WinLossTracker* (app-local and legacy);
        # the mutexes catch a Tracker started from anywhere else, such as a checkout.
        live = processes_under(real_local_appdata() / "Programs" / "AC6WinLossTracker")
        check(not live, f"a live Tracker is running; exit it first: {live}")
        held = held_tracker_mutexes()
        check(not held, f"a Tracker is running (holds {held}); exit it first")
        (self.command, url, expected,
         self.version_command, self.version_expected) = readme_rollback_section()
        self.evidence["readme_rollback_command"] = self.command
        check(url.startswith("https://raw.githubusercontent.com/") and
              hashlib.sha256(fetch(url)).hexdigest().upper() == expected,
              "the README bootstrap URL does not serve the README SHA-256")

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

        # ---- A. As written. This build, the setting saved OFF, step 1: exit.
        self.evidence["install_1"] = self.install_candidate("install-1")
        self.in_app(f"import settings_window; settings_window.save_settings({{'{KEY}': False}})")
        check(json.loads((self.data / "preferences.json").read_text(encoding="utf-8"))[KEY] is False,
              "setting not saved")
        lifetime = self.lifetime()
        self.exit_normally("install-1")
        digests = self.digests()
        check(KEY not in (self.data / "config.json").read_text(encoding="utf-8"), "key leaked into config.json")
        ids = self.history_ids()
        data_before = {str(p.relative_to(self.data)) for p in self.data.rglob("*")}
        real_user_site = Path(os.environ["APPDATA"]) / "Python"
        real_before = sorted(str(p.relative_to(real_user_site)) + f"@{p.stat().st_mtime_ns}"
                             for p in real_user_site.rglob("*")) if real_user_site.exists() else []

        # Steps 2 and 3.
        self.rollback("rollback")
        self.check_v120_shortcut("rollback_shortcut")
        self.wait_healthy("v1.2.0")
        server = self.server_command()
        check(str(self.legacy) in server, f"v1.2.0 is not the running Tracker: {server}")
        check(not any(str(self.app_local / "venv") in command for _, command in processes_under(self.root)),
              "the app-local venv is still in use")
        self.readme_version_check("rollback")
        self.check_data_kept("rollback", lifetime, ids, digests)
        data_after = {str(p.relative_to(self.data)) for p in self.data.rglob("*")}
        # Runtime files come and go with the process; nothing else may disappear.
        removed = {path for path in data_before - data_after if not Path(path).name.startswith(".")}
        check(not removed, f"the rollback deleted user data: {sorted(removed)}")
        check(list((self.roaming / "Python").glob("Python3*/site-packages/ttkbootstrap")),
              "v1.2.0 did not use the isolated user site")
        real_after = sorted(str(p.relative_to(real_user_site)) + f"@{p.stat().st_mtime_ns}"
                            for p in real_user_site.rglob("*")) if real_user_site.exists() else []
        check(real_after == real_before, "the real %APPDATA%\\Python was modified")
        self.exit_normally("v1.2.0")

        # This build again: the setting and all history survive.
        self.evidence["install_2"] = self.install_candidate("install-2")
        check(self.evidence["install_2"].get("environment_path") == self.evidence["install_1"].get("environment_path"),
              "re-upgrade did not reuse the app-local environment")
        settings = json.loads(self.in_app("import json, settings_window; print(json.dumps(settings_window.read_settings()))"))
        check(settings[KEY] is False, f"setting lost across the round trip: {settings}")
        check(self.lifetime() == lifetime, "lifetime changed across the round trip")
        check(not self.legacy.exists(), "legacy source left after re-upgrade")
        # README: v1.2.0's --user packages stay, and this build does not use them.
        check(list((self.roaming / "Python").glob("Python3*/site-packages/ttkbootstrap")),
              "the v1.2.0 user-site packages were removed by the re-upgrade")
        check(self.in_app("import site; print(site.ENABLE_USER_SITE)") == "False",
              "the app-local environment reads the user site")

        # ---- B. Step 1 skipped: this build is still running (install-2).
        lifetime, ids, digests = self.lifetime(), self.history_ids(), self.digests()
        self.rollback("rollback_running")
        self.check_v120_shortcut("rollback_running_shortcut")
        self.wait_healthy("rollback-running")
        server = self.server_command()
        self.evidence["rollback_running_server"] = server
        check(str(self.app_local / "app") in server and str(self.legacy) not in server,
              f"expected this build to keep running after a rollback without step 1: {server}")
        self.readme_version_check("rollback_running")
        self.check_data_kept("rollback-running", lifetime, ids, digests)
        # The IMPORTANT note: the Launcher's 「Trackerを終了」, then the shortcut again.
        target, _, workdir = self.shortcut()
        python = Path(target).with_name("python.exe")
        result = subprocess.run([str(python), "-c", V120_LAUNCHER, str(self.legacy / "launcher.pyw")],
                                cwd=workdir or str(self.legacy), env=self.env, capture_output=True,
                                text=True, timeout=self.budget(60))
        lines = result.stdout.strip().splitlines()
        launcher = json.loads(lines[-1]) if result.returncode == 0 and lines else None
        self.evidence["v120_launcher_recovery"] = launcher
        check(launcher == {"state": "already_running", "stopped": True},
              f"v1.2.0 Launcher could not stop the newer Tracker: {launcher} {result.stderr}")
        self.wait_no_processes("v1.2.0 Launcher shutdown")
        self.open_shortcut()
        self.wait_healthy("v1.2.0 from the shortcut")
        server = self.server_command()
        check(str(self.legacy) in server, f"the shortcut did not start v1.2.0: {server}")
        self.check_data_kept("v1.2.0 from the shortcut", lifetime, ids, digests)
        self.exit_normally("v1.2.0 from the shortcut")

        # This build a third time.
        self.evidence["install_3"] = self.install_candidate("install-3")
        settings = json.loads(self.in_app("import json, settings_window; print(json.dumps(settings_window.read_settings()))"))
        check(settings[KEY] is False, f"setting lost across the second round trip: {settings}")
        check(self.lifetime() == lifetime, "lifetime changed across the second round trip")
        check(not self.legacy.exists(), "legacy source left after the third install")
        self.exit_normally("install-3")
        check(not held_tracker_mutexes(), "a Tracker mutex is still held after the final exit")
        self.evidence["lifetime"] = lifetime
        self.evidence["history_ids"] = self.history_ids()
        self.evidence["result"] = "PASS"

    def cleanup(self):
        left = processes_under(self.root)
        for pid, _ in left:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=30)
        for process in self.opened:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
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
    print("public rollback round trips: PASS")


if __name__ == "__main__":
    main()
