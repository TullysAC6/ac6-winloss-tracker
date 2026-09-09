# Roadmap

Entry point on GitHub: [#6](https://github.com/TullysAC6/ac6-winloss-tracker/issues/6). Current position: [PROJECT_STATE.md](PROJECT_STATE.md).

Status vocabulary:

| Status | Meaning |
|---|---|
| **DONE** | Shipped in the public stable release (v1.0.1) and in use |
| **IN PROGRESS** | Being worked on right now |
| **ACCEPTANCE PENDING** | Code exists, CI green, **not released and not confirmed in a real session** |
| **PLANNED** | Agreed direction, scheduled after the current phases |
| **BACKLOG** | Agreed direction, not scheduled, no design yet |
| **DEFERRED** | Deliberately not doing it now; the reason is recorded |

`ACCEPTANCE PENDING` is not a synonym for done. It is the state that hides release risk, so it is called out separately everywhere.

---

## Phase 1 — Core detection

| Item | Status | Notes |
|---|---|---|
| WIN auto-detection | DONE | RANK MATCH: SINGLE only |
| LOSE auto-detection | DONE | |
| DRAW | DONE | Detected as `FINAL_DRAW` and reported as `last_result=draw`. **Deliberately not counted** — WIN / LOSE / streak are left unchanged. `history.db` and the analytics module accept `draw` rows, but none are ever written today |
| Manual WIN / LOSE entry | DEFERRED | No user-facing manual result entry exists. Only manual **undo** (`stats_undo.bat`, `control.py undo`) and **session reset** (dashboard button, `stats_reset.bat`, `control.py reset`). `record_result(..., "manual")` is an internal API used by tests, not a user feature |
| CLEAR / re-arm | DONE | |
| ResultGate | DONE | One cooldown gate shared by automatic and manual sources |
| Double-count prevention | DONE | Gate + `INSERT OR IGNORE` on a unique `event_id` |

## Phase 2 — Overlay / Dashboard / History

| Item | Status | Notes |
|---|---|---|
| Session totals | DONE | `stats.json`, reset when the server binds its port |
| Lifetime history | DONE | `history.db`: `sessions` / `matches` / `match_contexts` |
| Dashboard | DONE | |
| Game overlay | DONE | |
| OBS browser-source overlay | DONE | `http://127.0.0.1:8765/` |
| Dashboard history 10 → 50 rows, scrolling, no redundant repaint | ACCEPTANCE PENDING | On the RC branch |

## Phase 3 — Distribution / release safety

| Item | Status | Notes |
|---|---|---|
| Single instance — server | DONE | `SO_EXCLUSIVEADDRUSE` port bind is the ownership boundary |
| Single instance — overlay | DONE | Named mutex `Local\AC6StatsOverlayV22` |
| Single instance — launcher / dashboard | DONE | Runtime file + liveness + health probe |
| Child-process ownership | DONE | Win32 job objects with kill-on-close; workers do no native work until the parent has installed containment |
| Shutdown cleanup | DONE | Verified: no orphan process, runtime files removed, port released |
| Abnormal-termination recovery | DONE | Overlay exits by itself when the server dies; a stale `.runtime.json` neither blocks nor hijacks the next start |
| Startup-failure cleanup | DONE | Installer rolls back source and shortcut and stops what it started |
| Verified installer / update / uninstall | DONE | Hash-verified bootstrap, immutable commit install, retention on uninstall |
| Isolated install / update / rollback / uninstall flow test | ACCEPTANCE PENDING | Added on the RC; runs in CI |
| Dedicated venv isolation | DEFERRED | v1.0.1 installs into the user's Python environment. Recorded in README as a future version |

## Phase 4 — WGC / screenshot / stability

Everything in this phase is the current RC. See [#7](https://github.com/TullysAC6/ac6-winloss-tracker/issues/7).

| Item | Status | Notes |
|---|---|---|
| Windows Graphics Capture path | ACCEPTANCE PENDING | Detection continues while AC6 is not foreground |
| Guarded desktop-capture fallback (MSS) | ACCEPTANCE PENDING | Only with AC6 foreground, identical identity/geometry before and after, and nothing overlapping the ROI |
| Alt+Tab continuity | ACCEPTANCE PENDING | |
| Stale-frame safety | ACCEPTANCE PENDING | Unique native presentation times; a repeated or old frame cannot confirm |
| Capture-stall recovery | ACCEPTANCE PENDING | Request timeout, worker restart with backoff |
| Client-rect change handling | ACCEPTANCE PENDING | Unknown geometry fails closed |
| Milestone effects 5 / 10 / 15 / 20 | DONE | Released in v1.0.1 |
| Milestone effects 30 / 35 / 40 / 45 | DONE | Released in v1.0.1 |
| Milestone effect 50 | DONE | Released in v1.0.1 |
| SSE effect replay / recent effect | ACCEPTANCE PENDING | Fix for [#4](https://github.com/TullysAC6/ac6-winloss-tracker/issues/4) on the RC |
| Effect screenshot | ACCEPTANCE PENDING | Fail-closed: no synthesised overlay, no unrelated desktop image |

## Phase 5 — Settings

Umbrella issue: [#8](https://github.com/TullysAC6/ac6-winloss-tracker/issues/8). The settings screen does not exist in v1.0.1 at all.

| Item | Status | Notes |
|---|---|---|
| Screenshot ON / OFF (`effect_screenshot_enabled`) | ACCEPTANCE PENDING | Config key and launcher settings entry are on the RC, not in v1.0.1 |
| Milestone effect ON / OFF (`effect_enabled`) | ACCEPTANCE PENDING | Draft PR #5 |
| Session / lifetime display switch (`overlay_stats_scope`) | ACCEPTANCE PENDING | Draft PR #5 |
| Reset all win/loss history | ACCEPTANCE PENDING | Draft PR #5 |
| Delete history before a given date | ACCEPTANCE PENDING | Draft PR #5. Capped at today; refuses a cutoff crossing the open session |
| Check for a new version | ACCEPTANCE PENDING | Draft PR #5 |
| Automatic update | DEFERRED | Deliberate. See [DECISIONS.md](DECISIONS.md) |

## Phase 6 — Match analytics

Umbrella issue: [#9](https://github.com/TullysAC6/ac6-winloss-tracker/issues/9). All draft PR #5.

| Item | Status |
|---|---|
| Today / this week (Monday) / this month / all time | ACCEPTANCE PENDING |
| Recent 10 / 30 / 100 | ACCEPTANCE PENDING |
| CSV export (UTF-8 BOM, oldest first) | ACCEPTANCE PENDING |
| DB integrity check (`quick_check` / `integrity_check`) | ACCEPTANCE PENDING |
| History maintenance (reset all, delete before a date) | ACCEPTANCE PENDING |

Win rate is `WIN / (WIN + LOSE) * 100`, DRAW excluded from the denominator. Any new statistic reuses this definition.

## Phase 7 — Advanced statistics

| Item | Status | Notes |
|---|---|---|
| Daily win-rate series | PLANNED | Period *aggregates* exist; a per-day *trend* does not |
| Weekly win-rate series | PLANNED | |
| Monthly win-rate series | PLANNED | |
| Statistics UI (charts / trends) | PLANNED | Needs a home in the dashboard |

## Phase 8 — Opponent weapon analysis

[#10](https://github.com/TullysAC6/ac6-winloss-tracker/issues/10) — **BACKLOG**. Nothing records opponent loadout today. Needs a data-source decision, a schema migration and enrichment that stays outside the authoritative result write.

## Phase 9 — Opponent leg analysis

[#11](https://github.com/TullysAC6/ac6-winloss-tracker/issues/11) — **BACKLOG**. Same blocker as Phase 8.

## Phase 10 — Opponent build analysis

[#12](https://github.com/TullysAC6/ac6-winloss-tracker/issues/12) — **BACKLOG**. The superset of Phases 8 and 9: if opponent data is ever captured it should be captured once and sliced three ways. Includes richer match history and the statistics UI that depends on it.

## Phase 11 — UX / distribution improvements

| Item | Status | Notes |
|---|---|---|
| Dedicated venv isolation | DEFERRED | Phase 3 note |
| Support for CUSTOM MATCH / RANK MATCH: TEAM | DEFERRED | Explicitly out of scope in the README; detection is unverified for those modes |
| Localisation beyond Japanese | BACKLOG | |
| Signed installer / SmartScreen reputation | BACKLOG | |
