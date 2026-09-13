"""purge_all must never leave history and stats disagreeing, or claim success.

Independent review of 1819f748 reproduced the defect this file now guards: a
committed database purge followed by a failing stats reset left
``removed_matches=1, session_reset=false, stats_wins=1, lifetime_wins=0`` and
still answered HTTP 200 with ``ok=true``.

``history.db`` and ``stats.json`` are separate stores, so there is no single
transaction across both and none is claimed here. What is asserted instead is
that every failure boundary lands on one of two self-consistent states —
nothing purged, or history intact with the session reset — that the failure is
reported as a failure, and that the state survives a restart.

Every test owns a fresh temporary LOCALAPPDATA. No server is started, no port is
bound, and the user's real data directory is never touched.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class PurgeAtomicityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="ac6-purge-atomicity-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.data = self.root / "AC6WinLossTracker"
        self.data.mkdir()
        patcher = patch.dict(os.environ, {"LOCALAPPDATA": str(self.root)})
        patcher.start()
        self.addCleanup(patcher.stop)

        import importlib
        import app_paths
        import config_utils
        importlib.reload(app_paths)
        importlib.reload(config_utils)
        self.assertEqual(config_utils.CONFIG_PATH.parent.resolve(), self.data.resolve())

        (self.data / "config.json").write_text(json.dumps({
            "config_version": config_utils.CONFIG_VERSION, "port": 8765,
            "stats_enabled": True, "result_detector_enabled": False,
            "effect_screenshot_enabled": False, "effect_enabled": True,
            "overlay_stats_scope": "session"}), encoding="utf-8")

        import history_store
        import stats_manager
        importlib.reload(history_store)
        importlib.reload(stats_manager)
        self.history = history_store.HistoryStore(self.data)
        self.history.start_session()
        self.stats = stats_manager.StatsManager(self.data)

        import server
        importlib.reload(server)
        self.server = server
        server.history = self.history
        server.stats = self.stats
        server.detector = None
        server.history_event_ids.clear()

        # One recorded WIN, exactly as the review's probe did.
        self.stats.add("win", "manual")
        self.history.record_result("seed-1", "win", "manual",
                                   {"streak": 1, "wins": 1, "losses": 0})
        server.history_event_ids.append("seed-1")
        self.assertEqual(self.stats.snapshot()["wins"], 1)
        self.assertEqual(self.history.lifetime_summary()["wins"], 1)

    # ------------------------------------------------------------------ helpers
    def observed(self):
        """Every place the user can see the two stores disagree."""
        on_disk = json.loads((self.data / "stats.json").read_text(encoding="utf-8"))
        return {
            "stats_wins": self.stats.snapshot()["wins"],
            "stats_json_wins": on_disk["wins"],
            "lifetime_wins": self.history.lifetime_summary()["wins"],
            "lifetime_matches": self.history.lifetime_summary()["matches"],
        }

    def assert_consistent(self, state, where):
        """Session totals may never exceed what lifetime history can justify."""
        self.assertEqual(state["stats_wins"], state["stats_json_wins"],
                         f"{where}: in-memory stats and stats.json disagree")
        self.assertLessEqual(
            state["stats_wins"], state["lifetime_wins"],
            f"{where}: session claims {state['stats_wins']} win(s) but lifetime "
            f"history only has {state['lifetime_wins']} — this is the defect")

    def reloaded_state(self):
        """What a restart would see: fresh objects over the same files."""
        import history_store
        import stats_manager
        store = history_store.HistoryStore(self.data)
        manager = stats_manager.StatsManager(self.data)
        return {"stats_wins": manager.snapshot()["wins"],
                "lifetime_wins": store.lifetime_summary()["wins"]}

    # ------------------------------------------------------------------ 1. before
    def test_failure_before_any_write_changes_nothing(self):
        before = self.observed()
        with patch.object(self.stats, "snapshot", side_effect=OSError("snapshot failed")):
            with self.assertRaises(OSError):
                self.server.purge_history("all")
        after = self.observed()
        self.assertEqual(before, after)
        self.assert_consistent(after, "pre-write failure")

    # ------------------------------------------------------------------ 2. stats
    def test_stats_reset_failure_leaves_history_intact_and_reports_failure(self):
        """The exact reproduction from the independent review."""
        with patch.object(self.stats, "reset", side_effect=OSError("stats reset failed")):
            with self.assertRaises(self.server.PurgeFailed) as caught:
                self.server.purge_history("all")
        failure = caught.exception
        self.assertEqual(failure.stage, "session reset")
        self.assertFalse(failure.history_cleared)

        state = self.observed()
        self.assert_consistent(state, "stats reset failure")
        self.assertEqual(state["lifetime_wins"], 1, "history must not have been purged")
        self.assertEqual(state["stats_wins"], 1, "session must be untouched")
        self.assertEqual(self.reloaded_state(), {"stats_wins": 1, "lifetime_wins": 1})

        payload = failure.payload()
        self.assertFalse(payload["history_cleared"])
        self.assertIn("履歴は削除していません", payload["error"])

    # ------------------------------------------------------------------ 3. db
    def test_database_purge_failure_restores_the_session(self):
        with patch.object(self.history, "purge_all", side_effect=OSError("commit failed")):
            with self.assertRaises(self.server.PurgeFailed) as caught:
                self.server.purge_history("all")
        failure = caught.exception
        self.assertEqual(failure.stage, "history purge")
        self.assertFalse(failure.history_cleared)
        self.assertTrue(failure.stats_restored, "the session reset must be undone")

        state = self.observed()
        self.assert_consistent(state, "database purge failure")
        self.assertEqual(state["lifetime_wins"], 1)
        self.assertEqual(state["stats_wins"], 1, "session must be restored")
        self.assertEqual(self.reloaded_state(), {"stats_wins": 1, "lifetime_wins": 1})

    def test_database_purge_failure_with_failed_restore_is_still_consistent(self):
        """Both writes fail: the surviving state is 'session reset', not corruption."""
        with patch.object(self.history, "purge_all", side_effect=OSError("commit failed")), \
             patch.object(self.stats, "restore", side_effect=OSError("restore failed")):
            with self.assertRaises(self.server.PurgeFailed) as caught:
                self.server.purge_history("all")
        failure = caught.exception
        self.assertFalse(failure.history_cleared)
        self.assertFalse(failure.stats_restored)

        state = self.observed()
        self.assert_consistent(state, "restore failure")
        self.assertEqual(state["stats_wins"], 0, "session was reset")
        self.assertEqual(state["lifetime_wins"], 1, "history survived")
        self.assertEqual(self.reloaded_state(), {"stats_wins": 0, "lifetime_wins": 1})
        self.assertIn("セッション成績のみ初期化", failure.payload()["error"])

    # ------------------------------------------------------------------ 4. sync
    def test_post_purge_synchronization_failure_is_reported_not_swallowed(self):
        with patch.object(self.server.result_gate, "lock_now",
                          side_effect=RuntimeError("gate failed")):
            with self.assertRaises(self.server.PurgeFailed) as caught:
                self.server.purge_history("all")
        failure = caught.exception
        self.assertEqual(failure.stage, "post-purge synchronization")
        self.assertTrue(failure.history_cleared, "both stores were already cleared")

        state = self.observed()
        self.assert_consistent(state, "synchronization failure")
        self.assertEqual(state["stats_wins"], 0)
        self.assertEqual(state["lifetime_wins"], 0)
        self.assertEqual(self.reloaded_state(), {"stats_wins": 0, "lifetime_wins": 0})
        self.assertIn("再起動", failure.payload()["error"])

    # ------------------------------------------------------------------ 5. success
    def test_success_clears_both_stores_and_recording_resumes(self):
        outcome = self.server.purge_history("all")
        self.assertTrue(outcome["session_reset"])
        self.assertEqual(outcome["mode"], "all")
        self.assertEqual(outcome["removed_matches"], 1)

        state = self.observed()
        self.assert_consistent(state, "success")
        self.assertEqual(state["stats_wins"], 0)
        self.assertEqual(state["lifetime_wins"], 0)
        self.assertEqual(self.reloaded_state(), {"stats_wins": 0, "lifetime_wins": 0})

        # The session still has a valid history row, so the next result records.
        self.stats.add("win", "manual")
        self.history.record_result("after-purge", "win", "manual",
                                   {"streak": 1, "wins": 1, "losses": 0})
        resumed = self.observed()
        self.assert_consistent(resumed, "after resuming")
        self.assertEqual(resumed["lifetime_wins"], 1)

    # ------------------------------------------------------------------ HTTP
    def test_failure_is_never_reported_as_http_success(self):
        """The wrapper at the handler must not turn a PurgeFailed into ok=true."""
        source = (ROOT / "server.py").read_text(encoding="utf-8")
        handler = source[source.index('if path == "/api/history/purge"'):]
        handler = handler[:handler.index("def ") if "def " in handler else len(handler)]
        self.assertIn("except PurgeFailed", handler)
        self.assertIn('"ok": False', handler)
        self.assertIn("500", handler)

    def test_purge_before_is_unchanged_by_the_coordination(self):
        outcome = self.server.purge_history("before", cutoff=0.0)
        self.assertEqual(outcome["mode"], "before")
        self.assertFalse(outcome["session_reset"])
        self.assertEqual(outcome["removed_matches"], 0, "cutoff 0 removes nothing")
        self.assert_consistent(self.observed(), "purge_before")


class PurgeRecoveryContractTests(PurgeAtomicityTests):
    """Review 5186945882: a failed purge must complete the session-reset contract.

    The reviewer drove the real handler and found that after case C -- purge and
    restore both failing -- ``history_event_ids`` still held the old match ID and
    no session event was published. A following ``/api/stats/undo`` returned
    ``removed=None`` (the reset session had nothing to undo) yet still deleted
    the retained history row, destroying the history the error said was kept.
    """

    def published(self):
        """Capture what a connected client would receive."""
        seen = []
        original = self.server.publish

        def spy(kind, payload, remember=True):
            seen.append((kind, payload))
            return original(kind, payload, remember=remember)

        return seen, spy

    # ---------------------------------------------------------------- case C
    def test_empty_session_undo_after_failed_purge_keeps_retained_history(self):
        """The reviewer's reproduction, as a regression test."""
        self.assertEqual(len(self.server.history_event_ids), 1,
                         "the seeded result must own an undo identity")
        with patch.object(self.history, "purge_all", side_effect=OSError("commit failed")), \
             patch.object(self.stats, "restore", side_effect=OSError("restore failed")):
            with self.assertRaises(self.server.PurgeFailed):
                self.server.purge_history("all")

        state = self.observed()
        self.assertEqual(state["stats_wins"], 0, "session was reset")
        self.assertEqual(state["lifetime_wins"], 1, "history was retained")
        self.assertEqual(list(self.server.history_event_ids), [],
                         "stale undo identities would let an empty session "
                         "delete the retained history")

        # The destructive step itself: undo from the reset session.
        _, removed = self.server.undo_result()
        self.assertIsNone(removed, "an empty session has nothing to undo")
        after = self.observed()
        self.assertEqual(after["lifetime_wins"], 1,
                         "undo after a failed purge must not delete retained history")
        self.assertEqual(self.reloaded_state(), {"stats_wins": 0, "lifetime_wins": 1})

    def test_case_c_publishes_the_reset_session_to_connected_clients(self):
        seen, spy = self.published()
        with patch.object(self.server, "publish", spy), \
             patch.object(self.history, "purge_all", side_effect=OSError("commit failed")), \
             patch.object(self.stats, "restore", side_effect=OSError("restore failed")):
            with self.assertRaises(self.server.PurgeFailed):
                self.server.purge_history("all")
        kinds = [kind for kind, _ in seen]
        self.assertIn("stats", kinds, "a reset session must be published")
        self.assertIn("lifetime", kinds)
        stats_payload = [p for k, p in seen if k == "stats"][-1]
        self.assertEqual(stats_payload["wins"], 0)
        self.assertEqual(stats_payload["streak"], 0)

    def test_case_c_locks_the_gate_and_notifies_the_detector(self):
        detector = Mock()
        self.server.detector = detector
        self.addCleanup(setattr, self.server, "detector", None)
        with patch.object(self.server.result_gate, "lock_now") as lock, \
             patch.object(self.history, "purge_all", side_effect=OSError("commit failed")), \
             patch.object(self.stats, "restore", side_effect=OSError("restore failed")):
            with self.assertRaises(self.server.PurgeFailed):
                self.server.purge_history("all")
        lock.assert_called_once()
        detector.external_mutation.assert_called_once()

    def test_recording_resumes_and_undo_is_scoped_to_it_after_case_c(self):
        with patch.object(self.history, "purge_all", side_effect=OSError("commit failed")), \
             patch.object(self.stats, "restore", side_effect=OSError("restore failed")):
            with self.assertRaises(self.server.PurgeFailed):
                self.server.purge_history("all")

        self.stats.add("win", "manual")
        self.history.record_result("after-failure", "win", "manual",
                                   {"streak": 1, "wins": 1, "losses": 0})
        self.server.history_event_ids.append("after-failure")
        self.assertEqual(self.observed()["lifetime_wins"], 2)

        # Undo now removes only the new result, never the retained one.
        self.server.undo_result()
        final = self.observed()
        self.assertEqual(final["lifetime_wins"], 1, "only the new result is undone")
        self.assertEqual(self.reloaded_state()["lifetime_wins"], 1)

    # ---------------------------------------------------------------- case D
    def test_case_d_publishes_the_reset_session_even_though_the_gate_failed(self):
        seen, spy = self.published()
        with patch.object(self.server, "publish", spy), \
             patch.object(self.server.result_gate, "lock_now",
                          side_effect=RuntimeError("gate failed")):
            with self.assertRaises(self.server.PurgeFailed) as caught:
                self.server.purge_history("all")
        self.assertTrue(caught.exception.history_cleared)
        kinds = [kind for kind, _ in seen]
        self.assertIn("stats", kinds,
                      "a connected overlay must not keep the old session value")
        stats_payload = [p for k, p in seen if k == "stats"][-1]
        self.assertEqual(stats_payload["wins"], 0)
        self.assertEqual(list(self.server.history_event_ids), [])
        self.assertEqual(self.reloaded_state(), {"stats_wins": 0, "lifetime_wins": 0})

    def test_case_d_undo_cannot_touch_history(self):
        with patch.object(self.server.result_gate, "lock_now",
                          side_effect=RuntimeError("gate failed")):
            with self.assertRaises(self.server.PurgeFailed):
                self.server.purge_history("all")
        _, removed = self.server.undo_result()
        self.assertIsNone(removed)
        self.assertEqual(self.observed()["lifetime_wins"], 0)

    # ---------------------------------------------------------------- case B
    def test_case_b_keeps_undo_identities_and_republishes_the_restored_session(self):
        seen, spy = self.published()
        with patch.object(self.server, "publish", spy), \
             patch.object(self.history, "purge_all", side_effect=OSError("commit failed")):
            with self.assertRaises(self.server.PurgeFailed) as caught:
                self.server.purge_history("all")
        self.assertTrue(caught.exception.stats_restored)
        self.assertEqual(list(self.server.history_event_ids), ["seed-1"],
                         "the restored session still owns its history row")
        stats_payload = [p for k, p in seen if k == "stats"][-1]
        self.assertEqual(stats_payload["wins"], 1,
                         "clients must see the restored value, not the brief zero")

        # Undo is the supported operation again, and removes exactly one row.
        _, removed = self.server.undo_result()
        self.assertIsNotNone(removed)
        self.assertEqual(self.observed()["lifetime_wins"], 0)

    # ---------------------------------------------------------------- case A
    def test_case_a_leaves_undo_ownership_untouched(self):
        with patch.object(self.stats, "reset", side_effect=OSError("stats reset failed")):
            with self.assertRaises(self.server.PurgeFailed):
                self.server.purge_history("all")
        self.assertEqual(list(self.server.history_event_ids), ["seed-1"])
        _, removed = self.server.undo_result()
        self.assertIsNotNone(removed, "the untouched session can still undo")
        self.assertEqual(self.observed()["lifetime_wins"], 0)

if __name__ == "__main__":
    unittest.main(verbosity=2)
