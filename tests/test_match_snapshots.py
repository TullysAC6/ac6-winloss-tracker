"""T0: #15-2 per-match snapshots are coherent, revisioned, optional and dormant.

Structured values here are storage inputs, not recognition truth: no fixture
claims what the game displayed. Expected-success operations use the fixed
fixture clock; the one deadline control opts back into real time.
"""
import sqlite3
import sys
import tempfile
import unittest
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import enrichment_store as storage
from enrichment_test_clock import fixture_clock, real_clock
from history_store import HistoryStore
from optional_enrichment import MatchMetadata, MatchSnapshot, OptionalEnrichmentService

VALID_RANKS = ("UNRANKED", "S", "A", "A1", "A2", "A3", "A4", "B", "B3", "C1", "E", "U", "Z9")
INVALID_RANKS = ("A5", "A0", "S1", "S0", "a4", "a", "B10", "B0", "SS", "AA", "UNRANKED1", "", " A", "A4 ",
                 "Ａ", "A+", "B-", "S rank", "B\x00junk", "B1\x00x", "A\x00", 4, 1.0, b"A", True)


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(fixture_clock())
        self.directory = tempfile.TemporaryDirectory(prefix="ac6-snapshot-t0-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.history = HistoryStore(self.root)
        self.history.start_session(started_at=1)
        self.history.record_result("A", "win", "fixture", {}, created_at=2.125)
        self.service = OptionalEnrichmentService(self.root, active=True)
        self.ticket = self.service.prepare_binding("A").ticket
        self.path = self.root / "enrichment.db"
        self.recognized = MatchSnapshot("A", "ranked", "single", "A4", "S", "recognized", "rank-interpretation-1")

    def save(self, snapshot, revision=None, create_missing=True):
        revision = snapshot.revision if revision is None else revision
        return self.service.save_snapshot(self.ticket, replace(snapshot, revision=revision),
                                          expected_revision=revision, create_missing=create_missing)

    def current(self):
        return self.service.lookup_snapshot("A").snapshots[0]

    def sql(self, query, args=()):
        connection = sqlite3.connect(self.path)
        try:
            with connection:
                return connection.execute(query, args).fetchall()
        finally:
            connection.close()

    def build(self, version, *, binding=True, metadata=()):
        """An exact older store from the retained v1/v2 DDL; old-code creation is T2."""
        tables = storage._SCHEMAS[version]
        for table in tables:
            self.sql(storage._TABLES[table][0])
        self.sql("INSERT INTO maintenance_state VALUES(1,'A')")
        if binding:
            self.sql("INSERT INTO match_bindings VALUES(?,?,?,?)", self.ticket.binding.values())
        for row in metadata:
            self.sql("INSERT INTO match_metadata VALUES(?,?,?)", row)
        self.sql(f"PRAGMA application_id={storage._APPLICATION_ID}")
        self.sql(f"PRAGMA user_version={version}")

    # ------------------------------------------------------------- schema
    def test_fresh_creation_is_exact_v3_and_history_is_untouched(self):
        history = (self.root / "history.db").read_bytes()
        self.assertEqual(self.save(self.recognized).status, "saved")
        self.assertEqual(self.sql("PRAGMA user_version"), [(4,)])
        self.assertEqual(sorted(self.sql("SELECT type,name,tbl_name FROM sqlite_schema")), sorted([
            ("table", "match_bindings", "match_bindings"),
            ("index", "sqlite_autoindex_match_bindings_1", "match_bindings"),
            ("table", "maintenance_state", "maintenance_state"),
            ("table", "match_snapshots", "match_snapshots"),
            ("index", "sqlite_autoindex_match_snapshots_1", "match_snapshots"),
            ("table", "observations", "observations"),
            ("index", "sqlite_autoindex_observations_1", "observations"),
            ("table", "observation_links", "observation_links"),
            ("index", "sqlite_autoindex_observation_links_1", "observation_links"),
            ("table", "observation_state", "observation_state")]))
        self.assertEqual(self.sql("SELECT sql FROM sqlite_schema WHERE name='match_snapshots'"),
                         [(storage._SNAPSHOTS_DDL,)])
        self.assertEqual(self.sql("SELECT * FROM match_snapshots"),
                         [("A", 1, "ranked", "single", "A4", "S", "recognized", "rank-interpretation-1")])
        self.assertEqual((self.root / "history.db").read_bytes(), history)

    def test_each_supported_version_validates_exactly_and_others_fail_closed(self):
        for version in (1, 2):
            with self.subTest(version=version):
                self.build(version)
                self.assertEqual(self.service.inspect().status, "ready")
                self.path.unlink()
        mislabelled = ((2, 3), (3, 2), (1, 3), (3, 1))
        for built, label in mislabelled:
            with self.subTest(built=built, label=label):
                if built == 3:
                    self.assertEqual(self.save(self.recognized).status, "saved")
                else:
                    self.build(built)
                self.sql(f"PRAGMA user_version={label}")
                before = self.path.read_bytes()
                self.assertEqual(self.service.inspect().health.reason, "schema")
                self.assertEqual(self.service.upgrade_storage().health.reason, "schema")
                self.assertEqual(self.path.read_bytes(), before)
                self.path.unlink()
        for future in (0, 5, 6, -1):
            with self.subTest(future=future):
                self.assertEqual(self.save(self.recognized).status, "saved")
                self.sql(f"PRAGMA user_version={future}")
                before = self.path.read_bytes()
                self.assertEqual(self.service.inspect().health.state, "incompatible")
                self.assertEqual(self.service.upgrade_storage().health.state, "incompatible")
                self.assertEqual(self.save(self.recognized, 1, False).health.state, "incompatible")
                self.assertEqual(self.path.read_bytes(), before)
                self.path.unlink()

    def test_extra_objects_and_altered_columns_fail_closed(self):
        for mutation in ("CREATE INDEX unexpected ON match_snapshots(self_rank)",
                         "CREATE TABLE match_metadata(x)",
                         "CREATE TRIGGER unexpected AFTER UPDATE ON match_snapshots BEGIN SELECT 1; END",
                         "ALTER TABLE match_snapshots ADD COLUMN confidence REAL"):
            with self.subTest(mutation=mutation):
                self.assertEqual(self.save(self.recognized).status, "saved")
                self.sql(mutation)
                before = self.path.read_bytes()
                self.assertEqual(self.service.lookup_snapshot("A").health.reason, "schema")
                self.assertEqual(self.save(self.recognized, 1, False).health.reason, "schema")
                self.assertEqual(self.path.read_bytes(), before)
                self.path.unlink()

    # ------------------------------------------------------------- values
    def test_rank_tokens_are_bounded_and_exactly_preserved(self):
        revision = 0
        for token in VALID_RANKS:
            with self.subTest(token=token):
                result = self.save(MatchSnapshot("A", "ranked", "single", token, token), revision)
                self.assertEqual(result.status, "saved", result)
                revision = result.snapshots[0].revision
                stored = self.current()
                # A stays A, A4 stays A4: no normalization, subdivision or ordering.
                self.assertEqual((stored.self_rank, stored.opponent_rank), (token, token))
        for token in INVALID_RANKS:
            with self.subTest(token=token):
                before = self.path.read_bytes()
                for snapshot in (MatchSnapshot("A", "ranked", "single", self_rank=token),
                                 MatchSnapshot("A", "ranked", "single", opponent_rank=token)):
                    self.assertEqual(self.save(snapshot, revision).health.reason, "invalid_metadata")
                self.assertEqual(self.path.read_bytes(), before)

    def test_database_checks_mirror_the_value_rules(self):
        self.assertEqual(self.save(self.recognized).status, "saved")
        violations = (
            "UPDATE match_snapshots SET self_rank='A5'", "UPDATE match_snapshots SET opponent_rank='a4'",
            "UPDATE match_snapshots SET self_rank='S1'", "UPDATE match_snapshots SET self_rank=4",
            "UPDATE match_snapshots SET match_format='team'",  # opponent rank would remain
            "UPDATE match_snapshots SET recognition_version=NULL",
            "UPDATE match_snapshots SET recognition_status=NULL",
            "UPDATE match_snapshots SET recognition_status='partial'",
            "UPDATE match_snapshots SET recognition_status='failed'",  # facts would remain
            "UPDATE match_snapshots SET recognition_version='1.2.0 release'",
            "UPDATE match_snapshots SET recognition_version=''",
            # SQLite length() and GLOB stop at NUL; the instr() guard must still refuse these.
            "UPDATE match_snapshots SET self_rank='B'||char(0)||'junk'",
            "UPDATE match_snapshots SET opponent_rank='B1'||char(0)||'x'",
            "UPDATE match_snapshots SET recognition_version='v1'||char(0)||' not a token!'",
            # '2' would be stored as the integer 2 by column affinity; these cannot be.
            "UPDATE match_snapshots SET revision=0", "UPDATE match_snapshots SET revision='two'",
            "UPDATE match_snapshots SET revision=2.5",
            "INSERT INTO match_snapshots VALUES('no-parent',1,'ranked','single',NULL,NULL,NULL,NULL)",
            "INSERT INTO match_snapshots VALUES('A2',1,'unknown','unknown',NULL,NULL,'recognized','v1')")
        with self.service._store.open(storage._Deadline(), write=True) as connection:
            for sql in violations:
                with self.subTest(sql=sql), self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(sql)

    def test_recognition_status_and_version_contract(self):
        valid = (MatchSnapshot("A"),  # no recorded recognition evidence
                 MatchSnapshot("A", "custom", "team", "B2"),  # explicit values, no evidence claimed
                 MatchSnapshot("A", "ranked", "unknown", None, None, "recognized", "v1"),
                 MatchSnapshot("A", "unknown", "unknown", "UNRANKED", None, "recognized", "rank.v2:beta-1"),
                 MatchSnapshot("A", recognition_status="failed", recognition_version="meta-1"))
        revision = 0
        for snapshot in valid:
            with self.subTest(snapshot=snapshot):
                result = self.save(snapshot, revision)
                self.assertEqual(result.status, "saved", result)
                revision = result.snapshots[0].revision
                self.assertEqual(replace(self.current(), revision=0), snapshot)
        invalid = (MatchSnapshot("A", recognition_status="recognized"),
                   MatchSnapshot("A", "ranked", recognition_version="v1"),
                   MatchSnapshot("A", recognition_status="recognized", recognition_version="v1"),  # no facts
                   MatchSnapshot("A", "ranked", recognition_status="failed", recognition_version="v1"),
                   MatchSnapshot("A", self_rank="A", recognition_status="failed", recognition_version="v1"),
                   MatchSnapshot("A", "ranked", recognition_status="not_attempted", recognition_version="v1"),
                   MatchSnapshot("A", "ranked", recognition_status="partial", recognition_version="v1"),
                   MatchSnapshot("A", "ranked", recognition_status="recognized", recognition_version="x" * 65),
                   MatchSnapshot("A", "ranked", recognition_status="recognized", recognition_version="-v1"),
                   MatchSnapshot("A", "ranked", recognition_status="recognized", recognition_version="v 1"),
                   MatchSnapshot("A", "ranked", recognition_status="recognized", recognition_version="v1\x00x"),
                   MatchSnapshot("A", "ranked", recognition_status="recognized", recognition_version=1),
                   MatchSnapshot("A", "RANKED"), MatchSnapshot("A", "ranked", "duel"))
        for snapshot in invalid:
            with self.subTest(snapshot=snapshot):
                before = self.path.read_bytes()
                self.assertEqual(self.save(snapshot, revision).health.reason, "invalid_metadata")
                self.assertEqual(self.path.read_bytes(), before)

    def test_invalid_input_never_creates_a_file(self):
        for snapshot, revision in ((MatchSnapshot("A", "ranked", "team", None, "S"), 0),
                                   (MatchSnapshot("B", "ranked"), 0), (MatchSnapshot("A", revision=1), 0),
                                   (MatchSnapshot("A", revision=True), True), (MatchSnapshot("A", revision=-1), -1)):
            with self.subTest(snapshot=snapshot, revision=revision):
                result = self.service.save_snapshot(self.ticket, snapshot, expected_revision=revision,
                                                    create_missing=True)
                self.assertEqual(result.health.reason, "invalid_metadata")
                self.assertFalse(self.path.exists())
        self.assertEqual(self.service.save_snapshot(self.ticket, {}, expected_revision=0,
                                                    create_missing=True).health.reason, "invalid_metadata")
        self.assertFalse(self.path.exists())

    # ------------------------------------------------------------- transitions
    def test_single_team_unknown_transitions_never_keep_or_resurrect_opponent_rank(self):
        self.assertEqual(self.save(self.recognized).status, "saved")
        # TEAM with an opponent rank is rejected, not silently trimmed.
        self.assertEqual(self.save(replace(self.recognized, match_format="team"), 1).health.reason,
                         "invalid_metadata")
        self.assertEqual(self.current(), replace(self.recognized, revision=1))
        team = MatchSnapshot("A", "ranked", "team", "A4", None, "recognized", "rank-interpretation-1")
        self.assertEqual(self.save(team, 1).snapshots[0].revision, 2)
        self.assertEqual(self.sql("SELECT opponent_rank FROM match_snapshots"), [(None,)])
        unknown = replace(team, match_format="unknown")
        self.assertEqual(self.save(unknown, 2).snapshots[0].revision, 3)
        single = replace(team, match_format="single")
        self.assertEqual(self.save(single, 3).snapshots[0].revision, 4)
        self.assertIsNone(self.current().opponent_rank, "an old opponent rank must not come back")
        self.assertEqual(self.sql("SELECT count(*) FROM match_snapshots"), [(1,)])

    def test_a_failed_read_claims_no_old_facts(self):
        self.assertEqual(self.save(self.recognized).status, "saved")
        # Keeping the old facts under a failed status is refused...
        self.assertEqual(self.save(replace(self.recognized, recognition_status="failed", revision=1)).health.reason,
                         "invalid_metadata")
        # ...so recording the failure replaces the snapshot with no claimed values.
        failed = MatchSnapshot("A", recognition_status="failed", recognition_version="rank-interpretation-2")
        self.assertEqual(self.save(failed, 1).snapshots[0].revision, 2)
        self.assertEqual(self.current(), replace(failed, revision=2))

    # ------------------------------------------------------------- revisions
    def test_revision_cas_rejects_stale_writers_across_a_b_a_cycles(self):
        self.assertEqual(self.save(self.recognized).snapshots[0].revision, 1)
        stale = self.current()
        other = replace(stale, match_format="team", opponent_rank=None)
        self.assertEqual(self.save(other, 1).snapshots[0].revision, 2)
        self.assertEqual(self.save(self.recognized, 2).snapshots[0].revision, 3)  # back to A
        before = self.path.read_bytes()
        for target in (replace(stale, self_rank="A3"), stale):  # an identical target is no exception
            with self.subTest(target=target):
                self.assertEqual(self.save(target, 1).health.reason, "snapshot_conflict")
                self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.save(self.recognized, 0).health.reason, "snapshot_conflict")
        self.assertEqual(self.save(self.recognized, 4).health.reason, "snapshot_conflict")

    def test_only_an_unchanged_target_at_the_current_revision_is_a_no_op(self):
        self.assertEqual(self.save(MatchSnapshot("A")).snapshots, (MatchSnapshot("A"),))
        self.assertFalse(self.sql("SELECT * FROM match_snapshots"), "an unknown target stores nothing")
        self.assertEqual(self.save(self.recognized).snapshots[0].revision, 1)
        before = self.path.read_bytes()
        result = self.save(self.recognized, 1)
        self.assertEqual((result.status, result.snapshots[0].revision), ("saved", 1))
        self.assertEqual(self.path.read_bytes(), before)
        # Two writers at one revision: the first advances it, the second is stale.
        first = self.save(replace(self.recognized, self_rank="A3"), 1)
        second = self.save(replace(self.recognized, self_rank="A3"), 1)
        self.assertEqual((first.snapshots[0].revision, second.health.reason), (2, "snapshot_conflict"))

    def test_revision_bounds_and_stored_corruption(self):
        self.assertEqual(self.save(self.recognized).status, "saved")
        self.sql("UPDATE match_snapshots SET revision=?", (storage._MAX_REVISION,))
        before = self.path.read_bytes()
        self.assertEqual(self.save(MatchSnapshot("A"), storage._MAX_REVISION).health.reason, "revision_exhausted")
        self.assertEqual(self.path.read_bytes(), before)
        connection = sqlite3.connect(self.path)
        with connection:
            connection.execute("PRAGMA ignore_check_constraints=ON")
            connection.execute("UPDATE match_snapshots SET revision=1, recognition_status='partial'")
        connection.close()
        before = self.path.read_bytes()
        self.assertEqual(self.service.lookup_snapshot("A").health.reason, "invalid_metadata")
        # The category view reads the same row, so it fails closed as well.
        self.assertEqual(self.service.lookup_metadata("A").health.reason, "invalid_metadata")
        self.assertEqual(self.save(MatchSnapshot("A"), 1).health.reason, "invalid_metadata")
        self.assertEqual(self.path.read_bytes(), before)

    def test_failed_snapshot_write_rolls_back_binding_and_values(self):
        connect = sqlite3.connect
        reached = []
        history = (self.root / "history.db").read_bytes()
        class Fault(sqlite3.Connection):
            def execute(connection, sql, *args):
                result = super().execute(sql, *args)
                if sql.startswith(("INSERT INTO match_snapshots VALUES", "UPDATE match_snapshots SET revision")):
                    reached.append(connection.execute(
                        "SELECT (SELECT count(*) FROM match_bindings),(SELECT count(*) FROM match_snapshots)").fetchone())
                    raise OSError("after real snapshot write")
                return result
        with patch.object(sqlite3, "connect", side_effect=lambda *a, **kw: connect(*a, **kw, factory=Fault)):
            self.assertEqual(self.save(self.recognized).status, "unavailable")
        self.assertEqual(reached, [(1, 1)])
        self.assertEqual(self.sql("SELECT * FROM match_bindings"), [])
        self.assertEqual(self.sql("SELECT * FROM match_snapshots"), [])
        self.assertEqual(self.save(self.recognized).status, "saved")
        before = self.path.read_bytes()
        with patch.object(sqlite3, "connect", side_effect=lambda *a, **kw: connect(*a, **kw, factory=Fault)):
            self.assertEqual(self.save(replace(self.recognized, self_rank="A1"), 1).status, "unavailable")
        self.assertEqual(reached[-1], (1, 1))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.current(), replace(self.recognized, revision=1))
        self.assertEqual((self.root / "history.db").read_bytes(), history)

    # ------------------------------------------------------------- migration
    def test_v2_migrates_categories_only_and_v2_writes_stay_v2(self):
        self.history.record_result("B", "loss", "fixture", {}, created_at=3)
        self.build(2, metadata=[("A", "custom", "team")])
        # The #15-1 v2 contract is unchanged until an explicit upgrade.
        self.assertEqual(self.service.save_metadata(self.ticket, MatchMetadata("A", "ranked", "team"),
                                                    expected=MatchMetadata("A", "custom", "team")).status, "saved")
        self.assertEqual(self.service.lookup_snapshot("A").health.reason, "migration_required")
        self.assertEqual(self.save(self.recognized, 0, False).health.reason, "migration_required")
        history = (self.root / "history.db").read_bytes()
        self.assertEqual(self.service.upgrade_storage().status, "ready")
        self.assertEqual(self.sql("PRAGMA user_version"), [(4,)])
        self.assertEqual(self.sql("SELECT * FROM match_snapshots"),
                         [("A", 1, "ranked", "team", None, None, None, None)])
        self.assertEqual(self.sql("SELECT * FROM maintenance_state"), [(1, "A")])
        self.assertEqual(self.current(), MatchSnapshot("A", "ranked", "team", revision=1))
        self.assertEqual(self.service.lookup_metadata("A").metadata, (MatchMetadata("A", "ranked", "team"),))
        # The category-only write cannot keep v3 evidence true, so v3 refuses it.
        before = self.path.read_bytes()
        self.assertEqual(self.service.save_metadata(self.ticket, MatchMetadata("A", "ranked", "single"),
                                                    expected=MatchMetadata("A", "ranked", "team")).health.reason,
                         "snapshot_required")
        self.assertEqual(self.path.read_bytes(), before)
        upgraded = self.path.read_bytes()
        self.assertEqual(self.service.upgrade_storage().status, "ready")
        self.assertEqual(self.path.read_bytes(), upgraded)
        self.assertEqual((self.root / "history.db").read_bytes(), history)

    def test_v1_migrates_to_v3_without_evidence(self):
        self.build(1)
        self.assertEqual(self.service.lookup_metadata("A").metadata, (MatchMetadata("A"),))
        self.assertEqual(self.service.save_metadata(self.ticket, MatchMetadata("A", "ranked"),
                                                    expected=MatchMetadata("A")).health.reason, "snapshot_required")
        self.assertEqual(self.service.upgrade_storage().status, "ready")
        self.assertEqual(self.sql("PRAGMA user_version"), [(4,)])
        self.assertEqual(self.sql("SELECT * FROM match_snapshots"), [])
        self.assertEqual(self.current(), MatchSnapshot("A"))

    def test_migration_failure_after_each_real_mutation_restores_the_source(self):
        steps = {1: (storage._SNAPSHOTS_DDL, "PRAGMA user_version=4"),
                 2: (storage._SNAPSHOTS_DDL,
                     "INSERT INTO match_snapshots (event_id,revision,match_type,match_format) "
                     "SELECT event_id,1,match_type,match_format FROM match_metadata",
                     "DROP TABLE match_metadata", "PRAGMA user_version=4")}
        connect = sqlite3.connect
        for version, targets in steps.items():
            for target in targets:
                with self.subTest(version=version, target=target[:40]):
                    self.build(version, metadata=[("A", "ranked", "single")] if version == 2 else ())
                    before = self.path.read_bytes()
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
                    self.assertEqual(self.sql("PRAGMA user_version"), [(version,)])
                    self.assertFalse(list(self.root.glob("*journal")))
                    self.path.unlink()

    def test_real_expiry_after_all_migration_mutations_rolls_back(self):
        self.build(2, metadata=[("A", "ranked", "single")])
        before = self.path.read_bytes()
        original = self.service._store._validate
        reached = []
        deadline = storage._Deadline()
        with ExitStack() as clocks:
            def after_upgrade(connection):
                result = original(connection)
                if connection.execute("PRAGMA user_version").fetchone()[0] == 4:
                    reached.append(connection.execute("SELECT count(*) FROM match_snapshots").fetchone())
                    deadline.end = 0
                    clocks.enter_context(real_clock())
                return result
            with patch.object(self.service._store, "_validate", side_effect=after_upgrade):
                with self.assertRaisesRegex(storage._Unavailable, "^deadline$"):
                    self.service._store.upgrade(deadline)
        self.assertEqual(reached, [(1,)])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.sql("PRAGMA user_version"), [(2,)])
        self.assertEqual(self.sql("SELECT * FROM match_metadata"), [("A", "ranked", "single")])

    def test_invalid_v2_source_rows_abort_the_migration(self):
        self.build(2, metadata=[("A", "ranked", "single")])
        connection = sqlite3.connect(self.path)
        with connection:
            connection.execute("PRAGMA ignore_check_constraints=ON")
            connection.execute("UPDATE match_metadata SET match_type='inferred'")
        connection.close()
        before = self.path.read_bytes()
        self.assertEqual(self.service.upgrade_storage().status, "unavailable")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.sql("PRAGMA user_version"), [(2,)])

    # ------------------------------------------------------------- authority
    def test_parent_witness_deletion_cleanup_and_late_commit(self):
        self.assertEqual(self.save(self.recognized).status, "saved")
        self.history.discard_result("A")
        self.assertEqual(self.service.lookup_snapshot("A").snapshots, ())
        self.assertEqual(self.save(self.recognized, 1, False).status, "unknown")
        self.history.record_result("A", "win", "fixture", {}, created_at=4)
        self.assertEqual(self.service.lookup_snapshot("A").health.reason, "identity_mismatch")
        self.assertEqual(self.save(self.recognized, 1, False).health.reason, "identity_mismatch")
        self.assertEqual(self.service.cleanup_step().deleted, 1)
        self.assertEqual(self.sql("SELECT * FROM match_snapshots"), [])
        self.assertEqual(self.history.lifetime_summary()["wins"], 1)
        ticket = self.service.prepare_binding("A").ticket
        original = self.service._store.save_snapshot
        committed = []
        def late(*args):
            self.history.discard_result("A")
            self.service.invalidate_history()
            result = original(*args)
            committed.extend(self.sql("SELECT event_id FROM match_snapshots"))
            return result
        with patch.object(self.service._store, "save_snapshot", side_effect=late):
            self.assertEqual(self.service.save_snapshot(ticket, self.recognized, expected_revision=0).status, "rejected")
        self.assertEqual(committed, [("A",)])
        self.assertEqual(self.service.lookup_snapshot("A").snapshots, ())
        self.assertEqual(self.service.cleanup_step().deleted, 1)
        self.assertEqual(self.sql("SELECT * FROM match_snapshots"), [])

    def test_stale_ticket_publication_and_unknown_authority_never_expose(self):
        self.assertEqual(self.save(self.recognized).status, "saved")
        self.service.invalidate_history()
        self.assertEqual(self.save(self.recognized, 1, False).health.reason, "stale")
        self.ticket = self.service.prepare_binding("A").ticket
        original = self.service._store.read_snapshots
        def revoke(*args):
            rows = original(*args)
            self.service.invalidate_history()
            return rows
        with patch.object(self.service._store, "read_snapshots", side_effect=revoke):
            self.assertEqual(self.service.lookup_snapshot("A").status, "rejected")
        self.ticket = self.service.prepare_binding("A").ticket  # the revocation staled the previous one
        with patch.object(self.service, "_parents", side_effect=storage._Unavailable("authority_unavailable")):
            self.assertEqual(self.service.lookup_snapshot("A").snapshots, ())
            self.assertEqual(self.save(self.recognized, 1, False).health.reason, "authority_unavailable")
        history = self.root / "history.db"
        connection = sqlite3.connect(history)
        with connection:
            connection.execute("PRAGMA user_version=5")
        connection.close()
        self.assertEqual(self.service.lookup_snapshot("A").snapshots, ())
        self.assertEqual(self.sql("SELECT count(*) FROM match_snapshots"), [(1,)])

    def test_bound_event_without_snapshot_and_batch_bound(self):
        self.history.record_result("B", "loss", "fixture", {}, created_at=3)
        self.assertEqual(self.save(self.recognized).status, "saved")
        self.assertEqual(self.service.save_binding(self.service.prepare_binding("B").ticket).status, "saved")
        rows = sorted(self.service.lookup_snapshot_many(["A", "B", "missing"]).snapshots, key=lambda s: s.event_id)
        self.assertEqual(rows, [replace(self.recognized, revision=1), MatchSnapshot("B")])
        self.assertEqual(self.service.lookup_snapshot_many([f"k{i}" for i in range(65)]).health.reason,
                         "invalid_arguments")

    def test_dormant_service_touches_nothing(self):
        disabled = OptionalEnrichmentService(self.root)
        with patch.object(sqlite3, "connect", side_effect=AssertionError("unexpected SQLite")), \
                patch.object(Path, "stat", side_effect=AssertionError("unexpected stat")):
            for response in (disabled.lookup_snapshot("A"), disabled.upgrade_storage(),
                             disabled.save_snapshot(self.ticket, self.recognized, expected_revision=0,
                                                    create_missing=True)):
                self.assertEqual(response.health.state, "disabled")
        self.assertFalse(self.path.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
