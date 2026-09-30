"""T0: #15-1 category persistence is optional, parent-checked and recognition-agnostic.

Since #15-2 a fresh store is v3, so this v2 category API is exercised on an
explicit exact v2 store; on v3 (and for a fresh file) it is refused as
snapshot_required. tests/test_match_snapshots.py owns the v3 snapshot contract.
"""
import sqlite3
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import enrichment_store as storage
from enrichment_test_clock import fixture_clock, real_clock
from history_store import HistoryStore
from optional_enrichment import MatchMetadata, OptionalEnrichmentService


class MetadataTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(fixture_clock())
        self.directory = tempfile.TemporaryDirectory(prefix="ac6-metadata-t0-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.history = HistoryStore(self.root)
        self.history.start_session(started_at=1)
        self.history.record_result("A", "win", "fixture", {}, created_at=2.125)
        self.service = OptionalEnrichmentService(self.root, active=True)
        self.ticket = self.service.prepare_binding("A").ticket
        self.path = self.root / "enrichment.db"
        self.unknown = MatchMetadata("A")
        self.ranked = MatchMetadata("A", "ranked", "single")

    def save(self, value=None, expected=None):
        if not self.path.exists():
            self.v2()
        return self.service.save_metadata(self.ticket, value or self.ranked, expected=expected or self.unknown)

    def v2(self):
        # Canonical accepted v2 DDL; real #15-1 creation is covered by T2.
        for table in storage._SCHEMAS[2]:
            self.sql(storage._TABLES[table][0])
        self.sql("INSERT INTO maintenance_state VALUES(1,NULL)")
        self.sql(f"PRAGMA application_id={storage._APPLICATION_ID}")
        self.sql("PRAGMA user_version=2")

    def sql(self, query, args=()):
        connection = sqlite3.connect(self.path)
        try:
            with connection:
                return connection.execute(query, args).fetchall()
        finally:
            connection.close()

    def v1(self):
        # Canonical accepted v1 DDL; real old-code creation is also covered by T2.
        for ddl in storage._DDL[:2]:
            self.sql(ddl)
        self.sql("INSERT INTO maintenance_state VALUES(1,'A')")
        self.sql("INSERT INTO match_bindings VALUES(?,?,?,?)", self.ticket.binding.values())
        self.sql(f"PRAGMA application_id={storage._APPLICATION_ID}")
        self.sql("PRAGMA user_version=1")

    def test_unknown_mixed_values_all_categories_and_no_inference(self):
        before = (self.root / "history.db").read_bytes()
        absent = self.service.lookup_metadata("A")
        self.assertEqual(absent.health.state, "absent")
        self.assertEqual(absent.metadata, ())
        self.assertFalse(self.path.exists())
        current = self.unknown
        for kind in ("unknown", "ranked", "custom"):
            for form in ("unknown", "single", "team"):
                value = MatchMetadata("A", kind, form)
                self.assertEqual(self.save(value, current).status, "saved")
                self.assertEqual(self.service.lookup_metadata("A").metadata, (value,))
                current = value
        self.assertEqual((self.root / "history.db").read_bytes(), before)
        self.history.record_result("B", "loss", "fixture", {}, created_at=3)
        ticket = self.service.prepare_binding("B").ticket
        self.assertEqual(self.service.save_binding(ticket).status, "saved")
        self.assertEqual(self.service.lookup_metadata("B").metadata, (MatchMetadata("B"),))
        self.assertEqual(self.sql("SELECT event_id FROM match_metadata"), [("A",)])
        # The category writes themselves did not modify authoritative history.
        self.history.discard_result("B")
        self.assertEqual(self.history.lifetime_summary()["wins"], 1)

    def test_atomic_compare_and_set_and_idempotent_retry(self):
        self.assertEqual(self.save().status, "saved")
        before = self.path.read_bytes()
        self.assertEqual(self.save().status, "saved")
        self.assertEqual(self.path.read_bytes(), before)
        custom = MatchMetadata("A", "custom", "team")
        self.assertEqual(self.save(custom).health.reason, "metadata_conflict")
        self.assertEqual(self.service.lookup_metadata("A").metadata, (self.ranked,))
        self.assertEqual(self.save(custom, self.ranked).status, "saved")
        self.assertEqual(self.save(self.ranked).health.reason, "metadata_conflict")
        self.assertEqual(self.service.lookup_metadata("A").metadata, (custom,))

    def test_invalid_inputs_never_initialize_or_rewrite(self):
        for invalid in (MatchMetadata("A", "RANKED", "single"), MatchMetadata("A", None, "team"),
                        MatchMetadata("A", "ranked", "duel"), MatchMetadata("B", "ranked", "single"), {}):
            with self.subTest(invalid=invalid):
                response = self.service.save_metadata(self.ticket, invalid, expected=self.unknown, create_missing=True)
                self.assertEqual(response.health.reason, "invalid_metadata")
                self.assertFalse(self.path.exists())
        self.assertEqual(self.save().status, "saved")
        before = self.path.read_bytes()
        self.assertEqual(self.service.save_metadata(self.ticket, self.ranked, expected={}).health.reason,
                         "invalid_metadata")
        self.assertEqual(self.path.read_bytes(), before)

    def test_failed_metadata_insert_rolls_back_new_binding_and_values(self):
        connect = sqlite3.connect
        reached = []
        history = (self.root / "history.db").read_bytes()
        class Fault(sqlite3.Connection):
            def execute(connection, sql, *args):
                result = super().execute(sql, *args)
                if sql.startswith("INSERT INTO match_metadata VALUES"):
                    reached.append(connection.execute("SELECT event_id FROM match_bindings").fetchall())
                    raise OSError("after real metadata insertion")
                return result
        with patch.object(sqlite3, "connect", side_effect=lambda *a, **kw: connect(*a, **kw, factory=Fault)):
            self.assertEqual(self.save().status, "unavailable")
        self.assertEqual(reached, [[("A",)]])
        self.assertEqual(self.sql("SELECT * FROM match_bindings"), [])
        self.assertEqual(self.sql("SELECT * FROM match_metadata"), [])
        self.assertEqual((self.root / "history.db").read_bytes(), history)
        self.assertEqual(self.save().status, "saved")

    def test_v1_reads_unknown_and_requires_explicit_atomic_upgrade(self):
        self.v1()
        before = self.path.read_bytes()
        self.assertEqual(self.service.inspect().status, "ready")
        self.assertEqual(self.service.lookup_metadata("A").metadata, (self.unknown,))
        # v1 upgrades only to v3, so the category-only write is refused here too.
        self.assertEqual(self.save().health.reason, "snapshot_required")
        self.assertEqual(self.path.read_bytes(), before)
        history = (self.root / "history.db").read_bytes()
        self.assertEqual(self.service.upgrade_storage().status, "ready")
        self.assertEqual(self.sql("PRAGMA user_version"), [(4,)])
        self.assertEqual(self.sql("SELECT * FROM maintenance_state"), [(1, "A")])
        self.assertEqual(self.service.lookup_metadata("A").metadata, (self.unknown,))
        upgraded = self.path.read_bytes()
        # The upgrade goes to v3, where category-only writes are refused, not merged.
        self.assertEqual(self.save().health.reason, "snapshot_required")
        self.assertEqual(self.service.upgrade_storage().status, "ready")
        self.assertEqual(self.path.read_bytes(), upgraded)
        self.assertEqual((self.root / "history.db").read_bytes(), history)

    def test_fresh_store_is_never_created_for_a_category_only_write(self):
        result = self.service.save_metadata(self.ticket, self.ranked, expected=self.unknown, create_missing=True)
        self.assertEqual((result.health.state, result.health.reason), ("incompatible", "snapshot_required"))
        self.assertFalse(self.path.exists())
        self.assertFalse(list(self.root.glob(".enrichment-*")))

    def test_migration_failure_at_each_step_restores_v1(self):
        self.v1()
        before = self.path.read_bytes()
        connect = sqlite3.connect
        for target in (storage._SNAPSHOTS_DDL, "PRAGMA user_version=4"):
            with self.subTest(target=target):
                reached = []
                class Fault(sqlite3.Connection):
                    def execute(connection, sql, *args):
                        result = super().execute(sql, *args)
                        if sql == target:
                            reached.append(sql)
                            raise OSError("after migration step")
                        return result
                with patch.object(sqlite3, "connect", side_effect=lambda *a, **kw: connect(*a, **kw, factory=Fault)):
                    self.assertEqual(self.service.upgrade_storage().status, "unavailable")
                self.assertEqual(reached, [target])
                self.assertEqual(self.path.read_bytes(), before)
                self.assertEqual(self.sql("PRAGMA user_version"), [(1,)])
                self.assertEqual(self.service.lookup_metadata("A").metadata, (self.unknown,))
        self.assertFalse(list(self.root.glob("*journal")))

    def test_real_expiry_after_migration_mutations_rolls_back(self):
        self.v1()
        before = self.path.read_bytes()
        original = self.service._store._validate
        reached = []
        deadline = storage._Deadline()
        with ExitStack() as clocks:
            def after_upgrade(connection):
                result = original(connection)
                if connection.execute("PRAGMA user_version").fetchone()[0] == 4:
                    reached.append(True)
                    self.assertEqual(connection.execute("SELECT count(*) FROM match_snapshots").fetchone(), (0,))
                    deadline.end = 0
                    clocks.enter_context(real_clock())
                return result
            with patch.object(self.service._store, "_validate", side_effect=after_upgrade):
                with self.assertRaisesRegex(storage._Unavailable, "^deadline$"):
                    self.service._store.upgrade(deadline)
        self.assertEqual(reached, [True])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.sql("PRAGMA user_version"), [(1,)])

    def test_constraints_exact_schema_and_incompatible_preservation(self):
        self.assertEqual(self.save().status, "saved")
        with self.service._store.open(storage._Deadline(), write=True) as connection:
            for sql in ("UPDATE match_metadata SET match_type='inferred'", "UPDATE match_metadata SET match_format=NULL",
                        "INSERT INTO match_metadata VALUES('no-parent','ranked','team')"):
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(sql)
        self.sql("CREATE INDEX unexpected ON match_metadata(match_type)")
        before = self.path.read_bytes()
        self.assertEqual(self.service.lookup_metadata("A").health.reason, "schema")
        self.assertEqual(self.service.upgrade_storage().health.reason, "schema")
        self.assertEqual(self.path.read_bytes(), before)
        self.sql("DROP INDEX unexpected")
        self.sql("PRAGMA user_version=5")  # 4 is now a supported version with its own schema
        before = self.path.read_bytes()
        self.assertEqual(self.service.lookup_metadata("A").health.state, "incompatible")
        self.assertEqual(self.service.upgrade_storage().health.state, "incompatible")
        self.assertEqual(self.path.read_bytes(), before)

    def test_corrupt_values_fail_closed_without_touching_history(self):
        self.assertEqual(self.save().status, "saved")
        history = (self.root / "history.db").read_bytes()
        connection = sqlite3.connect(self.path)
        with connection:
            connection.execute("PRAGMA ignore_check_constraints=ON")
            connection.execute("UPDATE match_metadata SET match_type='not-evidence'")
        connection.close()
        before = self.path.read_bytes()
        self.assertEqual(self.service.lookup_metadata("A").health.reason, "invalid_metadata")
        self.assertEqual(self.save().health.reason, "invalid_metadata")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual((self.root / "history.db").read_bytes(), history)
        self.history.record_result("B", "loss", "fixture", {}, created_at=3)
        self.assertEqual(self.history.lifetime_summary()["losses"], 1)

    def test_missing_parent_reuse_and_cascade_cleanup(self):
        self.assertEqual(self.save().status, "saved")
        self.history.discard_result("A")
        self.assertEqual(self.service.lookup_metadata("A").metadata, ())
        self.assertEqual(self.sql("SELECT event_id FROM match_metadata"), [("A",)])
        self.assertEqual(self.save().status, "unknown")
        self.history.record_result("A", "win", "fixture", {}, created_at=4)
        self.assertEqual(self.service.lookup_metadata("A").health.reason, "identity_mismatch")
        self.assertEqual(self.save().health.reason, "identity_mismatch")
        self.assertEqual(self.service.cleanup_step().deleted, 1)
        self.assertEqual(self.service.cleanup_step().deleted, 0)
        self.assertEqual(self.sql("SELECT * FROM match_metadata"), [])
        self.assertEqual(self.history.lifetime_summary()["wins"], 1)

    def test_late_metadata_commit_cannot_resurrect_parent(self):
        original = self.service._store.save_metadata
        committed = []
        def late(*args):
            self.history.discard_result("A")
            self.service.invalidate_history()
            original(*args)
            committed.extend(self.sql("SELECT event_id FROM match_metadata"))
        with patch.object(self.service._store, "save_metadata", side_effect=late):
            self.assertEqual(self.save().status, "rejected")
        self.assertEqual(committed, [("A",)])
        self.assertEqual(self.service.lookup_metadata("A").metadata, ())
        self.assertEqual(self.history.lifetime_summary()["wins"], 0)
        self.assertEqual(self.service.cleanup_step().deleted, 1)
        self.assertEqual(self.sql("SELECT * FROM match_metadata"), [])

    def test_publication_invalidation_and_unknown_authority_never_expose(self):
        self.assertEqual(self.save().status, "saved")
        original = self.service._store.read_metadata
        def revoke(*args):
            rows = original(*args)
            self.service.invalidate_history()
            return rows
        with patch.object(self.service._store, "read_metadata", side_effect=revoke):
            self.assertEqual(self.service.lookup_metadata("A").status, "rejected")
        with patch.object(self.service, "_parents", side_effect=storage._Unavailable("authority_unavailable")):
            self.assertEqual(self.service.lookup_metadata("A").metadata, ())
            self.assertEqual(self.service.cleanup_step().status, "unavailable")
        self.assertEqual(self.sql("SELECT event_id FROM match_metadata"), [("A",)])

    def test_dormant_methods_touch_nothing_and_draw_not_added(self):
        disabled = OptionalEnrichmentService(self.root)
        with patch.object(sqlite3, "connect", side_effect=AssertionError("unexpected SQLite")), \
                patch.object(Path, "stat", side_effect=AssertionError("unexpected stat")):
            for response in (disabled.lookup_metadata("A"), disabled.upgrade_storage(),
                             disabled.save_metadata(self.ticket, self.ranked, expected=self.unknown, create_missing=True)):
                self.assertEqual(response.health.state, "disabled")
        connection = sqlite3.connect(self.root / "history.db")
        with connection:
            connection.execute("UPDATE matches SET result='draw' WHERE event_id='A'")
        connection.close()
        self.assertEqual(self.service.prepare_binding("A").status, "unknown")
        self.assertEqual(self.service.save_metadata(self.ticket, self.ranked, expected=self.unknown,
                                                    create_missing=True).health.reason, "identity_mismatch")
        self.assertFalse(self.path.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
