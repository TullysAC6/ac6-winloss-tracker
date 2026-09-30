"""Dormant optional storage facade; production startup does not import this.

An explicitly designated owner supplies an isolated/owned absolute data root.
All match-bound data is ephemeral, parent-checked and revision-stamped. Future
consumers must invalidate after core deletion commits and before acknowledgment,
and must not install older responses into caches/transports after that boundary.
No production callback, recognition worker, preference or cache is provided here.
"""
from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

from enrichment_store import (_BATCH, _Binding, _Deadline, _Store, _Unavailable,
                              _connection, _key, _metadata, _observation, _revision, _snapshot, _time_bits)

__all__ = ("OptionalEnrichmentService", "EnrichmentHealth", "Response", "BindingTicket", "MatchMetadata",
           "MatchSnapshot", "RankRatingObservation", "ObservationTicket", "ObservationView")


@dataclass(frozen=True)
class EnrichmentHealth:
    state: str
    reason: str = ""
    cleanup: str = "unknown"
    writable: bool | None = None


@dataclass(frozen=True)
class BindingTicket:
    binding: _Binding
    activation: object
    revision: int


@dataclass(frozen=True)
class MatchMetadata:
    """Explicit category values; no recognition or inference is performed."""
    event_id: str
    match_type: str = "unknown"
    match_format: str = "unknown"


@dataclass(frozen=True)
class MatchSnapshot:
    """One coherent per-match metadata snapshot (docs/ISSUE15_2_RANK_EVIDENCE_CONTRACT.md).

    Category, ranks and recognition evidence are written and read together.
    recognition_status is None (no recorded recognition evidence), "recognized"
    (every value here came from that recognizer) or "failed" (no values claimed);
    recognition_version names interpretation semantics, never an app release.
    revision is the stored revision this snapshot was read at (0: none stored).
    """
    event_id: str
    match_type: str = "unknown"
    match_format: str = "unknown"
    self_rank: str | None = None
    opponent_rank: str | None = None
    recognition_status: str | None = None
    recognition_version: str | None = None
    revision: int = 0


@dataclass(frozen=True)
class RankRatingObservation:
    """Immutable event evidence. Rating is observed text, never an implicit number.

    observed_at is caller-supplied event time in UTC epoch seconds, not ingestion
    time. No match/session owns this record. Season storage belongs to #28-A.
    """
    observation_id: str
    observed_at: float
    self_rank: str | None = None
    rating_mode: str | None = None
    rating_value: str | None = None
    recognition_status: str | None = None
    recognition_version: str | None = None
    source: str = ""

    def values(self):
        return (self.observation_id, self.observed_at, self.self_rank, self.rating_mode, self.rating_value,
                self.recognition_status, self.recognition_version, self.source)


@dataclass(frozen=True)
class ObservationTicket:
    observation_id: str
    generation: int
    activation: object
    association: _Binding | None = None


@dataclass(frozen=True)
class ObservationView:
    observation: RankRatingObservation
    association: _Binding | None = None
    association_status: str = "none"  # none | valid | invalid | unavailable


@dataclass(frozen=True)
class Response:
    status: str
    health: EnrichmentHealth
    bindings: tuple[_Binding, ...] = ()
    ticket: BindingTicket | None = None
    stamp: tuple[object, int] | None = None
    examined: int = 0
    deleted: int = 0
    pass_complete: bool = False
    metadata: tuple[MatchMetadata, ...] = ()
    snapshots: tuple[MatchSnapshot, ...] = ()
    observations: tuple[ObservationView, ...] = ()
    observation_ticket: ObservationTicket | None = None
    observation_generation: int | None = None


def _ids(values):
    if not isinstance(values, (tuple, list)) or len(values) > _BATCH:
        raise _Unavailable("invalid_arguments")
    return tuple(dict.fromkeys(_key(value) for value in values))


class OptionalEnrichmentService:
    """The only supported reader/writer. Construction is in-memory only.

    Storage operations serialize with a bounded I/O lock. The separate state
    lock never covers I/O or callbacks, so invalidation can revoke blocked work.
    """

    def __init__(self, root: str | Path, *, active=False):
        self._root = Path(root)
        self._store = _Store(self._root)
        self._operation_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._activation = object()
        self._revision = 0
        self._active = active is True

    def invalidate_history(self):
        with self._state_lock:
            self._revision += 1

    def deactivate(self):
        with self._state_lock:
            self._active = False
            self._revision += 1

    def _current(self, stamp):
        with self._state_lock:
            return self._active and stamp == (self._activation, self._revision)

    def _publish(self, stamp, response):
        # Admission and invalidation serialize here, never around a DB query.
        with self._state_lock:
            if not self._active or stamp != (self._activation, self._revision):
                return Response("rejected", EnrichmentHealth("degraded", "stale", "pending"))
            return response

    def _call(self, work, *, maintenance=False):
        with self._state_lock:
            if not self._active:
                return Response("unavailable", EnrichmentHealth("disabled"))
            stamp = (self._activation, self._revision)
        deadline = _Deadline()
        acquired = False
        try:
            acquired = self._operation_lock.acquire(timeout=deadline.remaining())
            if not acquired:
                raise _Unavailable("busy")
            if not self._current(stamp):
                raise _Unavailable("stale")
            response = work(deadline, stamp)
            deadline.remaining()
            return self._publish(stamp, response)
        except _Unavailable as error:
            state, reason = error.state, error.reason
        except sqlite3.Error as error:
            code = getattr(error, "sqlite_errorcode", 0) & 255
            reason = {sqlite3.SQLITE_BUSY: "busy", sqlite3.SQLITE_LOCKED: "busy",
                      sqlite3.SQLITE_READONLY: "write_unavailable", sqlite3.SQLITE_FULL: "write_full",
                      sqlite3.SQLITE_IOERR: "io_error", sqlite3.SQLITE_INTERRUPT: "deadline"}.get(code, "sqlite_error")
            state = "degraded"
        except OSError:
            state, reason = "degraded", "filesystem"
        except Exception:
            # Last optional boundary: never couple a library defect to core success.
            state, reason = "degraded", "internal_error"
        finally:
            if acquired:
                self._operation_lock.release()
        return Response("rejected" if reason in ("invalid_arguments", "invalid_identity", "stale") else "unavailable",
                        EnrichmentHealth(state, reason, "pending" if maintenance else "unknown",
                                         False if reason == "write_unavailable" else None))

    def _parents(self, ids, deadline):
        path = self._root / "history.db"
        try:
            with _connection(path, deadline) as connection:
                connection.execute("BEGIN")
                if connection.execute("PRAGMA user_version").fetchone()[0] != 3:
                    raise _Unavailable("authority_unavailable")
                tables = connection.execute("SELECT type FROM sqlite_schema WHERE name='matches'").fetchall()
                if tables != [("table",)]:
                    raise _Unavailable("authority_unavailable")
                columns = connection.execute("PRAGMA table_xinfo(matches)").fetchmany(20)
                types = {r[1]: (r[2], r[3], r[6]) for r in columns}
                if any(types.get(k) != (t, 1, 0) for k, t in (
                        ("event_id", "TEXT"), ("created_at", "REAL"), ("result", "TEXT"))):
                    raise _Unavailable("authority_unavailable")
                indexes = connection.execute("PRAGMA index_list(matches)").fetchmany(10)
                valid_index = False
                for index in indexes:
                    if index[2] and not index[4]:
                        # Fixed identifier quoting; never interpolate event values.
                        name = index[1].replace('"', '""')
                        fields = connection.execute(f'PRAGMA index_xinfo("{name}")').fetchmany(4)
                        keys = [r for r in fields if r[5]]
                        valid_index |= len(keys) == 1 and keys[0][2] == "event_id" and keys[0][4] == "BINARY"
                if not valid_index:
                    raise _Unavailable("authority_unavailable")
                rows = connection.execute(
                    f"SELECT event_id,created_at,result FROM matches WHERE event_id IN ({','.join('?' for _ in ids)})",
                    ids).fetchmany(_BATCH + 1)
                result = {}
                for event_id, created_at, outcome in rows:
                    if outcome not in ("win", "loss", "draw") or event_id in result:
                        raise _Unavailable("authority_unavailable")
                    # Reading an existing core enum does not create a DRAW path.
                    result[_key(event_id)] = _Binding(event_id, 1, _time_bits(created_at), outcome)
                deadline.remaining()
                return result
        except (OSError, sqlite3.Error, _Unavailable):
            raise _Unavailable("authority_unavailable") from None

    def inspect(self):
        def work(deadline, stamp):
            with self._store.open(deadline):
                pass
            return Response("ready", EnrichmentHealth("ready"), stamp=stamp)
        return self._call(work)

    def lookup(self, event_id):
        return self.lookup_many([event_id])

    def lookup_many(self, event_ids):
        def work(deadline, stamp):
            ids = _ids(event_ids)
            rows = self._store.read(ids, deadline)
            # A fresh authority snapshot at publication, not a cached snapshot.
            parents = self._parents(ids, deadline)
            visible = tuple(row for row in rows if parents.get(row.event_id) == row)
            mismatch = any(row.event_id in parents and parents[row.event_id] != row for row in rows)
            pending = len(visible) != len(rows)
            health = EnrichmentHealth("degraded" if mismatch else "ready",
                                      "identity_mismatch" if mismatch else "",
                                      "pending" if pending else "unknown")
            return Response("visible" if visible else "unknown", health, visible, stamp=stamp)
        return self._call(work)

    def prepare_binding(self, event_id):
        def work(deadline, stamp):
            parent = self._parents((_key(event_id),), deadline).get(event_id)
            if parent is None or parent.parent_result not in ("win", "loss"):
                return Response("unknown", EnrichmentHealth("ready"), stamp=stamp)
            ticket = BindingTicket(parent, *stamp)
            return Response("prepared", EnrichmentHealth("ready"), ticket=ticket, stamp=stamp)
        return self._call(work)

    def _binding_for_write(self, ticket, deadline, stamp):
        if not isinstance(ticket, BindingTicket) or (ticket.activation, ticket.revision) != stamp:
            raise _Unavailable("stale")
        binding = _Binding.from_row(ticket.binding.values())
        parent = self._parents((binding.event_id,), deadline).get(binding.event_id)
        if parent is not None and parent != binding:
            raise _Unavailable("identity_mismatch")
        if not self._current(stamp):
            raise _Unavailable("stale")
        return binding if parent is not None else None

    def save_binding(self, ticket, *, create_missing=False):
        def work(deadline, stamp):
            binding = self._binding_for_write(ticket, deadline, stamp)
            if binding is None:
                return Response("unknown", EnrichmentHealth("ready", cleanup="pending"), stamp=stamp)
            if create_missing is True:
                self._store.initialize(deadline)
            self._store.save(binding, deadline)
            return Response("saved", EnrichmentHealth("ready", writable=True), stamp=stamp)
        return self._call(work)

    def upgrade_storage(self):
        """Opt in to atomic v1/v2 -> v3 migration; reads never migrate a store."""
        def work(deadline, stamp):
            self._store.upgrade(deadline)
            return Response("ready", EnrichmentHealth("ready", writable=True), stamp=stamp)
        return self._call(work)

    def lookup_metadata(self, event_id):
        return self.lookup_metadata_many([event_id])

    def lookup_metadata_many(self, event_ids):
        """Return only parent-checked bindings; absent/unbound metadata is unknown.

        As with lookup_many(), consumers must honor stamps/invalidate_history.
        Empty metadata is not evidence for any concrete category.
        """
        def work(deadline, stamp):
            ids = _ids(event_ids)
            rows = self._store.read_metadata(ids, deadline)
            parents = self._parents(ids, deadline)
            visible = tuple(MatchMetadata(binding.event_id, *values) for binding, values in rows
                            if parents.get(binding.event_id) == binding)
            mismatch = any(binding.event_id in parents and parents[binding.event_id] != binding
                           for binding, _ in rows)
            health = EnrichmentHealth("degraded" if mismatch else "ready",
                                      "identity_mismatch" if mismatch else "",
                                      "pending" if len(visible) != len(rows) else "unknown")
            return Response("visible" if visible else "unknown", health, stamp=stamp, metadata=visible)
        return self._call(work)

    def save_metadata(self, ticket, metadata, *, expected, create_missing=False):
        """Atomically store explicit values for a checked binding.

        expected is the caller's prior MatchMetadata (unknown/unknown if absent).
        A different current value rejects with metadata_conflict; equal target
        values are idempotent retries. This does not decide recognition precedence.

        This is the v2 category write. A v3 store (and therefore a fresh one,
        which is always v3) rejects it as snapshot_required: use save_snapshot().
        A v1 store does too, because its only upgrade leads to v3.
        create_missing never creates a file this method would then refuse.
        """
        def work(deadline, stamp):
            binding = self._binding_for_write(ticket, deadline, stamp)
            if binding is None:
                return Response("unknown", EnrichmentHealth("ready", cleanup="pending"), stamp=stamp)
            if (not isinstance(metadata, MatchMetadata) or not isinstance(expected, MatchMetadata)
                    or metadata.event_id != binding.event_id or expected.event_id != binding.event_id):
                raise _Unavailable("invalid_metadata")
            values = _metadata((metadata.match_type, metadata.match_format))
            prior = _metadata((expected.match_type, expected.match_format))
            if create_missing is True and not self._store.path.exists():
                raise _Unavailable("snapshot_required", "incompatible")
            self._store.save_metadata(binding, values, prior, deadline)
            return Response("saved", EnrichmentHealth("ready", writable=True), stamp=stamp)
        return self._call(work)

    def lookup_snapshot(self, event_id):
        return self.lookup_snapshot_many([event_id])

    def lookup_snapshot_many(self, event_ids):
        """Parent-checked v3 snapshots; a bound event without one is revision 0, all unknown.

        Absence is no recorded evidence, not evidence of any category or rank, and
        not an explicit "not attempted". v1/v2 stores reject as migration_required;
        lookup_metadata_many() still reads their categories.
        """
        def work(deadline, stamp):
            ids = _ids(event_ids)
            rows = self._store.read_snapshots(ids, deadline)
            parents = self._parents(ids, deadline)
            visible = tuple(MatchSnapshot(binding.event_id, *values, revision=revision)
                            for binding, revision, values in rows if parents.get(binding.event_id) == binding)
            mismatch = any(binding.event_id in parents and parents[binding.event_id] != binding
                           for binding, _, _ in rows)
            health = EnrichmentHealth("degraded" if mismatch else "ready",
                                      "identity_mismatch" if mismatch else "",
                                      "pending" if len(visible) != len(rows) else "unknown")
            return Response("visible" if visible else "unknown", health, stamp=stamp, snapshots=visible)
        return self._call(work)

    def save_snapshot(self, ticket, snapshot, *, expected_revision, create_missing=False):
        """Atomically replace one event's whole snapshot at an expected revision.

        expected_revision (and snapshot.revision, which must equal it) is the
        revision the caller read; 0 when no snapshot exists. A different stored
        revision rejects with snapshot_conflict, even for an identical target, so
        an A -> B -> A cycle cannot be mistaken for no change. The response's one
        snapshot carries the stored revision: unchanged for a no-op, else +1.
        Invalid input is rejected before create_missing may create a fresh v3 file.
        """
        def work(deadline, stamp):
            binding = self._binding_for_write(ticket, deadline, stamp)
            if binding is None:
                return Response("unknown", EnrichmentHealth("ready", cleanup="pending"), stamp=stamp)
            if (not isinstance(snapshot, MatchSnapshot) or snapshot.event_id != binding.event_id
                    or type(snapshot.revision) is not int or snapshot.revision != expected_revision):
                raise _Unavailable("invalid_metadata")
            values = _snapshot((snapshot.match_type, snapshot.match_format, snapshot.self_rank,
                                snapshot.opponent_rank, snapshot.recognition_status, snapshot.recognition_version))
            revision = _revision(expected_revision)
            if create_missing is True:
                self._store.initialize(deadline)
            stored_revision, stored = self._store.save_snapshot(binding, values, revision, deadline)
            return Response("saved", EnrichmentHealth("ready", writable=True), stamp=stamp,
                            snapshots=(MatchSnapshot(binding.event_id, *stored, revision=stored_revision),))
        return self._call(work)

    def _cleanup(self, ids):
        def work(deadline, stamp):
            selected = None if ids is None else _ids(ids)
            rows = self._store.scan(deadline) if selected is None else self._store.read(selected, deadline)
            keys = tuple(row.event_id for row in rows)
            parents = self._parents(keys, deadline)
            candidates = tuple(row for row in rows if parents.get(row.event_id) != row)
            fresh = self._parents(tuple(row.event_id for row in candidates), deadline)
            candidates = tuple(row for row in candidates if fresh.get(row.event_id) != row)
            if not self._current(stamp):
                raise _Unavailable("stale")
            complete = selected is None and len(rows) < _BATCH
            cursor = None if complete or not rows else rows[-1].event_id
            deleted = self._store.remove(candidates, deadline, cursor_update=selected is None, cursor=cursor)
            mismatch = any(row.event_id in fresh for row in candidates)
            health = EnrichmentHealth("degraded" if mismatch else "ready",
                                      "identity_mismatch" if mismatch else "",
                                      "complete-for-observed-pass" if complete else "pending", True)
            return Response("cleaned", health, stamp=stamp, examined=len(rows), deleted=deleted, pass_complete=complete)
        return self._call(work, maintenance=True)

    def cleanup_step(self):
        return self._cleanup(None)

    def cleanup_deleted(self, event_ids):
        return self._cleanup(event_ids)

    def prepare_observation(self, observation_id, *, match_event_id=None, create_missing=False):
        """Explicitly prepare independent work; optional context never becomes ownership."""
        def work(deadline, stamp):
            identity = _key(observation_id)
            event_id = None if match_event_id is None else _key(match_event_id)
            if create_missing is True:
                self._store.initialize(deadline)
            generation = self._store.observation_generation(deadline)
            association = None
            if event_id is not None:
                try:
                    association = self._parents((event_id,), deadline).get(event_id)
                    if association is not None and association.parent_result not in ('win', 'loss'):
                        association = None
                except _Unavailable as error:
                    if error.reason != 'authority_unavailable':
                        raise
            return Response('prepared', EnrichmentHealth('ready'), stamp=stamp,
                observation_ticket=ObservationTicket(identity, generation, self._activation, association),
                observation_generation=generation)
        return self._call(work)

    def _observation_views(self, rows, deadline):
        keys = tuple(dict.fromkeys(binding.event_id for _, binding in rows if binding is not None))
        parents, unavailable = {}, False
        if keys:
            try:
                parents = self._parents(keys, deadline)
            except _Unavailable as error:
                if error.reason != 'authority_unavailable':
                    raise
                unavailable = True
        return tuple(ObservationView(RankRatingObservation(*values),
            binding if binding is not None and parents.get(binding.event_id) == binding else None,
            'none' if binding is None else 'unavailable' if unavailable
            else 'valid' if parents.get(binding.event_id) == binding else 'invalid') for values, binding in rows)

    def lookup_observation(self, observation_id):
        return self.lookup_observations_many([observation_id])

    def lookup_observations_many(self, observation_ids):
        """Return independent evidence even with missing/unreadable match context.

        Deferred consumers must install through publish_observations(), not cache
        an unchecked old Response: generation is durable across service instances.
        """
        def work(deadline, stamp):
            ids = _ids(observation_ids)
            generation, rows = self._store.read_observations(ids, deadline)
            views = self._observation_views(rows, deadline)
            degraded = any(view.association_status in ('invalid', 'unavailable') for view in views)
            return Response('visible' if views else 'unknown',
                EnrichmentHealth('degraded' if degraded else 'ready', cleanup='pending' if degraded else 'unknown'),
                stamp=stamp, observations=views, observation_generation=generation)
        return self._call(work)

    def save_observation(self, ticket, observation):
        """Insert immutable evidence, or retry an identical identity/content without relinking."""
        def work(deadline, stamp):
            if (type(ticket) is not ObservationTicket or ticket.activation is not self._activation
                    or type(observation) is not RankRatingObservation or ticket.observation_id != observation.observation_id):
                raise _Unavailable('invalid_arguments')
            values = _observation(observation.values())
            generation = _revision(ticket.generation)
            association = ticket.association
            if association is not None:
                if type(association) is not _Binding:
                    raise _Unavailable('invalid_arguments')
                _Binding.from_row(association.values())
                try:
                    if self._parents((association.event_id,), deadline).get(association.event_id) != association:
                        association = None
                except _Unavailable as error:
                    if error.reason != 'authority_unavailable':
                        raise
                    association = None
            self._store.save_observation(values, generation, association, deadline)
            return Response('saved', EnrichmentHealth('ready', writable=True), stamp=stamp,
                            observation_generation=generation)
        return self._call(work)

    def delete_observation_history(self):
        """Explicit internal operation. Future UI must obtain informed confirmation.

        Atomic deletion + durable generation increment. No match/snapshot deletion,
        no secure erasure, no successful-empty response for missing/invalid stores.
        """
        def work(deadline, stamp):
            generation, removed = self._store.purge_observations(deadline)
            return Response('deleted', EnrichmentHealth('ready', writable=True), stamp=stamp,
                            deleted=removed, observation_generation=generation)
        return self._call(work, maintenance=True)

    def publish_observations(self, response, consumer):
        """Install a read through a generation barrier, serialized with observation purge.

        consumer receives freshly validated views while the SQLite writer transaction
        and service state lock are held. It must be bounded, in-memory and non-reentrant;
        no I/O, callbacks into this service, or deferred installation. An exception is
        isolated but cannot undo arbitrary external consumer effects. No product caller
        is activated here. A purge failure never falsely reports successful deletion.
        """
        def work(deadline, stamp):
            if (type(response) is not Response or response.stamp != stamp
                    or response.status not in ('visible', 'unknown') or not callable(consumer)
                    or type(response.observation_generation) is not int):
                raise _Unavailable('stale')
            ids = _ids([view.observation.observation_id for view in response.observations])
            with self._store.observation_connection(deadline, write=True) as connection:
                generation = self._store._observation_state(connection)[0]
                if generation != response.observation_generation:
                    raise _Unavailable('stale_observation_generation')
                views = self._observation_views(self._store._observation_rows(connection, ids), deadline)
                deadline.remaining()
                with self._state_lock:
                    if not self._active or stamp != (self._activation, self._revision):
                        raise _Unavailable('stale')
                    consumer(views)
            return Response('published', EnrichmentHealth('ready'), stamp=stamp, observation_generation=generation)
        return self._call(work)

    def cleanup_observation_associations(self):
        def work(deadline, stamp):
            generation, rows = self._store.scan_observation_links(deadline)
            parents = self._parents(tuple(dict.fromkeys(binding.event_id for _, binding in rows)), deadline) if rows else {}
            candidates = tuple((identity, binding) for identity, binding in rows if parents.get(binding.event_id) != binding)
            fresh = self._parents(tuple(dict.fromkeys(binding.event_id for _, binding in candidates)), deadline) if candidates else {}
            candidates = tuple((identity, binding) for identity, binding in candidates if fresh.get(binding.event_id) != binding)
            if not self._current(stamp):
                raise _Unavailable('stale')
            complete = len(rows) < _BATCH
            cursor = None if complete or not rows else rows[-1][0]
            removed = self._store.remove_observation_links(candidates, generation, cursor, deadline)
            return Response('cleaned', EnrichmentHealth('ready', cleanup='complete-for-observed-pass' if complete else 'pending'),
                stamp=stamp, examined=len(rows), deleted=removed, pass_complete=complete, observation_generation=generation)
        return self._call(work, maintenance=True)
