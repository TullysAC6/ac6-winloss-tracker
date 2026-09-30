# #15-1: dormant match-category persistence

[Owner-authorized scope and gated-merge authority](https://github.com/TullysAC6/ac6-winloss-tracker/issues/15#issuecomment-5866557707).
Live acceptance/merge evidence belongs in the candidate PR and [Issue #6](https://github.com/TullysAC6/ac6-winloss-tracker/issues/6).
Public v1.2.0 does not contain this slice. No following slice is authorized.

> **Superseded in part by #15-2** ([contract](ISSUE15_2_RANK_EVIDENCE_CONTRACT.md)). A fresh sidecar is now v3, and
> `upgrade_storage()` migrates v1/v2 to v3. On v3, and for a fresh file, `save_metadata` is refused as
> `snapshot_required`, because a category-only write cannot keep rank or recognition evidence true. It is unchanged
> on v2, and `lookup_metadata` still reads categories on v1/v2/v3. The rest of this document records the #15-1
> contract as merged.

## Boundary

This slice implements only the complete category enums in MASTER_REQUIREMENTS §§10–15:
`match_type = ranked | custom | unknown`, `match_format = single | team | unknown`.
Any pair, including a partially unknown pair, is valid. A lack of evidence never becomes a category.
Rank, recognition status/version/provenance, observations, Season, UI, statistics and recognizers remain later work.
No result path, DRAW persistence, history schema, config/preference, capture, worker or startup integration changes.

## Service contract

Only `OptionalEnrichmentService` is supported; private storage queries are not product readers.
Construction and the default `active=False` touch no sidecar. No production caller activates this service.

- `MatchMetadata(event_id, match_type="unknown", match_format="unknown")` is immutable structured input/output.
- `lookup_metadata(event_id)` / `lookup_metadata_many(ids)` return `Response.metadata` after fresh authoritative
  parent/witness checks. The batch bound is 64. Existing bindings without metadata, including v1 bindings, return
  unknown/unknown. Missing sidecars retain the existing absent/unavailable health contract; unbound or missing
  parents produce no metadata entries. None of these states is evidence for Ranked/Single.
- `prepare_binding(event_id)` issues the existing activation/revision-stamped witness ticket.
- `save_metadata(ticket, metadata, expected=prior, create_missing=False)` atomically writes the binding and values.
  `prior` is a `MatchMetadata` for the same event, unknown/unknown when no metadata exists. A different current value
  rejects as `metadata_conflict`; if the target already equals the stored value, retry is idempotent. This CAS
  mechanism does not decide recognition confidence, correction precedence or any future observation policy.
  `create_missing=True` explicitly permits fresh v2 creation; it never upgrades or replaces an existing file.
- `upgrade_storage()` explicitly migrates an exact v1 sidecar to v2, or validates an already v2 sidecar. A missing
  or incompatible sidecar is not recreated. Metadata writes to v1 reject as `migration_required` until upgrade.

All responses retain the existing stamp/invalidation contract. Future consumers must invalidate after an
authoritative deletion commit, before acknowledging that deletion, and must not install older responses.
No historical backfill/import or recognition producer is enabled by providing these internal APIs.

## Format and migration

v2 retains the exact v1 binding/witness and maintenance tables. `match_metadata` adds one optional row per binding,
with enum/NOT NULL constraints and a foreign key to `match_bindings(event_id) ON DELETE CASCADE`.
Every owned SQLite connection enables foreign-key enforcement. Exact DDL, columns, indexes, foreign keys,
application id, format bounds and journal mode are checked. Production cooperative budget remains 0.100 s.

v1 reads and original binding operations remain supported without migration. Explicit upgrade validates v1 under
`BEGIN IMMEDIATE`, creates the new table, updates the version, validates v2 and commits. Schema/version changes
share one transaction and roll back on failure/expiry. Existing bindings and cleanup cursor survive; no metadata
is inferred or backfilled. Unknown/future/malformed stores fail closed; no silent repair/replacement is provided.

## Parent, deletion and rollback

`history.db` remains authoritative. Sidecar writes cannot create, restore, delete or count an authoritative match.
Missing/mismatched parents hide metadata immediately; late optional commits cannot resurrect results. Existing
bounded/idempotent cleanup conditionally deletes obsolete bindings, cascading only their metadata rows. Unreadable
history is never interpreted as empty; optional failure cannot block or reverse WIN/LOSE persistence/deletion.

The exact #15-0 rollback build supports v1 only and rejects v2 without rewriting it. Its ordinary startup and
history operations continue, since the sidecar is dormant. Re-upgrade can read v2 again and clean any orphaned
match-bound data. Public v1.2.0 remains unaware of the sidecar and preserves it during rollback. Neither path
requires editing databases/config by hand. This is compatibility, not a downgrade migration or secure-erasure promise.

## Gates

T0 covers enums/unknown, exact schema/constraints, explicit migration and fault/real-expiry rollback, CAS,
parent identity, stale publication, orphan cleanup and optional failure isolation. Expected-success operations
use the deterministic test clock; intentional deadline controls explicitly restore real time.
Formal T1 remains the canonical result replay; synthetic category values are storage tests, not recognition truth.
T2 uses exact old source, isolated roots/ports, bounded owned children and real core operations for v1 creation,
upgrade, v2 rejection on rollback, re-upgrade and deletion. Manual public rollback checks category values too.
T3 is N/A while this contract stays dormant: no real AC6 behavior is activated and no new worker lifecycle exists.

Merge requires independent implementation/architecture GO and green required gates/exact-head CI under the owner's
authorization. Verify exact-main and synchronize Issue #6 afterwards, then stop. #15-2 / remaining recognition,
#28-A, UI-2 and release work are not authorized.
