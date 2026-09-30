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
                              _connection, _key, _metadata, _revision, _snapshot, _time_bits)

__all__ = ("OptionalEnrichmentService", "EnrichmentHealth", "Response", "BindingTicket", "MatchMetadata",
           "MatchSnapshot")


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
        Existing v1 files require a separate explicit upgrade_storage() call.

        This is the v2 category write. A v3 store (and therefore a fresh one,
        which is always v3) rejects it as snapshot_required: use save_snapshot().
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
