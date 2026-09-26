# Project state

Last updated: 2026-09-26 JST

Requirements: [MASTER_REQUIREMENTS.md](MASTER_REQUIREMENTS.md) (Revision 4; canonical on main since PR #31 merged as e214ae0) · roadmap: [ROADMAP.md](ROADMAP.md) · decisions: [DECISIONS.md](DECISIONS.md) · GitHub entry point: [#6](https://github.com/TullysAC6/ac6-winloss-tracker/issues/6)

`Requirement` / `Implemented` / `Accepted` / `Released` are separate states throughout this file.

## Position

| Item | State |
|---|---|
| Public stable | **[v1.2.0 — RELEASED](https://github.com/TullysAC6/ac6-winloss-tracker/releases/tag/v1.2.0)**, annotated tag `v1.2.0` → `c64b241c6b14b54bf7a4ac7897d3be42c8d7d9f4` |
| `main` product baseline | PR #48 (UI-1B) merged as `b64ce73c27b7b5c65fa5203ec90bdb85886b0d3b`, whose tree is identical to the accepted head `499939f2e3e5ccfcfd25972fe8be3037172afe33`. [Exact-main CI 36230655908](https://github.com/TullysAC6/ac6-winloss-tracker/actions/runs/36230655908) SUCCESS. Before it: PR #46 (UI-1A) merged as `c2b00dc65a3511c120dc3a6595a55cdd014b8c28`, tree identical to accepted head `6bb7ecbc156b995187ea8ad39976e18a0a85b198` ([exact-main CI 36132227642](https://github.com/TullysAC6/ac6-winloss-tracker/actions/runs/36132227642) SUCCESS), then docs-only PR #47. Earlier: PR #40 merged as `216648741d2c193f8eeb9694e9ff9572dd825a3d` ([exact-main CI 35564747847](https://github.com/TullysAC6/ac6-winloss-tracker/actions/runs/35564747847) SUCCESS), then documentation PRs #41 / #42 / #44 / #45 and test-only PR #43, none of which changed the production runtime. Resolve `origin/main` again before later work |
| Accepted runtime | PR #48 exact head `499939f2e3e5ccfcfd25972fe8be3037172afe33` (UI-1B), merged as `b64ce73c27b7b5c65fa5203ec90bdb85886b0d3b`, on top of PR #46 exact head `6bb7ecbc156b995187ea8ad39976e18a0a85b198` (UI-1A, merged as `c2b00dc65a3511c120dc3a6595a55cdd014b8c28`) and the PR #40 runtime (`9aa883c`, merged as `2166487`); **all accepted but not released**. Public stable v1.2.0 still contains the earlier PR #5 runtime (`38a21c2`, merged as `a042b18`) |
| UI-1B Broadcast Overlay generation | **PR #48: Implemented + Accepted + merged to `main`** (`b64ce73`, 2026-09-26). Real-machine T3 PASS on the exact head. **Not released**. See the PR #48 section below |
| UI-1A Player Overlay generation | **PR #46: Implemented + Accepted + merged to `main`** (`c2b00dc`, 2026-09-25). Real-machine T3 PASS on the exact head. **Not released**. See the PR #46 section below |
| Superseded release | v1.1.0, tag `7a5959f` — published, runtime accepted, **formal release acceptance never completed**; superseded by v1.1.1 |
| Settings / analytics generation | **PR #5: Implemented + Accepted + merged to `main`** (`a042b18`, 2026-09-13). Focused real-AC6 T3 PASS on the exact head `38a21c2`. **Released in v1.2.0**; not part of v1.1.1 |
| Test-harness generation | **PR #35 (#14): Implemented + Accepted + merged to `main`** (`f9f5f0f`, 2026-09-18). Test infrastructure only; production runtime unchanged, so it carries no release payload. **[#14](https://github.com/TullysAC6/ac6-winloss-tracker/issues/14) is CLOSED as completed** (2026-09-19) |
| §42 performance/security baseline | Moved out of #14 to its own owner, **[#37](https://github.com/TullysAC6/ac6-winloss-tracker/issues/37) — OPEN, PLANNED, not started**. #17 and #25 consume it for their own acceptance |
| T0 TEMP leak | **Fixed and merged.** PR #38 (test-only; production runtime unchanged) merged as `3bd89a3` on 2026-09-19 after [independent review](https://github.com/TullysAC6/ac6-winloss-tracker/pull/38#pullrequestreview-5249924515) GO. A full T0 run now leaves 0 `tmp*` directories (was 12). [`main` CI 35366334508](https://github.com/TullysAC6/ac6-winloss-tracker/actions/runs/35366334508) green |
| Source-install CI race | **Fixed and merged (test-only).** The first exact-main CI of `be77844` ([run 35798045591](https://github.com/TullysAC6/ac6-winloss-tracker/actions/runs/35798045591), attempt 1) failed on Python 3.13 because `tests/test_source_install_flow.ps1` read `.dashboard-runtime.json` after a fixed 2 s sleep; an unchanged rerun of the same SHA passed. Classified as a test-fixture timing race, not a product defect. [PR #43](https://github.com/TullysAC6/ac6-winloss-tracker/pull/43) replaces the sleep with a bounded 7 s / 100 ms readiness poll that fails on early exit, requires parseable runtime JSON with a PID, and requires that PID to equal the launched process. Merged as `0333183b8abe512262910ad81a6a1aadf09e8dda`. Independent review GO is recorded in the PR #43 handoff checklist (no separate GitHub review object); T3 N/A (production runtime unchanged) |
| Documentation generation | PR #13 merged Revision 3. PR #31 preserves those requirements and adds Revision 4 / §65; Revision 4 is canonical on main after merge `e214ae0`. UI-0 design spec PR #42 merged as `be77844` |
| Unmerged generations | **No product generation is active.** PR #48 (UI-1B) is merged. #15, #28-A, UI-2, the shared visibility settings and Player personalization have not started |
| Formal T1 | **AVAILABLE, and PASSES on `main`.** `python tests/run_t1.py` — 42 of 42, 0 skipped, 25 images + 17 sequences, corpus SHA-256 `6cfc4873bd0aa2bd…`. **T1 is no longer N/A** |
| Current position | [#24](https://github.com/TullysAC6/ac6-winloss-tracker/issues/24) is **ACCEPTED — UNRELEASED**. [UI-0](UI0_DESIGN_SPEC.md) is **DESIGN SPEC APPROVED + MERGED** (docs-only PR #42, `be77844`); the spec is canonical on `main`. This is a design state, not Implemented, Accepted or Released. **UI-1A is ACCEPTED + MERGED — UNRELEASED** (PR #46, `c2b00dc`). **UI-1B is ACCEPTED + MERGED — UNRELEASED** (PR #48, `b64ce73`). Issue #25 remains open. Next in the Sequencing order is #15; like UI-2, the shared visibility settings and Player personalization, it is **NOT STARTED and NOT AUTHORIZED** |

## v1.2.0 published integrity — 2026-09-14

- **RELEASED:** [v1.2.0](https://github.com/TullysAC6/ac6-winloss-tracker/releases/tag/v1.2.0); PR #33 merged as `c64b241c6b14b54bf7a4ac7897d3be42c8d7d9f4`.
- Annotated tag object `d2a5c01065c3191c3b7bb3f821452abe518fbdf2` → the same release commit. The tagged tree equals independently reviewed PR head `9cffa5148e785d059049394d7085788a28693b39`.
- Independent release re-review: [5192880235](https://github.com/TullysAC6/ac6-winloss-tracker/pull/33#pullrequestreview-5192880235), GO. Exact-head push and PR CI passed; [release main CI](https://github.com/TullysAC6/ac6-winloss-tracker/actions/runs/34792826474) passed before tagging.
- Release T0: 42/42 files; distribution checks passed on PowerShell 7 and Windows PowerShell 5.1. T1 remains **N/A / not run**. Accepted feature T2 (209/209), lifecycle T2 (7 phases) and focused PR #5 T3 on `38a21c2` carry forward: release prep changes no product behaviour.
- Bootstrap Git blob `7e7f817c9ab8870611aac1cdca2cae18a451a472`, 8619 bytes: SHA-256 `82B223413A44BF9FDBBF399E7EED2AF6983794151DD25C9EE939B569BCD5881B`. Committed blob, public raw and both README literals agree.
- Assets were generated by `scripts/prepare-release-assets.ps1` in a clean checkout of `v1.2.0`. Script assets retain the Windows checkout bytes, while README bootstrap hashes use the raw Git blob bytes.

| Public asset | Bytes | SHA-256 (also matches GitHub digest) |
|---|---:|---|
| `install.ps1` | 57356 | `b803d24bba00f5f55b392d4731f8ea80fc2d500e373963d5c79cd35954347028` |
| `install.ps1.sha256` | 78 | `932931e18353a026ef414b197debdd03bca3ee139f0770e967a4d2f7c2234d7a` |
| `uninstall.ps1` | 8831 | `f971c45ae8735b7040a89264f1379dda7c1d98f9402a4881434b2166917e2a00` |
| `uninstall.ps1.sha256` | 80 | `f00e0d518b4df08c3fabd46c8d8d61649e851e27b4cf10d8d0f4f9cf0e8ee669` |

Both checksum sidecars match. On PowerShell 7 and Windows PowerShell 5.1, the public tag README's unmodified install and uninstall one-liners passed their hash gates, selected only `/releases/tags/v1.2.0`, verified the public assets and reached the stubbed installer/uninstaller boundary with tag `v1.2.0`. Actual installer child execution was disabled: no live installation, history, config, Desktop, port 8765 or Tracker process was modified, and smoke TEMP was empty after each command.

v1.1.1 is superseded by v1.2.0; all published tags and Releases remain immutable. The historical v1.1.0 distribution defect and v1.1.1 repair remain documented below. #14 / #24 were not part of this release task; their later states are recorded separately.

## Release history and the v1.1.0 → v1.1.1 distinction

This distinction must not be collapsed, because both releases carry the same runtime.

**v1.1.0** — runtime was accepted, including a real-AC6 T3, and it was published. Its **formal release acceptance was never completed**: post-release verification found that the README install one-liner inside the tag carried a bootstrap `SHA-256` computed from Windows CRLF working-tree bytes (`435F7755…`, 8814 bytes) instead of the Git blob bytes `raw.githubusercontent.com` serves (`2FDE252F…`, 8619 bytes), so the published command failed closed with `bootstrap SHA-256 mismatch`. A published tag and Release are treated as immutable here, so the tag was not moved and the Release was not edited. Superseded by v1.1.1.

**v1.1.1** — the same accepted runtime plus corrected immutable distribution metadata. `git diff --stat v1.1.0 v1.1.1` touches only version metadata, the README one-liners, release notes and tests; no runtime, gameplay, dependency, schema or install-strategy change.

## v1.1.1 published integrity

| | |
|---|---|
| Tag object | `7bf2a866646f46ad6e3804cdbb9229dc305be4e1` (annotated) → peeled commit `e0d84768dd739118f4bb9183af3d111041bddf33` |
| `bootstrap.ps1` blob | `0960b5ea9764c1c30328db9278a887869e3cb7fd` |
| Git blob SHA-256 | `C99A08AE973B745407A2FD2C6855F48DD0BCC9B498D34A7CEE84D7DF737F9158` (8619 bytes) |
| Public raw SHA-256 at `refs/tags/v1.1.1` | identical |
| README literal inside the tag (×2) | identical |
| `install.ps1` asset | `f7e6a32eb2628c468c04b69d85d99204438e35f47cdb8ec8f5be1eeea03cadb5`, 57356 bytes |
| `uninstall.ps1` asset | `f971c45ae8735b7040a89264f1379dda7c1d98f9402a4881434b2166917e2a00`, 8831 bytes |

Both `.sha256` sidecars match their asset, and all four match the digests GitHub reports. Assets were generated by `scripts/prepare-release-assets.ps1` from a clean checkout of the `v1.1.1` tag and are byte-identical to the tagged tree as checked out on Windows — the CRLF form of the tagged blobs, which is the convention every prior release used. `bootstrap.ps1` verifies the installer against GitHub's asset digest, so the asset chain is self-consistent regardless of line-ending form.

The rule this encodes: **the SHA-256 published in the README is the hash of the committed Git blob, never of a working-tree file.** `tests/test_readme_bootstrap_hash.py` now enforces it and fails on the v1.1.0 defect.

## Implemented and released

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

The earlier runtime `93d5a57` was accepted and released in v1.1.1. Its evidence remains applicable to the unchanged capture paths below; PR #5 acceptance and v1.2.0 release evidence are recorded separately.

- T0: PASS on the runtime, on the v1.1.0 release prep, and again on the v1.1.1 release prep.
- T1: **N/A / not run** for this runtime. The formal fixture/replay harness (issue #14) did not exist then; no T1 PASS is claimed for it. The harness now exists: see PR #35 below.
- T2: PASS, including the isolated install → update → injected rollback → abnormal-exit recovery → uninstall → reinstall flow with history/config/Screenshot-setting preservation.
- Independent runtime review: PASS for PR #20. Independent release-diff review: PASS for PR #29, no High or Release blocker.
- Focused real-AC6 T3: PASS on 2026-09-11 JST. Five real wins produced exactly one PNG for effect `1m4k1FRsEEoa3U5mqRiDCL51`; the PNG contains the AC6 game, the real `5連勝 激アツ!!` banner, and the user's visible overlay composition. Evidence in issue #7.
- v1.1.1 did **not** re-request real-AC6 T3. The runtime is byte-unchanged, so the v1.1.0 T3 evidence carries forward. Post-release public-distribution verification PASS: the published one-liner's hash gate passes, the release chain resolves `v1.1.1`, and the verified installer asset matches. Installer child execution was stubbed, so no user data and no live Tracker was touched.

## Release Acceptance scope

The following were not treated as blockers, because the same paths have automation and/or existing real-session evidence while forcing every case in live play would be disproportionate:

- Natural DRAW: semantics are covered by result/state/history automation; no natural DRAW occurred in the recorded sessions.
- Milestones 10–50: the milestone-5 T3 exercises the shared real capture path; all configured levels are covered by automation/native screenshot checks.
- Natural WGC stall, stale-age rejection, client-rect mutation, and a milestone precisely during SSE reconnect: covered by targeted automation plus existing real recovery evidence where available.
- Alt+Tab: real capture loss/recovery and a subsequent exactly-once result are recorded; targeted automation covers the boundary conditions.

Residual non-blocking risk: rare GPU/compositor/window-manager timing may behave differently on an unobserved desktop. The bounded diagnostics and fail-closed optional screenshot path remain in place; screenshot failure cannot alter result counting.

## Release scope

Supported mode in v1.2.0 remains **RANK MATCH: SINGLE only**. The broader TEAM/CUSTOM requirements in the master requirements are future requirements, not implemented or released behavior.

## Accepted and released in v1.2.0 — PR #5 (#8 / #9)

| | |
|---|---|
| Implemented | **yes**. The four-tab Settings GUI (表示・演出 / 成績 / メンテナンス / サポート); `effect_enabled` and `overlay_stats_scope` (config 17 → 18); history analytics (today / week / month / all time, recent 10 / 30 / 100); CSV export; DB `quick_check` / `integrity_check`; history purge (all / before a date); the Diagnostic Report from Settings; the update-check extension; and ordered result persistence with `pending-history.json` recovery |
| Accepted | **yes**. Focused real-AC6 T3 PASS on the exact head `38a21c2`, 2026-09-13 ([record](https://github.com/TullysAC6/ac6-winloss-tracker/pull/5#issuecomment-5653677193)) |
| Merged | `main` at `a042b18`, a merge commit whose tree is identical to `38a21c2`. `main` CI is green ([run 34761001222](https://github.com/TullysAC6/ac6-winloss-tracker/actions/runs/34761001222)) |
| Released | **yes**, [v1.2.0](https://github.com/TullysAC6/ac6-winloss-tracker/releases/tag/v1.2.0) on 2026-09-14. PR #33 only changes distribution/version metadata; accepted runtime behaviour is retained |

Gates on `38a21c2`:

- **T0:** PASS. The full local suite (42 files) passed, and CI is green.
- **T1:** **N/A / not run** on `38a21c2`; the #14 harness did not exist then.
- **T2:** PASS on 209/209 feature checks, and the lifecycle run passed all 7 phases.
- **Independent review:** five rounds requested changes, then [5190665161](https://github.com/TullysAC6/ac6-winloss-tracker/pull/5#pullrequestreview-5190665161) gave GO with no High or Medium findings.
- **T3:** **PASS**, as an isolated T3:
  - **Setup.** The exact-head worktree ran against an isolated `LOCALAPPDATA` seeded from a SHA-256-verified copy of the real data root.
  - **Result.** One real Ranked Single WIN (`auto`, 2026-09-13 22:24:40) took history from 184 → 185 and stats to 1 / 0, streak 1.
  - **Effect.** `effect_enabled` was `false`, and there were **0 effect events**. No `pending-history.json` remained.
  - **Exercised.** The Settings tabs, analytics on the real history, `quick_check` / `integrity_check`, CSV export (185 rows), session ↔ lifetime switching and the Diagnostic Report.
  - **Shutdown.** It returned HTTP 200 and left no process, port, runtime file or mutex behind.

The evidence is split by source on purpose; #8 and #9 name the source of each criterion.

| Source | Covers |
|---|---|
| Real T3 | Settings tabs; effect OFF with the result and streak still counted; exactly one real result; session / lifetime display; analytics on the real history; integrity check; CSV; Diagnostic Report |
| Prior real acceptance (v1.1.1, #7) | Effect Screenshot control and capture path, unchanged by PR #5 |
| T0 / T2 | Destructive reset / delete-before-date, restart persistence, update-check mechanics, milestone suppression with the effect OFF, and every failure path |

Some checks were **not** run on the user's real history: destructive maintenance, live-DB fault injection, a forced pending-history state and a 5-win milestone replay. They would be irreversible on the only copy, and T2 already drives the same server path. T2 also covers failure boundaries that a live run cannot inject safely.

**Live v1.1.1 data was not changed by the T3.** `history.db`, `stats.json` and `config.json` stayed byte-identical to the pre-T3 baseline. The only live change was a `startup.log` entry: the user accidentally started the normal launcher while the isolated instance owned port 8765, and it failed closed with `ENV-PORT-IN-USE` before taking ownership.

PR #5 (*Known limitations*) lists the known residuals; none of them blocks:

- the abrupt-death window between the history commit and the stats write
- the missing recovery hint for a corrupt `pending-history.json`
- the two meanings of `matches`

## Implemented, accepted and merged — PR #48 (UI-1B Broadcast Overlay)

| | |
|---|---|
| Implemented | **yes**, on exact head `499939f2e3e5ccfcfd25972fe8be3037172afe33`. The owner authorized UI-1B on 2026-09-25 ([record](https://github.com/TullysAC6/ac6-winloss-tracker/issues/25#issuecomment-5832871482)) within the pre-authorization decisions of [UI0_DESIGN_SPEC.md](UI0_DESIGN_SPEC.md) §20, decision 8 |
| Reviewed | **yes**. A fresh read-only independent review gave GO with no High or Medium finding. Its two Low findings were fixed in `499939f`, and the delta review of `499939f` also gave GO. Both are recorded in the [PR #48](https://github.com/TullysAC6/ac6-winloss-tracker/pull/48) description; there is no separate GitHub review object |
| Accepted | **yes**. Real-machine T3 PASS on the exact head ([T3 record](https://github.com/TullysAC6/ac6-winloss-tracker/pull/48#issuecomment-5844417204), [acceptance checkpoint](https://github.com/TullysAC6/ac6-winloss-tracker/issues/25#issuecomment-5844418399)) |
| Merged | `main` as `b64ce73c27b7b5c65fa5203ec90bdb85886b0d3b` (2026-09-26), a merge commit whose tree is identical to the accepted head. [Exact-main CI 36230655908](https://github.com/TullysAC6/ac6-winloss-tracker/actions/runs/36230655908) SUCCESS ([merge checkpoint](https://github.com/TullysAC6/ac6-winloss-tracker/issues/25#issuecomment-5844811665)) |
| Released | **no**. Public stable v1.2.0 does not contain UI-1B |

Merged Broadcast Overlay behaviour:

- **BEST STREAK toggle, the only new setting.**
  - `broadcast_show_best_streak` is a boolean in `preferences.json`, and `preferences_version` goes 1 → 2.
  - It is **ON by default**. A missing file, key or `/config` field behaves as ON, which is what every earlier build showed.
  - It is set in Settings → 表示・演出 → **OBS用オーバーレイ（配信画面）** → **最高連勝を表示する**.
  - It reaches OBS through the existing 3 s `/config` poll, so it applies without a Tracker restart or a source refresh.
  - `config.json` stays frozen.
- **Static streak status.** `アツい` … `RUSH継続中` keep their wording and colours. The infinite blink is removed and nothing replaces it; the milestone banner is the only animation.
- **Layout.** The primary text stays 24 px bold, the panel keeps the 22 px top-left default, and the product copy is unchanged.
  - The shown health warnings form one stack at the top right.
  - On a narrow source they drop below the panel instead of covering it.
  - Below 300 px of height they keep their previous top-right place.
- **Player isolation.** The Player Overlay is unchanged. In the merged implementation the Player streak-status setting and Broadcast BEST are separate, independent keys, and only the Broadcast key is published on `/config`.
- **Unchanged:** milestones (thresholds including 25 and 50, dedup/TTL, `effect_enabled`), detection, ResultGate, stats and history writes, and the server's thread and route counts and process topology.

Acceptance evidence:

- **Automated.** Exact-head CI [36145969986](https://github.com/TullysAC6/ac6-winloss-tracker/actions/runs/36145969986) and exact-main CI 36230655908 both passed. In each:
  - T0 passed;
  - T1 42/42, 0 skipped, corpus `6cfc4873bd0aa2bd`;
  - T2 passed, including the real round trip through UI-1A `7cc8ebe` (5/5) and headless-browser Broadcast geometry (9/9).
- **T3 environment.** An OBS Browser Source at 1920×1080 and 30 FPS. The installer was 72,457 bytes with SHA-256 `623FD96A3CD4965151392BA06DD77AA4588A83ACE912EEEE45408C794E462AF2`. Installing and starting did not rewrite `preferences.json`, which was still version 1.
- **OBS checks B1–B14 all passed:**
  - transparency, placement and copy;
  - BEST ON by default, then OFF and ON live;
  - a `preferences.json` v2 with neither key in `config.json`;
  - Settings usable;
  - Player and Broadcast independent in both directions;
  - a source refresh with no milestone replay.
- **Real result.** One genuine match passed with no double count.
- **Rollback round trip, with BEST saved OFF.**
  - Rolling back to the exact UI-1A build `7cc8ebe` worked. It started healthy (`/health` ok), kept the unknown v2 key, and showed its old always-visible BEST.
  - Re-upgrading restored OFF.
  - `config.json` and `preferences.json` stayed byte-identical throughout, and no file was edited by hand.
- **Shutdown.** 0 processes, 0 port listeners, 0 runtime files, and no named mutex held. The shortcut relaunch passed.
- **Not observed or measured, and not claimed:**
  - a natural streak-status word or milestone;
  - CPU, frame time, OBS render lag or skipped frames (the #37 baseline is not implemented);
  - the Windows display scaling;
  - the match outcome and Lifetime counts.

Known limitation, Low, accepted and non-blocking: on a Browser Source shorter than 300 px and narrower than about 1060 px, a shown health warning covers part of the stats panel. This keeps the warning visible instead of pushing it off the page, and the previous build also overlapped on narrow sources. It did not apply to the owner's 1920×1080 source. Separately, the PR #48 known limitations record a delta-review nit that the owner did not accept separately: on a source about 330 px wide or narrower and at least 300 px tall, the last warning can end below the page, which the previous build did not do. The other review nits are listed in the PR #48 description.

**Future shared visibility settings** were decided on 2026-09-26 ([#25](https://github.com/TullysAC6/ac6-winloss-tracker/issues/25#issuecomment-5843053161)):

- 連勝ステータスを表示 and 最高連勝を表示 each become one user preference shared by the Player and Broadcast overlays. The renderers may remain separate; only the preference state is shared.
- This supersedes the earlier assumption that these two toggles stay independent per overlay. It does not change what UI-1A and UI-1B delivered.
- Today the Player Overlay has the streak-status toggle but never shows BEST, and the Broadcast Overlay has the BEST toggle but always shows the status.
- **Open until the cleanup is authorized:**
  - whether BEST returns to the Player Overlay;
  - the shared defaults;
  - how the existing keys combine with the other overlay's fixed behaviour, deterministically where a user's Player and Broadcast behaviour of the same setting differ.

  The Player removal of BEST stays in force; any change to it is decided with the cleanup and takes effect only once implemented and accepted.
- It is **not started and not authorized**. Its migration keeps one-generation rollback, never touches `config.json` and needs no hand edit.
- Size, opacity, layout and position stay separately configurable; sharing any of them would need a separate owner decision. See [DECISIONS.md](DECISIONS.md#shared-visibility-settings-across-the-two-overlays).

## Implemented, accepted and merged — PR #46 (UI-1A Player Overlay)

| | |
|---|---|
| Implemented | **yes**, on exact head `6bb7ecbc156b995187ea8ad39976e18a0a85b198`. The owner authorized UI-1A on 2026-09-24 and added three requirements before T3: a user-toggleable Player streak status, rollback resilience, and README rollback documentation |
| Reviewed | **yes**. Fresh read-only independent reviews gave GO on every revision, the last on `f1c4982` and `6bb7ecb` with nothing above Low. They are recorded in the [PR #46](https://github.com/TullysAC6/ac6-winloss-tracker/pull/46) description; there is no separate GitHub review object |
| Accepted | **yes**. Real-machine T3 PASS on the exact head ([T3 record](https://github.com/TullysAC6/ac6-winloss-tracker/pull/46#issuecomment-5831572036), [acceptance checkpoint](https://github.com/TullysAC6/ac6-winloss-tracker/issues/25#issuecomment-5831590907)) |
| Merged | `main` as `c2b00dc65a3511c120dc3a6595a55cdd014b8c28` (2026-09-25), a merge commit whose tree is identical to the accepted head. [Exact-main CI 36132227642](https://github.com/TullysAC6/ac6-winloss-tracker/actions/runs/36132227642) SUCCESS |
| Released | **no**. Public stable v1.2.0 does not contain UI-1A |

Merged Player Overlay behaviour:

- **Value first.** Four values lead, with the existing labels `WIN / LOSE / 勝率 / 連勝` beneath them. A real-T3 finding on 2026-09-24 restored those labels; the UI-0 diagram showed hierarchy, not new copy.
- **BEST STREAK left the always-on Player panel only.** The statistic is unchanged and still shown in Dashboard Overview and in the Broadcast Overlay.
- **Streak status (`アツい` … `RUSH継続中`) is user-toggleable** in Settings → 表示・演出 → ゲーム内オーバーレイ, and applies live.
  - With no saved preference it is **ON**; a saved choice persists across updates.
  - It is Player-only static text and never blinks. OFF gives the quiet telemetry surface.
- **Japanese labels and status use a Japanese face on purpose** (Yu Gothic UI → Meiryo UI → Meiryo), after a second T3 finding. Numerals keep Segoe UI Variable.
- A responsive safe zone of max(24 logical px, 1.25% of the client) at the monitor's DPI, a one-shot 160 ms result acknowledgement, and a cyan rule, corner ticks and caption.
- **Panel opacity defaults to 15%** (was 10%).
- Unchanged: detection, ResultGate, stats and history writes, milestones (including the 25-win one), the Broadcast Overlay, the server, lifecycle and process topology.

Rollback architecture, established by this PR:

- **`config.json` is frozen** at `config_version` 18 and its current key set. The previous build and v1.2.0 (whose `config_utils.py` is identical) refuse unknown keys at startup (`ENV-CONFIG-INVALID`), so a new key there would block rollback.
- **Additive settings live in a versioned `preferences.json`** beside it, which neither the previous build nor v1.2.0 reads. The rules are recorded in [DECISIONS.md](DECISIONS.md#additive-settings-live-in-preferencesjson-configjson-is-frozen).
- README 「1つ前の公開版に戻す（ロールバック）」 documents the rollback to public **v1.2.0**, one generation only, with no manual `config.json` edit.

Acceptance evidence:

- **Automated.** Exact-head CI [36062379920](https://github.com/TullysAC6/ac6-winloss-tracker/actions/runs/36062379920) and exact-main CI 36132227642 both passed. In each: T0 passed; T1 42/42, 0 skipped, corpus `6cfc4873bd0aa2bd`; T2 passed, including the real previous-build round trip in `tests/test_rollback_previous_version.py`. The manual public-v1.2.0 rollback harness passed both scenarios on `d4cd682`; the later T3 fixes did not touch its paths.
- **T3 scope.** Run on the owner's real installation and data (plan v6 plus the owner-machine amendment). The installer was 72,457 bytes with SHA-256 `623FD96A3CD4965151392BA06DD77AA4588A83ACE912EEEE45408C794E462AF2`.
- **Player checks.** Labels, typography, HUD collision, readability, clipping, click-through, Alt+Tab, the Settings toggle, Session/Lifetime, reopen and second launch all passed. `アツい` appeared naturally at a real 3-win streak.
- **Settings isolation.** The setting was stored only in `preferences.json`; `config.json` stayed byte-identical and never contained the key.
- **Real results.** Five automatic results (ids 269–273) were each recorded once. Final Lifetime: WIN 190 / LOSE 83 / MATCHES 273 / BEST 8.
- **Rollback round trip.** With the setting saved OFF, the README rollback to v1.2.0 was run. v1.2.0 started healthy and ignored `preferences.json`. Re-upgrading restored OFF. `config.json` and `preferences.json` stayed byte-identical throughout.
- **Shutdown.** Normal shutdown left 0 processes, 0 port listeners, 0 runtime files and no named mutexes. The shortcut relaunch passed.
- **Not observed or measured, and not claimed:** a natural milestone; status words other than `アツい`; frametime or CPU, since the #37 baseline is not implemented; the owner's display resolution and scale.

Non-blocking, owner-accepted: at 15% opacity, large white `RANK MATCH: SINGLE` game text can compete with the overlay where the two overlap. The owner accepted this for UI-1A.

**Future Player personalization** was adopted on 2026-09-25 ([#25](https://github.com/TullysAC6/ac6-winloss-tracker/issues/25#issuecomment-5831075818)): text size, panel size/density, opacity, position and reset to defaults, stored through `preferences.json`. It is a future requirement, **not scheduled and not authorized**, and not yet part of MASTER_REQUIREMENTS.

Recorded residuals, not changed:

- An invalid `preferences.json` disables the Settings display tab (strict by design).
- The diagnostics ZIP does not include `preferences.json`.
- Settings validation assumes boolean preferences; a non-boolean preference needs it extended.

Release-preparation items, not done here:

- The README key list and screenshot.
- The two README rollback wording nits from the `d4cd682` review.
- At the next release, the README rollback section and its test and harness constants move to the release being superseded, and the public-rollback harness is re-run for real.

## Implemented, accepted and merged — PR #40 (#24 app-local Python environment)

| | |
|---|---|
| Implemented | **yes**, on exact candidate `9aa883cd0a09ad7940b0b38f95e94c701a203f7a`. The Tracker now owns an app-local environment and supports verified exact-commit candidate installation and migration from the legacy shared-Python layout |
| Reviewed | **yes**. Fresh independent Codex Sol review: GO, Critical 0 / High 0 / Medium 0 / Low 0 ([PR record](https://github.com/TullysAC6/ac6-winloss-tracker/pull/40#issuecomment-5755487286), [issue record](https://github.com/TullysAC6/ac6-winloss-tracker/issues/24#issuecomment-5755487353)) |
| Accepted | **yes**. Real-machine T3 PASS on the exact candidate ([PR evidence](https://github.com/TullysAC6/ac6-winloss-tracker/pull/40#issuecomment-5755833997), [issue evidence](https://github.com/TullysAC6/ac6-winloss-tracker/issues/24#issuecomment-5755833964)) |
| Merged | `main` as `216648741d2c193f8eeb9694e9ff9572dd825a3d`; the merge preserves exact tested head `9aa883cd0a09ad7940b0b38f95e94c701a203f7a`. [Exact-main CI 35564747847](https://github.com/TullysAC6/ac6-winloss-tracker/actions/runs/35564747847) SUCCESS |
| Released | **no**. Public stable v1.2.0 does not contain #24 |

Acceptance evidence:

- T0, formal T1, full T2, lifecycle and candidate-installer matrices passed before T3; exact-head CI was green.
- The T3 installer was 72,457 bytes with SHA-256 `623FD96A3CD4965151392BA06DD77AA4588A83ACE912EEEE45408C794E462AF2`; downloaded raw bytes matched the committed/raw hash and installation exited 0.
- Migration from stable v1.2.0 preserved 246 logical history rows (172 wins / 74 losses / 0 draws) and the exact `config.json` hash. The legacy source was removed only after success, and the shortcut moved to the app-owned `pythonw` and launcher.
- Four real candidate-session matches were recorded once with distinct event IDs. The scripted pair changed 248 / 173 W / 75 L to 249 / 174 W / 75 L, then 250 / 174 W / 76 L.
- Health, HTTP, overlay and port ownership passed. Normal shutdown, shortcut relaunch, and final shutdown left zero Tracker processes, port listeners, runtime files and named mutexes.
- The Effect Screenshot milestone was not naturally reached because the best streak was 1. No history or statistics were manipulated; this was allowed by the approved T3 plan.
- One earlier cleanup check ran before the user selected **Trackerを終了**. After normal exit was requested, lifecycle cleanup passed; this was operator sequencing, not a product failure.

Known non-blocking residuals remain bounded: shared user-site packages are deliberately not deleted; removal of the shared base Python can invalidate a venv and is detected; a post-commit cleanup failure is deferred rather than corrupting the committed install; and rare native process timing remains covered by bounded ownership and cleanup checks.

## Implemented, accepted and merged — PR #35 (#14 formal T1 harness)

| | |
|---|---|
| Implemented | **yes**. `python tests/run_t1.py`: stored pixels replayed through the real `ResultDetector.run`, `ResultClassifier`, motion helpers, `ResultStateMachine` and `ResultGate` (and the real `GameCapture.grab` for two WGC-boundary sequences), in isolated, guarded, job-owned workers. A strict schema, loader and path confinement; 25 image records plus 17 sequences; reserved families for match metadata, ranks, season, rating and builds. `tests/gate_registry.py` gives every test exactly one of T0 / T1 / T2; CI runs T0 → T1 → T2 → source install on 3.13 and 3.14 |
| Accepted | **yes**. Independent review [5246833703](https://github.com/TullysAC6/ac6-winloss-tracker/pull/35#pullrequestreview-5246833703): GO, Critical 0 / High 0 / Medium 0. [Acceptance record on #14](https://github.com/TullysAC6/ac6-winloss-tracker/issues/14#issuecomment-5731935668) |
| Merged | `main` at `f9f5f0f` (2026-09-18), a merge commit whose tree is identical to the reviewed head `09cd516`. [`main` CI 35336092717](https://github.com/TullysAC6/ac6-winloss-tracker/actions/runs/35336092717) green |
| Released | not applicable. Test infrastructure only; **production runtime changed: NO**. v1.2.0 is unchanged |

Gates:

- **T0:** PASS.
- **T1:** **PASS — 42/42, 0 skipped, 25 images + 17 sequences, corpus SHA-256 `6cfc4873bd0aa2bd…`**, on the exact head, on `main` CI (3.13 and 3.14, read from the job logs) and in the independent local run. This is the first formal T1. From here on **T1 is a real gate, not N/A**, and a T1 regression blocks progression.
- **T2:** PASS, all 22 Python entries. **Source install flow:** 7/7.
- **T3:** **N/A**. No production runtime changed, so a real AC6 session could observe nothing new. The earlier T3 on `38a21c2` carries forward.
- **Non-vacuity:** 18 of 19 independent mutants make T1 fail. All 42 pre-#14 pixel assertions were kept (36 in T1, 6 in T0) and all 25 manifest labels are unchanged.

PR #35 did not deliver one #14 checklist item, which the #14 design explicitly kept out of T1: *Performance/security regression baseline capture (Tracker OFF / current accepted / + feature)*, MASTER_REQUIREMENTS §42. That item has since moved to its own owner, **[#37](https://github.com/TullysAC6/ac6-winloss-tracker/issues/37) (OPEN, PLANNED)**, and **#14 is CLOSED as completed** (2026-09-19, [closure](https://github.com/TullysAC6/ac6-winloss-tracker/issues/14#issuecomment-5732296924)).

Non-blocking follow-ups, recorded and not implemented:

- **L-A:** `tests/t1/guards.py` `guarded_pair()` binds keyword paths as `src`/`dst`, so `_winapi.CopyFile2(existing_file_name=…, new_file_name=…)` is not refused. It is a private API; the public `shutil.copy2` is guarded.
- **L-B:** `canonical_corpus` is a path comparison, and the corpus SHA is reported but not pinned. Deleting a redundant sequence still gives T1 PASS.
- **Pre-existing, not from #35:** the `tempfile.mkdtemp()` TEMP leak in `tests/test_stats_manager.py` and `tests/test_detector.py` (12 `tmp*` per T0 run). **Fixed by PR #38**, merged as `3bd89a3`: each case now owns a `TemporaryDirectory()` context, assertions are unchanged, and residue is 0. On a cp932 host, `tests/test_pending_history_recovery.py` needs `PYTHONUTF8=1` for child output.

## Requirements added on 2026-09-12 — Revision 3

Adopted by the user, recorded here so they are recoverable from GitHub alone. All are **planned or backlog**; none is authorisation to implement now.

| Area | Requirement | Issue | Status |
|---|---|---|---|
| Runtime isolation | App-local Python environment and dependency isolation. A venv is **not** a sandbox: `requirements.lock`, hash pinning and binary-only policy are unchanged. `pythonw` / worker actual-PID ownership is covered by automated and real-machine lifecycle evidence | [#24](https://github.com/TullysAC6/ac6-winloss-tracker/issues/24) | **ACCEPTED — UNRELEASED**; PR #40 merged, but v1.2.0 does not contain it |
| UI/UX | `Fluent shell × AC6 telemetry × Pachinko celebration`. Polish, not a rebuild. Player Overlay and Broadcast Overlay are separate audiences sharing one design system. Performance is a hard constraint; no framework migration as the opening move | [#25](https://github.com/TullysAC6/ac6-winloss-tracker/issues/25) | [UI-0 design spec](UI0_DESIGN_SPEC.md) **APPROVED + MERGED** (docs-only PR #42, `be77844`); **UI-1A ACCEPTED + MERGED — UNRELEASED** (PR #46, `c2b00dc`); **UI-1B ACCEPTED + MERGED — UNRELEASED** (PR #48, `b64ce73`); UI-2 and later phases not started |
| Tray / Launcher | A process-architecture change, not visual polish. Last phase, own issue and own PR. Not implemented unless its lifecycle safety can be demonstrated | [#26](https://github.com/TullysAC6/ac6-winloss-tracker/issues/26) | BACKLOG |
| Self-build linkage | Explicit `self_build_id` selection per match, never inferred; unset stays `unknown`. Enables self × opponent cross-analysis | [#27](https://github.com/TullysAC6/ac6-winloss-tracker/issues/27) | BACKLOG |
| Seasonal rank / rating progression | The user's own rank and rating over time, separated by season and never carried forward. **The pre-S and S rating systems are not one scale** — the boundary is pre-S / non-S (UNRANKED through A4) vs S, with **A4 on the pre-S side** — and the pre-S → S boundary is not drawn as one continuous line without a justified basis. Event-driven recognition only; recognition failure never touches WIN/LOSE, ResultGate, streak or match persistence. Work lands in #15 (acquisition, persistence, `rating_mode`), #16 (progression, chart, Season High/Low, delta, transition markers, `S RANK REACHED`) and #25 (season selector, chart presentation) | [#28](https://github.com/TullysAC6/ac6-winloss-tracker/issues/28) | PLANNED, gated on reliable self-rank recognition |

Requirements: MASTER_REQUIREMENTS §52, §56–§63 and §64. Reasoning: [DECISIONS.md](DECISIONS.md). Order: **Sequencing** in [ROADMAP.md](ROADMAP.md).

## Requirements added on 2026-09-13 — Revision 4

| Area | Requirement | Issue | Status |
|---|---|---|---|
| Season Catalog / Assignment | Manual-first GitHub catalog refresh, local cache, fixed-schema validation, retrospective assignment, multi-season reconciliation, idempotency, transition / unresolved handling, and offline fallback. Season assignment is derived metadata and never alters authoritative result or Rating observation fields | [#28-A](https://github.com/TullysAC6/ac6-winloss-tracker/issues/28) | PLANNED after #15, before UI-2 |
| Seasonal analytics | #16-A / #28-B consumes resolved Season data only and recomputes after reconciliation; unresolved records are never placed in the current Season by guess | [#16](https://github.com/TullysAC6/ac6-winloss-tracker/issues/16), [#28](https://github.com/TullysAC6/ac6-winloss-tracker/issues/28) | PLANNED after UI-2 |

Known seed: Season 16 starts `2026-07-24 18:00 JST`; reset / transition starts
`2026-09-25 16:00 JST`; Season 17 starts only after reset completion and its exact timestamp is
not yet confirmed. The approximate two-month / Friday cadence is never authoritative.

## Next actions

1. **Issue [#24](https://github.com/TullysAC6/ac6-winloss-tracker/issues/24), the app-local Python environment: ACCEPTED — UNRELEASED.** Fresh independent Codex Sol review gave GO with zero findings; real-machine T3 passed on exact head `9aa883cd0a09ad7940b0b38f95e94c701a203f7a`; PR #40 merged as `216648741d2c193f8eeb9694e9ff9572dd825a3d`; exact-main CI 35564747847 passed.
2. [UI-0 design specification](UI0_DESIGN_SPEC.md) is **DESIGN SPEC APPROVED + MERGED**: docs-only PR #42 (exact head `587431d263b174b916efe42cae554e137fc54e93`) merged as `be77844dd86852f70e04d07b6885fbbdaa974fa8`. Owner decisions are in its §20, including the 2026-09-23 decision to keep the Broadcast primary text at the current 24 px bold baseline. UI-0 is a design state only, not Implemented, Accepted or Released.
3. **UI-1A Player Overlay polish: ACCEPTED + MERGED — UNRELEASED.** Real-machine T3 passed on exact head `6bb7ecbc156b995187ea8ad39976e18a0a85b198`; PR #46 merged as `c2b00dc65a3511c120dc3a6595a55cdd014b8c28`; exact-main CI 36132227642 passed. See the PR #46 section above.
4. **UI-1B Broadcast Overlay polish: ACCEPTED + MERGED — UNRELEASED.** Real-machine T3 passed on exact head `499939f2e3e5ccfcfd25972fe8be3037172afe33`; PR #48 merged as `b64ce73c27b7b5c65fa5203ec90bdb85886b0d3b`; exact-main CI 36230655908 passed. See the PR #48 section above.
5. **Next in the Sequencing order is #15**, the match metadata foundation, then #28-A and UI-2. None of them has started or is authorized, and each needs its own explicit owner authorization. Neither UI-1B's acceptance nor its merge starts any of them.
6. The shared visibility settings (see the PR #48 section) and future Player personalization (see the PR #46 section) are adopted but **not started and not authorized**.

**PR #35 (#14 formal T1 harness) has landed** (2026-09-18, `f9f5f0f`), and **#14 is closed**. Its §42 baseline item belongs to **#37** (OPEN, PLANNED), not to #24.

**PR #38 (test-only T0 TEMP-leak cleanup) has landed** (2026-09-19, `3bd89a3`), with green `main` CI.

PR #40, documentation PRs #41 / #42 / #44 / #45 / #47, test-only PR #43, UI-1A PR #46 and UI-1B
PR #48 are merged; no product generation is active. No UI-2, shared-settings or other product work
starts as part of this bookkeeping (items 5 and 6 above).
The L-A / L-B harness follow-ups remain recorded, not scheduled.

MASTER_REQUIREMENTS §34 / §36 now explicitly mark their older PR #5 snapshots as historical; this file remains the live position.

### Order from here

The authoritative interleaved order is under **Sequencing** in [ROADMAP.md](ROADMAP.md):

```text
✓ UI-0 → ✓ UI-1A (merged, unreleased) → ✓ UI-1B (merged, unreleased) → #15 → #28-A → UI-2 → #16-A / #28-B → UI-3A
   → #17 → #10/#11/#12 → UI-3B → #16-B → #18 → #27 → UI-4
```

**#15 precedes #28-A**, which settles Season catalog / assignment before UI-2 builds manual refresh
and History state. **#16 owns the analytics data while UI-3A / UI-3B own the chart
rendering**.

Never move the `v1.1.0`, `v1.1.1` or `v1.2.0` tag.

## Process safety

Do not modify the user's live Tracker, history, config, stats, diagnostics, port 8765, runtime files, or Overlay mutex during release testing. Use isolated temporary roots, bounded waits, and verify child/grandchild/process/port/temp cleanup.
