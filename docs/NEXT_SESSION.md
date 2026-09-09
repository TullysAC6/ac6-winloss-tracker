# Next session

Last updated: 2026-09-10

Read this first. Requirements: [MASTER_REQUIREMENTS.md](MASTER_REQUIREMENTS.md) ·
Full state: [PROJECT_STATE.md](PROJECT_STATE.md) · Roadmap: [ROADMAP.md](ROADMAP.md) ·
GitHub entry point: [#6](https://github.com/TullysAC6/ac6-winloss-tracker/issues/6)

## Canonical requirement

**Master Requirements Revision 2 (2026-09-09)** is the current canonical requirement, and it is in
the repository. If an older chat, issue, PR comment or README note conflicts with it, do not
silently follow the older material — check the code, keep
`Requirement` / `Implemented` / `Accepted` / `Released` separate, and reconcile it in the documents.

## Current branch and owner

Public stable: `v1.0.1`. `main`: `a6b7994`.
RC: `codex/wgc-rc-validation-20260908` at `3853cfd`, following PR #19 integration.
Pre-fix RC baseline: `b9d627c`; reviewed fix: `cdefd57`.
Owner: **Claude Code (handoff from Astra)**. PR #19 owner: **Claude Code**.

## Current PRs

- [#19](https://github.com/TullysAC6/ac6-winloss-tracker/pull/19) — merged into RC; technical review PASS / focused real-AC6 T3 pending. Not released.

- [#5](https://github.com/TullysAC6/ac6-winloss-tracker/pull/5) — settings / analytics. **Draft. Do not merge.**
- [#13](https://github.com/TullysAC6/ac6-winloss-tracker/pull/13) — coordination documents. **Draft. Documentation only.**

## Current focus and evidence (2026-09-10)

**RC owner: Claude Code (handoff from Astra). PR #19 owner: Claude Code.**
The immediate blocker is **PR #19 focused real-AC6 T3 pending**.

PR #19 (`cdefd57`) was merged into the RC at `3853cfd` on 2026-09-10 JST.
Its pre-fix RC baseline was `b9d627c`. It is no longer Draft or unmerged.
This RC integration is not a merge to `main`, release, or real-AC6 acceptance.

| Gate for PR #19 | Status |
|---|---|
| T0 | PASS |
| T1 | N/A — formal fixture/replay harness #14 is not implemented; no PASS claimed |
| T2 | PASS |
| Independent technical review | PASS — supports RC integration and focused T3 |
| GitHub review state | COMMENTED, not APPROVED: the author account cannot approve its own PR |
| Focused real-AC6 T3 | Pending |

Evidence: [2026-09-09 real session](https://github.com/TullysAC6/ac6-winloss-tracker/issues/7#issuecomment-5603490470),
[PR #19 and independent review](https://github.com/TullysAC6/ac6-winloss-tracker/pull/19),
[Windows CI](https://github.com/TullysAC6/ac6-winloss-tracker/actions/runs/34363401003).
The gate results describe the reviewed fix; no new test execution is claimed by this documentation sync.
Ordinary PR CI skips native screenshot paths; the independent opt-in Windows runs supply that evidence.

The 2026-09-09 session confirmed WIN detection/counting and the five-win banner.
Screenshot PNG saving FAILED because a DWM-cloaked shell window was treated as an occluder.
The cause is identified and corrected in PR #19, now integrated into the RC, but the actual
AC6 scene plus Tracker banner in one saved PNG remains unaccepted.
Earlier blanket statements that no code defect blocked the RC are superseded by this evidence.

### Focused screenshot T3

1. Turn Screenshot ON on the fixed RC.
2. Reach a five-win milestone.
3. Observe the Tracker banner.
4. Confirm exactly one PNG is saved to the Desktop for that effect.
5. Confirm the PNG contains the actual AC6 scene and Tracker banner together.
6. Confirm `effect-screenshot.jsonl` records `status=saved` for the same effect.

**Focused T3 PASS does not complete RC-wide acceptance (#7).**
Preserve relevant existing T3 evidence; do not repeat unrelated accepted observations merely
because the screenshot-only correction was integrated.

## Release blockers

Issue #7 remains the RC-wide acceptance checklist. Retain all unaccepted items, including:

- LOSE detection/counting
- DRAW semantics: WIN / LOSE / streak unchanged
- No double counting across a match sequence
- Alt+Tab recovery
- WGC stall recovery
- Stale-frame safety
- Client-rect changes
- SSE reconnect effect behavior: exactly-once fresh milestone playback
- Focused screenshot T3 above, and any remaining milestone coverage in #7

WIN/counting and the five-win banner have dated real-session evidence; this is not acceptance
of all result cases or all milestone levels. Reconcile evidence with #7 without claiming unchecked
items passed. Its older body and review-pending comment must be read with the newer PR #19 review
and merge state; this sync does not tick its acceptance boxes.

## Next actions

1. Claude: PR #19 integration into the RC is complete (`3853cfd`); confirm the fixed RC used for T3 includes it. Do not merge the RC to `main` yet.
2. User: perform the focused screenshot T3 above.
3. Complete the remaining Issue #7 T3 acceptance, retaining valid unrelated evidence.
4. After all RC acceptance passes, proceed through the normal RC → `main` → release process.
5. Close #4 with a reference to the release containing its fix.
6. Reconcile PR #5 with the new `main` without reset, rebase or force-push.
7. On reconciled PR #5: T0 → T1 (where available; otherwise explicitly N/A) → T2 → PR handoff / review → fixes → affected gate rerun → T3, then merge only after acceptance.
8. After that, begin #14 on a fresh branch/worktree from the new `main`.

## Ownership and process safety

The RC handoff does not waive process ownership. Before reusing Astra-origin processes or
worktrees, Claude must check PID, port, mutex/lock/runtime files and residual processes.
Avoid duplicate launch; use bounded timeouts, maintain ownership, clean up on every exit path,
and verify child processes and port/lock release. No long-running process is needed for this
documentation/project-management sync.

## Do not start a third feature generation

Generation A = RC + corrective PR #19; Generation B = PR #5 (Draft, blocked on RC acceptance).
PR #19 belongs to A and is not a third generation. The
limit is two and is reached; do not start #14 or another third feature generation now. A third starts only after the RC reaches `main` and PR #5 is reconciled with it.

## The development flow, in order

```text
Implementation
→ T0  Unit / DB / Static
→ T1  Fixture Replay
→ T2  Isolated E2E / Lifecycle
→ PR handoff
→ Codex/Astra independent review
→ Required fixes
→ Re-run the affected T0–T2
→ T3  Real AC6 Smoke
→ main
```

**T0–T2 pass before the PR goes to review**, not merely before T3 — the reviewer reads the gate
evidence as part of the change. T1 (fixture replay) does not exist yet; it is #14.

**A review fix invalidates the gate evidence for the paths it touched.** Re-run the affected
T0–T2 before asking the user for T3.

Ask the user only for the smallest real-AC6 set the change actually needs — not a full manual
regression pass. See [DECISIONS.md](DECISIONS.md).

Every implementation PR fills in
[`.github/pull_request_template.md`](../.github/pull_request_template.md). `Not changed` is
mandatory.

## Do not touch

- `main` directly
- Another assistant's branch or worktree (no reset, rebase or force-push)
- Detector thresholds, `ResultGate`, CLEAR re-arm, WGC / MSS safety conditions, screenshot safety
  conditions
- Version strings, `install.ps1`, tags, releases — until #7 passes

## Never start and forget a long-running process

Tracker, server, overlay, watcher, dev server, capture worker, polling loop. If one is genuinely
needed: check existing PID, port, mutex/lock/runtime file; avoid a duplicate launch; use a timeout;
record the PID; keep ownership; clean up on success, failure, exception **and** interruption; check
children and grandchildren; verify the port and lock are released; and state in the final report
whether anything remains.

"The command did not return, so leave it running and continue" is forbidden.

## If something fails during acceptance

Create the diagnostic report **before quitting the Tracker**: Settings → サポート →
「Diagnostic Reportを作成」. The recent-frame ring only exists inside the running process.
