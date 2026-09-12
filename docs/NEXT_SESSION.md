# Next session

Last updated: 2026-09-12 JST

Read [MASTER_REQUIREMENTS.md](MASTER_REQUIREMENTS.md) first, then [PROJECT_STATE.md](PROJECT_STATE.md), and the current GitHub branches/PRs/releases. **Read GitHub as the source of truth** — do not trust a SHA, version or status quoted in a chat log or in an older document, including this one.

## Current handoff

- Public stable is **v1.1.1 — RELEASED**. Tag `v1.1.1` → `e0d84768dd739118f4bb9183af3d111041bddf33`; `main` is `e0d8476`.
- **v1.1.0 is superseded.** Its runtime was accepted and it was published, but its formal release acceptance was never completed: the README one-liner inside the tag carried a bootstrap `SHA-256` computed from Windows CRLF working-tree bytes instead of the published Git blob bytes, so the command failed closed. The tag was not moved and the Release was not edited.
- v1.1.1 ships the **same accepted runtime** (`93d5a57`) with corrected immutable distribution metadata. Real-AC6 T3 was not re-requested; the v1.1.0 T3 evidence carries forward because the runtime is byte-unchanged.
- Issues #7 and #4 are closed against v1.1.1. Issue #6 stays open as the roadmap entry point.
- T1 remains **N/A / not run** because issue #14's formal fixture/replay harness does not exist. Do not call it PASS.
- Draft PR #5 is a separate generation and has not been reconciled with the current `main`.
- Draft PR #13 carries Master Requirements Revision 3 and is documentation only.

## The rule that cost a release — do not lose it

**A SHA-256 published in the README is the hash of the committed Git blob, never of a file on disk.**

`raw.githubusercontent.com` serves the blob. On Windows, `core.autocrlf=true` leaves CRLF in the working tree, so the same file hashes differently there — 8814 bytes versus 8619 for `bootstrap.ps1`. Compute it with `git show <commit>:bootstrap.ps1` captured as raw bytes, then confirm it against the real public raw URL for that commit before it goes anywhere near the README.

`tests/test_readme_bootstrap_hash.py` now enforces this. It derives the expected value from the blob, refuses to run while `bootstrap.ps1` is dirty, and fails specifically when the README matches the working-tree bytes. Do not replace it with a literal-comparison check — `test_stable_distribution_static.py` already does that, and that is the assertion that passed while v1.1.0 was broken.

## Release procedure that worked, for next time

1. Change version metadata and `bootstrap.ps1`, then **commit to freeze `bootstrap.ps1`'s bytes**.
2. Compute the blob SHA-256 from that commit.
3. Push, and confirm the same hash from `raw.githubusercontent.com/<repo>/<commit>/bootstrap.ps1` with `-OutFile` + `Get-FileHash`.
4. Only then write the hash into the README, and re-confirm `bootstrap.ps1`'s blob did not move in any later commit.
5. T0 → T2 → independent release-diff review → re-run affected gates. T1 stays N/A until #14.
6. PR to `main`, green CI, merge, green `main` CI.
7. Pre-tag STOP gate: tag and Release absent, versions consistent, and blob == raw == README.
8. Annotated tag on the exact `main` SHA; assets from `scripts/prepare-release-assets.ps1` in a clean checkout of that tag.
9. Publish, then verify against the real public URLs, including the tag's own README one-liner.

## Required order from here

1. Merge documentation PR #13 so Revision 3 is canonical on `main`.
2. Reconcile draft PR #5 with the current `main`. **Merge only — no reset, no rebase, no force-push.** Then T0 → T1 (N/A) → T2 → PR handoff (§40) → independent review → required fixes → re-run the affected gates → T3 → merge.
3. Then [#14](https://github.com/TullysAC6/ac6-winloss-tracker/issues/14), the fixture / replay harness, on a fresh branch and worktree from the new `main`.

At most two unmerged generations exist at a time. Today those are PR #5 and PR #13.

## STOP conditions

Stop before publication on: an unexpected `main`; an existing tag or Release for the version being prepared; CI failure; a Git-blob / raw / README hash mismatch; a tagged-tree or asset mismatch; a checksum or digest mismatch; a High or Release blocker; a required runtime or gameplay change during release prep; user-data damage; or any state that cannot be rolled back.

Never move the `v1.1.0` or `v1.1.1` tag.

## Process safety

Do not stop or reinstall the user's current Tracker unless explicitly required and authorized. Release tests use isolated roots and ports, bounded waits, and must leave no child/grandchild process, test port, runtime file, mutex, staging directory or temporary asset directory behind. Stub installer child execution when smoke-testing the public distribution, so the user's live install, history and config are untouched.
