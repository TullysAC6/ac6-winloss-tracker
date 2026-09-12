# Project state

Last updated: 2026-09-12 JST

Requirements: [MASTER_REQUIREMENTS.md](MASTER_REQUIREMENTS.md) (Revision 3) · roadmap: [ROADMAP.md](ROADMAP.md) · decisions: [DECISIONS.md](DECISIONS.md) · handover: [NEXT_SESSION.md](NEXT_SESSION.md) · GitHub entry point: [#6](https://github.com/TullysAC6/ac6-winloss-tracker/issues/6)

`Requirement` / `Implemented` / `Accepted` / `Released` are separate states throughout this file. This snapshot was produced by reading GitHub, not chat history.

## Current position

| Item | State |
|---|---|
| `main` | `f2f72a5` — "Merge pull request #23 from TullysAC6/hotfix/readme-bootstrap-hash-v1.1.0" |
| Latest GitHub Release | **v1.1.0**, published 2026-09-11, tag `v1.1.0` → `7a5959f` |
| Previous releases | v1.0.1 → `54ba0d8`; v1.0.0 |
| Release acceptance | **BLOCKED** — see below. `Released` is *not* recorded for v1.1.0 |
| Canonical requirements | Revision 3 (2026-09-12) |
| Open generation A | draft PR #5 on `claude/settings-analytics-20260909` — 6 commits ahead of `main`, **17 commits behind** |
| Open generation B | draft PR #13 on `coordination/project-management-20260909` — documentation only |

Merged since the last snapshot: PR #19 (screenshot occlusion), PR #20 (visible overlays in effect screenshots), PR #21 (v1.1.0 release preparation), PR #22 (RC → `main`), PR #23 (bootstrap hash hotfix).

## v1.1.0 — published, but acceptance is blocked

The stable GitHub Release exists and the code is public and in use. Formal release acceptance is nevertheless **blocked**, and this distinction must not be collapsed.

Post-release verification found that the README integrity hash had been calculated from a Windows CRLF archive checkout (`435F7755…`) instead of the public Git blob bytes returned by `raw.githubusercontent.com` (`2FDE252F…`). The install command in the tagged README therefore stops safely, before execution, with `bootstrap SHA-256 mismatch`.

| | |
|---|---|
| Immutable annotated tag | `v1.1.0` → `7a5959fe4b18c6b2cf74f26c239c1463a9feac12` — not moved, deleted or overwritten |
| Release draft conversion | Refused by GitHub (HTTP 422); a published Release is immutable |
| Corrective PR | #23, merged to `main` as `f2f72a5c2e004f01bdd53009d230a4be88955dea` |
| Correct public bootstrap SHA-256 | `2FDE252FA841430C845681BB23860E2D365F8A575CB2ED39515AC5F9F2CB41B7` |
| PR #23 CI | PASS on Python 3.12 / 3.13 / 3.14 plus the aggregate job |
| Post-correction distribution smoke | PASS — latest install and fixed-tag uninstall through metadata, GitHub digest, checksum and syntax validation, with child execution stubbed so user data and the live tracker were untouched |

Because the immutable `v1.1.0` tag still contains the historical README hash mismatch, a follow-up immutable version (recommended `v1.1.1`) is required to satisfy the tagged-tree invariant. Until then:

- **Do not record `Released`** in this file or in `NEXT_SESSION.md`.
- **Do not close** [#7](https://github.com/TullysAC6/ac6-winloss-tracker/issues/7) or [#4](https://github.com/TullysAC6/ac6-winloss-tracker/issues/4).
- **Never move the `v1.1.0` tag.**

## Implemented and shipped in v1.1.0

- Windows Graphics Capture for result recognition, with a guarded foreground desktop fallback.
- Continued recognition and recovery around Alt+Tab and capture interruption.
- Exactly-once fresh milestone effect recovery across an SSE reconnect ([#4](https://github.com/TullysAC6/ac6-winloss-tracker/issues/4)).
- Dashboard history expanded to the latest 50 rows with scrolling.
- Optional milestone Effect Screenshot and its settings GUI.
- Effect Screenshot capture of the composition the user actually sees over the AC6 client, including visible SteamP2PScanner / NVIDIA / Steam / Discord-style overlays. Overlap alone is not a rejection reason.
- Diagnostic and process/lifecycle hardening, including duplicate prevention and owned-worker cleanup.
- Source install / update / rollback / uninstall flows that preserve history and configuration.

Detector MSS fallback still uses `region_unobscured()`. Screenshot capture still requires the AC6 foreground/target HWND/client rect, a visible Tracker effect, real banner pixels, duplicate suppression, and the bounded owned-worker lifecycle.

Supported shipped mode remains **RANK MATCH: SINGLE only**. The TEAM / CUSTOM requirements in the master requirements are future requirements, not released behaviour.

## Gate evidence carried forward

For runtime RC `93d5a57`, accepted for v1.1.0:

- **T0** PASS. **T1 N/A / not run** — the formal fixture/replay harness is [#14](https://github.com/TullysAC6/ac6-winloss-tracker/issues/14) and does not exist; no T1 PASS is claimed. **T2** PASS.
- Independent runtime review PASS for PR #20; no High or Release blocker.
- Focused real-AC6 **T3** PASS on 2026-09-11: five real wins produced exactly one PNG for effect `1m4k1FRsEEoa3U5mqRiDCL51`, containing the AC6 game, the real `5連勝 激アツ!!` banner, and the user's visible overlay composition. Evidence in [#7](https://github.com/TullysAC6/ac6-winloss-tracker/issues/7).

The earlier `spsgui.exe` occlusion FAIL predates PR #20 and is superseded. Natural DRAW, milestones 10–50, a natural WGC stall, a stale-age anomaly, client-rect mutation and a milestone precisely during an SSE reconnect were recorded as non-blocking for this release, covered by targeted automation plus existing real-session evidence. This documentation sync executed no tests and claims no new gate results.

## Requirements added on 2026-09-12 — Revision 3

Adopted by the user, recorded here so they are recoverable from GitHub alone. All are **planned or backlog**; none is authorisation to implement now.

| Area | Requirement | Issue | Status |
|---|---|---|---|
| Runtime isolation | App-local Python environment and dependency isolation. A venv is **not** a sandbox: `requirements.lock`, hash pinning and binary-only policy are unchanged. `pythonw` / worker actual-PID ownership is a known hazard with unmerged historical evidence on `fix/venv-launcher-ownership` and `release/v1.1.0-venv` | [#24](https://github.com/TullysAC6/ac6-winloss-tracker/issues/24) | PLANNED, after #14 |
| UI/UX | `Fluent shell × AC6 telemetry × Pachinko celebration`. Polish, not a rebuild. Player Overlay and Broadcast Overlay are separate audiences sharing one design system. Performance is a hard constraint; no framework migration as the opening move | [#25](https://github.com/TullysAC6/ac6-winloss-tracker/issues/25) | PLANNED, UI-0 is documentation only |
| Tray / Launcher | A process-architecture change, not visual polish. Last phase, own issue and own PR. Not implemented unless its lifecycle safety can be demonstrated | [#26](https://github.com/TullysAC6/ac6-winloss-tracker/issues/26) | BACKLOG |
| Self-build linkage | Explicit `self_build_id` selection per match, never inferred; unset stays `unknown`. Enables self × opponent cross-analysis | [#27](https://github.com/TullysAC6/ac6-winloss-tracker/issues/27) | BACKLOG |
| Seasonal rank / rating progression | The user's own rank and rating over time, separated by season and never carried forward. **The pre-S and S rating systems are not one scale** — the boundary is pre-S / non-S (UNRANKED through A4) vs S, with **A4 on the pre-S side** — and the pre-S → S boundary is not drawn as one continuous line without a justified basis. Event-driven recognition only; recognition failure never touches WIN/LOSE, ResultGate, streak or match persistence. Work lands in #15 (acquisition, persistence, `rating_mode`), #16 (progression, chart, Season High/Low, delta, transition markers, `S RANK REACHED`) and #25 (season selector, chart presentation) | [#28](https://github.com/TullysAC6/ac6-winloss-tracker/issues/28) | PLANNED, gated on reliable self-rank recognition |

Requirements: MASTER_REQUIREMENTS §52, §56–§63 and §64. Reasoning: [DECISIONS.md](DECISIONS.md).

## Next actions

1. Publish a corrected immutable version (recommended `v1.1.1`) so the tagged tree and the published README agree, then re-run the public-distribution smoke test in isolation.
2. Only then record `Released`, and close [#7](https://github.com/TullysAC6/ac6-winloss-tracker/issues/7) and [#4](https://github.com/TullysAC6/ac6-winloss-tracker/issues/4) in a docs-only commit. Never move the `v1.1.0` tag.
3. Reconcile draft PR #5 with the current `main` — **merge only; no reset, rebase or force-push** — then T0 → T1 (N/A until #14) → T2 → PR handoff and review → required fixes → re-run the affected gates → T3 → `main`.
4. Then [#14](https://github.com/TullysAC6/ac6-winloss-tracker/issues/14) on a fresh branch and worktree cut from the new `main`.
5. Then [#24](https://github.com/TullysAC6/ac6-winloss-tracker/issues/24) runtime isolation, and [#25](https://github.com/TullysAC6/ac6-winloss-tracker/issues/25) UI-0 as a reviewed design specification.
6. Merge documentation PR #13 when the user is ready; it is documentation only.

At most **two unmerged generations** at a time (MASTER_REQUIREMENTS §4). Today those are PR #5 and PR #13.

## Process safety

Do not modify the user's live Tracker, history, config, stats, diagnostics, port 8765, runtime files or Overlay mutex. Use isolated temporary roots, bounded waits, and verify child/grandchild process, port, runtime-file and temporary-directory cleanup. Do not kill processes owned by another session. This documentation sync started no long-running process.
