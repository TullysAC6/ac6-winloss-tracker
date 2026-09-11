# Next session

Last updated: 2026-09-11 JST

Read [MASTER_REQUIREMENTS.md](MASTER_REQUIREMENTS.md) first, then [PROJECT_STATE.md](PROJECT_STATE.md), issue #7, and the current GitHub branches/PRs/releases.

## Current handoff

- Public stable is still v1.0.1 until GitHub Release v1.1.0 is actually published.
- Runtime RC `93d5a57b88a86ec4b8846a3082089ed97f13a818` is accepted for v1.1.0.
- Focused Effect Screenshot T3 PASS is recorded in issue #7: real AC6 + real `5連勝 激アツ!!` banner + visible user overlay composition, one saved PNG for effect `1m4k1FRsEEoa3U5mqRiDCL51`.
- The older `spsgui.exe` occluded FAIL is superseded by PR #20 and the 2026-09-11 PASS.
- T1 is N/A/not run because issue #14's formal fixture/replay harness does not exist. Do not call it PASS.
- Release work is on `release/v1.1.0`, based on exact RC `93d5a57`. No runtime/gameplay change is permitted during release prep.
- Draft PR #5 remains a separate generation and is not part of v1.1.0.

## Release Acceptance decision

Natural DRAW, every 10–50 milestone, a natural WGC stall, stale-age anomaly, client-rect mutation, and a milestone precisely during SSE reconnect are non-blocking for this release. Targeted automation and existing real-session evidence cover their code paths; forcing them as additional human T3 would be impractical. Residual risk is limited to rare environment/timing interactions and remains mitigated by fail-closed behavior, bounded diagnostics, and isolation from authoritative result counting.

Supported shipped mode remains **RANK MATCH: SINGLE only**.

## Required order

1. Finalize v1.1.0 metadata, README commands, immutable bootstrap hash, docs, and test expectations.
2. Run T0, then T2. T1 remains N/A.
3. Review the release-prep diff; re-run affected gates if review changes anything.
4. PR release prep into the RC and wait for green CI; record the final RC SHA in issue #7.
5. Re-check that `main` is still expected, then PR the RC into `main` and wait for green CI.
6. If and only if all STOP conditions remain clear, tag the exact main release SHA as `v1.1.0`, build fresh assets, publish a non-draft/non-prerelease Release, and verify latest/tag/assets/digests/checksums/bootstrap bytes.
7. Run a public-distribution smoke test in isolation from the user's real history/config.
8. After post-release PASS, close #7 and #4 and make any Released-state docs-only coordination commit. Never move the v1.1.0 tag.

## STOP conditions

Stop before publication on unexpected `main`, an existing v1.1.0 tag/Release, CI failure, checksum/bootstrap mismatch, tagged-tree/asset mismatch, High/Release blocker, required runtime/gameplay change, user-data damage, or an unrollbackable state.

## Process safety

Do not stop or reinstall the user's current Tracker unless explicitly required and authorized. Release tests use isolated roots and ports, bounded waits, and must leave no child/grandchild process, test port, runtime file, mutex, staging directory, or temporary asset directory behind.
