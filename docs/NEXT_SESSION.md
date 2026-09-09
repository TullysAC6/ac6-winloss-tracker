# Next session

Read this first. Full state: [PROJECT_STATE.md](PROJECT_STATE.md) · Roadmap: [#6](https://github.com/TullysAC6/ac6-winloss-tracker/issues/6)

## Current objective

Get the RC through real-AC6 acceptance. No code work is blocking it.

## Current branch

`codex/wgc-rc-validation-20260908` (RC) — acceptance tracked in [#7](https://github.com/TullysAC6/ac6-winloss-tracker/issues/7)

## Current PR

[#5](https://github.com/TullysAC6/ac6-winloss-tracker/pull/5) — settings / analytics. **Draft. Do not merge.**

## Next 3 actions

1. Run the [#7](https://github.com/TullysAC6/ac6-winloss-tracker/issues/7) real-AC6 checklist and tick only what was actually observed in game.
2. If it passes: merge the RC to `main`, release, then close [#4](https://github.com/TullysAC6/ac6-winloss-tracker/issues/4) referencing that release.
3. Re-check PR #5 against the RC head, then take it out of draft for review.

## Release blockers

Real-AC6 behaviour only — WIN / LOSE counting, DRAW leaving totals unchanged, no double counting, Alt+Tab and WGC stall recovery, stale-frame safety, client-rect change, effect rendering, effect screenshot actually written, effect replay after a reconnect.

No known code defect.

## Do not touch

- `main` directly
- Another assistant's branch or worktree (no reset, rebase or force-push)
- Detector thresholds, `ResultGate`, CLEAR re-arm, WGC / MSS safety conditions, screenshot safety conditions
- Version strings, `install.ps1`, tags, releases — until #7 passes

## If something fails during acceptance

Create the diagnostic report **before quitting the Tracker**: Settings → サポート → 「Diagnostic Reportを作成」. The recent-frame ring only exists inside the running process.
