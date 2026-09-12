# Next session

Last updated: 2026-09-12 JST

## Reading order

```text
Issue #6
→ docs/MASTER_REQUIREMENTS.md   (canonical requirement, Revision 3)
→ docs/PROJECT_STATE.md         (where the project actually is)
→ docs/NEXT_SESSION.md          (this file)
→ docs/ROADMAP.md
→ docs/DECISIONS.md
→ GitHub Project "AC6 Win/Loss Tracker Development"
→ the target issue / PR
```

**Read GitHub as the source of truth before trusting any SHA, version or status written in a prompt, a chat log or an older document — including this one.** Re-check `gh release list`, `gh issue list`, `gh pr list` and `main` at the start of the session.

## Current handoff

- `main` is `f2f72a5`. The **v1.1.0 GitHub Release is published**.
- **v1.1.0 release acceptance is BLOCKED.** The immutable `v1.1.0` tag (`7a5959f`) carries a README bootstrap hash taken from a CRLF archive checkout (`435F7755…`) rather than the public Git blob bytes (`2FDE252F…`), so the tagged install command fails closed with `bootstrap SHA-256 mismatch`. PR #23 fixed `main`; the tag cannot be changed and the Release cannot be returned to draft.
- Therefore: **do not write `Released`**, **do not close #7 or #4**, and **never move the `v1.1.0` tag**. A corrected immutable version — recommended `v1.1.1` — is required first.
- T1 remains **N/A / not run** because issue #14's fixture/replay harness does not exist. Do not call it PASS.
- Draft PR #5 is a separate generation, 6 ahead of and 17 behind `main`. It has not been reconciled.
- Draft PR #13 is this documentation branch. Documentation only.
- Canonical requirements are at **Revision 3** (2026-09-12), adding §56 runtime isolation, §57–§63 UI/UX and §64 seasonal rank / rating progression, and promoting §52 self-build linkage to a recorded requirement.

## Required order

1. Prepare and publish a corrected immutable version (recommended `v1.1.1`) whose tagged README hash matches the public bootstrap blob. Re-run the public-distribution smoke test against isolated roots and ports.
2. After that passes: record `Released`, close #7 and #4, and make the docs-only state update. Never move `v1.1.0`.
3. Merge documentation PR #13, so Revision 3 is canonical on `main` instead of on a branch. Documentation only — nothing in the release path waits on it, but merging it early frees a generation slot.
4. Reconcile draft PR #5 with the current `main`. **Merge only — no reset, no rebase, no force-push.** Then T0 → T1 (N/A) → T2 → PR handoff (§40) → independent review → required fixes → re-run the affected gates → T3 → merge.
5. Then [#14](https://github.com/TullysAC6/ac6-winloss-tracker/issues/14), the fixture / replay harness, on a fresh branch and worktree from the new `main`. Its layout must leave room for the later fixture families — season boundaries, and pre-S (UNRANKED through A4) and S rating presentations as distinct cases — because the harness is built once and then extended.
6. Then [#24](https://github.com/TullysAC6/ac6-winloss-tracker/issues/24), runtime isolation, with the full migration gate in MASTER_REQUIREMENTS §56.
7. Then [#25](https://github.com/TullysAC6/ac6-winloss-tracker/issues/25) **UI-0 only** — a design specification with no code change, reviewed by the user before any UI code is written.

## Order after that — dependency, not preference

The authoritative interleaved order is under **Sequencing** in [ROADMAP.md](ROADMAP.md):

```text
v1.1.1 → PR #13 → PR #5 → #14 → #24 → UI-0 → UI-1A → UI-1B
→ #15 → UI-2 → #16-A / #28 → UI-3A → #17 → #10/#11/#12 → UI-3B
→ #16-B → #18 → #27 → UI-4
```

Four placements in it are load-bearing and will look wrong to anyone reading only the phase tables:

- **#15 before UI-2.** UI-2 rebuilds the Dashboard and History shell. Doing it before the match-metadata contract is fixed means building that shell twice — once for today's row shape, once for the Ranked/Custom, Single/Team, rank-bearing shape.
- **#16 owns the data, #25 owns the charts.** #16 computes periods, rolling win rate, per-season rank/rating series, deltas and achievements. UI-3A / UI-3B render them. Neither implements the other's half.
- **UI-3 is split.** UI-3A is Growth / Rank / Rating (#16-A, #28) and can ship as soon as its data exists; UI-3B is Opponent build statistics (#17, #10/#11/#12) and follows the recognition programme. They are not one milestone.
- **#16-B before #18.** Advanced analytics run on data the user has actually accumulated. Historical backfill is retroactive data entry and does not gate them.

At most two unmerged generations exist at a time (§4). Today those are PR #5 and PR #13, so nothing new starts until one of them lands.

## What is newly recorded and must not be lost

| | Issue | Requirement |
|---|---|---|
| App-local Python environment / dependency isolation | [#24](https://github.com/TullysAC6/ac6-winloss-tracker/issues/24) | §56 |
| UI/UX polish — `Fluent shell × AC6 telemetry × Pachinko celebration`, Player vs Broadcast overlays | [#25](https://github.com/TullysAC6/ac6-winloss-tracker/issues/25) | §57–§63 |
| Tray / Launcher modernization — lifecycle change, last, own PR | [#26](https://github.com/TullysAC6/ac6-winloss-tracker/issues/26) | §63 |
| Self-build linkage — `self_build_id`, explicit selection, never inferred | [#27](https://github.com/TullysAC6/ac6-winloss-tracker/issues/27) | §52 |
| Seasonal rank / rating progression — per season, never carried forward, A/S not one scale | [#28](https://github.com/TullysAC6/ac6-winloss-tracker/issues/28) | §64 |

Four things in there are easy to erode and are the reason they are written down:

- **A venv is not a sandbox.** `requirements.lock`, hash pinning and binary-only policy survive the runtime-isolation migration untouched, and `pythonw` worker PID ownership must be re-proved, not assumed.
- **A UI change may not cost game performance**, and UI polish is never a reason to touch Detector, ResultGate, WGC or process lifecycle.
- **No framework migration as the opening move** of visual modernisation.
- **The pre-S → S rating boundary is not one continuous line.** The ladder is `UNRANKED → … → A4 → S`, and the boundary is **pre-S / non-S (through A4) vs S** — A4 is on the pre-S side, so never write "below A" for it. The game presents rating differently on each side, so the obvious-looking single-line chart asserts a comparison the game does not support, and it fails silently. Rank/Rating recognition is event-driven, never a continuous OCR loop, and a failed read is recorded as a failed read — never as a rating change.

## STOP conditions

Stop before publishing anything on: an unexpected `main`; an existing tag or Release for the version being prepared; CI failure; checksum or bootstrap mismatch; tagged-tree/asset mismatch; a High or Release blocker; a required runtime or gameplay change during release preparation; user-data damage; or any state that cannot be rolled back.

## Process safety

Do not stop or reinstall the user's live Tracker unless explicitly required and authorised. Use isolated roots and ports and bounded waits. Leave no child or grandchild process, test port, runtime file, mutex, staging directory or temporary asset directory behind. Do not reset, rebase or force-push a branch owned by another session, and do not kill another session's processes. Check PID, port, mutex/lock and runtime files before reusing anything.
