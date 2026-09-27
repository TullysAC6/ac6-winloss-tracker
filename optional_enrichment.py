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
                              _connection, _key, _time_bits)

__all__ = ("OptionalEnrichmentService", "EnrichmentHealth", "Response", "BindingTicket")


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
class Response:
    status: str
    health: EnrichmentHealth
    bindings: tuple[_Binding, ...] = ()
    ticket: BindingTicket | None = None
    stamp: tuple[object, int] | None = None
    examined: int = 0
    deleted: int = 0
    pass_complete: bool = False


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

    def save_binding(self, ticket, *, create_missing=False):
        def work(deadline, stamp):
            if not isinstance(ticket, BindingTicket) or (ticket.activation, ticket.revision) != stamp:
                raise _Unavailable("stale")
            binding = _Binding.from_row(ticket.binding.values())
            parent = self._parents((binding.event_id,), deadline).get(binding.event_id)
            if parent is None:
                return Response("unknown", EnrichmentHealth("ready", cleanup="pending"), stamp=stamp)
            if parent != binding:
                raise _Unavailable("identity_mismatch")
            if not self._current(stamp):
                raise _Unavailable("stale")
            if create_missing is True:
                self._store.initialize(deadline)
            self._store.save(binding, deadline)
            return Response("saved", EnrichmentHealth("ready", writable=True), stamp=stamp)
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
