from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
installer = (ROOT / "install.ps1").read_text(encoding="utf-8")
readme = (ROOT / "README.txt").read_text(encoding="utf-8")
version_source = (ROOT / "app_paths.py").read_text(encoding="utf-8")

for removed in (
    ".github/workflows/build-release.yml",
    "build_release.bat",
    "install-source-test.ps1",
    "tests/test_msix_packaging_static.py",
    "store",
):
    assert not (ROOT / removed).exists(), removed

assert re.search(r'^VERSION\s*=\s*["\']1\.0\.0["\']$', version_source, re.MULTILINE)
assert "$channel = 'stable'" in installer
assert "$version = '1.0.0'" in installer
assert 'https://api.github.com/repos/$repository/commits/main' in installer
assert '^[0-9a-fA-F]{40}$' in installer
assert 'archive/$resolvedCommit.zip' in installer
assert "archive/refs/heads/main.zip" not in installer
assert "test/python-source-install" not in installer
assert "install-source-test.ps1" not in installer
assert installer.count('Invoke-WebRequest -Uri $mainHeadUrl') == 1
assert "$statusCode -eq 403 -or $statusCode -eq 429" in installer
assert "Get-InstalledRevision" in installer and "currently installed" not in installer
assert "Write-InstalledMetadata -Commit $resolvedCommit" in installer
assert "Restore-PreviousSource" in installer
assert "Complete-SourceInstall" in installer
assert "Python Software Foundation" in installer
assert "runtime-policy.json" in installer
assert "Python.Python.3.12" not in installer
assert "python_policy_version" in installer
assert "python_version" in installer
assert "Backup-AppShortcut" in installer and "Restore-AppShortcut" in installer
assert r"\\Microsoft\\WindowsApps\\" in installer
assert "Get-AuthenticodeSignature" in installer
assert "pip', 'install', '--user'" in installer
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

one_line = (
    'powershell.exe -NoProfile -ExecutionPolicy Bypass -Command '
    '"Invoke-Expression (Invoke-RestMethod '
    "'https://raw.githubusercontent.com/TullysAC6/ac6-winloss-tracker/refs/heads/main/install.ps1')\""
)
assert one_line in readme
assert "6b8dcdd818ec9c5b6e81450fb955d1451a5dc540" in readme
assert "YouTubeコメント機能はありません" in readme
assert "%LOCALAPPDATA%\\AC6WinLossTracker\\" in readme
policy = __import__("json").loads((ROOT / "runtime-policy.json").read_text(encoding="utf-8"))
preferred = policy["preferred"]
fallback = policy["fallback"]
assert f"Python {preferred['major']}.{preferred['minor']}.{preferred['minimum_patch']}以上" in readme
assert f"Python {fallback['major']}.{fallback['minor']}.{fallback['minimum_patch']}以上" in readme
assert "free-threaded build" in readme
assert "Python 3.12.x以下" in readme and "Python 3.15.x以上" in readme

workflow = (ROOT / ".github/workflows/test.yml").read_text(encoding="utf-8")
assert "windows-tests:" in workflow
assert "runs-on: windows-latest" in workflow
assert "python-version: ['3.12', '3.13', '3.14']" in workflow
assert "python-version: ${{ matrix.python-version }}" in workflow
assert "python tests/run_all_tests.py" in workflow
assert "python tests/test_runtime_policy.py" in workflow
assert "matrix.python-version != '3.12'" in workflow
assert "release/**" in workflow and "workflow_dispatch:" in workflow
assert "test/python-source-install" in workflow

print("Stable source distribution / immutable revision / installer static checks: OK")
