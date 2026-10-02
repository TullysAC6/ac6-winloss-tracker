# #15-5 assisted runtime integration

Owner [adopted architecture and implementation authorization](https://github.com/TullysAC6/ac6-winloss-tracker/issues/15#issuecomment-5943012970), 2026-10-02.
Base main: `27e5f49aa7bb73b9a90f04a0465624e4e5d58008`; #59 closed;
#15-0 through #15-4 Accepted + Merged + Unreleased; stable v1.2.0.
This slice is **Acceptance PENDING, T3 PENDING, unmerged, unreleased**.

## Explicit control and capture

Preferences v3 adds `match_metadata_detection=false`. Missing/invalid preferences
mean OFF. Neither startup nor enabling starts a recognizer. Existing GUI settings
preserve the flag; this slice adds no GUI. v2 readers preserve the unknown scalar
through their one-generation reader/save; stable v1.2.0 ignores the file.

Authenticated `POST /api/metadata` on the existing 127.0.0.1 server accepts only
`enable` with a boolean, `acquire`, `status`, `cancel`, or `confirm` with fixed
`request_id`, `event_id`, and boolean `same_match=true`. Bodies are capped at
4096 bytes, read timeout 2 seconds. `control.py metadata-*` validates the local
runtime identity/port/token. Confirmation never means "latest".

An explicit acquisition discovers one visible nonminimized AC6 window, native
1920x1080 client, exact process birth and hwnd. The short-lived child uses the
existing `owned_entry + KillOnCloseJob + stop_process` pattern and the actual
interpreter handle from `python_spawn`; its parent handle includes creation time.
Before Job assignment and GO it imports no WGC, recognizer, server, or databases.
No new CreateProcess architecture is introduced. Parent death before GO is
observed by the bootstrap; after assignment kill-on-close contains descendants.

Attempt deadline: 5 seconds, native capture: at most 2 seconds/3 callback samples,
one replaceable copied header ROI. Native WGC timestamp/geometry checks are reused;
only deterministic translation of the accepted `(80,40,480,60)` client ROI is
allowed. No resize/MSS fallback/raw screenshot archive. Foreground is required
during capture; background status/confirmation still checks the exact live target.
Unsupported/stale/repeated/discontinuous/changed targets fail UNKNOWN.
The child response contains only bounded structured JSON. Graceful join then Job
closure and bounded terminate/kill verify death before publishing evidence.
Unreapable work blocks replacement. No resident OCR thread/process exists.

## Single pending and first result

One memory-only candidate has a monotonic 600-second discard TTL from capture.
New acquisition replaces an idle pending/confirmation; while a task is active a
new request returns busy. Restart, cancellation, OFF, expiry, target/window/process
change, detector capture uncertainty, Undo/reset/purge and lost notifications revoke
evidence. Native work never holds authoritative result locks.

The first accepted game result consumes pending. **Accepted DRAW consumes it even
though the existing product creates no DRAW match row.** A result during acquisition
invalidates its late completion. WIN/LOSS publishes a fixed confirmation only after
the existing history and stats writes succeed; the original committed event receipt
contains event_id, created_at and result. A memory-only receipt is added to
HistoryStore; SQL, schema and authoritative result semantics remain unchanged.
Core notification uses a nonblocking memory lock and a loss latch: contention
discards optional evidence, never waits or defers assignment to a later result.
Manual/unrelated results conservatively discard. A second result revokes confirmation.

Confirmation expires after min(pending TTL, acceptance + 60 seconds). Time and
process equality are discard guards, not match identity. The user must attest this
header belonged to that displayed fixed event with no intervening match/mode change.
The control response exposes WIN/LOSS and original persisted UTC timestamp.
Failure/duplicate confirmation cannot restore the consumed candidate.

## Checked optional persistence

Only a server-owned optional task uses the existing facade: prepare binding,
compare original immutable witness bit-for-bit, lookup current snapshot, check
publication/activation/revision/schema, save with revision CAS. The complete value is:
`ranked / single / NULL / NULL / recognized / rank-single-header.v1`.
Every existing nonempty/revisioned snapshot conflicts, including independently
sourced ranks or provenance. No overwrite, rank carry-forward or provenance relabel.
Missing sidecar is created only by explicitly confirmed work. Incompatible/unreadable
sidecars fail without migration. No direct runtime SQL writes.

Revocation is rechecked before write admission and suppresses stale completion.
Existing facade semantics still govern an already-admitted write racing deletion:
fresh checked reads hide invalid parents and bounded conditional cleanup removes
orphan bindings without deleting unrelated observations. This is not a distributed
transaction with authoritative history, and no optional failure undoes core results.
No periodic maintenance/recognition worker is introduced.

## Gates and T3

Gate evidence and exact-head SHA belong to the implementation PR. T0 covers clocks,
state/races, original receipt, CAS, failure and geometry. T1 retains all 53 canonical
cases with unchanged labels/bytes. T2 uses isolated roots/ports/processes, authenticated
actual HTTP and actual child lifecycle, parent death before/after GO, descendants,
OFF/cancel/shutdown, malformed IPC, and previous-reader rollback. Native real-game
capture and CPU/memory/game responsiveness require owner T3; synthetic process
measurements must be labelled separately, with no invented acceptance threshold.

Local 3.14.7 synthetic actual-server measurement, five OFF control cycles vs five
explicit owned-child acquisitions (structured fake header, **no WGC/game**): server
CPU total 15.625 -> 31.250 ms (delta +15.625 ms/5); final working set 45.691 ->
46.574 MiB (delta +0.883 MiB); mean request-through-cleanup/status latency
6.274 -> 249.080 ms (delta +242.806 ms). ON includes worker bootstrap and an
extra completion IPC; OFF performs no worker work. These are mechanism observations,
not a game-performance result or an acceptance threshold. Native WGC/game and child
resource impact remain owner T3 measurements.

After review GO and exact-head CI SUCCESS, follow [owner T3 procedure](ISSUE15_5_T3.md).
Do not merge until explicit owner T3 PASS. Do not infer Acceptance or publish a release.
STOP before #15-6.
