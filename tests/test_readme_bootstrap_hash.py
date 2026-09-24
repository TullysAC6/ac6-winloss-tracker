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
# README "1つ前の公開版に戻す（ロールバック）" runs the previous public release's
# own install command. Its bootstrap is that release's published blob, not
# HEAD's; the value below is the v1.2.0 blob hash recorded at release. Move
# both constants to the release being superseded when the next one ships.
ROLLBACK_HEADING = "## 1つ前の公開版に戻す（ロールバック）"
PREVIOUS_RELEASE = "v1.2.0"
PREVIOUS_RELEASE_BOOTSTRAP_SHA256 = "82B223413A44BF9FDBBF399E7EED2AF6983794151DD25C9EE939B569BCD5881B"


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
before, heading, after = readme.partition(ROLLBACK_HEADING)
if not heading:
    raise SystemExit(f"README has no rollback section: {ROLLBACK_HEADING}")
rollback_section, _, rest = after.partition("\n## ")
current = before + "\n## " + rest
found = re.findall(r"-ne '([0-9A-Fa-f]{64})'", current)
if len(found) != 2:
    raise SystemExit(
        f"expected exactly 2 bootstrap hash comparisons in README, found {len(found)}"
    )
install_hash, uninstall_hash = found

rollback_found = re.findall(r"-ne '([0-9A-Fa-f]{64})'", rollback_section)
if rollback_found != [PREVIOUS_RELEASE_BOOTSTRAP_SHA256]:
    raise SystemExit(
        f"README rollback command must pin the {PREVIOUS_RELEASE} bootstrap "
        f"{PREVIOUS_RELEASE_BOOTSTRAP_SHA256}, found {rollback_found}"
    )
if f"refs/tags/{PREVIOUS_RELEASE}/bootstrap.ps1" not in rollback_section:
    raise SystemExit(f"README rollback command does not download the {PREVIOUS_RELEASE} bootstrap")
# CI checkouts are shallow and carry no tags; a full clone verifies the pinned
# value against the release's own committed blob.
if subprocess.run(("git", "cat-file", "-e", f"{PREVIOUS_RELEASE}:bootstrap.ps1"), cwd=str(ROOT),
                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
    previous_blob_hash = hashlib.sha256(git("show", f"{PREVIOUS_RELEASE}:bootstrap.ps1")).hexdigest().upper()
    if previous_blob_hash != PREVIOUS_RELEASE_BOOTSTRAP_SHA256:
        raise SystemExit(
            f"{PREVIOUS_RELEASE}:bootstrap.ps1 hashes to {previous_blob_hash}, "
            f"not the pinned {PREVIOUS_RELEASE_BOOTSTRAP_SHA256}"
        )
    print(f"README rollback bootstrap SHA-256 matches the {PREVIOUS_RELEASE} blob: {previous_blob_hash}")
else:
    print(f"{PREVIOUS_RELEASE} tag not in this clone; rollback hash checked against the pinned value")

# A convention check, not a correctness one: `Get-FileHash ... .Hash` emits
# uppercase, so an uppercase literal is what the README should carry. PowerShell
# `-ne` is case-insensitive, so a lowercase literal would still have matched at
# install time -- only `-cne` would not.
for value in found:
    if value != value.upper():
        raise SystemExit(f"README bootstrap hash must be uppercase hex: {value}")

if install_hash != uninstall_hash:
    raise SystemExit(
        "README install and uninstall commands disagree on the bootstrap hash: "
        f"{install_hash} vs {uninstall_hash}"
    )

# Name the v1.1.0 failure mode specifically before the general check, so the
# error says what went wrong rather than only that two hashes differ. This is
# reachable only while the two byte streams differ, which is exactly when the
# mistake is possible.
if worktree != blob and install_hash == worktree_hash:
    raise SystemExit(
        "README bootstrap SHA-256 matches the working-tree (CRLF) bytes rather "
        "than the published blob. This is the v1.1.0 defect.\n"
        f"  README / working tree : {install_hash}\n"
        f"  published Git blob    : {blob_hash}"
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

print(f"README bootstrap SHA-256 matches the published Git blob: {blob_hash}")
print(
    "working tree differs from blob (line-ending translation active): "
    f"{worktree != blob}"
)
print("OK")
