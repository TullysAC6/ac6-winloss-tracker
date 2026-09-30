# #15-3 independent Rank/Rating observations

Owner authorization and Option B: [Issue #15](https://github.com/TullysAC6/ac6-winloss-tracker/issues/15#issuecomment-5912336221). Earlier #15-1/#15-2 are explicitly [accepted](https://github.com/TullysAC6/ac6-winloss-tracker/issues/15#issuecomment-5912055608). This slice establishes dormant persistence and deletion lifecycle only. Its own acceptance is PENDING; completion evidence lives on its PR and Issues #15/#6. Public stable remains v1.2.0. STOP after this slice.

## Evidence and representation

Canonical §64/§65 require actual event time, precise observed rank, discontinuous pre-S/S modes, recognition provenance and unresolved Season. They do not establish a numeric Rating domain, scale, separators or arithmetic. Structured tests establish persistence, never OCR truth.

| Field | Stored contract |
|---|---|
| observation_id | Stable independent printable ASCII token, 1–128 characters; supplied by acquisition, never timestamp/session/match identity |
| observed_at | Caller-supplied actual observation/event Unix timestamp, finite nonnegative binary64; never substituted with ingestion time; historical acquisition must supply historical event time |
| self_rank | Accepted #15-2 bounded exact rank token or NULL; A differs from A4, no ordinal, subdivision guess or normalization |
| rating_mode | NULL / pre_s / s_rank; A4 is pre_s, S is s_rank; rank and mode cannot contradict |
| rating_value | NULL or lossless Unicode TEXT, 1–128 characters, no NUL; a value requires an explicit mode; no parsing, ordering, zero inference or arithmetic |
| recognition_status/version | Both NULL for no recorded recognizer evidence, or recognized/failed with a bounded interpretation-version token. recognized requires rank or Rating evidence; failed requires all three evidence fields NULL. Version is recognizer semantics, never app release |
| source | Required bounded ASCII provenance token (1–64); acquisition must supply its legitimate path. No speculative closed enum or confidence field |
| match context | Optional existing event_id + immutable binary64 timestamp/result witness; never matches.id or session identity |
| Season | Entirely deferred to #28-A; storable with no assignment |

No Season column is frozen: the catalog identifier, assignment provenance and transition contract are not settled. A nullable guessed identifier would commit those semantics prematurely. #28-A may explicitly migrate/add observation-owned derived assignment storage and must delete it with observation history. This choice implements unresolved Season by absence without implementing catalog/assignment logic.

Evidence is immutable. Repeating identical identity/content is an idempotent retry; different content conflicts. Retrying never recreates a removed link. Correction/import/restore semantics are not introduced.

## Ownership and deletion

Sidecar v4 has independent `observations`, observation-owned `observation_links` and singleton `observation_state`. Only observation deletion cascades to its link. Neither table refers to match_bindings or history.db by FK. Shared enrichment.db is a container, not shared lifetime.

| Action | Independent evidence | Optional context | Match-bound snapshots |
|---|---|---|---|
| Individual/date/Undo/full match deletion | Survives | Missing/mismatched parent hidden immediately on authoritative revalidation; later bounded cleanup removes link | Existing logical exclusion + cleanup rules |
| Authority unreadable | Returned | unavailable; no destructive cleanup | Existing failure isolation |
| delete_observation_history | Atomically deleted | Atomically deleted | Preserved, authoritative matches preserved |
| Future delete all Tracker user data | Separate broader operation | Separate broader operation | Must account for every artifact, not just both histories |

Each observation purge advances a durable observation-only generation in the same BEGIN IMMEDIATE transaction. Empty repeat purges succeed with zero deletions and still revoke previously prepared work. Generation exhaustion fails closed. Preparation tickets bind identity, activation and generation. A stale writer cannot insert after successful purge, including through a second service instance. Ordinary match deletion never changes this generation. File deletion/replacement/import is outside this in-file purge contract.

Queued read consumers must use `publish_observations(response, consumer)` to install current state. It holds a SQLite writer barrier shared with purge, checks generation and local stamp, and rereads evidence and match witnesses before invoking the consumer. A read queued before successful purge is rejected. A publication already completed before purge is not a new post-purge publication; future activated consumers must clear their own installed state on successful deletion. No cache, UI or background consumer exists here. The callback must be short, in-memory, synchronous and non-reentrant, with no I/O or deferred installation. Exceptions cannot undo arbitrary external callback effects.

Association cleanup scans at most 64 links, rechecks missing/mismatched parents, conditionally deletes matching links under the generation barrier and advances a binary cursor. Repeated passes finish and are idempotent. History failure preserves both evidence and links. Cleared associations are never heuristically reconstructed. Lookup accepts at most 64 explicit identities. Operations retain the existing cooperative 100ms deadline and SQLite busy bounds; there is no scheduling or worker.

## Migration and rollback

Fresh explicit initialization creates exact v4. Ordinary startup imports/accesses neither optional module nor sidecar. Existing exact v1/v2/v3 stores remain readable through their supported APIs; observation APIs require explicit upgrade. Category writes remain v2-only; whole-snapshot writes remain supported on v3/v4.

Explicit v1/v2/v3 → v4 upgrade validates source schema/header/index/FK and carried rows before mutation, then creates any missing snapshot schema, copies v2 categories as revision 1 with no invented rank/evidence, adds observation tables and generation 0, validates destination/count/FKs, and commits once. v3 snapshot values/revisions are retained. No observations or associations are backfilled. Failure, interruption or deadline expiry rolls schema/data/version back; malformed/future stores stay unavailable and unmodified. All validation runs within the same writer transaction and cooperative deadline.

Exact #15-2/v3 (`148a25c955ae3b6fd0a7624005f1465e31a13256`) rejects/preserves unsupported v4 when explicitly called. Public v1.2.0 (`c64b241c6b14b54bf7a4ac7897d3be42c8d7d9f4`) never accesses it. Both can delete authoritative matches while unaware; re-upgrade returns surviving evidence, hides invalid links and cleans context only. No manual DB/config edit. Optional failures do not block or reverse WIN/LOSE, Undo or match purges, and are never reported as successful empty/deleted storage.

## Gates and boundaries

T0 covers semantic validation, exact schemas, constraints/corruption, link witnesses, deletion lifetimes, generations/publication, bounded cleanup, migration mutations/rollback/expiry. T1 remains the existing formal corpus with no new invented recognition truth. T2 exercises real isolated current → exact-v3/public → current processes, unaware deletions, dormant startup, independent survival, link cleanup, explicit purge and stale revocation, authenticated shutdown and owned-process/port/runtime cleanup. The public installer rollback harness additionally retains observations across both documented rollback routes. T3 is expected N/A and must be independently confirmed.

history.db, runtime imports/callers, ResultGate, detector/CLEAR, DRAW, capture/OCR, workers, UI, analytics, #28-A, release/publication and cloud sync are unchanged/out of scope. #59 remains deferred; a CI job blocked only by its cumulative 20-minute limit requires STOP, without workflow modification or repeated reruns.
