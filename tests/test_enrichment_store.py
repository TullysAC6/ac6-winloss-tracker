"""T0: exact optional format, transactions and preservation in owned roots."""
import importlib
import math
import os
import sqlite3
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import enrichment_store as storage
from history_store import HistoryStore
from optional_enrichment import OptionalEnrichmentService


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="ac6-enrichment-t0-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.history = HistoryStore(self.root)
        self.history.start_session(started_at=1)
        self.history.record_result("A", "win", "test", {}, created_at=2.125)
        self.service = OptionalEnrichmentService(self.root, active=True)
        self.path = self.root / "enrichment.db"

    def create(self):
        prepared = self.service.prepare_binding("A")
        self.assertEqual(prepared.status, "prepared", prepared)
        result = self.service.save_binding(prepared.ticket, create_missing=True)
        self.assertEqual(result.status, "saved", result)
        return prepared.ticket

    def sql(self, statement, args=()):
        with sqlite3.connect(self.path) as connection:
            result = connection.execute(statement, args).fetchall()
        connection.close()
        return result

    def test_explicit_only_exact_format_and_no_rewrite(self):
        self.assertEqual(self.service.inspect().health.state, "absent")
        self.assertFalse(self.path.exists())
        ticket = self.create()
        self.assertEqual(self.sql("PRAGMA application_id"), [(0x41433645,)])
        self.assertEqual(self.sql("PRAGMA user_version"), [(1,)])
        self.assertEqual(self.sql("PRAGMA page_size"), [(4096,)])
        self.assertEqual(self.sql("SELECT name FROM sqlite_schema WHERE type='table' ORDER BY name"),
                         [("maintenance_state",), ("match_bindings",)])
        self.assertEqual(self.sql("SELECT * FROM match_bindings"), [("A", 1, struct.pack(">d", 2.125), "win")])
        before = self.path.read_bytes()
        self.assertEqual(self.service.save_binding(ticket).status, "saved")
        self.assertEqual(self.path.read_bytes(), before)
        with self.service._store.open(storage._Deadline(), write=True) as connection:
            self.assertEqual(connection.execute("PRAGMA max_page_count").fetchone()[0], 16384)
        self.assertFalse(list(self.root.glob(".enrichment-*")))

    def test_unknown_corrupt_and_future_preserved(self):
        for data in (b"", b"not sqlite"):
            with self.subTest(data=data):
                self.path.write_bytes(data)
                self.assertEqual(self.service.inspect().health.state, "degraded")
                self.service.save_binding(self.service.prepare_binding("A").ticket, create_missing=True)
                self.assertEqual(self.path.read_bytes(), data)
                self.path.unlink()
        for statement in ("PRAGMA user_version=0", "PRAGMA user_version=2", "PRAGMA user_version=-1",
                          "PRAGMA application_id=7"):
            with self.subTest(statement=statement):
                self.create()
                self.sql(statement)
                before = self.path.read_bytes()
                self.assertEqual(self.service.inspect().health.state, "incompatible")
                self.assertEqual(self.service.cleanup_step().status, "unavailable")
                self.assertEqual(self.path.read_bytes(), before)
                self.path.unlink()

    def test_exact_constraints_extra_objects_and_cursor(self):
        mutations = (
            "CREATE TABLE unwanted(x)",
            "CREATE INDEX unwanted ON match_bindings(parent_result)",
            "CREATE VIEW unwanted AS SELECT * FROM match_bindings",
            "CREATE TRIGGER unwanted AFTER INSERT ON match_bindings BEGIN DELETE FROM maintenance_state; END",
            "DELETE FROM maintenance_state",
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.create()
                self.sql(mutation)
                before = self.path.read_bytes()
                self.assertEqual(self.service.inspect().health.state, "degraded")
                self.assertEqual(self.path.read_bytes(), before)
                self.path.unlink()
        # Test-local malformed schemas: the version/header alone is insufficient.
        for replacement in ("PRIMARY KEY", "NOT NULL", "COLLATE BINARY",
                            "CHECK(typeof(witness_version) = 'integer' AND witness_version = 1)"):
            with self.subTest(replacement=replacement):
                with sqlite3.connect(self.path) as connection:
                    connection.execute(storage._DDL[0].replace(replacement, "", 1))
                    connection.execute(storage._DDL[1])
                    connection.execute("INSERT INTO maintenance_state VALUES(1,NULL)")
                    connection.execute("PRAGMA application_id=1094923845")
                    connection.execute("PRAGMA user_version=1")
                connection.close()
                before = self.path.read_bytes()
                self.assertEqual(self.service.inspect().health.state, "degraded")
                self.assertEqual(self.path.read_bytes(), before)
                self.path.unlink()

    def test_invalid_values_fail_closed_without_repair(self):
        self.create()
        with sqlite3.connect(self.path) as connection:
            connection.execute("PRAGMA ignore_check_constraints=ON")
            connection.execute("UPDATE match_bindings SET parent_created_at_bits=?", (struct.pack(">d", math.inf),))
        connection.close()
        before = self.path.read_bytes()
        self.assertEqual(self.service.lookup("A").status, "unavailable")
        self.assertEqual(self.service.cleanup_step().status, "unavailable")
        self.assertEqual(self.path.read_bytes(), before)

    def test_creation_failure_and_no_clobber(self):
        original = storage._Store._validate
        with patch.object(storage._Store, "_validate", side_effect=OSError("injected")):
            result = self.service.save_binding(self.service.prepare_binding("A").ticket, create_missing=True)
        self.assertEqual(result.status, "unavailable")
        self.assertFalse(self.path.exists())
        self.assertFalse(list(self.root.glob(".enrichment-*")))
        def competitor(store, connection):
            result = original(store, connection)
            self.path.write_bytes(b"competing foreign store")
            return result
        with patch.object(storage._Store, "_validate", competitor):
            result = self.service.save_binding(self.service.prepare_binding("A").ticket, create_missing=True)
        self.assertEqual(result.status, "unavailable")
        self.assertEqual(self.path.read_bytes(), b"competing foreign store")
        self.assertFalse(list(self.root.glob(".enrichment-*")))

    def test_transaction_failure_rolls_back_rows_and_cursor_and_version(self):
        self.create()
        with self.assertRaisesRegex(RuntimeError, "fault"):
            with self.service._store.open(storage._Deadline(), write=True) as connection:
                connection.execute("DELETE FROM match_bindings")
                connection.execute("UPDATE maintenance_state SET after_event_id='A'")
                # Synthetic future migration only; there is no v2 product schema.
                connection.execute("CREATE TABLE future_fixture(x)")
                connection.execute("PRAGMA user_version=2")
                raise RuntimeError("fault")
        self.assertEqual(self.service.inspect().status, "ready")
        self.assertEqual(self.sql("SELECT event_id FROM match_bindings"), [("A",)])
        self.assertEqual(self.sql("SELECT * FROM maintenance_state"), [(1, None)])

    def test_partial_initialization_never_publishes(self):
        connect = sqlite3.connect
        targets = (*storage._DDL, "INSERT INTO maintenance_state VALUES (1,NULL)",
                   f"PRAGMA application_id={storage._APPLICATION_ID}", "PRAGMA user_version=1")
        for target in targets:
            with self.subTest(target=target):
                class FaultConnection(sqlite3.Connection):
                    def execute(connection, sql, *args):
                        result = super().execute(sql, *args)
                        if sql == target:
                            raise OSError("injected after initialization statement")
                        return result
                def faulty_connect(*args, **kwargs):
                    return connect(*args, **kwargs, factory=FaultConnection)
                ticket = self.service.prepare_binding("A").ticket
                before = (self.root / "history.db").read_bytes()
                with patch.object(sqlite3, "connect", side_effect=faulty_connect):
                    result = self.service.save_binding(ticket, create_missing=True)
                self.assertEqual(result.status, "unavailable")
                self.assertFalse(self.path.exists())
                self.assertFalse(list(self.root.glob(".enrichment-*")))
                self.assertEqual((self.root / "history.db").read_bytes(), before)

    def test_busy_readonly_full_io_and_unexpected_failure_isolation(self):
        ticket = self.create()
        lock = sqlite3.connect(self.path)
        try:
            lock.execute("BEGIN EXCLUSIVE")
            self.assertEqual(self.service.lookup("A").health.reason, "busy")
        finally:
            lock.rollback()
            lock.close()
        with storage._connection(self.path, storage._Deadline()) as connection:
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute("DELETE FROM match_bindings")
        for code in (sqlite3.SQLITE_READONLY, sqlite3.SQLITE_FULL, sqlite3.SQLITE_IOERR):
            error = sqlite3.OperationalError("controlled fault")
            error.sqlite_errorcode = code
            with patch.object(storage._Store, "save", side_effect=error):
                result = self.service.save_binding(ticket)
            self.assertEqual(result.status, "unavailable")
        for error in (PermissionError("denied"), RuntimeError("defect")):
            with patch.object(storage._Store, "save", side_effect=error):
                self.assertEqual(self.service.save_binding(ticket).status, "unavailable")
        self.assertEqual(self.service.lookup("A").status, "visible")
        self.assertEqual(self.history.lifetime_summary()["wins"], 1)

    def test_import_disabled_and_path_guards(self):
        import optional_enrichment
        with patch.object(Path, "stat", side_effect=AssertionError("stat")), \
                patch.object(sqlite3, "connect", side_effect=AssertionError("open")):
            importlib.reload(storage)
            importlib.reload(optional_enrichment)
            disabled = optional_enrichment.OptionalEnrichmentService(self.root)
            self.assertEqual(disabled.lookup("A").health.state, "disabled")
            disabled.invalidate_history()
            disabled.deactivate()
        # Reload changes private class identity; create a fresh service afterwards.
        relative = optional_enrichment.OptionalEnrichmentService(Path("relative"), active=True)
        self.assertEqual(relative.inspect().health.reason, "unsafe_path")

    def test_hardlink_and_expired_transaction_do_not_mutate(self):
        self.create()
        alias = self.root / "linked.db"
        os.link(self.path, alias)
        try:
            self.assertEqual(self.service.inspect().health.reason, "unsafe_path")
        finally:
            alias.unlink()
        deadline = storage._Deadline()
        with self.assertRaises(storage._Unavailable):
            with self.service._store.open(deadline, write=True) as connection:
                connection.execute("DELETE FROM match_bindings")
                connection.execute("UPDATE maintenance_state SET after_event_id='A'")
                deadline.end = 0  # Expiry before commit must roll back both changes.
        self.assertEqual(self.sql("SELECT event_id FROM match_bindings"), [("A",)])
        self.assertEqual(self.sql("SELECT * FROM maintenance_state"), [(1, None)])


if __name__ == "__main__":
    unittest.main(verbosity=2)
