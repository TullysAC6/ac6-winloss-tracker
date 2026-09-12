# Next session

Last updated: 2026-09-13 JST

Read [MASTER_REQUIREMENTS.md](MASTER_REQUIREMENTS.md) first, then [PROJECT_STATE.md](PROJECT_STATE.md), and the current GitHub branches/PRs/releases. **Read GitHub as the source of truth** — do not trust a SHA, version or status quoted in a chat log or in an older document, including this one.

## Reading order

```text
Issue #6
→ docs/MASTER_REQUIREMENTS.md   (Revision 4 in the Season docs-only generation; canonical when merged)
→ docs/PROJECT_STATE.md         (where the project actually is)
→ docs/NEXT_SESSION.md          (this file)
→ docs/ROADMAP.md
→ docs/DECISIONS.md
→ GitHub Project "AC6 Win/Loss Tracker Development"
→ the target issue / PR
```

## Current handoff

- Public stable is **v1.1.1 — RELEASED**. Tag `v1.1.1` → `e0d84768dd739118f4bb9183af3d111041bddf33`. `main` was `e032499` when this was written and advanced again when PR #13 merged; run `git rev-parse origin/main` rather than trusting that.
- **v1.1.0 is superseded.** Its runtime was accepted and it was published, but its formal release acceptance was never completed: the README one-liner inside the tag carried a bootstrap `SHA-256` computed from Windows CRLF working-tree bytes instead of the published Git blob bytes, so the command failed closed. The tag was not moved and the Release was not edited.
- v1.1.1 ships the **same accepted runtime** (`93d5a57`) with corrected immutable distribution metadata. Real-AC6 T3 was not re-requested; the v1.1.0 T3 evidence carries forward because the runtime is byte-unchanged.
- Issues #7 and #4 are closed against v1.1.1. Issue #6 stays open as the roadmap entry point.
- T1 remains **N/A / not run** because issue #14's formal fixture/replay harness does not exist. Do not call it PASS.
- Draft PR #5 is a separate generation and has not been reconciled with the current `main`.
- PR #13 is **merged**: Master Requirements Revision 3 (§52, §56–§64) is canonical on `main`. That documentation generation is closed; PR #5 was the only unmerged generation before this Revision 4 docs-only work began.
- Draft [PR #31](https://github.com/TullysAC6/ac6-winloss-tracker/pull/31) adds Revision 4 / §65 Season Catalog / Assignment as the docs-only second generation. It does not implement a manifest, network client, cache, schema, migration or Settings button.

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

1. Reconcile draft PR #5 with the current `main`. **Merge only — no reset, no rebase, no force-push.** Then T0 → T1 (N/A) → T2 → PR handoff (§40) → independent review → required fixes → re-run the affected gates → T3 → merge.
2. Then [#14](https://github.com/TullysAC6/ac6-winloss-tracker/issues/14), the fixture / replay harness, on a fresh branch and worktree from the new `main`.
3. Then [#24](https://github.com/TullysAC6/ac6-winloss-tracker/issues/24), and from there the **Sequencing** order in [ROADMAP.md](ROADMAP.md).

At most two unmerged generations exist at a time. PR #5 is the product generation and the Revision
4 Season requirements PR is the permitted docs-only second generation. Do not open a third or a
new feature branch until one lands.

## Order after that — dependency, not preference

The authoritative interleaved order is under **Sequencing** in [ROADMAP.md](ROADMAP.md):

v1.1.1 and PR #13 are done. From here:

```text
PR #5 → #14 → #24 → UI-0 → UI-1A → UI-1B
→ #15 → #28-A → UI-2 → #16-A / #28-B → UI-3A → #17 → #10/#11/#12 → UI-3B
→ #16-B → #18 → #27 → UI-4
```

Four placements in it are load-bearing and will look wrong to anyone reading only the phase tables:

- **#15 → #28-A → UI-2.** #15 supplies match / observation timestamps; #28-A settles catalog reconciliation and derived Season assignment; only then does UI-2 add Settings manual refresh and History Season state.
- **#16 owns the data, #25 owns the charts.** #16 computes periods, rolling win rate, per-season rank/rating series, deltas and achievements. UI-3A / UI-3B render them. Neither implements the other's half.
- **UI-3 is split.** UI-3A is Growth / Rank / Rating (#16-A, #28-B) and can ship as soon as its data exists; UI-3B is Opponent build statistics (#17, #10/#11/#12) and follows the recognition programme. They are not one milestone.
- **#16-B before #18.** Advanced analytics run on data the user has actually accumulated. Historical backfill is retroactive data entry and does not gate them.

At most two unmerged generations exist at a time (§4). PR #5 plus this docs-only Revision 4 work
use both slots; nothing else starts until one lands.

## What is newly recorded and must not be lost

| | Issue | Requirement |
|---|---|---|
| App-local Python environment / dependency isolation | [#24](https://github.com/TullysAC6/ac6-winloss-tracker/issues/24) | §56 |
| UI/UX polish — `Fluent shell × AC6 telemetry × Pachinko celebration`, Player vs Broadcast overlays | [#25](https://github.com/TullysAC6/ac6-winloss-tracker/issues/25) | §57–§63 |
| Tray / Launcher modernization — lifecycle change, last, own PR | [#26](https://github.com/TullysAC6/ac6-winloss-tracker/issues/26) | §63 |
| Self-build linkage — `self_build_id`, explicit selection, never inferred | [#27](https://github.com/TullysAC6/ac6-winloss-tracker/issues/27) | §52 |
| Seasonal rank / rating progression — per season, never carried forward, A/S not one scale | [#28-B](https://github.com/TullysAC6/ac6-winloss-tracker/issues/28) | §64 |
| Season Catalog / Assignment — manual-first, retrospective, multi-season and transition-safe | [#28-A](https://github.com/TullysAC6/ac6-winloss-tracker/issues/28) | §65 |

Four things in there are easy to erode and are the reason they are written down:

- **A venv is not a sandbox.** `requirements.lock`, hash pinning and binary-only policy survive the runtime-isolation migration untouched, and `pythonw` worker PID ownership must be re-proved, not assumed.
- **A UI change may not cost game performance**, and UI polish is never a reason to touch Detector, ResultGate, WGC or process lifecycle.
- **No framework migration as the opening move** of visual modernisation.
- **The pre-S → S rating boundary is not one continuous line.** The ladder is `UNRANKED → … → A4 → S`, and the boundary is **pre-S / non-S (through A4) vs S** — A4 is on the pre-S side, so never write "below A" for it. The game presents rating differently on each side, so the obvious-looking single-line chart asserts a comparison the game does not support, and it fails silently. Rank/Rating recognition is event-driven, never a continuous OCR loop, and a failed read is recorded as a failed read — never as a rating change.
- **Season synchronization is manual-first catalog reconciliation, not cadence inference.** Unknown and transition records stay unresolved; a long absence fetches every missing Season definition; failure keeps cached data and never affects result persistence.

## STOP conditions

Stop before publication on: an unexpected `main`; an existing tag or Release for the version being prepared; CI failure; a Git-blob / raw / README hash mismatch; a tagged-tree or asset mismatch; a checksum or digest mismatch; a High or Release blocker; a required runtime or gameplay change during release prep; user-data damage; or any state that cannot be rolled back.

Never move the `v1.1.0` or `v1.1.1` tag.

## Process safety

Do not stop or reinstall the user's current Tracker unless explicitly required and authorized. Release tests use isolated roots and ports, bounded waits, and must leave no child/grandchild process, test port, runtime file, mutex, staging directory or temporary asset directory behind. Stub installer child execution when smoke-testing the public distribution, so the user's live install, history and config are untouched.
