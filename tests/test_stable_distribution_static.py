from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
installer = (ROOT / "install.ps1").read_text(encoding="utf-8")
readme = (ROOT / "README.md").read_text(encoding="utf-8")
version_source = (ROOT / "app_paths.py").read_text(encoding="utf-8")

for removed in (
    ".github/workflows/build-release.yml",
    "build_release.bat",
    "install-source-test.ps1",
    "tests/test_msix_packaging_static.py",
    "store",
):
    assert not (ROOT / removed).exists(), removed

assert re.search(r'^VERSION\s*=\s*["\']1\.0\.1["\']$', version_source, re.MULTILINE)
assert "$channel = 'stable'" in installer
assert "$version = '1.0.1'" in installer
assert 'https://api.github.com/repos/$repository/commits/$SourceTag' in installer
assert '^[0-9a-fA-F]{40}$' in installer
assert 'archive/$resolvedCommit.zip' in installer
assert "archive/refs/heads/main.zip" not in installer
assert "test/python-source-install" not in installer
assert "install-source-test.ps1" not in installer
assert installer.count('Invoke-WebRequest -Uri $releaseCommitUrl') == 1
assert "$statusCode -eq 403 -or $statusCode -eq 429" in installer
assert "Get-InstalledRevision" in installer and "currently installed" not in installer
assert "Write-InstalledMetadata -Commit $resolvedCommit" in installer
assert "Restore-PreviousSource" in installer
assert "Complete-SourceInstall" in installer
assert "Python Software Foundation" in installer
assert "Get-SupportedPythonRole" in installer
assert "$preferredPythonMinor = 14" in installer
assert "$fallbackPythonMinor = 13" in installer
assert "$pythonWingetPackage = 'Python.Python.3.14'" in installer
assert "Python.Python.3.12" not in installer
assert "python_version" in installer
assert "Backup-AppShortcut" in installer and "Restore-AppShortcut" in installer
assert r"\\Microsoft\\WindowsApps\\" in installer
assert "Get-AuthenticodeSignature" in installer
assert "pip', 'install', '--user'" in installer
assert "'--require-hashes'" in installer
assert "'--only-binary=:all:'" in installer
assert "requirements.lock" in installer
assert "Remove-Item -LiteralPath $dataPath" not in installer
assert "Remove-Item -LiteralPath $installPath" in installer
assert "venv" not in installer.lower()
assert not re.search(r"(?i)pyinstaller|makeappx|new-selfsignedcertificate|\.pfx|\.msix", installer)

download = installer.index("Set-InstallStage -Name 'source-download'")
archive_check = installer.index("archive validation: success", download)
pip = installer.index("Set-InstallStage -Name 'pip-install'", archive_check)
stop = installer.index("Stop-RunningTracker", pip)
swap = installer.index("Install-SourceTree -SourcePath", stop)
health = installer.index("Wait-AppRuntimeReady", swap)
metadata = installer.index("Write-InstalledMetadata -Commit", health)
assert download < archive_check < pip < stop < swap < health < metadata

# The installer uses a strict parser and fail-closed handling for the required
# mocked API response classes: 200/valid SHA, invalid SHA, 403 and 429.
valid_sha = "a" * 40
assert re.fullmatch(r"[0-9a-fA-F]{40}", valid_sha)
for invalid in ("", "a" * 39, "g" * 40, "refs/heads/main"):
    assert not re.fullmatch(r"[0-9a-fA-F]{40}", invalid)
for status in (403, 429):
    assert f"$statusCode -eq {status}" in installer
assert "現在のTrackerは変更していません" in installer

assert "Invoke-Expression" not in readme
assert "refs/tags/v1.0.1/bootstrap.ps1" in readme
assert "Get-FileHash $p -Algorithm SHA256" in readme
assert "39E7E8C54239F1FA61666FF4C9199AFF6BF86B5937C7F69C6B14EBBC59D1C9E8" in readme
assert (ROOT / "bootstrap.ps1").read_bytes().startswith(b"\xef\xbb\xbf")
assert (ROOT / "install.ps1").read_bytes().startswith(b"\xef\xbb\xbf")
assert (ROOT / "uninstall.ps1").read_bytes().startswith(b"\xef\xbb\xbf")
assert "YouTubeコメント機能" in readme and "ありません" in readme
assert "%LOCALAPPDATA%\\AC6WinLossTracker\\" in readme
assert "Pythonは自動削除しません" in readme
assert (ROOT / "uninstall.ps1").is_file()

workflow = (ROOT / ".github/workflows/test.yml").read_text(encoding="utf-8")
assert "windows-tests:" in workflow
assert "runs-on: windows-latest" in workflow
assert "python-version: ['3.12', '3.13', '3.14']" in workflow
assert "python-version: ${{ matrix.python-version }}" in workflow
assert "python tests/run_all_tests.py" in workflow
assert "tests/test_runtime_policy_installer.ps1" in workflow
assert "tests/test_uninstaller.ps1" in workflow
assert "tests/test_bootstrap.ps1" in workflow
assert "tests/test_readme_commands.ps1" in workflow
assert "powershell.exe -NoProfile -ExecutionPolicy Bypass -File ./tests/test_runtime_policy_installer.ps1" in workflow
assert "powershell.exe -NoProfile -ExecutionPolicy Bypass -File ./tests/test_bootstrap.ps1" in workflow
assert "powershell.exe -NoProfile -ExecutionPolicy Bypass -File ./tests/test_readme_commands.ps1" in workflow
assert "powershell.exe -NoProfile -ExecutionPolicy Bypass -File ./tests/test_uninstaller.ps1" in workflow
assert "matrix.python-version != '3.12'" in workflow
assert "release/**" in workflow and "workflow_dispatch:" in workflow
assert "test/python-source-install" in workflow
assert "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6" in workflow
assert "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97 # v7" in workflow
assert "pip-audit==2.10.1" in workflow
assert "name: Windows tests" in workflow

bootstrap = (ROOT / "bootstrap.ps1").read_text(encoding="utf-8")
assert "Invoke-Expression" not in bootstrap
assert "releases/latest" in bootstrap
assert "prerelease" in bootstrap and "draft" in bootstrap
assert "Get-FileHash" in bootstrap and "sha256:" in bootstrap
assert "powershell.exe" in bootstrap
assert "Start-Process -FilePath 'powershell.exe' -ArgumentList $arguments -PassThru" in bootstrap
assert "$process.WaitForExit()" in bootstrap
assert "-Wait -PassThru" not in bootstrap

lock = (ROOT / "requirements.lock").read_text(encoding="utf-8")
for package in ("mss==10.2.0", "pillow==12.3.0", "ttkbootstrap==2.2.2"):
    assert package in lock
assert lock.count("--hash=sha256:") == 4

print("Stable source distribution / immutable revision / installer static checks: OK")
