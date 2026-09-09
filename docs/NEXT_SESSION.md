# Next session

Read this first. Requirements: [MASTER_REQUIREMENTS.md](MASTER_REQUIREMENTS.md) ·
Full state: [PROJECT_STATE.md](PROJECT_STATE.md) · Roadmap: [ROADMAP.md](ROADMAP.md) ·
GitHub entry point: [#6](https://github.com/TullysAC6/ac6-winloss-tracker/issues/6)

## Canonical requirement

**Master Requirements Revision 2 (2026-09-09)** is the current canonical requirement, and it is in
the repository. If an older chat, issue, PR comment or README note conflicts with it, do not
silently follow the older material — check the code, keep
`Requirement` / `Implemented` / `Accepted` / `Released` separate, and reconcile it in the documents.

## Current objective

Get the RC through real-AC6 acceptance. No code work is blocking it.

## Current branch

`codex/wgc-rc-validation-20260908` (RC, `746f73f`) — acceptance tracked in
[#7](https://github.com/TullysAC6/ac6-winloss-tracker/issues/7)

## Current PRs

- [#5](https://github.com/TullysAC6/ac6-winloss-tracker/pull/5) — settings / analytics. **Draft. Do not merge.**
- [#13](https://github.com/TullysAC6/ac6-winloss-tracker/pull/13) — coordination documents. **Draft. Documentation only.**

## Next 3 actions

1. Run the [#7](https://github.com/TullysAC6/ac6-winloss-tracker/issues/7) real-AC6 checklist and
   tick only what was actually observed in game.
2. If it passes: merge the RC to `main`, release, then close
   [#4](https://github.com/TullysAC6/ac6-winloss-tracker/issues/4) referencing that release.
3. Re-check PR #5 against the RC head, then take it out of draft for review.

Only after those: Phase D — the fixture / replay harness
([#14](https://github.com/TullysAC6/ac6-winloss-tracker/issues/14)) — on a fresh branch and
worktree from the new `main`.

## Do not start a third feature generation

Two unmerged generations already exist: the RC (under acceptance) and PR #5 (under review). The
limit is two. A third starts only after the RC reaches `main` and PR #5 is reconciled with it.

## Before handing anything to the user for testing

T0 (unit / DB / static) and T2 (isolated E2E, lifecycle, process cleanup, feature-flag OFF) must
pass first, and T1 (fixture replay) once #14 exists. Ask only for the smallest real-AC6 set the
change actually needs — not a full manual regression pass. See [DECISIONS.md](DECISIONS.md).

Every implementation PR fills in
[`.github/pull_request_template.md`](../.github/pull_request_template.md). `Not changed` is
mandatory.

## Release blockers

Real-AC6 behaviour only — WIN / LOSE counting, DRAW leaving totals unchanged, no double counting,
Alt+Tab and WGC stall recovery, stale-frame safety, client-rect change, effect rendering, effect
screenshot actually written, effect replay after a reconnect.

No known code defect.

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
