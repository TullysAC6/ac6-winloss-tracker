# #15-2: dormant per-match rank and recognition evidence

[Owner authorization](https://github.com/TullysAC6/ac6-winloss-tracker/issues/15#issuecomment-5901149943) and
[architecture proposal (boundary A)](https://github.com/TullysAC6/ac6-winloss-tracker/issues/15#issuecomment-5901186697).
Live review, CI, merge and acceptance evidence belongs in the slice PR and [Issue #6](https://github.com/TullysAC6/ac6-winloss-tracker/issues/6).
Public v1.2.0 does not contain this slice. No following slice is authorized.

## Boundary

This slice adds the remaining per-match fields of MASTER_REQUIREMENTS §10 / §13: `self_rank`, `opponent_rank`
(explicit Single only), and the metadata recognition status and version. They sit in one revisioned snapshot with
the #15-1 `match_type` / `match_format`.

The slice does not add:

- Rating observations, `rating_value` or `rating_mode`;
- observation identity, timestamps or source;
- Season fields or assignment;
- OCR, capture, recognizers, runtime callers, workers or UI;
- statistics or opponent builds;
- DRAW persistence, or any ResultGate, detector or CLEAR change.

`history.db` is unchanged and authoritative.

## Rank representation

The repository establishes only part of the rank vocabulary:

- **Established:** the ladder `UNRANKED → … → A4 → S`. The A band is `A1`–`A4` and A4 is directly below S (§64). Examples use the coarse `A` and `S`.
- **Not established:** the bands inside "…", and their subdivisions. No document or fixture names them, and every rank fixture family is reserved and empty.

A closed enum would therefore invent vocabulary. A rank is stored as one canonical token, exactly as observed:

| Token | Status |
|---|---|
| `UNRANKED`, `S` | established; no subdivision |
| `A`, `A1`, `A2`, `A3`, `A4` | established; coarse `A` is kept distinct from `A4` |
| one letter `B`–`R` or `T`–`Z`, optionally followed by one digit `1`–`9` | a bounded syntax for pre-S bands not yet established. It is not a claim that any such band exists |

Missing rank is `NULL`, which means unknown. Tokens are never normalized (`a4`, `A4 ` and fullwidth forms are rejected), never
completed (`A` never becomes `A4`), and never ordered. There is no ordinal or rating scale. S is the only S-side token,
so A4 and everything else is pre-S; no side column is stored.

Tightening the syntax later, once the real vocabulary is evidenced, is a separate migration. A token outside the
syntax is rejected, so a future reader records unknown rather than an invented rank.

## Snapshot and recognition evidence

One `match_snapshots` row per binding holds category, ranks and recognition evidence together.

Two applicability rules hold in every snapshot:

- `opponent_rank` is allowed only when `match_format = single`. A TEAM or unknown snapshot carrying one is rejected,
  never silently trimmed.
- Every write replaces the whole snapshot. So a Single → TEAM/unknown write removes the opponent rank atomically, and
  a later TEAM/unknown → Single write never brings it back.

`recognition_status` defines only the states current requirements need, with no confidence scale:

| status | meaning | constraint |
|---|---|---|
| `NULL` | no recorded recognition evidence: legacy/migrated values, or explicit values from a non-recognizer caller | `recognition_version` is `NULL` |
| `recognized` | every value in this snapshot came from the named recognizer; unknown fields stay unknown/`NULL` | at least one known value |
| `failed` | the named recognizer's read failed; no facts are claimed | category unknown/unknown and both ranks `NULL` |

- **Version:** `recognition_version` is required when a status is set. It is a token of 1–64 characters of
  `[A-Za-z0-9._:-]` that starts with a letter or digit. It names interpretation or recognizer semantics, never the
  application release.
- **Failed reads:** a failed read cannot relabel older facts as its own output. Recording it replaces the snapshot
  with an empty failed one; a caller that wants to keep the old facts simply does not write.
- **One recognizer per snapshot:** callers must not carry values from one snapshot into a new snapshot with a different status or version.
- **Absence:** "no snapshot" and "no recorded evidence" never mean "not attempted". No "partial" or "not_attempted"
  state is stored. Partiality shows as unknown fields; which fields apply to Custom or TEAM is not established.

The same rules are enforced twice: in code before any file is touched, and as `CHECK` constraints in the table.

## Revision and stale writers

A value-only compare-and-set cannot see an A → B → A cycle. Each snapshot row therefore stores `revision` (1 to 2^53−1),
and a binding without a row is revision 0.

`save_snapshot(ticket, snapshot, expected_revision=r)` writes only if the stored revision is still `r`; `snapshot.revision` must also equal `r`:

- If the stored revision differs, the write is rejected as `snapshot_conflict`, even when the target equals the
  current values. A retry therefore never bypasses stale-writer rejection. After an unknown outcome, the caller
  re-reads and decides.
- At the current revision, an unchanged target is a no-op and the revision stays the same.
- Any other accepted write stores revision `r + 1` and returns it.

The existing activation/revision ticket, the authoritative parent witness, fresh parent checks at publication,
publication invalidation and bounded cascade cleanup all apply unchanged.

## Service API

These are internal only; no product caller exists.

- `MatchSnapshot(event_id, match_type="unknown", match_format="unknown", self_rank=None, opponent_rank=None,
  recognition_status=None, recognition_version=None, revision=0)` is immutable input and output.
- `lookup_snapshot(event_id)` and `lookup_snapshot_many(ids)` (at most 64) are v3 only. They return parent-checked snapshots; a bound event
  without a row reads as revision 0, all unknown. v1/v2 reject as `migration_required`.
- `save_snapshot(...)` is v3 only. Invalid input is rejected before `create_missing=True` may create a fresh v3 file.
- `lookup_metadata(_many)` keeps reading categories on v1, v2 and v3.
- `save_metadata` (the #15-1 category write) stays unchanged on v2.
  - On v3 it is rejected as `snapshot_required`: it can carry no revision and cannot keep the evidence true.
  - With `create_missing=True` and no file it is also rejected, without creating one.
  - The only callers are tests; product code has none.
- `upgrade_storage()` migrates an exact v1 or v2 store to v3, and validates v3 as a no-op.

## Format and migration

v3 is `match_bindings` and `maintenance_state` (both unchanged), plus `match_snapshots`, with a foreign key to
`match_bindings(event_id) ON DELETE CASCADE`. v2's `match_metadata` table is replaced. A fresh store and a migrated
store therefore have byte-identical DDL.

Exact DDL, columns, indexes, foreign keys, the application id, the page bounds and the journal mode are checked
for each supported version (1, 2, 3). Every other version fails closed as incompatible, and nothing is repaired or
replaced.

`upgrade_storage()` runs in one `BEGIN IMMEDIATE` writer transaction:

1. Validate the source.
2. Create `match_snapshots`.
3. For v2, copy each category row as revision 1 with no rank or recognition evidence (nothing is inferred), then drop `match_metadata`.
4. Set version 3.
5. Validate the v3 schema, the carried row count and `foreign_key_check`.
6. Commit once.

A failure or deadline after any mutation rolls schema, rows and version back together. An invalid source row
aborts the whole migration.

## Rollback

- **The exact #15-1 build (v2):** its header check rejects v3 as `unsupported_format` without rewriting the file.
  Its ordinary startup and history operations continue, because the sidecar is dormant.
- **Re-upgrade:** reads v3 again, hides orphans at once and cleans them with their snapshots.
- **Public v1.2.0:** does not know the sidecar and preserves it.

None of these paths needs a hand edit. This is compatibility, not a downgrade migration.

## Gates

- **T0:** exact v1/v2/v3 schemas; valid and invalid ranks and evidence (in both code and CHECKs); legacy and `NULL`
  semantics; Single/TEAM/unknown transitions; atomic writes; A→B→A stale writers; parent, witness, stale-ticket and
  authority failure; migration faults after each real mutation plus a real-expiry control; future and corrupt stores;
  cascade cleanup. The fixture clock is used for correctness, and real time only for the deliberate expiry control.
- **T1:** stays the canonical result replay. No rank or OCR fixtures are invented.
- **T2:**
  - Against the exact #15-1 build: dormant startup, v2 creation, v3 upgrade, v3 rejection without rewrite, real core
    deletions, and re-upgrade with cleanup.
  - Optional failure isolation from real WIN/LOSE.
  - Owned process, port and temp residue checks.
  - A manual run of the public v1.2.0 rollback with v3 snapshots.
- **T3:** N/A while this contract stays dormant.
