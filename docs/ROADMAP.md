# Roadmap

Entry point on GitHub: [#6](https://github.com/TullysAC6/ac6-winloss-tracker/issues/6). Current position: [PROJECT_STATE.md](PROJECT_STATE.md).

This file records **implementation order and status only**. The requirement itself lives in
[MASTER_REQUIREMENTS.md](MASTER_REQUIREMENTS.md) and the reasoning in [DECISIONS.md](DECISIONS.md).
An item appearing here is not authorisation to implement it now.

Status vocabulary:

| Status | Meaning |
|---|---|
| **DONE** | Shipped in the current public stable release and in use |
| **IN PROGRESS** | Being worked on right now |
| **ACCEPTED** | Automated/review/required real-session acceptance complete; release publication pending |
| **ACCEPTANCE PENDING** | Code exists, CI green, **not released and not confirmed in a real session** |
| **PLANNED** | Agreed direction, scheduled after the current phases |
| **BACKLOG** | Agreed direction, not scheduled, no design yet |
| **DEFERRED** | Deliberately not doing it now; the reason is recorded |
| **FUTURE / EXPERIMENTAL** | Agreed as a direction, but a design bar must be met before it may be built |

`ACCEPTANCE PENDING` is not a synonym for done. It is the state that hides release risk, so it is called out separately everywhere.

---

## Phase numbering — 2026-09-09 reorganisation

Master Requirements Revision 2 added a development-acceleration track, a match-metadata
foundation and a full opponent-build programme. Rather than renumber existing phases and break
every existing reference, the old phases were **folded in place**:

| Old | Now | Note |
|---|---|---|
| Phase 1–6 | unchanged | Still accurate |
| Phase 7 — Advanced statistics | **Phase 7B** | The daily/weekly/monthly series is now one part of growth analytics, and gains Phase 7A as its prerequisite |
| Phase 8 — Opponent weapon analysis ([#10](https://github.com/TullysAC6/ac6-winloss-tracker/issues/10)) | **Phase 8C** slice | One slice of one capture pipeline, not its own pipeline |
| Phase 9 — Opponent leg analysis ([#11](https://github.com/TullysAC6/ac6-winloss-tracker/issues/11)) | **Phase 8C** slice | Same |
| Phase 10 — Opponent build analysis ([#12](https://github.com/TullysAC6/ac6-winloss-tracker/issues/12)) | **Phase 8A–8C** | The superset; it is now the whole capture programme |
| Phase 11 — UX / distribution | unchanged | Number kept |
| — | **Phase D** (new) | Development acceleration; cross-cutting, runs first |

No phase number was reused for a different subject, and no item was dropped.

---

## Implementation priority

Two independent tracks. Phase D is not a feature, and it comes first because it changes how
expensive every later phase is to verify.

**Development acceleration (Phase D)**

1. Fixture / replay harness
2. Standard PR handoff template
3. T0–T3 acceptance gates
4. Optional feature flags
5. Performance / security regression checks

**User analytics**

1. Recent 10 / 30 / 100 + day / week / month
2. Ranked / Custom + Single / Team
3. Self rank vs opponent rank
4. Rolling win rate
5. Full Opponent Build
6. Part / build matchup win rates
7. Recently improved matchup
8. Session / post-loss tendencies
9. Next Goal
10. Future self-build cross analysis

Prerequisite foundations may be implemented before the visible feature that depends on them.

---

## Phase D — Development acceleration

The bottleneck is defects that only surface in a real AC6 session. This phase moves detection
earlier. Requirements: [MASTER_REQUIREMENTS.md](MASTER_REQUIREMENTS.md) sections 38–42.

The development flow every item on this roadmap follows (MASTER_REQUIREMENTS §3):

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

| Item | Status | Notes |
|---|---|---|
| Fixture / replay harness | **PLANNED — HIGH PRIORITY** | `tests/fixtures/{results,match_metadata,ranks,opponent_builds}/` with expected structured truth per fixture. Replays the real recognition/classification path without AC6 running. Bounded and curated — see [DECISIONS.md](DECISIONS.md) |
| Standard PR handoff template | **ADOPTED (process)** | [`.github/pull_request_template.md`](../.github/pull_request_template.md). `Not changed` is mandatory |
| T0–T3 acceptance gates | **ADOPTED (process)** | Recorded in [DECISIONS.md](DECISIONS.md). T0–T2 pass before the **PR goes to review**; the affected gates are re-run after review fixes; T3 last |
| Optional feature-flag policy | **ADOPTED (process)** | Policy recorded. No flag is implemented yet |
| Performance / security regression checks | PLANNED | Baseline deltas (Tracker OFF / current accepted / + feature). No invented absolute targets. A capture script exists on the RC branch |

`ADOPTED (process)` means the rule is in force for future work — a documentation state, not a
shipped application feature.

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
| Dashboard history 10 → 50 rows, scrolling, no redundant repaint | ACCEPTED | Accepted on RC `93d5a57`; v1.1.0 publication pending |

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
| Isolated install / update / rollback / uninstall flow test | ACCEPTED | Accepted on the RC; runs in CI; v1.1.0 publication pending |
| Dedicated venv isolation | DEFERRED | v1.1.0 installs into the user's Python environment. Recorded in README as a future version |

## Phase 4 — WGC / screenshot / stability

Everything in this phase is the current RC. See [#7](https://github.com/TullysAC6/ac6-winloss-tracker/issues/7).

| Item | Status | Notes |
|---|---|---|
| Windows Graphics Capture path | ACCEPTED | Detection continues while AC6 is not foreground; v1.1.0 publication pending |
| Guarded desktop-capture fallback (MSS) | ACCEPTED | Only with AC6 foreground, identical identity/geometry before and after, and nothing overlapping the ROI |
| Alt+Tab continuity | ACCEPTED | Existing real recovery evidence plus targeted automation |
| Stale-frame safety | ACCEPTED | Unique native presentation times; a repeated or old frame cannot confirm |
| Capture-stall recovery | ACCEPTED | Request timeout, worker restart with backoff |
| Client-rect change handling | ACCEPTED | Unknown geometry fails closed |
| Milestone effects 5 / 10 / 15 / 20 | DONE | Released in v1.0.1 |
| Milestone effects 30 / 35 / 40 / 45 | DONE | Released in v1.0.1 |
| Milestone effect 50 | DONE | Released in v1.0.1 |
| SSE effect replay / recent effect | ACCEPTED | Fix for [#4](https://github.com/TullysAC6/ac6-winloss-tracker/issues/4); v1.1.0 publication pending |
| Effect screenshot | ACCEPTED | Focused real-AC6 T3 PASS on RC `93d5a57`; visible user overlays are part of the captured composition |

## Phase 5 — Settings

Umbrella issue: [#8](https://github.com/TullysAC6/ac6-winloss-tracker/issues/8). The settings screen does not exist in v1.0.1 at all — `settings_window.py` is not in the `v1.0.1` tag.

| Item | Status | Notes |
|---|---|---|
| Screenshot ON / OFF (`effect_screenshot_enabled`) | ACCEPTED | Config key and launcher settings entry accepted on the RC; v1.1.0 publication pending |
| Check for a new version | ACCEPTED | Metadata-only check introduced on the RC; v1.1.0 publication pending. Draft PR #5 extensions are not included |
| Milestone effect ON / OFF (`effect_enabled`) | ACCEPTANCE PENDING | Draft PR #5 |
| Session / lifetime display switch (`overlay_stats_scope`) | ACCEPTANCE PENDING | Draft PR #5 |
| Reset all win/loss history | ACCEPTANCE PENDING | Draft PR #5 |
| Delete history before a given date | ACCEPTANCE PENDING | Draft PR #5. Capped at today; refuses a cutoff crossing the open session |
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

Win rate is `WIN / (WIN + LOSE) * 100`, DRAW excluded from the denominator. Any new statistic reuses this definition, and always displays its sample size: `80.0% (8W-2L / 10 matches)`.

---

## Phase 7 — Match metadata and growth analytics

Everything below is **PLANNED**. Nothing here is implemented, and the whole phase depends on 7A.

### Phase 7A — Match metadata foundation

The prerequisite for every category-aware statistic. Requirements: [MASTER_REQUIREMENTS.md](MASTER_REQUIREMENTS.md) sections 10–15.

| Item | Status | Notes |
|---|---|---|
| `match_type` — ranked / custom / unknown | PLANNED | |
| `match_format` — single / team / unknown | PLANNED | |
| `self_rank` | PLANNED | Single and Team, where reliable |
| `opponent_rank` | PLANNED | Single only. Team opponent ranks deferred |
| `metadata_recognition_status` / `metadata_recognition_version` | PLANNED | |
| TEAM recorded through the normal result path | PLANNED | WIN/LOSE/DRAW, match history and win-rate stats. **Supersedes the previous "SINGLE only" scope decision** — see [DECISIONS.md](DECISIONS.md) |
| Match-history row shows mode / format / ranks | PLANNED | `RANKED · SINGLE`, `自分 A / 相手 S`. Unknown shown honestly |
| Win-rate breakdown by Ranked / Custom / Single / Team | PLANNED | Top-level UI may stay Overall / Single / Team, with the detail elsewhere |
| `相手機体: TEAMのため対象外` distinct from `未取得` | PLANNED | |

Not negotiable in this phase: metadata failure never discards a result; `unknown` is never
inferred into a specific category; existing historical rows stay `unknown`.

### Phase 7B — Growth trend analytics

Absorbs the former Phase 7 (daily / weekly / monthly series). Priority **HIGH**.

| Item | Status | Notes |
|---|---|---|
| Recent 10 / 30 / 100 | ACCEPTANCE PENDING | Already in draft PR #5 (Phase 6) |
| Today / this week / **previous week** / this month | PLANNED | Previous-week comparison is new in Revision 2 |
| Daily win-rate series | PLANNED | Period *aggregates* exist; a per-day *trend* does not |
| Weekly win-rate series | PLANNED | |
| Monthly win-rate series | PLANNED | |
| 30-match rolling win rate | PLANNED | The recommended primary trend |
| Period-vs-period comparison | PLANNED | Shows both sample sizes, the difference in percentage points and a sparse-data marker. `最近30戦 57% / 前30戦 51% / +6pt` |
| Statistics UI (charts / trends) | PLANNED | Needs a home in the dashboard |

### Phase 7C — Rank-relative performance

Priority **HIGH once rank metadata is reliable**. Depends on 7A.

| Item | Status | Notes |
|---|---|---|
| vs higher rank / same rank / lower rank | PLANNED | Only matches whose rank metadata is sufficiently reliable. Unknown ranks are not classified |
| Expected Performance vs a rank-conditioned baseline | **FUTURE / EXPERIMENTAL** | Must not be built as a hand-written expectation. Needs transparent methodology, adequate sample, a defined baseline population, uncertainty handling, and validation that it is not misleading. Until then, raw rank-relative W/L is preferred |

### Phase 7D — Session tendencies, goals and achievements

| Item | Status | Notes |
|---|---|---|
| Session tendencies | PLANNED | Matches 1–5 / 6–10 / 11+; after a win; after a loss; after 2 and after 3 consecutive losses. Requires an adequate sample. Neutral wording only — surface a pattern, never diagnose psychology |
| Next Goal cards | PLANNED / USER-CHOSEN | A few actionable cards (今の調子 / 最近の成長 / 次に注目) with `[このMatchupを目標にする]`. Recommended, never imposed |
| Personal Best / achievements | PLANNED | Best streak, best 30-match win rate, higher-rank wins, largest matchup improvement, match milestones. Improvement itself counts as an achievement. No manipulative or excessive notification |

---

## Phase 8 — Opponent recognition and build analytics

The feature is **full opponent build recognition**, not weapon recognition. One capture pipeline
feeds every opponent statistic. Requirements: [MASTER_REQUIREMENTS.md](MASTER_REQUIREMENTS.md)
sections 16–26 and 53. Everything below is **PLANNED / BACKLOG** — nothing records opponent
loadout today.

### Phase 8A — Opponent recognition foundation

| Item | Status | Notes |
|---|---|---|
| Local versioned parts master | PLANNED | `part_id`, `category`, `display_name`, `aliases`, `game_version`, `active`. Game8 is reference material only, never a runtime dependency |
| Opponent-build schema | PLANNED | Per match: 4 weapons, HEAD / CORE / ARMS / LEGS, BOOSTER, FCS, GENERATOR, EXPANSION |
| `recognition_status` / `recognition_source` / `recognition_version`, `recognized_at`, `confidence`, `manual_corrected` | PLANNED | Status distinguishes complete / partial / failed / not acquired / not supported for TEAM |
| Build Fingerprint | PLANNED | Deterministic, for grouping only. Never replaces the component fields |
| Short-lived recognition lifecycle | PLANNED | No always-on worker. Post-result, event or user initiated, exits when done |
| Self / opponent UI identification | PLANNED | Verified from UI / player context. "The parts differ from mine" is not sufficient evidence |
| Feature flag `opponent_build_recognition` | PLANNED | Defaults OFF before acceptance |

### Phase 8B — Live opponent build recognition

| Item | Status | Notes |
|---|---|---|
| Single only, after the result is finalised | PLANNED | WIN / LOSE / DRAW all valid |
| Capture only the required frame(s), recognise, normalise, save to that match, exit | PLANNED | |
| Partial recognition stored, not discarded | PLANNED | |
| Manual correction | PLANNED | Updates the current value; no unbounded edit log |
| **Never carry a build forward between matches** | PLANNED — CRITICAL | The same opponent, the same visible weapons or frame is not a source. FCS / GENERATOR / EXPANSION can change invisibly. See [DECISIONS.md](DECISIONS.md) |
| Build failure never affects the result | PLANNED | |

### Phase 8C — Opponent statistics

Replaces the former Phases 8 / 9 / 10 as slices of one dataset.
[#10](https://github.com/TullysAC6/ac6-winloss-tracker/issues/10) weapons ·
[#11](https://github.com/TullysAC6/ac6-winloss-tracker/issues/11) legs ·
[#12](https://github.com/TullysAC6/ac6-winloss-tracker/issues/12) build.

| Item | Status | Notes |
|---|---|---|
| Per-slot win rates — weapons, HEAD, CORE, ARMS, LEGS, BOOSTER, FCS, GENERATOR, EXPANSION | BACKLOG | Every figure carries its match count |
| Complete-build win rate | BACKLOG | Grouped by Build Fingerprint |
| Combinations (weapon + legs, and so on) | BACKLOG | Only where the sample is meaningful |
| 得意な相手 / 苦手な相手 summary | BACKLOG | A short summary, not every dimension by default. Minimum-sample rule or a visible sparse marker |
| Improved-matchup highlight | BACKLOG | `vs Tetra: previous 30 31% / recent 10 50% / +19pt`. Consistent selection rule, no cherry-picking |
| Single and Team analysis kept separate | BACKLOG | |

### Phase 8D — Manual historical backfill

| Item | Status | Notes |
|---|---|---|
| `[対戦履歴から取得]` on an eligible Single match with no build | PLANNED | Explicit dashboard action only |
| Target `match_id` fixed before recognition | PLANNED | |
| Soft evidence matching; ambiguous asks the user; a clear mismatch does not write | PLANNED | Timestamp is a signal, not a strict gate; time proximity alone never associates |
| `recognition_source = history_manual` | PLANNED | |
| Cancel action; a pending backfill is cleared on restart | PLANNED | |
| Browsing AC6 history never triggers recognition or a DB write | PLANNED | |

### Phase 8E — Recognition regression tests

| Item | Status | Notes |
|---|---|---|
| Fixture-based recognition regression tests | PLANNED | Built on the Phase D harness. Eventually mandatory for recognition changes. Expected labels are never edited to make a failing test pass |

---

## Phase 11 — UX / distribution improvements

Number unchanged from the original roadmap.

| Item | Status | Notes |
|---|---|---|
| Dedicated venv isolation | DEFERRED | Phase 3 note |
| Support for CUSTOM MATCH / RANK MATCH: TEAM | **PLANNED — moved to Phase 7A** | No longer deferred. Revision 2 requires TEAM and CUSTOM to be recorded as normal matches. The README statement stays accurate for the *shipped* release until 7A passes acceptance |
| Patch / version awareness (`game_version`, `parts_master_version`, `recognition_version`, `analytics_version`) | PLANNED | Leaves room for before/after balance-patch comparison. Existing data is not back-filled with a guessed version |
| Localisation beyond Japanese | BACKLOG | |
| Signed installer / SmartScreen reputation | BACKLOG | |

---

## Much later

| Item | Status | Notes |
|---|---|---|
| Self-build linkage (`Current Build` selection, self × opponent cross analysis) | FUTURE | Does not require per-match OCR. Must not delay the current roadmap |
| TEAM three-opponent build recognition | DEFERRED | Request-driven only |
| Safe automatic historical completion | DEFERRED | Only if justified |
| Discord login | DEFERRED | Local-first is mandatory: with no login every core function still works, and a Discord outage disables nothing. Internal identity stays separate from external identity |
| Cloud sync / community features | DEFERRED | |
