# Project state

Last updated: 2026-09-11 JST

Requirements: [MASTER_REQUIREMENTS.md](MASTER_REQUIREMENTS.md) · roadmap: [ROADMAP.md](ROADMAP.md) · decisions: [DECISIONS.md](DECISIONS.md) · GitHub entry point: [#6](https://github.com/TullysAC6/ac6-winloss-tracker/issues/6)

`Requirement` / `Implemented` / `Accepted` / `Released` are separate states throughout this file.

## Position before publication

| Item | State |
|---|---|
| Public stable | **v1.0.1**, tag target `54ba0d8` |
| `main` | `a6b7994` before the v1.1.0 merge |
| Accepted runtime RC | `codex/wgc-rc-validation-20260908` at `93d5a57b88a86ec4b8846a3082089ed97f13a818` |
| Release preparation | `release/v1.1.0`, based on exact RC `93d5a57`; publication pending |
| Other product generation | draft PR #5 on `claude/settings-analytics-20260909`; do not merge as part of v1.1.0 |

This release-preparation snapshot does **not** claim that v1.1.0 is released. `Released` is recorded only after the stable GitHub Release exists and post-release verification passes.

## Implemented

The accepted RC implements:

- Windows Graphics Capture for result recognition, with a guarded foreground desktop fallback.
- Continued recognition and recovery around Alt+Tab/capture interruption.
- Exactly-once fresh milestone effect recovery across an SSE reconnect (#4).
- Dashboard history expanded to the latest 50 rows with scrolling.
- Optional milestone Effect Screenshot and its settings GUI.
- Effect Screenshot capture of the composition the user actually sees over the AC6 client, including visible SteamP2PScanner/NVIDIA/Steam/Discord-style overlays. Overlap alone is not a screenshot rejection reason.
- Diagnostic and process/lifecycle hardening, including duplicate prevention and owned-worker cleanup.
- Source install/update/rollback/uninstall flows that preserve history and configuration.

Detector MSS fallback still uses `region_unobscured()`. Screenshot capture still requires the AC6 foreground/target HWND/client rect, visible Tracker effect, real banner pixels, duplicate suppression, and the bounded owned-worker lifecycle.

## Accepted

RC runtime `93d5a57` is accepted for the v1.1.0 release.

- T0: PASS on the RC; release metadata gates must be re-run on the final release-prep diff.
- T1: **N/A / not run**. The formal fixture/replay harness is issue #14 and is not implemented; no T1 PASS is claimed.
- T2: PASS on the RC; release distribution/lifecycle gates must be re-run on the final release-prep diff.
- Independent runtime review: PASS for PR #20; no High/Release blocker.
- Focused real-AC6 T3: PASS on 2026-09-11 JST. Five real wins produced exactly one PNG for effect `1m4k1FRsEEoa3U5mqRiDCL51`; the PNG contains the AC6 game, the real `5連勝 激アツ!!` banner, and the user's visible overlay composition. Evidence is recorded in issue #7.

The earlier `spsgui.exe` occlusion failure predates PR #20 and is superseded by this PASS.

## Release Acceptance scope

The following are not blockers for v1.1.0 because the same paths have automation and/or existing real-session evidence, while forcing every case in live play would be disproportionate or impractical:

- Natural DRAW: semantics are covered by result/state/history automation; no natural DRAW occurred in the recorded sessions.
- Milestones 10–50: the milestone-5 T3 exercises the shared real capture path; all configured levels are covered by automation/native screenshot checks.
- Natural WGC stall, stale-age rejection, client-rect mutation, and a milestone precisely during SSE reconnect: covered by targeted automation plus existing real recovery evidence where available.
- Alt+Tab: real capture loss/recovery and a subsequent exactly-once result are recorded; targeted automation covers the boundary conditions.

Residual non-blocking risk: rare GPU/compositor/window-manager timing may behave differently on an unobserved desktop. The bounded diagnostics and fail-closed optional screenshot path remain in place; screenshot failure cannot alter result counting.

## Release scope

Supported mode in v1.1.0 remains **RANK MATCH: SINGLE only**. The broader TEAM/CUSTOM requirements in the master requirements are future requirements, not implemented or released behavior.

## Next actions

1. Finish v1.1.0 release metadata/docs and re-run T0 then T2.
2. Review only the release-prep diff against runtime RC `93d5a57`.
3. Integrate release prep to the RC through a PR and confirm CI green.
4. Merge the accepted RC to `main` through a PR; confirm main CI green.
5. Create immutable tag `v1.1.0`, publish the stable GitHub Release with four verified assets, and perform post-release verification/smoke testing.
6. Only after publication and verification: close #7 and #4 and update this file to `Released` in a docs-only commit without moving the tag.

## Process safety

Do not modify the user's live Tracker, history, config, stats, diagnostics, port 8765, runtime files, or Overlay mutex during release testing. Use isolated temporary roots, bounded waits, and verify child/grandchild/process/port/temp cleanup.
