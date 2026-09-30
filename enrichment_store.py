"""Private v1/v2/v3/v4 sidecar mechanics. Importing performs no file access.

Only optional_enrichment may use this module in product code. In particular,
these unfiltered rows are NOT a history, statistics or export API.
"""
from __future__ import annotations

import math
import os
import re
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
_VERSION = 4
_SUPPORTED_VERSIONS = (1, 2, 3, 4)
_MAX_PAGES = 16384
_PAGE_SIZE = 4096
_BATCH = 64
_BUDGET = 0.100
_MAX_REVISION = 9007199254740991
_BINDINGS_DDL, _MAINTENANCE_DDL, _METADATA_DDL = (
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
# #15-2: one coherent per-match snapshot (docs/ISSUE15_2_RANK_EVIDENCE_CONTRACT.md).
# Rank tokens: the established UNRANKED, S and A/A1-A4, or an as yet unestablished
# pre-S band letter with an optional 1-9 subdivision. No ordinal, no normalization.
# instr(..., char(0)) guards the text rules: SQLite length() and GLOB stop at NUL.
_SNAPSHOTS_DDL = """CREATE TABLE match_snapshots (
    event_id TEXT COLLATE BINARY PRIMARY KEY NOT NULL
        REFERENCES match_bindings(event_id) ON DELETE CASCADE,
    revision INTEGER NOT NULL
        CHECK(typeof(revision) = 'integer' AND revision BETWEEN 1 AND 9007199254740991),
    match_type TEXT NOT NULL
        CHECK(typeof(match_type) = 'text' AND match_type IN ('ranked', 'custom', 'unknown')),
    match_format TEXT NOT NULL
        CHECK(typeof(match_format) = 'text' AND match_format IN ('single', 'team', 'unknown')),
    self_rank TEXT
        CHECK(self_rank IS NULL OR (typeof(self_rank) = 'text' AND instr(self_rank, char(0)) = 0 AND (
              self_rank IN ('UNRANKED', 'S', 'A', 'A1', 'A2', 'A3', 'A4')
              OR (length(self_rank) = 1 AND self_rank GLOB '[B-RT-Z]')
              OR (length(self_rank) = 2 AND self_rank GLOB '[B-RT-Z][1-9]')))),
    opponent_rank TEXT
        CHECK(opponent_rank IS NULL OR (typeof(opponent_rank) = 'text' AND instr(opponent_rank, char(0)) = 0 AND (
              opponent_rank IN ('UNRANKED', 'S', 'A', 'A1', 'A2', 'A3', 'A4')
              OR (length(opponent_rank) = 1 AND opponent_rank GLOB '[B-RT-Z]')
              OR (length(opponent_rank) = 2 AND opponent_rank GLOB '[B-RT-Z][1-9]')))),
    recognition_status TEXT
        CHECK(recognition_status IS NULL OR (typeof(recognition_status) = 'text'
              AND recognition_status IN ('recognized', 'failed'))),
    recognition_version TEXT
        CHECK(recognition_version IS NULL OR (typeof(recognition_version) = 'text'
              AND instr(recognition_version, char(0)) = 0 AND length(recognition_version) BETWEEN 1 AND 64
              AND recognition_version GLOB '[A-Za-z0-9]*'
              AND NOT recognition_version GLOB '*[^A-Za-z0-9._:-]*')),
    CHECK(opponent_rank IS NULL OR match_format = 'single'),
    CHECK((recognition_status IS NULL) = (recognition_version IS NULL)),
    CHECK(recognition_status IS NOT 'failed' OR (match_type = 'unknown' AND match_format = 'unknown'
          AND self_rank IS NULL AND opponent_rank IS NULL)),
    CHECK(recognition_status IS NOT 'recognized' OR match_type <> 'unknown' OR match_format <> 'unknown'
          OR self_rank IS NOT NULL OR opponent_rank IS NOT NULL)
)"""
# The schema a fresh store is created with: always the current version.
_OBSERVATIONS_DDL = """CREATE TABLE observations (
    observation_id TEXT COLLATE BINARY PRIMARY KEY NOT NULL
        CHECK(typeof(observation_id) = 'text' AND length(observation_id) BETWEEN 1 AND 128
              AND instr(observation_id, char(0)) = 0),
    observed_at REAL NOT NULL
        CHECK(typeof(observed_at) = 'real' AND observed_at >= 0 AND observed_at <= 1.7976931348623157e308),
    self_rank TEXT
        CHECK(self_rank IS NULL OR (typeof(self_rank) = 'text' AND instr(self_rank, char(0)) = 0 AND (
              self_rank IN ('UNRANKED', 'S', 'A', 'A1', 'A2', 'A3', 'A4')
              OR (length(self_rank) = 1 AND self_rank GLOB '[B-RT-Z]')
              OR (length(self_rank) = 2 AND self_rank GLOB '[B-RT-Z][1-9]')))),
    rating_mode TEXT CHECK(rating_mode IS NULL OR rating_mode IN ('pre_s', 's_rank')),
    rating_value TEXT
        CHECK(rating_value IS NULL OR (typeof(rating_value) = 'text'
              AND length(rating_value) BETWEEN 1 AND 128 AND instr(rating_value, char(0)) = 0)),
    recognition_status TEXT CHECK(recognition_status IS NULL OR recognition_status IN ('recognized', 'failed')),
    recognition_version TEXT
        CHECK(recognition_version IS NULL OR (typeof(recognition_version) = 'text'
              AND instr(recognition_version, char(0)) = 0 AND length(recognition_version) BETWEEN 1 AND 64
              AND recognition_version GLOB '[A-Za-z0-9]*'
              AND NOT recognition_version GLOB '*[^A-Za-z0-9._:-]*')),
    source TEXT NOT NULL
        CHECK(typeof(source) = 'text' AND instr(source, char(0)) = 0 AND length(source) BETWEEN 1 AND 64
              AND source GLOB '[A-Za-z0-9]*' AND NOT source GLOB '*[^A-Za-z0-9._:-]*'),
    CHECK(rating_value IS NULL OR rating_mode IS NOT NULL),
    CHECK(rating_mode IS NOT 'pre_s' OR self_rank IS NOT 'S'),
    CHECK(rating_mode IS NOT 's_rank' OR self_rank IS NULL OR self_rank = 'S'),
    CHECK((recognition_status IS NULL) = (recognition_version IS NULL)),
    CHECK(recognition_status IS NOT 'failed' OR (self_rank IS NULL AND rating_mode IS NULL AND rating_value IS NULL)),
    CHECK(recognition_status IS NOT 'recognized' OR self_rank IS NOT NULL OR rating_value IS NOT NULL)
)"""
_OBSERVATION_LINKS_DDL = """CREATE TABLE observation_links (
    observation_id TEXT COLLATE BINARY PRIMARY KEY NOT NULL
        REFERENCES observations(observation_id) ON DELETE CASCADE,
    event_id TEXT COLLATE BINARY NOT NULL
        CHECK(typeof(event_id) = 'text' AND length(event_id) BETWEEN 1 AND 128 AND instr(event_id, char(0)) = 0),
    witness_version INTEGER NOT NULL CHECK(typeof(witness_version) = 'integer' AND witness_version = 1),
    parent_created_at_bits BLOB NOT NULL
        CHECK(typeof(parent_created_at_bits) = 'blob' AND length(parent_created_at_bits) = 8),
    parent_result TEXT NOT NULL CHECK(typeof(parent_result) = 'text' AND parent_result IN ('win', 'loss'))
)"""
_OBSERVATION_STATE_DDL = """CREATE TABLE observation_state (
    singleton INTEGER PRIMARY KEY NOT NULL CHECK(typeof(singleton) = 'integer' AND singleton = 1),
    generation INTEGER NOT NULL
        CHECK(typeof(generation) = 'integer' AND generation BETWEEN 0 AND 9007199254740991),
    after_observation_id TEXT COLLATE BINARY
        CHECK(after_observation_id IS NULL OR (typeof(after_observation_id) = 'text'
              AND length(after_observation_id) BETWEEN 1 AND 128 AND instr(after_observation_id, char(0)) = 0))
)"""
_OBSERVATION_DDL = (_OBSERVATIONS_DDL, _OBSERVATION_LINKS_DDL, _OBSERVATION_STATE_DDL)
_DDL = (_BINDINGS_DDL, _MAINTENANCE_DDL, _SNAPSHOTS_DDL, *_OBSERVATION_DDL)
_TABLES = {
    "match_bindings": (_BINDINGS_DDL, (("event_id", "TEXT", 1, 1), ("witness_version", "INTEGER", 1, 0),
                                       ("parent_created_at_bits", "BLOB", 1, 0), ("parent_result", "TEXT", 1, 0))),
    "maintenance_state": (_MAINTENANCE_DDL, (("singleton", "INTEGER", 1, 1), ("after_event_id", "TEXT", 0, 0))),
    "match_metadata": (_METADATA_DDL, (("event_id", "TEXT", 1, 1), ("match_type", "TEXT", 1, 0),
                                       ("match_format", "TEXT", 1, 0))),
    "match_snapshots": (_SNAPSHOTS_DDL, (("event_id", "TEXT", 1, 1), ("revision", "INTEGER", 1, 0),
                                         ("match_type", "TEXT", 1, 0), ("match_format", "TEXT", 1, 0),
                                         ("self_rank", "TEXT", 0, 0), ("opponent_rank", "TEXT", 0, 0),
                                         ("recognition_status", "TEXT", 0, 0),
                                         ("recognition_version", "TEXT", 0, 0))),
}
_TABLES.update({
    'observations': (_OBSERVATIONS_DDL, (('observation_id', 'TEXT', 1, 1), ('observed_at', 'REAL', 1, 0),
        ('self_rank', 'TEXT', 0, 0), ('rating_mode', 'TEXT', 0, 0), ('rating_value', 'TEXT', 0, 0),
        ('recognition_status', 'TEXT', 0, 0), ('recognition_version', 'TEXT', 0, 0), ('source', 'TEXT', 1, 0))),
    'observation_links': (_OBSERVATION_LINKS_DDL, (('observation_id', 'TEXT', 1, 1), ('event_id', 'TEXT', 1, 0),
        ('witness_version', 'INTEGER', 1, 0), ('parent_created_at_bits', 'BLOB', 1, 0), ('parent_result', 'TEXT', 1, 0))),
    'observation_state': (_OBSERVATION_STATE_DDL, (('singleton', 'INTEGER', 1, 1), ('generation', 'INTEGER', 1, 0),
        ('after_observation_id', 'TEXT', 0, 0))),
})
# Exactly these tables per supported version; v3 replaces match_metadata.
_SCHEMAS = {1: ("match_bindings", "maintenance_state"),
            2: ("match_bindings", "maintenance_state", "match_metadata"),
            3: ("match_bindings", "maintenance_state", "match_snapshots"),
            4: ("match_bindings", "maintenance_state", "match_snapshots", "observations", "observation_links", "observation_state")}
_CHILD_TABLES = ("match_metadata", "match_snapshots")
_TEXT_KEY_TABLES = ("match_bindings", "match_metadata", "match_snapshots", "observations", "observation_links")
_SNAPSHOT_COLUMNS = "match_type,match_format,self_rank,opponent_rank,recognition_status,recognition_version"
_RANK_EXACT = frozenset(("UNRANKED", "S", "A", "A1", "A2", "A3", "A4"))
_RANK_BANDS = frozenset("BCDEFGHIJKLMNOPQRTUVWXYZ")
_RECOGNITION_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}")
_DEFAULT_SNAPSHOT = ("unknown", "unknown", None, None, None, None)


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


def _rank(value):
    """None, or a canonical rank token exactly as observed; never normalized or ordered."""
    if value is None:
        return None
    if type(value) is str and (value in _RANK_EXACT or (
            value[:1] in _RANK_BANDS and (len(value) == 1 or (len(value) == 2 and value[1] in "123456789")))):
        return value
    raise _Unavailable("invalid_metadata")


def _snapshot(values):
    """Validate one coherent snapshot; the same rules as the match_snapshots CHECKs."""
    match_type, match_format, self_rank, opponent_rank, status, version = values
    match_type, match_format = _metadata((match_type, match_format))
    self_rank, opponent_rank = _rank(self_rank), _rank(opponent_rank)
    if opponent_rank is not None and match_format != "single":
        raise _Unavailable("invalid_metadata")  # TEAM / unknown format carries no opponent rank
    if status is None:
        if version is not None:
            raise _Unavailable("invalid_metadata")
    else:
        if (type(status) is not str or status not in ("recognized", "failed") or type(version) is not str
                or _RECOGNITION_VERSION.fullmatch(version) is None):
            raise _Unavailable("invalid_metadata")
        facts = (match_type != "unknown" or match_format != "unknown"
                 or self_rank is not None or opponent_rank is not None)
        # A failed read claims no facts; a recognized snapshot claims at least one.
        if facts != (status == "recognized"):
            raise _Unavailable("invalid_metadata")
    return match_type, match_format, self_rank, opponent_rank, status, version


def _revision(value, *, stored=False):
    low = 1 if stored else 0
    if type(value) is not int or not low <= value <= _MAX_REVISION:
        raise _Unavailable("invalid_metadata")
    return value


def _observation(values):
    """Lossless event evidence; no numeric Rating interpretation or Season inference."""
    identity, observed_at, rank, mode, value, status, version, source = values
    _key(identity)
    _time_bits(observed_at)
    rank = _rank(rank)
    if mode is not None and (type(mode) is not str or mode not in ('pre_s', 's_rank')):
        raise _Unavailable('invalid_observation')
    if value is not None and (type(value) is not str or not 1 <= len(value) <= 128 or '\0' in value or mode is None):
        raise _Unavailable('invalid_observation')
    if (mode == 'pre_s' and rank == 'S') or (mode == 's_rank' and rank not in (None, 'S')):
        raise _Unavailable('invalid_observation')
    if type(source) is not str or _RECOGNITION_VERSION.fullmatch(source) is None:
        raise _Unavailable('invalid_observation')
    if status is None:
        if version is not None:
            raise _Unavailable('invalid_observation')
    elif (type(status) is not str or status not in ('recognized', 'failed') or type(version) is not str
          or _RECOGNITION_VERSION.fullmatch(version) is None
          or (status == 'recognized' and rank is None and value is None)
          or (status == 'failed' and any(x is not None for x in (rank, mode, value)))):
        raise _Unavailable('invalid_observation')
    return identity, float(observed_at), rank, mode, value, status, version, source


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
                or int.from_bytes(header[60:64], "big") not in _SUPPORTED_VERSIONS):
            raise _Unavailable("unsupported_format", "incompatible")
        if header[18:20] != b"\x01\x01":
            raise _Unavailable("unsupported_journal", "incompatible")

    def _validate(self, connection):
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if (connection.execute("PRAGMA application_id").fetchone()[0] != _APPLICATION_ID
                or version not in _SUPPORTED_VERSIONS):
            raise _Unavailable("unsupported_format", "incompatible")
        if (connection.execute("PRAGMA page_size").fetchone()[0] != _PAGE_SIZE
                or connection.execute("PRAGMA page_count").fetchone()[0] > _MAX_PAGES
                or connection.execute("PRAGMA journal_mode").fetchone()[0] != "delete"):
            raise _Unavailable("storage_format")
        tables = _SCHEMAS[version]
        expected = {}
        for table in tables:
            expected[("table", table, table)] = _TABLES[table][0]
            if table in _TEXT_KEY_TABLES:
                expected[("index", f"sqlite_autoindex_{table}_1", table)] = None
        rows = connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_schema LIMIT ?",
                                  (len(expected) + 1,)).fetchall()
        if len(rows) != len(expected):
            raise _Unavailable("schema")
        for kind, name, table, sql in rows:
            key = (kind, name, table)
            if key not in expected or ((sql is None) != (expected[key] is None)):
                raise _Unavailable("schema")
            if sql is not None and _canonical(sql) != _canonical(expected[key]):
                raise _Unavailable("schema")
        for table in tables:
            columns = _TABLES[table][1]
            actual = connection.execute(f"PRAGMA table_xinfo({table})").fetchmany(len(columns) + 1)
            if tuple((r[1], r[2], r[3], r[5]) for r in actual) != columns or any(r[4] is not None or r[6] for r in actual):
                raise _Unavailable("schema")
            foreign_keys = connection.execute(f"PRAGMA foreign_key_list({table})").fetchmany(2)
            expected_keys = ([(0, 0, "match_bindings", "event_id", "event_id", "NO ACTION", "CASCADE", "NONE")]
                             if table in _CHILD_TABLES else [])
            if table == 'observation_links':
                expected_keys = [(0, 0, 'observations', 'observation_id', 'observation_id', 'NO ACTION', 'CASCADE', 'NONE')]
            if foreign_keys != expected_keys:
                raise _Unavailable("schema")
        for table in tables:
            if table in _TEXT_KEY_TABLES:
                index = connection.execute(f"PRAGMA index_xinfo(sqlite_autoindex_{table}_1)").fetchmany(3)
                key = 'observation_id' if table in ('observations', 'observation_links') else 'event_id'
                if len(index) != 2 or index[0][2:] != (key, 0, "BINARY", 1):
                    raise _Unavailable("schema")
        cursor = connection.execute("SELECT singleton,after_event_id FROM maintenance_state LIMIT 2").fetchall()
        if len(cursor) != 1 or type(cursor[0][0]) is not int or cursor[0][0] != 1:
            raise _Unavailable("cursor")
        if cursor[0][1] is not None:
            _key(cursor[0][1])
        if version == 4:
            self._observation_state(connection)
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
                connection.execute("INSERT INTO observation_state VALUES (1,0,NULL)")
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
        """Explicit, atomic v1/v2/v3 -> v4 migration; never repair an invalid store.

        One writer transaction: the source was validated by open() under BEGIN
        IMMEDIATE; v2 category rows are copied as revision 1 with no rank or
        recognition evidence (nothing is inferred); the destination is validated
        before the single commit. Any failure rolls schema, rows and version back.
        """
        with self.open(deadline, write=True) as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version == _VERSION:
                return
            # Validate carried data before the first mutation, including corruption
            # introduced by writers that bypassed CHECK/FK constraints. Stream in
            # bounded batches under the same cooperative operation deadline.
            tables = [('match_bindings', lambda row: _Binding.from_row(row))]
            if version == 2:
                tables.append(('match_metadata', lambda row: (_key(row[0]), _metadata(row[1:]))))
            if version == 3:
                tables.append(('match_snapshots', lambda row: (_key(row[0]), _revision(row[1], stored=True), _snapshot(row[2:]))))
            for table, validate in tables:
                cursor = connection.execute('SELECT * FROM ' + table)
                while True:
                    deadline.remaining()
                    batch = cursor.fetchmany(_BATCH)
                    if not batch:
                        break
                    for row in batch:
                        validate(row)
            if connection.execute('PRAGMA foreign_key_check').fetchmany(1):
                raise _Unavailable('migration_verification')
            carried = connection.execute('SELECT count(*) FROM match_snapshots').fetchone()[0] if version == 3 else 0
            if version < 3:
                connection.execute(_SNAPSHOTS_DDL)
                if version == 2:
                    carried = connection.execute("SELECT count(*) FROM match_metadata").fetchone()[0]
                    connection.execute("INSERT INTO match_snapshots (event_id,revision,match_type,match_format) "
                                       "SELECT event_id,1,match_type,match_format FROM match_metadata")
                    connection.execute("DROP TABLE match_metadata")
            for ddl in _OBSERVATION_DDL:
                connection.execute(ddl)
            connection.execute('INSERT INTO observation_state VALUES (1,0,NULL)')
            connection.execute(f"PRAGMA user_version={_VERSION}")
            self._validate(connection)
            if (connection.execute("SELECT count(*) FROM match_snapshots").fetchone()[0] != carried
                    or connection.execute("PRAGMA foreign_key_check").fetchmany(1)):
                raise _Unavailable("migration_verification")

    def read_metadata(self, ids, deadline):
        with self.open(deadline) as connection:
            connection.execute("BEGIN")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version >= 3:
                # The category view of whole v3 snapshots: a corrupt row fails closed here too.
                return tuple((binding, values[:2]) for binding, _, values in self._snapshot_rows(connection, ids))
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
        """The #15-1 category write, unchanged on v2 only.

        v3 keeps category, ranks and recognition evidence as one revisioned
        snapshot; a category-only write could neither carry a revision nor keep
        that evidence true, so it is refused there, never silently merged. A v1
        store gets the same answer: its only upgrade leads to v3.
        """
        values, expected = _metadata(values), _metadata(expected)
        with self.open(deadline, write=True) as connection:
            if connection.execute("PRAGMA user_version").fetchone()[0] != 2:
                raise _Unavailable("snapshot_required", "incompatible")
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

    @staticmethod
    def _snapshot_rows(connection, ids):
        rows = connection.execute(
            "SELECT b.event_id,b.witness_version,b.parent_created_at_bits,b.parent_result,s.revision,"
            + ",".join("s." + column for column in _SNAPSHOT_COLUMNS.split(","))
            + " FROM match_bindings b LEFT JOIN match_snapshots s ON s.event_id=b.event_id"
            + f" WHERE b.event_id IN ({','.join('?' for _ in ids)})", ids).fetchmany(_BATCH + 1)
        result = []
        for row in rows:
            if row[4] is None:
                result.append((_Binding.from_row(row[:4]), 0, _DEFAULT_SNAPSHOT))
            else:
                result.append((_Binding.from_row(row[:4]), _revision(row[4], stored=True), _snapshot(row[5:])))
        return tuple(result)

    def read_snapshots(self, ids, deadline):
        """(binding, revision, values) per binding; revision 0 means no snapshot row."""
        with self.open(deadline) as connection:
            connection.execute("BEGIN")
            if connection.execute("PRAGMA user_version").fetchone()[0] < 3:
                raise _Unavailable("migration_required", "incompatible")
            return self._snapshot_rows(connection, ids)

    def save_snapshot(self, binding, values, expected_revision, deadline):
        """Full-snapshot compare-and-set on the stored revision.

        A stale expected revision always rejects, even when its target equals the
        current values (an A -> B -> A cycle is a different revision). Only a write
        at the current revision whose target equals the current snapshot is a
        no-op; every other accepted write advances the revision by one.
        """
        values, expected_revision = _snapshot(values), _revision(expected_revision)
        with self.open(deadline, write=True) as connection:
            if connection.execute("PRAGMA user_version").fetchone()[0] < 3:
                raise _Unavailable("migration_required", "incompatible")
            self._save_binding(connection, binding)
            row = connection.execute(f"SELECT revision,{_SNAPSHOT_COLUMNS} FROM match_snapshots WHERE event_id=?",
                                     (binding.event_id,)).fetchone()
            revision, current = (0, _DEFAULT_SNAPSHOT) if row is None else (
                _revision(row[0], stored=True), _snapshot(row[1:]))
            if revision != expected_revision:
                raise _Unavailable("snapshot_conflict")
            if current == values:
                return revision, current
            if revision >= _MAX_REVISION:
                raise _Unavailable("revision_exhausted")
            if row is None:
                connection.execute("INSERT INTO match_snapshots VALUES (?,?,?,?,?,?,?,?)",
                                   (binding.event_id, 1, *values))
            else:
                changed = connection.execute(
                    "UPDATE match_snapshots SET revision=?," + ",".join(f"{c}=?" for c in _SNAPSHOT_COLUMNS.split(","))
                    + " WHERE event_id=? AND revision=?", (revision + 1, *values, binding.event_id, revision)).rowcount
                if changed != 1:
                    raise _Unavailable("snapshot_conflict")
            return revision + 1, values

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

    @staticmethod
    def _observation_state(connection):
        rows = connection.execute('SELECT singleton,generation,after_observation_id FROM observation_state LIMIT 2').fetchall()
        if len(rows) != 1 or type(rows[0][0]) is not int or rows[0][0] != 1:
            raise _Unavailable('observation_state')
        _revision(rows[0][1])
        if rows[0][2] is not None:
            _key(rows[0][2])
        return rows[0][1], rows[0][2]

    @contextmanager
    def observation_connection(self, deadline, *, write=False):
        with self.open(deadline, write=write) as connection:
            if connection.execute('PRAGMA user_version').fetchone()[0] != 4:
                raise _Unavailable('migration_required', 'incompatible')
            if not write:
                connection.execute('BEGIN')
            yield connection

    def observation_generation(self, deadline):
        with self.observation_connection(deadline) as connection:
            return self._observation_state(connection)[0]

    @staticmethod
    def _observation_rows(connection, ids):
        rows = connection.execute('SELECT o.*,l.event_id,l.witness_version,l.parent_created_at_bits,l.parent_result '
            'FROM observations o LEFT JOIN observation_links l ON l.observation_id=o.observation_id '
            f"WHERE o.observation_id IN ({','.join('?' for _ in ids)}) ORDER BY o.observation_id COLLATE BINARY",
            ids).fetchmany(_BATCH + 1)
        return tuple((_observation(row[:8]), None if row[8] is None else _Binding.from_row(row[8:])) for row in rows)

    def read_observations(self, ids, deadline):
        with self.observation_connection(deadline) as connection:
            return self._observation_state(connection)[0], self._observation_rows(connection, ids)

    def save_observation(self, values, generation, association, deadline):
        values = _observation(values)
        with self.observation_connection(deadline, write=True) as connection:
            if self._observation_state(connection)[0] != generation:
                raise _Unavailable('stale_observation_generation')
            row = connection.execute('SELECT * FROM observations WHERE observation_id=?', (values[0],)).fetchone()
            if row is not None:
                if _observation(row) != values:
                    raise _Unavailable('observation_conflict')
                # A retry never reattaches a cleared or changed association.
                return
            connection.execute('INSERT INTO observations VALUES (?,?,?,?,?,?,?,?)', values)
            if association is not None:
                connection.execute('INSERT INTO observation_links VALUES (?,?,?,?,?)', (values[0], *association.values()))

    def purge_observations(self, deadline):
        with self.observation_connection(deadline, write=True) as connection:
            generation = self._observation_state(connection)[0]
            if generation == _MAX_REVISION:
                raise _Unavailable('generation_exhausted')
            removed = connection.execute('SELECT count(*) FROM observations').fetchone()[0]
            connection.execute('DELETE FROM observations')
            connection.execute('UPDATE observation_state SET generation=?,after_observation_id=NULL WHERE singleton=1',
                               (generation + 1,))
            return generation + 1, removed

    def scan_observation_links(self, deadline):
        with self.observation_connection(deadline) as connection:
            generation, cursor = self._observation_state(connection)
            rows = connection.execute('SELECT * FROM observation_links '
                + ('WHERE observation_id > ? COLLATE BINARY ' if cursor is not None else '')
                + 'ORDER BY observation_id COLLATE BINARY LIMIT 64', () if cursor is None else (cursor,)).fetchall()
            return generation, tuple((_key(row[0]), _Binding.from_row(row[1:])) for row in rows)

    def remove_observation_links(self, candidates, generation, cursor, deadline):
        with self.observation_connection(deadline, write=True) as connection:
            if self._observation_state(connection)[0] != generation:
                raise _Unavailable('stale_observation_generation')
            removed = 0
            for identity, binding in candidates:
                deadline.remaining()
                removed += connection.execute('DELETE FROM observation_links WHERE observation_id=? AND event_id=? '
                    'AND witness_version=? AND parent_created_at_bits=? AND parent_result=?',
                    (identity, *binding.values())).rowcount
            connection.execute('UPDATE observation_state SET after_observation_id=? WHERE singleton=1', (cursor,))
            return removed
