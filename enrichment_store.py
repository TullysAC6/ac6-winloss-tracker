"""Private v1/v2 sidecar mechanics. Importing performs no file access.

Only optional_enrichment may use this module in product code. In particular,
these unfiltered rows are NOT a history, statistics or export API.
"""
from __future__ import annotations

import math
import os
import sqlite3
import stat
import struct
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

__all__ = ()
_APPLICATION_ID = 0x41433645
_VERSION = 2
_MAX_PAGES = 16384
_PAGE_SIZE = 4096
_BATCH = 64
_BUDGET = 0.100
_DDL = (
    """CREATE TABLE match_bindings (
    event_id TEXT COLLATE BINARY PRIMARY KEY NOT NULL
        CHECK(typeof(event_id) = 'text'
              AND length(event_id) BETWEEN 1 AND 128
              AND instr(event_id, char(0)) = 0),
    witness_version INTEGER NOT NULL
        CHECK(typeof(witness_version) = 'integer' AND witness_version = 1),
    parent_created_at_bits BLOB NOT NULL
        CHECK(typeof(parent_created_at_bits) = 'blob'
              AND length(parent_created_at_bits) = 8),
    parent_result TEXT NOT NULL
        CHECK(typeof(parent_result) = 'text'
              AND parent_result IN ('win', 'loss'))
)""",
    """CREATE TABLE maintenance_state (
    singleton INTEGER PRIMARY KEY NOT NULL
        CHECK(typeof(singleton) = 'integer' AND singleton = 1),
    after_event_id TEXT COLLATE BINARY
        CHECK(after_event_id IS NULL OR
              (typeof(after_event_id) = 'text'
               AND length(after_event_id) BETWEEN 1 AND 128
               AND instr(after_event_id, char(0)) = 0))
)""",
    """CREATE TABLE match_metadata (
    event_id TEXT COLLATE BINARY PRIMARY KEY NOT NULL
        REFERENCES match_bindings(event_id) ON DELETE CASCADE,
    match_type TEXT NOT NULL
        CHECK(typeof(match_type) = 'text' AND match_type IN ('ranked', 'custom', 'unknown')),
    match_format TEXT NOT NULL
        CHECK(typeof(match_format) = 'text' AND match_format IN ('single', 'team', 'unknown'))
)""",
)


class _Unavailable(Exception):
    def __init__(self, reason: str, state: str = "degraded"):
        self.reason, self.state = reason, state
        super().__init__(reason)


def _key(value):
    if (type(value) is not str or not 1 <= len(value) <= 128
            or not all(32 <= ord(c) <= 126 for c in value)):
        raise _Unavailable("invalid_identity")
    return value


def _time_bits(value):
    if type(value) not in (float, int) or not math.isfinite(value) or value < 0:
        raise _Unavailable("invalid_witness")
    return struct.pack(">d", float(value))


def _metadata(values):
    match_type, match_format = values
    if (type(match_type) is not str or match_type not in ("ranked", "custom", "unknown")
            or type(match_format) is not str or match_format not in ("single", "team", "unknown")):
        raise _Unavailable("invalid_metadata")
    return match_type, match_format


@dataclass(frozen=True)
class _Binding:
    event_id: str
    witness_version: int
    parent_created_at_bits: bytes
    parent_result: str

    @classmethod
    def from_row(cls, row):
        event_id, version, bits, result = row
        _key(event_id)
        if (type(version) is not int or version != 1 or type(bits) is not bytes
                or len(bits) != 8 or result not in ("win", "loss")):
            raise _Unavailable("invalid_binding")
        _time_bits(struct.unpack(">d", bits)[0])
        return cls(event_id, version, bits, result)

    def values(self):
        return (self.event_id, self.witness_version,
                self.parent_created_at_bits, self.parent_result)


class _Deadline:
    def __init__(self):
        self.end = time.monotonic() + _BUDGET

    def remaining(self):
        remaining = self.end - time.monotonic()
        if remaining <= 0:
            raise _Unavailable("deadline")
        return remaining

    def configure(self, connection):
        milliseconds = max(0, min(20, int(self.remaining() * 1000)))
        connection.execute(f"PRAGMA busy_timeout={milliseconds}")
        connection.set_progress_handler(lambda: int(time.monotonic() >= self.end), 1000)


def _paths(root: Path, basename: str):
    """Refuse aliases at the owned root/SQLite leaves; never repair them."""
    if not root.is_absolute():
        raise _Unavailable("unsafe_path")
    for path in (root, *(root / (basename + suffix)
                         for suffix in ("", "-journal", "-wal", "-shm"))):
        try:
            info = path.lstat()
        except FileNotFoundError:
            if path == root:
                raise _Unavailable("missing_root")
            continue
        if (stat.S_ISLNK(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & 0x400
                or (path != root and (not stat.S_ISREG(info.st_mode) or info.st_nlink > 1))
                or (path == root and not stat.S_ISDIR(info.st_mode))):
            raise _Unavailable("unsafe_path")


@contextmanager
def _connection(path: Path, deadline: _Deadline, *, write=False):
    _paths(path.parent, path.name)
    connection = sqlite3.connect(path.as_uri() + ("?mode=rw" if write else "?mode=ro"),
                                 uri=True, timeout=min(.020, deadline.remaining()),
                                 isolation_level=None)
    try:
        deadline.configure(connection)
        connection.execute("PRAGMA trusted_schema=OFF")
        connection.execute("PRAGMA foreign_keys=ON")
        if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            raise _Unavailable("storage_format")
        # Extension loading is disabled by sqlite3 by default and is never enabled.
        if not write:
            connection.execute("PRAGMA query_only=ON")
        yield connection
    finally:
        try:
            if connection.in_transaction:
                connection.rollback()
        finally:
            connection.close()


def _canonical(sql):
    return sql.replace("\r\n", "\n").strip()


class _Store:
    def __init__(self, root: Path):
        self.path = root / "enrichment.db"

    def _header(self):
        _paths(self.path.parent, self.path.name)
        try:
            size = self.path.stat().st_size
        except FileNotFoundError:
            raise _Unavailable("missing", "absent") from None
        if size == 0:
            raise _Unavailable("uninitialized")
        if size > _PAGE_SIZE * _MAX_PAGES:
            raise _Unavailable("size_limit")
        # Reject unknown formats before allowing SQLite native journal recovery.
        with self.path.open("rb") as stream:
            header = stream.read(100)
        if len(header) != 100 or header[:16] != b"SQLite format 3\0":
            raise _Unavailable("corrupt")
        if (int.from_bytes(header[68:72], "big") != _APPLICATION_ID
                or int.from_bytes(header[60:64], "big") not in (1, _VERSION)):
            raise _Unavailable("unsupported_format", "incompatible")
        if header[18:20] != b"\x01\x01":
            raise _Unavailable("unsupported_journal", "incompatible")

    def _validate(self, connection):
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if (connection.execute("PRAGMA application_id").fetchone()[0] != _APPLICATION_ID
                or version not in (1, _VERSION)):
            raise _Unavailable("unsupported_format", "incompatible")
        if (connection.execute("PRAGMA page_size").fetchone()[0] != _PAGE_SIZE
                or connection.execute("PRAGMA page_count").fetchone()[0] > _MAX_PAGES
                or connection.execute("PRAGMA journal_mode").fetchone()[0] != "delete"):
            raise _Unavailable("storage_format")
        expected = {
            ("table", "match_bindings", "match_bindings"): _DDL[0],
            ("table", "maintenance_state", "maintenance_state"): _DDL[1],
            ("index", "sqlite_autoindex_match_bindings_1", "match_bindings"): None,
        }
        if version == 2:
            expected[("table", "match_metadata", "match_metadata")] = _DDL[2]
            expected[("index", "sqlite_autoindex_match_metadata_1", "match_metadata")] = None
        rows = connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_schema LIMIT 6").fetchall()
        if len(rows) != len(expected):
            raise _Unavailable("schema")
        for kind, name, table, sql in rows:
            key = (kind, name, table)
            if key not in expected or ((sql is None) != (expected[key] is None)):
                raise _Unavailable("schema")
            if sql is not None and _canonical(sql) != _canonical(expected[key]):
                raise _Unavailable("schema")
        tables = [
                ("match_bindings", (("event_id", "TEXT", 1, 1), ("witness_version", "INTEGER", 1, 0),
                                    ("parent_created_at_bits", "BLOB", 1, 0), ("parent_result", "TEXT", 1, 0))),
                ("maintenance_state", (("singleton", "INTEGER", 1, 1), ("after_event_id", "TEXT", 0, 0)))]
        if version == 2:
            tables.append(("match_metadata", (("event_id", "TEXT", 1, 1), ("match_type", "TEXT", 1, 0),
                                               ("match_format", "TEXT", 1, 0))))
        for table, columns in tables:
            actual = connection.execute(f"PRAGMA table_xinfo({table})").fetchmany(5)
            if tuple((r[1], r[2], r[3], r[5]) for r in actual) != columns or any(r[4] is not None or r[6] for r in actual):
                raise _Unavailable("schema")
            foreign_keys = connection.execute(f"PRAGMA foreign_key_list({table})").fetchmany(2)
            expected_keys = ([(0, 0, "match_bindings", "event_id", "event_id", "NO ACTION", "CASCADE", "NONE")]
                             if table == "match_metadata" else [])
            if foreign_keys != expected_keys:
                raise _Unavailable("schema")
        for table in (("match_bindings", "match_metadata") if version == 2 else ("match_bindings",)):
            index = connection.execute(f"PRAGMA index_xinfo(sqlite_autoindex_{table}_1)").fetchmany(3)
            if len(index) != 2 or index[0][2:] != ("event_id", 0, "BINARY", 1):
                raise _Unavailable("schema")
        cursor = connection.execute("SELECT singleton,after_event_id FROM maintenance_state LIMIT 2").fetchall()
        if len(cursor) != 1 or type(cursor[0][0]) is not int or cursor[0][0] != 1:
            raise _Unavailable("cursor")
        if cursor[0][1] is not None:
            _key(cursor[0][1])
        return cursor[0][1]

    @contextmanager
    def open(self, deadline, *, write=False):
        self._header()
        with _connection(self.path, deadline, write=write) as connection:
            self._validate(connection)
            if write:
                if connection.execute(f"PRAGMA max_page_count={_MAX_PAGES}").fetchone()[0] != _MAX_PAGES:
                    raise _Unavailable("size_limit")
                connection.execute("PRAGMA synchronous=FULL")
                if connection.execute("PRAGMA synchronous").fetchone()[0] != 2:
                    raise _Unavailable("storage_format")
                connection.execute("BEGIN IMMEDIATE")
                # Validate again under the writer transaction before mutating anything.
                self._validate(connection)
            yield connection
            if write:
                deadline.remaining()
                connection.commit()

    def initialize(self, deadline):
        _paths(self.path.parent, self.path.name)
        if self.path.exists():
            return  # The caller must validate it; never replace an existing file.
        descriptor, temporary = tempfile.mkstemp(prefix=".enrichment-", suffix=".db", dir=self.path.parent)
        os.close(descriptor)
        staging = Path(temporary)
        try:
            with _connection(staging, deadline, write=True) as connection:
                connection.execute(f"PRAGMA page_size={_PAGE_SIZE}")
                if connection.execute(f"PRAGMA max_page_count={_MAX_PAGES}").fetchone()[0] != _MAX_PAGES:
                    raise _Unavailable("size_limit")
                if connection.execute("PRAGMA journal_mode=DELETE").fetchone()[0] != "delete":
                    raise _Unavailable("storage_format")
                connection.execute("PRAGMA synchronous=FULL")
                if connection.execute("PRAGMA synchronous").fetchone()[0] != 2:
                    raise _Unavailable("storage_format")
                connection.execute("BEGIN IMMEDIATE")
                for statement in _DDL:
                    connection.execute(statement)
                connection.execute("INSERT INTO maintenance_state VALUES (1,NULL)")
                connection.execute(f"PRAGMA application_id={_APPLICATION_ID}")
                connection.execute(f"PRAGMA user_version={_VERSION}")
                deadline.remaining()
                connection.commit()
            with _connection(staging, deadline) as connection:
                self._validate(connection)
            if any(Path(str(staging) + suffix).exists() for suffix in ("-journal", "-wal", "-shm")):
                raise _Unavailable("staging_journal")
            deadline.remaining()
            _paths(self.path.parent, self.path.name)
            try:
                if os.name == "nt":
                    os.rename(staging, self.path)  # Windows rename fails if destination exists.
                else:
                    os.link(staging, self.path)  # Atomic no-clobber publication on POSIX.
                    staging.unlink()
            except FileExistsError:
                pass  # Preserve the winner; subsequent open validates it.
        finally:
            for suffix in ("", "-journal", "-wal", "-shm"):
                Path(str(staging) + suffix).unlink(missing_ok=True)

    def read(self, ids, deadline):
        with self.open(deadline) as connection:
            rows = connection.execute(
                "SELECT event_id,witness_version,parent_created_at_bits,parent_result "
                f"FROM match_bindings WHERE event_id IN ({','.join('?' for _ in ids)})", ids).fetchmany(_BATCH + 1)
            return tuple(_Binding.from_row(row) for row in rows)

    @staticmethod
    def _save_binding(connection, binding):
        row = connection.execute("SELECT * FROM match_bindings WHERE event_id=?", (binding.event_id,)).fetchone()
        if row is not None:
            if _Binding.from_row(row) != binding:
                raise _Unavailable("identity_mismatch")
            return
        connection.execute("INSERT INTO match_bindings VALUES (?,?,?,?)", binding.values())

    def save(self, binding, deadline):
        with self.open(deadline, write=True) as connection:
            self._save_binding(connection, binding)

    def upgrade(self, deadline):
        """Explicit, atomic v1 -> v2 migration; never repair an invalid store."""
        with self.open(deadline, write=True) as connection:
            if connection.execute("PRAGMA user_version").fetchone()[0] == 1:
                connection.execute(_DDL[2])
                connection.execute("PRAGMA user_version=2")
                self._validate(connection)

    def read_metadata(self, ids, deadline):
        with self.open(deadline) as connection:
            connection.execute("BEGIN")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            fields = ("m.event_id,m.match_type,m.match_format" if version == 2 else "NULL,NULL,NULL")
            join = (" LEFT JOIN match_metadata m ON m.event_id=b.event_id" if version == 2 else "")
            rows = connection.execute(
                "SELECT b.event_id,b.witness_version,b.parent_created_at_bits,b.parent_result," + fields
                + " FROM match_bindings b" + join
                + f" WHERE b.event_id IN ({','.join('?' for _ in ids)})", ids).fetchmany(_BATCH + 1)
            result = []
            for row in rows:
                # A missing metadata row (or v1 binding) has no category evidence.
                values = ("unknown", "unknown") if row[4] is None else _metadata(row[5:])
                result.append((_Binding.from_row(row[:4]), values))
            return tuple(result)

    def save_metadata(self, binding, values, expected, deadline):
        values, expected = _metadata(values), _metadata(expected)
        with self.open(deadline, write=True) as connection:
            if connection.execute("PRAGMA user_version").fetchone()[0] != 2:
                raise _Unavailable("migration_required", "incompatible")
            self._save_binding(connection, binding)
            row = connection.execute("SELECT match_type,match_format FROM match_metadata WHERE event_id=?",
                                     (binding.event_id,)).fetchone()
            current = ("unknown", "unknown") if row is None else _metadata(row)
            if current == values:
                return  # Idempotent retry, including an already unknown binding.
            if current != expected:
                raise _Unavailable("metadata_conflict")
            connection.execute("INSERT INTO match_metadata VALUES (?,?,?) ON CONFLICT(event_id) DO UPDATE SET "
                               "match_type=excluded.match_type,match_format=excluded.match_format",
                               (binding.event_id, *values))

    def scan(self, deadline):
        with self.open(deadline) as connection:
            connection.execute("BEGIN")
            cursor = self._validate(connection)
            query = "SELECT * FROM match_bindings "
            args = () if cursor is None else (cursor,)
            if cursor is not None:
                query += "WHERE event_id > ? COLLATE BINARY "
            rows = connection.execute(query + "ORDER BY event_id COLLATE BINARY LIMIT 64", args).fetchall()
            return tuple(_Binding.from_row(row) for row in rows)

    def remove(self, bindings, deadline, *, cursor_update=False, cursor=None):
        with self.open(deadline, write=True) as connection:
            deleted = 0
            for binding in bindings:
                deadline.remaining()
                deleted += connection.execute(
                    "DELETE FROM match_bindings WHERE event_id=? AND witness_version=? "
                    "AND parent_created_at_bits=? AND parent_result=?", binding.values()).rowcount
            if cursor_update:
                connection.execute("UPDATE maintenance_state SET after_event_id=? WHERE singleton=1", (cursor,))
            return deleted
