"""T0: independent evidence, Option B, deletion races and exact v4 integrity.

Structured inputs are persistence evidence, never invented OCR ground truth.
"""
import math
import sqlite3
import sys
import threading
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
from optional_enrichment import OptionalEnrichmentService, RankRatingObservation, MatchSnapshot


class ObservationTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(fixture_clock())
        self.directory = self.enterContext(tempfile.TemporaryDirectory(prefix='ac6-observation-t0-'))
        self.root = Path(self.directory)
        self.history = HistoryStore(self.root)
        self.history.start_session(started_at=1)
        self.history.record_result('A', 'win', 'fixture', {}, created_at=2.125)
        self.history.close_session(ended_at=3)
        self.history.start_session(started_at=4)
        self.service = OptionalEnrichmentService(self.root, active=True)
        self.path = self.root / 'enrichment.db'
        self.value = RankRatingObservation('O', 2.25, 'A4', 'pre_s', '001,250 / 進行', 'recognized', 'fixture.v1', 'fixture')

    def sql(self, query, values=()):
        connection = sqlite3.connect(self.path)
        try:
            with connection:
                return connection.execute(query, values).fetchall()
        finally:
            connection.close()

    def prepare(self, identity='O', link=None, service=None):
        result = (service or self.service).prepare_observation(identity, match_event_id=link, create_missing=True)
        self.assertEqual(result.status, 'prepared', result)
        return result.observation_ticket

    def save(self, value=None, link=None):
        value = value or self.value
        result = self.service.save_observation(self.prepare(value.observation_id, link), value)
        self.assertEqual(result.status, 'saved', result)
        return result

    def read(self):
        response = self.service.lookup_observation('O')
        self.assertEqual(response.status, 'visible', response)
        return response.observations[0]

    def build(self, version):
        for table in storage._SCHEMAS[version]:
            self.sql(storage._TABLES[table][0])
        self.sql("INSERT INTO maintenance_state VALUES (1,'A')")
        binding = self.service.prepare_binding('A').ticket.binding.values()
        self.sql('INSERT INTO match_bindings VALUES (?,?,?,?)', binding)
        if version == 2:
            self.sql("INSERT INTO match_metadata VALUES ('A','ranked','single')")
        if version == 3:
            self.sql("INSERT INTO match_snapshots VALUES ('A',7,'ranked','single','A4','S','recognized','fixture.v1')")
        self.sql(f'PRAGMA application_id={storage._APPLICATION_ID}')
        self.sql(f'PRAGMA user_version={version}')

    def test_identity_timestamp_precision_and_lossless_value(self):
        history = (self.root / 'history.db').read_bytes()
        self.save()
        self.assertEqual(self.read().observation, self.value)
        self.assertEqual(self.sql('PRAGMA user_version'), [(4,)])
        self.assertEqual(self.sql('SELECT * FROM observation_state'), [(1, 0, None)])
        self.assertEqual((self.root / 'history.db').read_bytes(), history)
        self.save(replace(self.value, observation_id='same-time', self_rank='A'))
        self.assertEqual(self.sql('SELECT count(*) FROM observations'), [(2,)])
        self.assertEqual(self.service.lookup_observation('same-time').observations[0].observation.self_rank, 'A')
        # Observation identity is not a timestamp, match identity or session id.
        self.assertEqual(self.sql('PRAGMA foreign_key_list(observations)'), [])
        self.assertNotIn('season_id', [row[1] for row in self.sql('PRAGMA table_info(observations)')])

    def test_unlinked_observation_needs_no_authoritative_database(self):
        (self.root / 'history.db').unlink()
        self.save()
        self.assertEqual(self.read().observation, self.value)
        self.assertEqual(self.read().association_status, 'none')

    def test_exact_retry_identity_conflict_and_no_relink_after_cleanup(self):
        self.save(link='A')
        self.assertEqual(self.read().association_status, 'valid')
        self.history.discard_result('A')
        self.assertEqual(self.service.cleanup_observation_associations().deleted, 1)
        self.history.record_result('A', 'win', 'fixture', {}, created_at=2.125)
        self.save(link='A')
        self.assertEqual(self.read().association_status, 'none')
        changed = replace(self.value, rating_value='different')
        self.assertEqual(self.service.save_observation(self.prepare(), changed).health.reason, 'observation_conflict')
        self.assertEqual(self.read().observation, self.value)

    def test_individual_date_and_full_match_deletions_only_remove_context(self):
        for mode in ('single', 'date', 'all'):
            with self.subTest(mode=mode):
                self.save(link='A')
                if mode == 'single':
                    self.history.discard_result('A')
                elif mode == 'date':
                    self.history.purge_before(3)
                else:
                    self.history.purge_all()
                self.assertEqual(self.read().observation, self.value)
                self.assertEqual(self.read().association_status, 'invalid')
                self.assertIsNone(self.read().association)
                self.assertEqual(self.sql('SELECT count(*) FROM observation_links'), [(1,)])
                self.assertEqual(self.service.cleanup_observation_associations().deleted, 1)
                self.assertEqual(self.service.cleanup_observation_associations().deleted, 0)
                self.assertEqual(self.read().observation, self.value)
                self.assertEqual(self.read().association_status, 'none')
                self.service.delete_observation_history()
                self.history.record_result('A', 'win', 'fixture', {}, created_at=2.125)
                self.history.close_session(ended_at=3)
                self.history.start_session(started_at=4)

    def test_witness_mismatch_and_match_binding_cleanup_cannot_cascade(self):
        ticket = self.service.prepare_binding('A').ticket
        self.assertEqual(self.service.save_snapshot(ticket, MatchSnapshot('A', 'ranked'),
                            expected_revision=0, create_missing=True).status, 'saved')
        self.save(link='A')
        self.history.discard_result('A')
        self.history.record_result('A', 'win', 'fixture', {}, created_at=8)
        self.assertEqual(self.read().association_status, 'invalid')
        self.assertEqual(self.service.cleanup_step().deleted, 1)
        self.assertEqual(self.read().observation, self.value)
        self.assertEqual(self.service.cleanup_observation_associations().deleted, 1)

    def test_unreadable_authority_is_not_destructive(self):
        self.save(link='A')
        before = self.path.read_bytes()
        with patch.object(self.service, '_parents', side_effect=storage._Unavailable('authority_unavailable')):
            self.assertEqual(self.read().association_status, 'unavailable')
            self.assertEqual(self.read().observation, self.value)
            self.assertEqual(self.service.cleanup_observation_associations().status, 'unavailable')
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.read().association_status, 'valid')

    def test_late_context_loss_does_not_discard_independent_evidence(self):
        ticket = self.prepare(link='A')
        self.history.discard_result('A')
        self.assertEqual(self.service.save_observation(ticket, self.value).status, 'saved')
        self.assertEqual(self.read().association_status, 'none')
        self.assertEqual(self.read().observation, self.value)

    def test_deleted_observations_revoke_writers_and_deferred_publication_across_instances(self):
        first_ticket = self.prepare()
        other = OptionalEnrichmentService(self.root, active=True)
        stale_ticket = self.prepare('queued', service=other)
        self.service.save_observation(first_ticket, self.value)
        stale_read = other.lookup_observation('O')
        match_ticket = self.service.prepare_binding('A').ticket
        self.service.save_snapshot(match_ticket, MatchSnapshot('A', 'ranked'), expected_revision=0)
        history = (self.root / 'history.db').read_bytes()
        purged = self.service.delete_observation_history()
        self.assertEqual((purged.status, purged.deleted), ('deleted', 1))
        self.assertEqual(self.service.save_observation(first_ticket, self.value).health.reason, 'stale_observation_generation')
        self.assertEqual(other.save_observation(stale_ticket, replace(self.value, observation_id='queued')).health.reason,
                         'stale_observation_generation')
        installs = []
        self.assertEqual(other.publish_observations(stale_read, installs.append).health.reason, 'stale_observation_generation')
        self.assertEqual(installs, [])
        self.assertEqual(self.sql('SELECT count(*) FROM observations'), [(0,)])
        self.assertEqual((self.root / 'history.db').read_bytes(), history)
        self.assertEqual(self.service.lookup_snapshot('A').snapshots[0].match_type, 'ranked')
        self.assertEqual(self.service.delete_observation_history().deleted, 0)
        self.save()
        # Reusing an id after deletion still cannot make the old response current.
        self.assertEqual(other.publish_observations(stale_read, installs.append).health.reason, 'stale_observation_generation')
        current = self.service.lookup_observation('O')
        self.assertEqual(self.service.publish_observations(current, installs.append).status, 'published')
        self.assertEqual(installs[0][0].observation, self.value)

    def test_publication_rechecks_optional_context_and_service_invalidation(self):
        self.save(link='A')
        response = self.service.lookup_observation('O')
        self.history.discard_result('A')
        installed = []
        self.assertEqual(self.service.publish_observations(response, installed.append).status, 'published')
        self.assertEqual(installed[0][0].association_status, 'invalid')
        response = self.service.lookup_observation('O')
        self.service.invalidate_history()
        self.assertEqual(self.service.publish_observations(response, installed.append).status, 'rejected')
        self.assertEqual(len(installed), 1)

    def test_failed_read_is_never_a_zero_or_carried_value(self):
        failed = replace(self.value, self_rank=None, rating_mode=None, rating_value=None, recognition_status='failed')
        self.save(failed)
        self.assertEqual(self.read().observation, failed)
        invalid = (replace(failed, rating_value='0', rating_mode='pre_s'), replace(failed, self_rank='A4'),
                   replace(failed, rating_mode='pre_s'), replace(failed, recognition_version=None))
        for value in invalid:
            self.assertNotEqual(self.service.save_observation(self.prepare(), value).status, 'saved')

    def test_invalid_semantics_preserve_database(self):
        ticket = self.prepare()
        invalid = [replace(self.value, observed_at=x) for x in (True, -1, math.inf, math.nan, '2')]
        invalid += [replace(self.value, self_rank=x) for x in ('a4', 'A5', 'S1', 'B\0junk')]
        invalid += [replace(self.value, rating_mode='s_rank'), replace(self.value, self_rank='S'),
                    replace(self.value, rating_mode=None), replace(self.value, rating_value=1250),
                    replace(self.value, rating_value=''), replace(self.value, rating_value='x'*129),
                    replace(self.value, rating_value='1\0junk'), replace(self.value, source=''),
                    replace(self.value, source='speculative source'), replace(self.value, recognition_status='partial'),
                    replace(self.value, recognition_status=None), replace(self.value, recognition_version='v\0bad')]
        before = self.path.read_bytes()
        for value in invalid:
            with self.subTest(value=value):
                self.assertNotEqual(self.service.save_observation(ticket, value).status, 'saved')
                self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.service.prepare_observation('', create_missing=True).status, 'rejected')

    def test_s_mode_rank_only_and_no_recorded_evidence(self):
        for value in (replace(self.value, self_rank='S', rating_mode='s_rank', rating_value='01234'),
                      replace(self.value, rating_mode=None, rating_value=None),
                      replace(self.value, recognition_status=None, recognition_version=None)):
            self.service.delete_observation_history() if self.path.exists() else None
            self.save(value)
            self.assertEqual(self.read().observation, value)

    def test_sql_constraints_and_corrupt_rows_fail_closed(self):
        self.save()
        invalid = ("UPDATE observations SET observed_at=-1", "UPDATE observations SET self_rank='A5'",
                   "UPDATE observations SET rating_mode='s_rank'", "UPDATE observations SET recognition_status='failed'",
                   "UPDATE observations SET rating_value='x'||char(0)||'y'", "UPDATE observations SET source=''",
                   "UPDATE observation_state SET generation=-1")
        with self.service._store.open(storage._Deadline(), write=True) as connection:
            for query in invalid:
                with self.subTest(query=query), self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(query)
        self.sql('PRAGMA ignore_check_constraints=ON')  # the pragma is connection-scoped
        connection = sqlite3.connect(self.path)
        with connection:
            connection.execute('PRAGMA ignore_check_constraints=ON')
            connection.execute("UPDATE observations SET self_rank='invented'")
        connection.close()
        self.assertEqual(self.service.lookup_observation('O').status, 'unavailable')

    def test_explicit_v1_v2_v3_migration_preserves_snapshot_and_has_no_backfill(self):
        for version in (1, 2, 3):
            with self.subTest(version=version):
                self.build(version)
                self.assertEqual(self.service.prepare_observation('O').health.reason, 'migration_required')
                self.assertEqual(self.service.upgrade_storage().status, 'ready')
                self.assertEqual(self.sql('PRAGMA user_version'), [(4,)])
                self.assertEqual(self.sql('SELECT count(*) FROM observations'), [(0,)])
                self.assertEqual(self.sql('SELECT * FROM observation_state'), [(1, 0, None)])
                if version == 3:
                    self.assertEqual(self.service.lookup_snapshot('A').snapshots[0].revision, 7)
                self.assertEqual(self.sql("SELECT sql FROM sqlite_schema WHERE name='observations'"), [(storage._OBSERVATIONS_DDL,)])
                before = self.path.read_bytes()
                self.assertEqual(self.service.upgrade_storage().status, 'ready')
                self.assertEqual(self.path.read_bytes(), before)
                self.path.unlink()

    def test_v3_migration_fault_after_every_real_mutation_is_atomic(self):
        connect = sqlite3.connect
        for target in (*storage._OBSERVATION_DDL, 'INSERT INTO observation_state VALUES (1,0,NULL)', 'PRAGMA user_version=4'):
            self.build(3)
            before, reached = self.path.read_bytes(), []
            class Fault(sqlite3.Connection):
                def execute(connection, query, *args):
                    result = super().execute(query, *args)
                    if query == target:
                        reached.append(query)
                        raise OSError('after actual mutation')
                    return result
            with patch.object(sqlite3, 'connect', side_effect=lambda *a, **kw: connect(*a, **kw, factory=Fault)):
                self.assertEqual(self.service.upgrade_storage().status, 'unavailable')
            self.assertEqual(reached, [target])
            self.assertEqual(self.path.read_bytes(), before)
            self.assertFalse(list(self.root.glob('*journal')))
            self.path.unlink()

    def test_real_migration_expiry_after_mutations_rolls_back(self):
        self.build(3)
        before, reached = self.path.read_bytes(), []
        original = self.service._store._validate
        deadline = storage._Deadline()
        with ExitStack() as clocks:
            def validate(connection):
                result = original(connection)
                if connection.execute('PRAGMA user_version').fetchone()[0] == 4:
                    reached.append(connection.execute('SELECT * FROM observation_state').fetchall())
                    deadline.end = 0
                    clocks.enter_context(real_clock())
                return result
            with patch.object(self.service._store, '_validate', side_effect=validate):
                with self.assertRaisesRegex(storage._Unavailable, '^deadline$'):
                    self.service._store.upgrade(deadline)
        self.assertEqual(reached, [[(1, 0, None)]])
        self.assertEqual(self.path.read_bytes(), before)

    def test_purge_fault_rolls_back_generation_links_and_observations(self):
        self.save(link='A')
        before, reached = self.path.read_bytes(), []
        connect = sqlite3.connect
        class Fault(sqlite3.Connection):
            def execute(connection, query, *args):
                result = super().execute(query, *args)
                if query.startswith('UPDATE observation_state SET generation='):
                    reached.append(connection.execute('SELECT count(*) FROM observations').fetchone())
                    raise OSError('after deletion and generation mutation')
                return result
        with patch.object(sqlite3, 'connect', side_effect=lambda *a, **kw: connect(*a, **kw, factory=Fault)):
            self.assertEqual(self.service.delete_observation_history().status, 'unavailable')
        self.assertEqual(reached, [(0,)])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.read().association_status, 'valid')

    def test_missing_corrupt_future_and_locked_stores_never_report_deleted(self):
        self.assertEqual(self.service.delete_observation_history().status, 'unavailable')
        for value in (b'', b'foreign store'):
            self.path.write_bytes(value)
            self.assertEqual(self.service.delete_observation_history().status, 'unavailable')
            self.assertEqual(self.path.read_bytes(), value)
            self.path.unlink()
        self.save()
        before = self.path.read_bytes()
        lock = sqlite3.connect(self.path)
        try:
            lock.execute('BEGIN EXCLUSIVE')
            self.assertEqual(self.service.delete_observation_history().status, 'unavailable')
            self.assertEqual(self.path.read_bytes(), before)
        finally:
            lock.rollback()
            lock.close()
        self.sql('PRAGMA user_version=5')
        before = self.path.read_bytes()
        self.assertEqual(self.service.delete_observation_history().status, 'unavailable')
        self.assertEqual(self.path.read_bytes(), before)

    def test_corrupt_v3_carried_data_is_rejected_before_migration(self):
        self.build(3)
        connection = sqlite3.connect(self.path)
        with connection:
            connection.execute('PRAGMA ignore_check_constraints=ON')
            connection.execute("UPDATE match_snapshots SET self_rank='invented'")
        connection.close()
        before = self.path.read_bytes()
        self.assertEqual(self.service.upgrade_storage().status, 'unavailable')
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.sql('PRAGMA user_version'), [(3,)])

    def test_insert_and_link_fault_are_one_transaction(self):
        ticket = self.prepare(link='A')
        before, reached = self.path.read_bytes(), []
        connect = sqlite3.connect
        class Fault(sqlite3.Connection):
            def execute(connection, query, *args):
                result = super().execute(query, *args)
                if query.startswith('INSERT INTO observation_links'):
                    reached.append(connection.execute('SELECT count(*) FROM observations').fetchone())
                    raise OSError('after both actual inserts')
                return result
        with patch.object(sqlite3, 'connect', side_effect=lambda *a, **kw: connect(*a, **kw, factory=Fault)):
            self.assertEqual(self.service.save_observation(ticket, self.value).status, 'unavailable')
        self.assertEqual(reached, [(1,)])
        self.assertEqual(self.path.read_bytes(), before)

    def test_bounded_association_cleanup_eventually_finishes(self):
        for index in range(70):
            self.save(replace(self.value, observation_id=f'O{index:03d}'), link='A')
        self.history.purge_all()
        counts = []
        for _ in range(3):
            response = self.service.cleanup_observation_associations()
            self.assertEqual(response.status, 'cleaned')
            self.assertLessEqual(response.examined, 64)
            counts.append(response.deleted)
        self.assertEqual(counts, [64, 6, 0])
        self.assertEqual(self.sql('SELECT count(*) FROM observations'), [(70,)])

    def test_corrupt_evidence_and_orphan_links_cannot_report_deleted(self):
        for broken in ('evidence', 'orphan'):
            with self.subTest(broken=broken):
                self.save(link='A')
                connection = sqlite3.connect(self.path)
                with connection:
                    if broken == 'evidence':
                        connection.execute('PRAGMA ignore_check_constraints=ON')
                        connection.execute("UPDATE observations SET self_rank='invented'")
                    else:
                        # Foreign writer with SQLite default FK OFF.
                        connection.execute('DELETE FROM observations')
                connection.close()
                before = self.path.read_bytes()
                self.assertEqual(self.service.delete_observation_history().status, 'unavailable')
                self.assertEqual(self.path.read_bytes(), before)
                self.assertEqual(self.sql('SELECT generation FROM observation_state'), [(0,)])
                self.path.unlink()

    def test_publication_writer_barrier_serializes_other_service_purge(self):
        self.save()
        response = self.service.lookup_observation('O')
        other = OptionalEnrichmentService(self.root, active=True)
        entered, release = threading.Event(), threading.Event()
        results = []
        def consumer(views):
            entered.set()
            if not release.wait(5):
                raise AssertionError('test owner failed to release consumer')
            results.extend(views)
        publications = []
        worker = threading.Thread(target=lambda: publications.append(
            self.service.publish_observations(response, consumer)))
        worker.start()
        try:
            self.assertTrue(entered.wait(5), 'owned worker did not reach barrier')
            self.assertEqual(other.delete_observation_history().health.reason, 'busy')
        finally:
            release.set()
            worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(publications[0].status, 'published')
        self.assertEqual(len(results), 1)
        self.assertEqual(other.delete_observation_history().status, 'deleted')
        self.assertEqual(self.service.publish_observations(response, results.extend).health.reason,
                         'stale_observation_generation')
        self.assertEqual(len(results), 1)

    def test_dormant_methods_touch_nothing(self):
        disabled = OptionalEnrichmentService(self.root)
        with patch.object(sqlite3, 'connect', side_effect=AssertionError('unexpected DB access')):
            for response in (disabled.prepare_observation('O', create_missing=True), disabled.lookup_observation('O'),
                             disabled.save_observation(None, self.value), disabled.delete_observation_history(),
                             disabled.cleanup_observation_associations(), disabled.publish_observations(None, lambda x: None)):
                self.assertEqual(response.health.state, 'disabled')
        self.assertFalse(self.path.exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
