"""The README bootstrap SHA-256 must describe the bytes GitHub actually serves.

`raw.githubusercontent.com` returns the committed Git blob. On Windows a checkout
with `core.autocrlf=true` leaves CRLF in the working tree, so the working-tree
file hashes to something else entirely. v1.1.0 shipped a README whose hash was
computed from those working-tree bytes, and the published install command
therefore failed closed with `bootstrap SHA-256 mismatch` for every user.

This test derives the expected value from the blob instead of trusting a literal,
which is the part neither `test_stable_distribution_static.py` (string presence)
nor `test_readme_commands.ps1` (command shape and parse) can check.
"""
from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def git(*args: str) -> bytes:
    result = subprocess.run(
        ("git", *args), cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    if result.returncode:
        raise SystemExit(
            f"git {' '.join(args)} failed: {result.stderr.decode('utf-8', 'replace')}"
        )
    return result.stdout


if not (ROOT / ".git").exists() and not (ROOT / ".git").is_file():
    print("SKIP: not a git checkout")
    sys.exit(0)

# bootstrap.ps1 must be committed, or HEAD does not describe what would be
# published and this test would vouch for bytes nobody can fetch.
if subprocess.run(
    ("git", "diff", "--quiet", "HEAD", "--", "bootstrap.ps1"), cwd=str(ROOT)
).returncode:
    raise SystemExit(
        "bootstrap.ps1 has uncommitted changes; its published hash cannot be verified"
    )

blob = git("show", "HEAD:bootstrap.ps1")
blob_hash = hashlib.sha256(blob).hexdigest().upper()

worktree = (ROOT / "bootstrap.ps1").read_bytes()
worktree_hash = hashlib.sha256(worktree).hexdigest().upper()

readme = (ROOT / "README.md").read_text(encoding="utf-8")
found = re.findall(r"-ne '([0-9A-Fa-f]{64})'", readme)
if len(found) != 2:
    raise SystemExit(
        f"expected exactly 2 bootstrap hash comparisons in README, found {len(found)}"
    )
install_hash, uninstall_hash = found

# `Get-FileHash ... .Hash` is uppercase, and the README compares with -ne, so a
# lowercase literal would never match at install time.
for value in found:
    if value != value.upper():
        raise SystemExit(f"README bootstrap hash must be uppercase hex: {value}")

if install_hash != uninstall_hash:
    raise SystemExit(
        "README install and uninstall commands disagree on the bootstrap hash: "
        f"{install_hash} vs {uninstall_hash}"
    )

if install_hash != blob_hash:
    raise SystemExit(
        "README bootstrap SHA-256 does not match the committed Git blob, which is "
        "what raw.githubusercontent.com serves.\n"
        f"  README        : {install_hash}\n"
        f"  Git blob      : {blob_hash}\n"
        f"  working tree  : {worktree_hash}\n"
        "Compute it from `git show HEAD:bootstrap.ps1`, never from the file on disk."
    )

# The specific way this went wrong before: the literal matched the CRLF working
# tree. Only assert it where the two actually differ, so the test stays valid on
# a checkout without line-ending translation.
if worktree != blob and install_hash == worktree_hash:
    raise SystemExit(
        "README bootstrap SHA-256 matches the working-tree (CRLF) bytes rather "
        "than the published blob. This is the v1.1.0 defect."
    )

print(f"README bootstrap SHA-256 matches the published Git blob: {blob_hash}")
print(
    "working tree differs from blob (line-ending translation active): "
    f"{worktree != blob}"
)
print("OK")
