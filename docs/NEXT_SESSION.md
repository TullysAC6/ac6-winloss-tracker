# Next session

Last updated: 2026-09-20 JST

Read [MASTER_REQUIREMENTS.md](MASTER_REQUIREMENTS.md) first, then [PROJECT_STATE.md](PROJECT_STATE.md), and the current GitHub branches/PRs/releases. **Read GitHub as the source of truth** — do not trust a SHA, version or status quoted in a chat log or in an older document, including this one.

## Reading order

```text
Issue #6
→ docs/MASTER_REQUIREMENTS.md   (Revision 4; canonical on main since PR #31 merged as e214ae0)
→ docs/PROJECT_STATE.md         (where the project actually is)
→ docs/NEXT_SESSION.md          (this file)
→ docs/ROADMAP.md
→ docs/DECISIONS.md
→ GitHub Project "AC6 Win/Loss Tracker Development"
→ the target issue / PR
```

## Current handoff

- Public stable is **[v1.2.0 — RELEASED](https://github.com/TullysAC6/ac6-winloss-tracker/releases/tag/v1.2.0)**, annotated tag `v1.2.0` → `c64b241c6b14b54bf7a4ac7897d3be42c8d7d9f4`. PR #33 merged, [main CI passed](https://github.com/TullysAC6/ac6-winloss-tracker/actions/runs/34792826474), public digests and both README one-liner smokes passed. Release bookkeeping advances `main` again; resolve `origin/main` for the current SHA.
- **v1.1.0 is superseded.** Its runtime was accepted and it was published, but its formal release acceptance was never completed: the README one-liner inside the tag carried a bootstrap `SHA-256` computed from Windows CRLF working-tree bytes instead of the published Git blob bytes, so the command failed closed. The tag was not moved and the Release was not edited.
- v1.1.1 (now superseded by v1.2.0) ships the **same accepted runtime** (`93d5a57`) with corrected immutable distribution metadata. Real-AC6 T3 was not re-requested; the v1.1.0 T3 evidence carries forward because the runtime is byte-unchanged.
- Issues #7 and #4 are closed against v1.1.1. Issue #6 stays open as the roadmap entry point.
- **Formal T1 exists and passes on `main`.** PR #35 (#14) is Implemented + Accepted + merged as `f9f5f0f` on 2026-09-18 ([review 5246833703](https://github.com/TullysAC6/ac6-winloss-tracker/pull/35#pullrequestreview-5246833703) GO, [main CI 35336092717](https://github.com/TullysAC6/ac6-winloss-tracker/actions/runs/35336092717) green).
  - `python tests/run_t1.py`: 42/42, 0 skipped, 25 images + 17 sequences, corpus SHA-256 `6cfc4873bd0aa2bd…`.
  - **T1 is a real gate now, not N/A.** Every product change runs T0 → T1 → T2, and a T1 regression blocks progression.
  - Production runtime was not changed by #35, so its T3 was N/A. v1.2.0 is unaffected.
  - **#14 is CLOSED as completed** (2026-09-19, [closure](https://github.com/TullysAC6/ac6-winloss-tracker/issues/14#issuecomment-5732296924); [acceptance record](https://github.com/TullysAC6/ac6-winloss-tracker/issues/14#issuecomment-5731935668)). Its §42 *Tracker OFF / accepted / + feature* baseline item now belongs to **[#37](https://github.com/TullysAC6/ac6-winloss-tracker/issues/37) — OPEN, PLANNED, not started**.
- **The T0 TEMP leak is fixed.** PR #38 (test-only; production runtime unchanged) merged as `3bd89a3` on 2026-09-19 after [independent review](https://github.com/TullysAC6/ac6-winloss-tracker/pull/38#pullrequestreview-5249924515) GO, and [`main` CI 35366334508](https://github.com/TullysAC6/ac6-winloss-tracker/actions/runs/35366334508) is green. A full T0 run leaves 0 `tmp*` directories (was 12).
  - Non-blocking harness follow-ups: L-A (`_winapi.CopyFile2` by keyword passes the T1 filesystem tripwire) and L-B (the corpus is path-identified, not content-pinned). Recorded; not scheduled.
  - On this cp932 host, run T2 with `PYTHONUTF8=1` and `PYTHONIOENCODING=utf-8`. `test_source_install_flow.ps1` needs `-PythonPath`, and `pwsh` is not installed locally (CI uses it).
- **PR #5 (#8 settings, #9 analytics) is Implemented + Accepted + merged to `main` (`a042b18`), and released in v1.2.0.**
  - Focused real-AC6 T3 PASS on the exact head `38a21c2` on 2026-09-13 ([record](https://github.com/TullysAC6/ac6-winloss-tracker/pull/5#issuecomment-5653677193)). `main` CI is green.
  - #8 and #9 are closed as completed.
  - v1.1.1 does **not** contain PR #5; v1.2.0 is the first release containing its settings and analytics.
- PR #13 merged Revision 3. PR #31 adds Revision 4 / §65, preserving manual-first Season catalog refresh, local cache, retrospective assignment, unresolved transitions and full multi-season reconciliation. Revision 4 is canonical on main after PR #31 merged as `e214ae0`; no Season runtime feature is implemented.
- PR #31 merged as `e214ae0` after independent review GO. [Exact-main CI 35479744429](https://github.com/TullysAC6/ac6-winloss-tracker/actions/runs/35479744429) is green. Its reconciliation and merge are complete; do not redo them.
- MASTER_REQUIREMENTS §34 and §36 retain their dated snapshots with explicit current-status corrections: PR #5 is released in v1.2.0, #14 is closed, §42 belongs to #37, and #24 is in progress.

## The rule that cost a release — do not lose it

**A SHA-256 published in the README is the hash of the committed Git blob, never of a file on disk.**

`raw.githubusercontent.com` serves the blob. On Windows, `core.autocrlf=true` leaves CRLF in the working tree, so the same file hashes differently there — 8814 bytes versus 8619 for `bootstrap.ps1`. Compute it with `git show <commit>:bootstrap.ps1` captured as raw bytes, then confirm it against the real public raw URL for that commit before it goes anywhere near the README.

`tests/test_readme_bootstrap_hash.py` now enforces this. It derives the expected value from the blob, refuses to run while `bootstrap.ps1` is dirty, and fails specifically when the README matches the working-tree bytes. Do not replace it with a literal-comparison check — `test_stable_distribution_static.py` already does that, and that is the assertion that passed while v1.1.0 was broken.

## Release procedure that worked, for next time

1. Change version metadata and `bootstrap.ps1`, then **commit to freeze `bootstrap.ps1`'s bytes**.
2. Compute the blob SHA-256 from that commit.
3. Push, and confirm the same hash from `raw.githubusercontent.com/<repo>/<commit>/bootstrap.ps1` with `-OutFile` + `Get-FileHash`.
4. Only then write the hash into the README, and re-confirm `bootstrap.ps1`'s blob did not move in any later commit.
5. T0 → T1 → T2 → independent release-diff review → re-run affected gates. (T1 was N/A for releases up to v1.2.0; it exists since PR #35.)
6. PR to `main`, green CI, merge, green `main` CI.
7. Pre-tag STOP gate: tag and Release absent, versions consistent, and blob == raw == README.
8. Annotated tag on the exact `main` SHA; assets from `scripts/prepare-release-assets.ps1` in a clean checkout of that tag.
9. Publish, then verify against the real public URLs, including the tag's own README one-liner.

Release publication and post-release verification are complete. See `PROJECT_STATE.md` for immutable tag, CI, review and digest evidence. No user installation was run during public smoke; installer/uninstaller child execution was stubbed.

## Required order from here

1. **[#24](https://github.com/TullysAC6/ac6-winloss-tracker/issues/24), the app-local Python environment.** IN PROGRESS — **[PR #40](https://github.com/TullysAC6/ac6-winloss-tracker/pull/40) is open on `claude/issue-24-app-local-python`; resolve the current head on GitHub.** The original review fixes are applied; exact-commit T3 install support is now being validated in the same PR.
   - Implementation is complete. T0 PASS, formal T1 42/42 with 0 skipped and corpus `6cfc4873bd0aa2bd…` unchanged, and the full T2 lifecycle/migration matrix PASS, all on Python 3.13 and 3.14. That pre-review evidence was produced on implementation commit `161e1b2`; documentation-only commits since did not alter the tested implementation.
   - The review required one test-only fix: the new containment test proved membership of *any* job rather than of the job the parent owns, so it could not fail. Repaired and re-verified by mutation on review-fix commit `c5f0767`, passing on Python 3.13 and 3.14. No production defect was found and no production file changed.
   - The review was performed by Claude Code as a one-time exception the user authorised for PR #40 only. MASTER_REQUIREMENTS §40 still names Codex/Astra as the independent reviewer and is deliberately unchanged; this is not a policy change.
   - T3 preparation exposed a real blocker: the public bootstrap and stable tag interface cannot install an unmerged PR head. `install.ps1 -SourceCommit <40-hex-SHA>` now selects an explicit candidate channel, verifies the exact commit returned by the official repository API and enters the same installer transaction. Stable defaults, bootstrap, public README commands and the dependency lock are unchanged.
   - This production installer change invalidates the earlier GO for the final candidate. Next: focused tests → full T0/T1/T2 (including PowerShell T0 and the installer matrix on 3.13/3.14) → exact-head CI → **fresh independent Codex review, explicitly authorized by the user** → required fixes and affected gate reruns → user T3. Resolve current evidence on PR #40; do not infer GO from the historical review below.
   - Only after final review GO, freeze the full PR head, hash the committed `install.ps1` Git blob, verify the public raw bytes have that hash, and give the user the verified script with `-SourceCommit` set to the same SHA. No moving refs, bootstrap edit, manual file copy or fake Release.
   - T3 checks real legacy migration, preserved historical results/configuration, one real WIN and one LOSE counted once, Overlay, normal close and relaunch. A screenshot is checked if its existing milestone occurs naturally; no manufactured results or forced win streak. Startup intentionally resets the session, so history preservation is a logical-data check, not byte equality of a running DB/stats file. Do not mark Accepted or Released, and do not merge before user T3.
   - Keep using the existing `claude/issue-24-app-local-python` branch / `ac6-wt-issue24-app-local-python` worktree. Never reset, recreate or rebase it.
2. Then the **Sequencing** order in [ROADMAP.md](ROADMAP.md).

The test-only TEMP-leak cleanup is **done**: PR #38, merged as `3bd89a3`.

At most two unmerged generations exist at a time (§4). PR #31 is merged. PR #40 (#24) is the sole active product generation; recheck GitHub before merging it.

## Order after that — dependency, not preference

The authoritative interleaved order is under **Sequencing** in [ROADMAP.md](ROADMAP.md):

v1.2.0, PR #13, PR #5 and PR #35 are done; PR #5 is accepted, merged and released in v1.2.0, and PR #35 (#14 formal T1) is accepted and merged. From here:

```text
#24 → UI-0 → UI-1A → UI-1B
→ #15 → #28-A → UI-2 → #16-A / #28-B → UI-3A → #17 → #10/#11/#12 → UI-3B
→ #16-B → #18 → #27 → UI-4
```

Four placements in it are load-bearing and will look wrong to anyone reading only the phase tables:

- **#15 → #28-A → UI-2.** #15 supplies match / observation timestamps; #28-A settles catalog reconciliation and derived Season assignment; only then does UI-2 add Settings manual refresh and History Season state.
- **#16 owns the data, #25 owns the charts.** #16 computes periods, rolling win rate, per-season rank/rating series, deltas and achievements. UI-3A / UI-3B render them. Neither implements the other's half.
- **UI-3 is split.** UI-3A is Growth / Rank / Rating (#16-A, #28-B) and can ship as soon as its data exists; UI-3B is Opponent build statistics (#17, #10/#11/#12) and follows the recognition programme. They are not one milestone.
- **#16-B before #18.** Advanced analytics run on data the user has actually accumulated. Historical backfill is retroactive data entry and does not gate them.

At most two unmerged generations exist at a time (§4). PR #31 is merged. PR #40 (#24) is the sole active product generation; recheck GitHub before merging it.

## What is newly recorded and must not be lost

| | Issue | Requirement |
|---|---|---|
| App-local Python environment / dependency isolation | [#24](https://github.com/TullysAC6/ac6-winloss-tracker/issues/24) | §56 |
| UI/UX polish — `Fluent shell × AC6 telemetry × Pachinko celebration`, Player vs Broadcast overlays | [#25](https://github.com/TullysAC6/ac6-winloss-tracker/issues/25) | §57–§63 |
| Tray / Launcher modernization — lifecycle change, last, own PR | [#26](https://github.com/TullysAC6/ac6-winloss-tracker/issues/26) | §63 |
| Self-build linkage — `self_build_id`, explicit selection, never inferred | [#27](https://github.com/TullysAC6/ac6-winloss-tracker/issues/27) | §52 |
| Seasonal rank / rating progression — per season, never carried forward, A/S not one scale | [#28-B](https://github.com/TullysAC6/ac6-winloss-tracker/issues/28) | §64 |
| Season Catalog / Assignment — manual-first, retrospective, multi-season and transition-safe | [#28-A](https://github.com/TullysAC6/ac6-winloss-tracker/issues/28) | §65 |

Five things in there are easy to erode and are the reason they are written down:

- **A venv is not a sandbox.** `requirements.lock`, hash pinning and binary-only policy survive the runtime-isolation migration untouched, and `pythonw` worker PID ownership must be re-proved, not assumed.
- **A UI change may not cost game performance**, and UI polish is never a reason to touch Detector, ResultGate, WGC or process lifecycle.
- **No framework migration as the opening move** of visual modernisation.
- **The pre-S → S rating boundary is not one continuous line.** The ladder is `UNRANKED → … → A4 → S`, and the boundary is **pre-S / non-S (through A4) vs S** — A4 is on the pre-S side, so never write "below A" for it. The game presents rating differently on each side, so the obvious-looking single-line chart asserts a comparison the game does not support, and it fails silently. Rank/Rating recognition is event-driven, never a continuous OCR loop, and a failed read is recorded as a failed read — never as a rating change.
- **Season synchronization is manual-first catalog reconciliation, not cadence inference.** Unknown and transition records stay unresolved; a long absence fetches every missing Season definition; failure keeps cached data and never affects result persistence.

## STOP conditions

Stop before publication on: an unexpected `main`; an existing tag or Release for the version being prepared; CI failure; a Git-blob / raw / README hash mismatch; a tagged-tree or asset mismatch; a checksum or digest mismatch; a High or Release blocker; a required runtime or gameplay change during release prep; user-data damage; or any state that cannot be rolled back.

Never move the `v1.1.0`, `v1.1.1` or `v1.2.0` tag.

## Process safety

Do not stop or reinstall the user's current Tracker unless explicitly required and authorized. Release tests use isolated roots and ports, bounded waits, and must leave no child/grandchild process, test port, runtime file, mutex, staging directory or temporary asset directory behind. Stub installer child execution when smoke-testing the public distribution, so the user's live install, history and config are untouched.

What worked for PR #5's T3, for the next one:

- **Isolate the run.** Run the exact-head worktree against an isolated `LOCALAPPDATA` seeded from a SHA-256-verified copy of the real data root. Use a read-only SSE logger and read-only before/after evidence, and the live data stays byte-identical.
- **Keep the normal launcher closed.** While that isolated instance owns port 8765, **do not start the normal launcher**. It correctly fails closed with `ENV-PORT-IN-USE`, but it still writes to the live `startup.log`.
- **Ignore empty SQLite sidecars.** A read-only (`?mode=ro`) SQLite query on the live `history.db` can leave empty `-wal` / `-shm` sidecars. That is not a data change.
