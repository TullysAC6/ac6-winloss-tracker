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
| **ACCEPTED** | Automated/review/required real-session acceptance complete; released in v1.1.1 |
| **ACCEPTANCE PENDING** | Code exists, CI green, **not released and not confirmed in a real session** |
| **PLANNED** | Agreed direction, scheduled after the current phases |
| **BACKLOG** | Agreed direction, not scheduled, no design yet |
| **DEFERRED** | Deliberately not doing it now; the reason is recorded |
| **FUTURE / EXPERIMENTAL** | Agreed as a direction, but a design bar must be met before it may be built |

`ACCEPTANCE PENDING` is not a synonym for done. It is the state that hides release risk, so it is called out separately everywhere.

## Release status note — 2026-09-12

Public stable is **v1.1.1 — RELEASED**, tag `v1.1.1` → `e0d8476`.

**v1.1.0 is superseded.** It was published and its runtime was accepted, including a real-AC6 T3,
but its formal release acceptance was never completed: the README install one-liner inside the tag
carried a bootstrap `SHA-256` computed from Windows CRLF working-tree bytes instead of the Git blob
bytes `raw.githubusercontent.com` serves, so the published command failed closed. The tag was not
moved and the Release was not edited. v1.1.1 ships the same accepted runtime with corrected
immutable distribution metadata.

Therefore, in the tables below, `DONE` means shipped in v1.1.1 and in use.
[#7](https://github.com/TullysAC6/ac6-winloss-tracker/issues/7) and
[#4](https://github.com/TullysAC6/ac6-winloss-tracker/issues/4) are closed against v1.1.1. Neither
the `v1.1.0` nor the `v1.1.1` tag is ever moved.

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
| — | **Phase R** (new, 2026-09-12) | Runtime isolation. Cross-cutting; after Phase D's harness |
| — | **Phase UI** (new, 2026-09-12) | UI/UX polish. Cross-cutting presentation layer; it does not own any feature it displays |

No phase number was reused for a different subject, and no item was dropped.

---

## Implementation priority

Two independent tracks plus a cross-cutting one. This section records **relative priority within
each track**; the single interleaved order the project actually follows is
[Sequencing](#sequencing--dependency-order-corrected-2026-09-12) below, and that section wins on any
question of *when*.

Phase D is not a feature, and it comes first because it changes how expensive every later phase is
to verify.

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
10. Future self-build cross analysis ([#27](https://github.com/TullysAC6/ac6-winloss-tracker/issues/27))

**Cross-cutting (added 2026-09-12)**

Not a competitor to the two tracks above; it interleaves with them. Requirements:
[MASTER_REQUIREMENTS.md](MASTER_REQUIREMENTS.md) sections 56-63.

1. Runtime isolation - app-local Python environment ([#24](https://github.com/TullysAC6/ac6-winloss-tracker/issues/24)), after the fixture/replay harness
2. UI-0 design specification ([#25](https://github.com/TullysAC6/ac6-winloss-tracker/issues/25)) - documentation only, human review before any UI code
3. UI-1A Player Overlay polish, then UI-1B Broadcast Overlay polish
4. UI-2 Dashboard / History / Settings shell - **after #15**, so the shell is built once against a settled match-metadata contract
5. UI-3A Growth / Rank / Rating presentation, then UI-3B Opponent build statistics presentation - each following its own data
6. UI-4 Tray / Launcher modernization ([#26](https://github.com/TullysAC6/ac6-winloss-tracker/issues/26)), last

Prerequisite foundations may be implemented before the visible feature that depends on them.

---

## Sequencing — dependency order, corrected 2026-09-12

This is the **authoritative order**. The phase tables below describe *what* each item is; this
section describes *when*. Where the two appear to disagree, this section wins and the table is
corrected.

```text
v1.1.1 corrected release
→ PR #13 merge                (documentation becomes canonical on main)
→ PR #5                       (#8 settings, #9 analytics)
→ #14                         fixture / replay harness
→ #24                         app-local Python environment
→ UI-0                        design specification, no code change
→ UI-1A                       Player Overlay polish
→ UI-1B                       Broadcast Overlay polish
→ #15                         match metadata foundation
→ UI-2                        Dashboard / History / Settings shell
→ #16-A  /  #28 analytics     growth + seasonal rank/rating data
→ UI-3A                       Growth / Rank / Rating presentation
→ #17                         full opponent build recognition
→ #10 / #11 / #12             opponent statistics
→ UI-3B                       Opponent build statistics presentation
→ #16-B                       advanced growth analytics
→ #18                         manual historical backfill
→ #27                         self-build linkage
→ UI-4                        Tray / Launcher modernization
```

### Why this order, and what changed

| Change | Reason |
|---|---|
| **#15 moves ahead of UI-2** | UI-2 rebuilds the Dashboard and History shell. If the match-metadata contract is not fixed first, History and the Dashboard get built twice — once against today's row shape and again against the Ranked/Custom, Single/Team, rank-bearing shape. Settling the contract first is cheaper than building the shell twice |
| **#16 owns data, #25 owns rendering** | #16 owns the analytics and time-series computation — periods, rolling win rate, per-season rank/rating series, deltas, achievements. The **actual chart rendering is UI-3A / UI-3B, owned by #25.** Neither issue implements the other's half |
| **UI-3 splits into UI-3A and UI-3B** | A logical split along the data that feeds it. **UI-3A** presents growth, rank and rating (#16-A, #28). **UI-3B** presents opponent build statistics (#17, #10/#11/#12). UI-3A can ship as soon as its data exists, without waiting for the whole opponent-recognition programme |
| **#16-B moves ahead of #18** | Advanced growth analytics work on the data the user has actually accumulated. #18 manual historical backfill is a data-entry programme whose value is retroactive; it does not gate the analytics, so it does not precede them |
| **#14 must carry the later fixture families** | See Phase D. The harness is built once and then extended, so its layout has to accommodate season, pre-S and S rating fixtures rather than being retrofitted for them |

Unchanged and not negotiable: at most **two unmerged generations** at a time
(MASTER_REQUIREMENTS §4), and every item follows the §3 gate order
T0 → T1 → T2 → PR handoff → review → fixes → affected-gate rerun → T3 → `main`.

An item appearing in this list is **not** authorisation to start it now. The current position is in
[PROJECT_STATE.md](PROJECT_STATE.md).

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
| Fixture / replay harness | **PLANNED — HIGH PRIORITY** | `tests/fixtures/{results,match_metadata,ranks,opponent_builds}/` with expected structured truth per fixture. Replays the real recognition/classification path without AC6 running. Bounded and curated — see [DECISIONS.md](DECISIONS.md). **Its layout must be able to carry the later fixture families without being retrofitted**: season boundaries, and pre-S (UNRANKED through A4) and S rating presentations as distinct cases — see [#28](https://github.com/TullysAC6/ac6-winloss-tracker/issues/28) and MASTER_REQUIREMENTS §64. The harness is built once and then extended |
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
| Dashboard history 10 → 50 rows, scrolling, no redundant repaint | **DONE** | Accepted on RC `93d5a57`; released in v1.1.1 |

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
| Isolated install / update / rollback / uninstall flow test | **DONE** | Accepted on the RC; runs in CI; released in v1.1.1 |
| Dedicated venv isolation | **PLANNED** | No longer only a deferral. Adopted as a direction on 2026-09-12: [#24](https://github.com/TullysAC6/ac6-winloss-tracker/issues/24), MASTER_REQUIREMENTS §56. Scheduled after [#14](https://github.com/TullysAC6/ac6-winloss-tracker/issues/14). v1.1.1 still installs into the user's shared Python environment |

## Phase 4 — WGC / screenshot / stability

Everything in this phase is the current RC. See [#7](https://github.com/TullysAC6/ac6-winloss-tracker/issues/7).

| Item | Status | Notes |
|---|---|---|
| Windows Graphics Capture path | **DONE** | Detection continues while AC6 is not foreground; released in v1.1.1 |
| Guarded desktop-capture fallback (MSS) | **DONE** | Only with AC6 foreground, identical identity/geometry before and after, and nothing overlapping the ROI |
| Alt+Tab continuity | **DONE** | Existing real recovery evidence plus targeted automation |
| Stale-frame safety | **DONE** | Unique native presentation times; a repeated or old frame cannot confirm |
| Capture-stall recovery | **DONE** | Request timeout, worker restart with backoff |
| Client-rect change handling | **DONE** | Unknown geometry fails closed |
| Milestone effects 5 / 10 / 15 / 20 | DONE | Released in v1.0.1 |
| Milestone effects 30 / 35 / 40 / 45 | DONE | Released in v1.0.1 |
| Milestone effect 50 | DONE | Released in v1.0.1 |
| SSE effect replay / recent effect | **DONE** | Fix for [#4](https://github.com/TullysAC6/ac6-winloss-tracker/issues/4); released in v1.1.1 |
| Effect screenshot | **DONE** | Focused real-AC6 T3 PASS on RC `93d5a57`; visible user overlays are part of the captured composition |

## Phase 5 — Settings

Umbrella issue: [#8](https://github.com/TullysAC6/ac6-winloss-tracker/issues/8). The settings screen does not exist in v1.0.1 at all — `settings_window.py` is not in the `v1.0.1` tag.

| Item | Status | Notes |
|---|---|---|
| Screenshot ON / OFF (`effect_screenshot_enabled`) | **DONE** | Config key and launcher settings entry accepted on the RC; released in v1.1.1 |
| Check for a new version | **DONE** | Metadata-only check introduced on the RC; released in v1.1.1. Draft PR #5 extensions are not included |
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
| Rank / Season / Rating acquisition | PLANNED | Added 2026-09-12 for [#28](https://github.com/TullysAC6/ac6-winloss-tracker/issues/28). Event-driven only — **no continuous OCR** |
| Rank / Season / Rating persistence | PLANNED | Conceptual shape in MASTER_REQUIREMENTS §64. Rating is never carried forward across seasons |
| Distinguish the pre-S (through A4) and the S recognition system | PLANNED | The `rating_mode` distinction — conceptually `pre_s` and `s_rank`. **A4 is on the pre-S side.** Without it the two systems collapse into one column and every later chart misstates the progression |

Not negotiable in this phase: metadata failure never discards a result; `unknown` is never
inferred into a specific category; existing historical rows stay `unknown`. Rank/Rating recognition
failure never affects WIN/LOSE, ResultGate, streak or match persistence.

### Phase 7B — Growth trend analytics

Absorbs the former Phase 7 (daily / weekly / monthly series). Priority **HIGH**.

[#16](https://github.com/TullysAC6/ac6-winloss-tracker/issues/16) is delivered in two slices:
**#16-A** is the core growth and seasonal rank/rating data, scheduled after #15 and presented by
UI-3A; **#16-B** is the advanced analytics (rank-relative depth, matchup and improved-matchup,
session tendencies, Next Goal), scheduled after the opponent-statistics work and **before**
[#18](https://github.com/TullysAC6/ac6-winloss-tracker/issues/18) — backfill is retroactive data
entry and does not gate the analytics.

**#16 owns the data; the charts are UI-3A / UI-3B, owned by
[#25](https://github.com/TullysAC6/ac6-winloss-tracker/issues/25).**

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
| Per-season Rank / Rating progression | PLANNED | Added 2026-09-12 for [#28](https://github.com/TullysAC6/ac6-winloss-tracker/issues/28), MASTER_REQUIREMENTS §64. Gated on reliable self-rank recognition. History separated by season, past seasons viewable |
| Rank / Rating line chart | PLANNED | **The pre-S → S boundary (A4 → S) is not drawn as one continuous rating line** without a justified basis. Separate scale or separate presentation where the two systems are not comparable |
| Current Rating, Season High / Low, selected-period delta | PLANNED | Sample-size and sparse-data honesty from §43/§44 applies |
| Rank transition markers | PLANNED | |
| `S RANK REACHED` achievement | PLANNED | An **Achievement**, not a feature unlock. Every pre-S rank (UNRANKED through A4) is in scope too — the climb is the point |

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

## Phase R — Runtime isolation

Added 2026-09-12. Umbrella issue: [#24](https://github.com/TullysAC6/ac6-winloss-tracker/issues/24).
Requirements: [MASTER_REQUIREMENTS.md](MASTER_REQUIREMENTS.md) §56.

Scheduled **after** [#14](https://github.com/TullysAC6/ac6-winloss-tracker/issues/14), so the
migration has a fixture/replay harness to regress against. Not part of v1.1.x and not part of
draft PR #5.

| Item | Status | Notes |
|---|---|---|
| App-local Python environment owned by AC6tool | PLANNED | Layout decided at implementation time. Conceptually `…\AC6WinLossTracker\{app,venv}` |
| Dependency isolation from the user's shared Python | PLANNED | The reason for the whole phase: an unrelated `pip upgrade` must not be able to break the Tracker, and vice versa |
| `requirements.lock`, hash pinning, binary-only policy | **UNCHANGED** | A venv is not a sandbox. This phase must not be used as an argument to relax any supply-chain control |
| `pythonw` / worker actual-PID ownership through the launcher wrapper | PLANNED — **KNOWN HAZARD** | Already observed here: unmerged `fix/venv-launcher-ownership` (`138fd8f`, `95cc816`) and `release/v1.1.0-venv` (`e4677ce`, `6c88dce`). "It is a venv now" is never evidence that containment lands on the right PID |
| Migration T2 gate | PLANNED | Clean install, upgrade from shared Python, isolation, `python.exe` and `pythonw.exe` launch, worker PID ownership, duplicate-launch refusal, normal and abnormal shutdown, no orphan worker, update, injected rollback, uninstall, reinstall, user-data retention, port/runtime/mutex cleanup |

---

## Phase UI — UI/UX polish

Added 2026-09-12. Umbrella issue: [#25](https://github.com/TullysAC6/ac6-winloss-tracker/issues/25).
Requirements: [MASTER_REQUIREMENTS.md](MASTER_REQUIREMENTS.md) §57–§63.

Design identity: `Fluent shell × AC6 telemetry × Pachinko celebration`. It blends into the game
normally, and breaks only at the moment of a win. This is **polish**, not a rebuild.

This phase is the **presentation layer only**. It does not reimplement what it displays: #8 owns
settings functionality, #9 history and current analytics, #15 match metadata, #16 growth analytics
logic, #17 opponent build capture, #10/#11/#12 opponent statistics.

The division of labour that matters most here: **#16 owns the analytics and time-series data;
[#25](https://github.com/TullysAC6/ac6-winloss-tracker/issues/25) owns the actual chart
rendering.** Neither implements the other's half.

Order within this phase is set by [Sequencing](#sequencing--dependency-order-corrected-2026-09-12)
above, not by the row order in the table. In particular **#15 lands before UI-2**, so the Dashboard
and History shell is built once against a settled match-metadata contract instead of twice.

| Phase | Item | Status | Risk |
|---|---|---|---|
| UI-0 | Design specification — current framework survey, Player Overlay, Broadcast Overlay, Dashboard, History, Settings, Launcher, performance, lifecycle, DPI, accessibility; Before → Proposed per item; Low/Medium/High classification; rollback plan; regression-test plan. **No code change. Human review before any implementation** | PLANNED | none |
| UI-1A | Player Overlay polish — value over label, telemetry framing, thin background, DPI/aspect/safe-zone, minimal animation. Low-risk visual changes only | PLANNED | low |
| UI-1B | Broadcast / streaming Overlay polish — stream-safe typography, OBS safe area, scene composition, viewer-distance sizing | PLANNED | low |
| UI-2 | Dashboard / History / Settings shell — top navigation (`OVERVIEW / HISTORY / STATISTICS / SETTINGS`), WIN RATE as primary KPI, surface and spacing hierarchy, explicit status vocabulary, date-grouped history with filters | PLANNED | medium |
| UI-3A | **Growth / Rank / Rating presentation.** Statistics page for the data from #16-A and [#28](https://github.com/TullysAC6/ac6-winloss-tracker/issues/28): win-rate trend, rolling win rate, the **season selector**, and the Rank / Rating chart — with a separate scale or separate presentation where pre-S (UNRANKED through A4) and S cannot be compared directly. Ships as soon as its data exists; does not wait for the opponent-recognition programme | PLANNED | medium |
| UI-3B | **Opponent build statistics presentation.** Weapon / leg-type / full-build views for #17 and #10/#11/#12. Follows its data | PLANNED | medium |
| UI-4 | Tray / Launcher modernization — [#26](https://github.com/TullysAC6/ac6-winloss-tracker/issues/26). Separate issue, separate PR, last | BACKLOG | **high** |

### Constraints on this phase

| Constraint | |
|---|---|
| Performance | A UI change may not cost game performance. Compare Tracker CPU, RAM, process/thread count, AC6 frametime p95/p99 and update frequency before and after. No permanent 60 fps animation. The capture/detection loop never moves onto the UI thread |
| Framework | No migration to WinUI 3, WPF or anything else as the opening move. Only if a framework limit is a demonstrated blocker, in its own issue |
| Scope | No unrelated refactoring of Detector, ResultGate, WGC or process lifecycle |
| Accessibility | DPI scaling, keyboard navigation, focus states, contrast, text scaling, high contrast, dark titlebar, narrow-window layout. **Never colour alone** |
| Overlays | Player and Broadcast overlays share tokens and components, but font size, opacity, density, animation, duration and layout stay separately configurable |

---

## Phase 11 — UX / distribution improvements

Number unchanged from the original roadmap.

| Item | Status | Notes |
|---|---|---|
| Dedicated venv isolation | **PLANNED** | Moved to its own track - see Phase R below and [#24](https://github.com/TullysAC6/ac6-winloss-tracker/issues/24) |
| Support for CUSTOM MATCH / RANK MATCH: TEAM | **PLANNED — moved to Phase 7A** | No longer deferred. Revision 2 requires TEAM and CUSTOM to be recorded as normal matches. The README statement stays accurate for the *shipped* release until 7A passes acceptance |
| Patch / version awareness (`game_version`, `parts_master_version`, `recognition_version`, `analytics_version`) | PLANNED | Leaves room for before/after balance-patch comparison. Existing data is not back-filled with a guessed version |
| Localisation beyond Japanese | BACKLOG | |
| Signed installer / SmartScreen reputation | BACKLOG | |

---

## Much later

| Item | Status | Notes |
|---|---|---|
| Self-build linkage (`Current Build` selection, self × opponent cross analysis) | **BACKLOG** | Recorded as a requirement on 2026-09-12: [#27](https://github.com/TullysAC6/ac6-winloss-tracker/issues/27), MASTER_REQUIREMENTS §52. Explicit selection only, never inferred; unset stays `unknown`. Does not require per-match OCR. Must not delay the current roadmap |
| TEAM three-opponent build recognition | DEFERRED | Request-driven only |
| Safe automatic historical completion | DEFERRED | Only if justified |
| Discord login | DEFERRED | Local-first is mandatory: with no login every core function still works, and a Discord outage disables nothing. Internal identity stays separate from external identity |
| Cloud sync / community features | DEFERRED | |
