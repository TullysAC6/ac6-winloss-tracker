# Project state

Where the project is right now. Keep this short — requirements belong in
[MASTER_REQUIREMENTS.md](MASTER_REQUIREMENTS.md), phases in [ROADMAP.md](ROADMAP.md),
reasoning in [DECISIONS.md](DECISIONS.md), and detail in the GitHub issues.

Last updated: 2026-09-10

## Requirements baseline

**Master Requirements Revision 2 (2026-09-09) is the canonical requirement.**
It is in the repository as [MASTER_REQUIREMENTS.md](MASTER_REQUIREMENTS.md). Anything older —
chat, issue text, PR comment, README note or branch document — that conflicts with it is stale
and must be reconciled explicitly, not silently followed.

What Revision 2 changed relative to the previous documents:

| Change | Where it landed |
|---|---|
| TEAM and CUSTOM MATCH are recorded matches, not out of scope | [DECISIONS.md](DECISIONS.md) "Supported game modes" (previous decision kept, marked superseded), ROADMAP Phase 7A and Phase 11 |
| Match metadata foundation — `match_type`, `match_format`, `self_rank`, `opponent_rank` | ROADMAP Phase 7A (new) |
| Growth analytics — rolling win rate, period comparison, rank-relative, improved matchup, session tendencies, Next Goal, Personal Best | ROADMAP Phase 7B–7D |
| Full opponent build (12 slots, not just weapons), per match, never carried forward | ROADMAP Phase 8A–8E, DECISIONS "Every-match opponent build independence" |
| Development acceleration — fixture/replay harness, T0–T3 gates, PR handoff contract, feature flags, performance baselines | ROADMAP Phase D (new), DECISIONS, `.github/pull_request_template.md` |
| Phases 8 / 9 / 10 (weapon / leg / build) folded into one capture pipeline | ROADMAP "Phase numbering" mapping table; issues #10 / #11 / #12 kept and re-scoped |

No application code was changed to record any of this.

## Position

| | |
|---|---|
| Stable (public) | **v1.0.1** — commit `54ba0d8` |
| `main` | `a6b7994` |
| Current RC | `codex/wgc-rc-validation-20260908` — `3853cfd` (PR #19 integrated); pre-fix baseline `b9d627c` |
| Open PRs | [#5](https://github.com/TullysAC6/ac6-winloss-tracker/pull/5) settings/analytics (**draft**) · [#13](https://github.com/TullysAC6/ac6-winloss-tracker/pull/13) coordination docs (**draft**) |

PR #19 is merged into the RC, with focused T3 pending; it is not released.

## Active branches

| Branch | Owner | Purpose |
|---|---|---|
| `codex/wgc-rc-validation-20260908` | Claude Code (handoff from Astra) | WGC / Alt+Tab / screenshot stability RC |
| `fix/rc-screenshot-occlusion-20260909` | Claude Code | PR #19, `cdefd57`, merged into RC `3853cfd`; focused T3 pending |
| `claude/settings-analytics-20260909` | Claude Code | Settings screen, analytics, CSV, DB check, history maintenance (PR #5) |
| `coordination/project-management-20260909` | — | Documentation only (PR #13) |

Older `codex/p0-*` and `release/*` branches are historical.

## Parallel-work generation count

At most two unmerged generations are allowed (see [DECISIONS.md](DECISIONS.md)).

| Generation | Branch | State |
|---|---|---|
| A — under review / acceptance | RC `codex/wgc-rc-validation-20260908` + corrective PR #19 | technical review PASS / focused real-AC6 T3 pending; RC acceptance pending (#7) |
| B — implemented, awaiting review | `claude/settings-analytics-20260909` (PR #5) | draft |

PR #19 is Generation A's corrective child, not a third feature generation.

**The limit is reached.** A third feature generation does not start until the RC reaches `main`
and PR #5 has been reconciled with the new `main`. The coordination branch does not count — it
contains no application code.

## Open issues that matter

| | |
|---|---|
| [#6](https://github.com/TullysAC6/ac6-winloss-tracker/issues/6) | `[META]` roadmap entry point |
| [#7](https://github.com/TullysAC6/ac6-winloss-tracker/issues/7) | `[RC]` acceptance checklist for the current RC |
| [#8](https://github.com/TullysAC6/ac6-winloss-tracker/issues/8) | `[EPIC]` settings and user controls |
| [#9](https://github.com/TullysAC6/ac6-winloss-tracker/issues/9) | `[EPIC]` match history and analytics |
| [#4](https://github.com/TullysAC6/ac6-winloss-tracker/issues/4) | milestone effect lost across an SSE reconnect — fixed on RC, not released |
| [#10](https://github.com/TullysAC6/ac6-winloss-tracker/issues/10) / [#11](https://github.com/TullysAC6/ac6-winloss-tracker/issues/11) / [#12](https://github.com/TullysAC6/ac6-winloss-tracker/issues/12) | opponent weapon / leg / build statistics — backlog, now slices of Phase 8C |
| [#14](https://github.com/TullysAC6/ac6-winloss-tracker/issues/14) | `[EPIC]` fixture / replay harness — Phase D |
| [#15](https://github.com/TullysAC6/ac6-winloss-tracker/issues/15) | `[EPIC]` match metadata foundation — Phase 7A |
| [#16](https://github.com/TullysAC6/ac6-winloss-tracker/issues/16) | `[EPIC]` growth analytics — Phase 7B–7D |
| [#17](https://github.com/TullysAC6/ac6-winloss-tracker/issues/17) | `[EPIC]` full opponent build recognition — Phase 8A–8B |
| [#18](https://github.com/TullysAC6/ac6-winloss-tracker/issues/18) | `[EPIC]` manual historical backfill — Phase 8D |

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

## Implemented but not accepted

Code exists and CI is green; full acceptance and release are not complete; the limited dated observations above remain valid. Do not count these as done when planning a release.

**On the RC branch:** WGC capture path, guarded desktop fallback, effect screenshots, launcher
settings entry, `effect_screenshot_enabled`, version check, #4 effect replay, dashboard 50-row
scrolling history, isolated install/update/rollback/uninstall flow test.

**In draft PR #5:** `effect_enabled`, `overlay_stats_scope`, period and recent-N analytics, CSV
export, DB integrity check, history purge, diagnostic report from the settings screen, expanded
update check.

## GitHub Project board

**[AC6 Win/Loss Tracker Development](https://github.com/users/TullysAC6/projects/1)** — created
2026-09-09 once the `project` scope was authorised. Private to the owner. It is the only project
on this account; no duplicate was created.

Status column: **Backlog · Planned · In Progress · Review · Blocked · Done**.
`Done` means released and in use — never "an assistant says the code is written".

Current placement, which must stay consistent with the table above and with `ROADMAP.md`:

| Status | Items | Why |
|---|---|---|
| **In Progress** | #6, #7, PR #19 | RC owner Claude Code; PR #19 is merged to RC but focused T3 remains pending, so it is not Done |
| **Blocked** | #4, #8, #9, PR #5 | All waiting on RC acceptance. #4's fix is on the RC; #8 and #9 are implemented across the RC and PR #5; PR #5 stays draft until the RC lands and it is reconciled |
| **Review** | PR #13 | Documentation review — a separate track from real-AC6 acceptance |
| **Planned** | #14, #15, #16, #17, #18 | Phase D and Phases 7A–8E |
| **Backlog** | #10, #11, #12 | Phase 8C slices, waiting on #17 |
| **Done** | — | Nothing yet. v1.0.1 predates the board |

When an item moves here, move it on the board too — and when they disagree, this file and
`ROADMAP.md` are right.

## Development flow

Every item follows this order (`MASTER_REQUIREMENTS.md` §3):

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

T0–T2 pass **before the PR goes to review**, and the affected gates are re-run after review fixes.
T3 is last because it is the only step that costs the user a play session.

T1 does not exist yet — the fixture/replay harness is #14, which is why #7's real-AC6 list is as
long as it is.

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

## Do not

Merge PR #5, merge the RC to `main`, create a tag or a release, or change `install.ps1` / version strings
until #7 passes. Do not start a third unmerged feature generation.
