"""T2: independent observations survive exact v3 and public v1.2.0 match deletion."""
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

import test_enrichment_rollback as rollback
from test_rollback_previous_version import PREVIOUS_VERSION, extract_previous

PUBLIC = 'c64b241c6b14b54bf7a4ac7897d3be42c8d7d9f4'
SEED_OBSERVATIONS = '''
        from optional_enrichment import RankRatingObservation
        for identity, link in (("obs-A","A"),("obs-C","C"),("obs-independent",None)):
            value = RankRatingObservation(identity, 10.125, "A4", "pre_s", "001,250", "recognized", "fixture.v1", "fixture")
            prepared = service.prepare_observation(identity, match_event_id=link)
            assert prepared.status == "prepared", prepared
            assert service.save_observation(prepared.observation_ticket, value).status == "saved"
'''
VERIFY_OBSERVATIONS = '''
        response = service.lookup_observations_many(["obs-A","obs-C","obs-independent"])
        assert len(response.observations) == 3, response
        assert all(v.observation.rating_value == "001,250" and v.observation.observed_at == 10.125 for v in response.observations)
        for view in response.observations:
            link = {"obs-A":"A", "obs-C":"C"}.get(view.observation.observation_id)
            assert view.association_status == ("none" if link is None else "valid" if link in expected else "invalid"), view
        cleaned = service.cleanup_observation_associations()
        assert cleaned.status == "cleaned" and cleaned.deleted == sum(key not in expected for key in ("A","C")), cleaned
        assert service.cleanup_observation_associations().deleted == 0
        assert len(service.lookup_observations_many(["obs-A","obs-C","obs-independent"]).observations) == 3
        stale = service.prepare_observation("obs-A").observation_ticket
        saved_value = response.observations[0].observation
        before_matches, before_snapshots = rows(), service.lookup_snapshot_many(["A","B","C"]).snapshots
        deleted = service.delete_observation_history()
        assert deleted.status == "deleted" and deleted.deleted == 3, deleted
        assert service.save_observation(stale, saved_value).health.reason == "stale_observation_generation"
        installed = []
        assert service.publish_observations(response, installed.extend).health.reason == "stale_observation_generation"
        assert not installed
        assert rows() == before_matches and service.lookup_snapshot_many(["A","B","C"]).snapshots == before_snapshots
        assert service.lookup_observations_many(["obs-A","obs-C","obs-independent"]).status == "unknown"
        assert service.delete_observation_history().deleted == 0
        state["observations_survived_and_explicit_purge_revoked_work"] = True
'''


def phase_script(public=False):
    script = rollback.PHASE
    seam = '    elif phase == "upgrade":'
    assert script.count(seam) == 1
    script = script.replace(seam, SEED_OBSERVATIONS + seam)
    start = script.index('        # The exact #15-1 build knows')
    end = script.index('        with dormant():', start)
    if public:
        script = script[:start] + script[end:]
    else:
        old = script[start:end].replace('enrichment_store._VERSION == 2', 'enrichment_store._VERSION == 3')
        script = script[:start] + old + script[end:]
    seam = '    elif phase == "bad":'
    assert script.count(seam) == 1
    return script.replace(seam, VERIFY_OBSERVATIONS + seam)


class ObservationRollbackTests(unittest.TestCase):
    phase = rollback.EnrichmentRollbackTests.phase
    profile = rollback.EnrichmentRollbackTests.profile

    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix='ac6-observation-t2-')
        cls.old_v3 = extract_previous(Path(cls.directory.name) / 'exact-v3', revision=PREVIOUS_VERSION)
        cls.public = extract_previous(Path(cls.directory.name) / 'public-v120', revision=PUBLIC)
        if cls.old_v3 is None or cls.public is None:
            cls.directory.cleanup()
            raise AssertionError('both pinned rollback sources are required')

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def roundtrip(self, source, scenario, public=False):
        root, data, port = self.profile(('public-' if public else 'v3-') + scenario)
        with patch.object(rollback, 'PHASE', phase_script(public)):
            seed = self.phase(rollback.ROOT, root, 'seed', scenario, port)
            untouched = (data / 'enrichment.db').read_bytes()
            old = self.phase(source, root, 'old', scenario, port)
            self.assertEqual((data / 'enrichment.db').read_bytes(), untouched)
            again = self.phase(rollback.ROOT, root, 'again', scenario, port)
        self.assertEqual(again['before'], old['after'])
        self.assertEqual(again['after'], old['after'])
        self.assertTrue(again['observations_survived_and_explicit_purge_revoked_work'])
        original = {row[0]: row for row in seed['after']}
        self.assertTrue(all(row == original[row[0]] for row in again['after'] if row[0] in original))

    def test_exact_v3_unaware_lifecycle(self):
        for scenario in ('record', 'undo', 'date', 'full', 'refuse', 'compensate'):
            with self.subTest(scenario=scenario):
                self.roundtrip(self.old_v3, scenario)

    def test_public_unaware_match_deletion(self):
        for scenario in ('date', 'full'):
            with self.subTest(scenario=scenario):
                self.roundtrip(self.public, scenario, public=True)


if __name__ == '__main__':
    unittest.main()
