# Project state

Where the project is right now. Keep this short — requirements belong in
[MASTER_REQUIREMENTS.md](MASTER_REQUIREMENTS.md), phases in [ROADMAP.md](ROADMAP.md),
reasoning in [DECISIONS.md](DECISIONS.md), and detail in the GitHub issues.

Last updated: 2026-09-09

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
| Current RC | `codex/wgc-rc-validation-20260908` — `b9d627c`, 8 commits ahead of `main` (Astra is still pushing to it) |
| Open PRs | [#5](https://github.com/TullysAC6/ac6-winloss-tracker/pull/5) settings/analytics (**draft**) · [#13](https://github.com/TullysAC6/ac6-winloss-tracker/pull/13) coordination docs (**draft**) |

## Active branches

| Branch | Owner | Purpose |
|---|---|---|
| `codex/wgc-rc-validation-20260908` | Astra | WGC / Alt+Tab / screenshot stability RC |
| `claude/settings-analytics-20260909` | Claude Code | Settings screen, analytics, CSV, DB check, history maintenance (PR #5) |
| `coordination/project-management-20260909` | — | Documentation only (PR #13) |

Older `codex/p0-*` and `release/*` branches are historical.

## Parallel-work generation count

At most two unmerged generations are allowed (see [DECISIONS.md](DECISIONS.md)).

| Generation | Branch | State |
|---|---|---|
| A — under review / acceptance | RC `codex/wgc-rc-validation-20260908` | acceptance pending (#7) |
| B — implemented, awaiting review | `claude/settings-analytics-20260909` (PR #5) | draft |

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

## Current focus

Real-AC6 acceptance of the RC (#7). No code work is blocking it.

## Release blockers

Only unverified real-AC6 behaviour. There is no known code defect blocking the RC.

- Real WIN / LOSE detection and counting
- DRAW leaves WIN / LOSE / streak unchanged
- No double counting
- Alt+Tab recovery, WGC stall recovery, stale-frame safety, client-rect change
- Milestone effect renders in game; effect screenshot is actually written
- Milestone reached during an SSE reconnect shows exactly once (#4)

## Implemented but not accepted

Code exists and CI is green; behaviour has not been confirmed in a real session or in a released
build. Do not count these as done when planning a release.

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
| **In Progress** | #6, #7 | #6 is the living coordination entry point; #7 is the current focus — real-AC6 acceptance of the RC |
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

1. Run the #7 acceptance checklist against real AC6.
2. If it passes, merge the RC to `main` and release; close #4 with that release.
3. Re-check PR #5 against the RC head, then take it out of draft for review.
4. Only after 1–3: start Phase D (fixture / replay harness, #14) on a fresh branch from `main`.

## Do not

Merge PR #5, merge the RC, create a tag or a release, or change `install.ps1` / version strings
until #7 passes. Do not start a third unmerged feature generation.
