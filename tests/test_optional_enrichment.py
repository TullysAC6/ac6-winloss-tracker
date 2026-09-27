"""T0: non-vacuous identity, cleanup and publication contracts; no server."""
import ast
import math
import sqlite3
import struct
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from history_store import HistoryStore
from optional_enrichment import OptionalEnrichmentService
import enrichment_store as storage


class FacadeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="ac6-facade-t0-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.history = HistoryStore(self.root)
        self.history.start_session(started_at=1)
        self.service = OptionalEnrichmentService(self.root, active=True)

    def add(self, key="A", result="win", when=2.125):
        self.history.record_result(key, result, "test", {}, created_at=when)
        prepared = self.service.prepare_binding(key)
        self.assertEqual(prepared.status, "prepared", prepared)
        saved = self.service.save_binding(prepared.ticket, create_missing=True)
        self.assertEqual(saved.status, "saved", saved)
        return prepared.ticket

    def raw(self):
        connection = sqlite3.connect(self.root / "enrichment.db")
        try:
            return connection.execute("SELECT event_id FROM match_bindings ORDER BY event_id").fetchall()
        finally:
            connection.close()

    def visible(self, key="A"):
        return tuple(row.event_id for row in self.service.lookup(key).bindings)

    def assert_visible(self, reader, key, expected):
        self.assertEqual(tuple(reader(key)), expected)

    def raw_reader(self, key):
        return tuple(row[0] for row in self.raw() if row[0] == key)

    def integer_reader(self, key):
        history = sqlite3.connect(self.root / "history.db")
        sidecar = sqlite3.connect(self.root / "enrichment.db")
        try:
            row = history.execute("SELECT id FROM matches WHERE event_id=?", (key,)).fetchone()
            return () if row is None else tuple(r[0] for r in sidecar.execute(
                "SELECT event_id FROM match_bindings WHERE rowid=?", row))
        finally:
            history.close()
            sidecar.close()

    def test_exact_witness_and_reuse_negative_controls(self):
        ticket = self.add()
        self.assertEqual(ticket.binding.parent_created_at_bits, struct.pack(">d", 2.125))
        self.history.discard_result("A")
        self.assert_visible(self.visible, "A", ())
        # Broken raw lookup is caught by the exact same parent-visibility oracle.
        with self.assertRaises(AssertionError):
            self.assert_visible(self.raw_reader, "A", ())
        # INTEGER PRIMARY KEY reuses 1 after removing the sole row.
        self.history.record_result("D", "win", "test", {}, created_at=3)
        with sqlite3.connect(self.root / "history.db") as connection:
            self.assertEqual(connection.execute("SELECT id FROM matches").fetchone(), (1,))
        connection.close()
        self.assert_visible(self.visible, "D", ())
        with self.assertRaises(AssertionError):
            self.assert_visible(self.integer_reader, "D", ())
        for outcome, when in (("loss", 2.125), ("win", math.nextafter(2.125, math.inf))):
            with self.subTest(outcome=outcome, when=when):
                self.history.record_result("A", outcome, "test", {}, created_at=when)
                self.assertEqual(self.service.lookup("A").health.reason, "identity_mismatch")
                self.assertEqual(self.service.save_binding(ticket).health.reason, "identity_mismatch")
                self.assert_visible(self.visible, "A", ())
                with self.assertRaises(AssertionError):
                    # This reader matches the current event ID but ignores its witness.
                    self.assert_visible(self.raw_reader, "A", ())
                self.history.discard_result("A")

    def test_cleanup_progress_restart_idempotence_and_insert_below_cursor(self):
        for index in range(130):
            self.add(f"K{index:03}", when=index + 2)
        self.history.discard_result("K129")
        first = self.service.cleanup_step()
        self.assertEqual((first.examined, first.deleted, first.pass_complete), (64, 0, False))
        self.add("A")
        self.history.discard_result("A")
        self.service = OptionalEnrichmentService(self.root, active=True)
        second = self.service.cleanup_step()
        third = self.service.cleanup_step()
        self.assertEqual((second.examined, third.examined, third.deleted), (64, 2, 1))
        self.assertTrue(third.pass_complete)
        # Cursor survives restart and a surviving prefix cannot starve the suffix.
        self.assertNotIn(("K129",), self.raw())
        self.assertIn(("A",), self.raw())
        self.assertEqual(self.service.cleanup_step().deleted, 1)
        for _ in range(3):
            self.assertEqual(self.service.cleanup_step().deleted, 0)

    def test_unavailable_authority_never_deletes_or_advances(self):
        self.add()
        before = (self.root / "enrichment.db").read_bytes()
        self.history.path.rename(self.root / "held-history.db")
        self.assertEqual(self.service.lookup("A").health.reason, "authority_unavailable")
        self.assertEqual(self.service.cleanup_step().health.reason, "authority_unavailable")
        self.assertEqual((self.root / "enrichment.db").read_bytes(), before)
        self.assertEqual(self.raw(), [("A",)])
        with patch.object(self.service, "_parents", return_value={}):
            self.assertEqual(self.service.cleanup_step().deleted, 1)
        with self.assertRaises(AssertionError):
            self.assertEqual(self.raw(), [("A",)])  # same preservation oracle catches the broken adapter

    def test_cleanup_failure_then_success_and_precommit_negative_control(self):
        self.add()
        # Rolled-back authoritative deletion must keep its binding.
        connection = sqlite3.connect(self.root / "history.db")
        connection.execute("DELETE FROM matches WHERE event_id='A'")
        self.assertEqual(self.service.cleanup_deleted(["A"]).deleted, 0)
        connection.rollback()
        connection.close()
        self.assertEqual(self.visible(), ("A",))
        # Execute the deliberately broken ordering, then feed the same visibility oracle.
        connection = sqlite3.connect(self.root / "history.db")
        connection.execute("DELETE FROM matches WHERE event_id='A'")
        with sqlite3.connect(self.root / "enrichment.db") as optional:
            optional.execute("DELETE FROM match_bindings WHERE event_id='A'")
        optional.close()
        connection.rollback()
        connection.close()
        with self.assertRaises(AssertionError):
            self.assert_visible(self.visible, "A", ("A",))
        self.assertEqual(self.service.save_binding(self.service.prepare_binding("A").ticket).status, "saved")
        self.history.discard_result("A")
        self.service.invalidate_history()
        with patch.object(storage._Store, "remove", side_effect=OSError("disk full")):
            self.assertEqual(self.service.cleanup_step().health.cleanup, "pending")
            self.assertEqual(self.visible(), ())
        self.assertEqual(self.raw(), [("A",)])
        self.assertEqual(self.service.cleanup_step().deleted, 1)
        self.assertEqual(self.service.cleanup_step().deleted, 0)

    def test_publication_and_tickets_revoke_without_waiting_for_io(self):
        ticket = self.add()
        entered, release = threading.Event(), threading.Event()
        responses = []
        original = self.service._publish
        def delayed(stamp, response):
            entered.set()
            self.assertTrue(release.wait(2))
            return original(stamp, response)
        with patch.object(self.service, "_publish", delayed):
            worker = threading.Thread(target=lambda: responses.append(self.service.lookup("A")))
            worker.start()
            try:
                self.assertTrue(entered.wait(2))
                self.history.discard_result("A")
                self.service.invalidate_history()  # I/O lock is still held by worker.
            finally:
                release.set()
                worker.join(3)
            self.assertFalse(worker.is_alive())
        self.assertEqual(responses[0].status, "rejected")
        self.assertEqual(responses[0].bindings, ())
        self.assertEqual(self.service.save_binding(ticket).status, "rejected")
        # Run the identical deletion barrier with deliberately broken admission.
        self.add()
        entered.clear()
        release.clear()
        responses.clear()
        def broken(stamp, response):
            entered.set()
            self.assertTrue(release.wait(2))
            return response
        with patch.object(self.service, "_publish", broken):
            worker = threading.Thread(target=lambda: responses.append(self.service.lookup("A")))
            worker.start()
            try:
                self.assertTrue(entered.wait(2))
                self.history.discard_result("A")
                self.service.invalidate_history()
            finally:
                release.set()
                worker.join(3)
            self.assertFalse(worker.is_alive())
        with self.assertRaises(AssertionError):
            self.assertEqual(responses[0].bindings, ())  # same oracle rejects stale admission
        other = OptionalEnrichmentService(self.root, active=True)
        self.assertEqual(other.save_binding(ticket).status, "rejected")

    def test_late_write_is_hidden_and_conditionally_removed(self):
        ticket = self.add()
        original = storage._Store.save
        def late(store, binding, deadline):
            self.history.discard_result("A")
            self.service.invalidate_history()
            return original(store, binding, deadline)
        with patch.object(storage._Store, "save", late):
            self.assertEqual(self.service.save_binding(ticket).status, "rejected")
        self.assertEqual(self.visible(), ())
        self.assertEqual(self.service.cleanup_step().deleted, 1)

    def test_bounds_and_dormant_dependency_boundary(self):
        self.add()
        for ids in (["A"] * 65, iter(["A"]), ["\0"], [123]):
            self.assertEqual(self.service.lookup_many(ids).status, "rejected")
        self.assertEqual(self.service.cleanup_deleted(["A"] * 65).status, "rejected")
        # No current runtime may even import the dormant foundation.
        for path in ROOT.glob("*.py*"):
            if path.name in ("enrichment_store.py", "optional_enrichment.py") or path.suffix not in (".py", ".pyw"):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
            for node in ast.walk(tree):
                names = ([node.module] if isinstance(node, ast.ImportFrom) else
                         [a.name for a in node.names] if isinstance(node, ast.Import) else [])
                self.assertTrue(all(name not in ("enrichment_store", "optional_enrichment") for name in names), path)
        # Unrelated future observations are a test-local model, never a v1 table.
        observations = {"independent": {"event_id": "missing", "rank": "fixture"}}
        before = dict(observations)
        self.service.cleanup_step()
        self.assertEqual(observations, before)
        original = storage._Store.remove
        def generic_sweep(store, *args, **kwargs):
            observations.clear()  # broken test-local extension sweeps an unrelated domain
            return original(store, *args, **kwargs)
        with patch.object(storage._Store, "remove", generic_sweep):
            self.service.cleanup_step()
        with self.assertRaises(AssertionError):
            self.assertEqual(observations, before)

    def test_operation_lock_deadline_and_malformed_authority(self):
        self.add()
        with self.service._operation_lock:
            started = time.monotonic()
            self.assertEqual(self.service.lookup("A").health.reason, "busy")
            self.assertLess(time.monotonic() - started, 1.0)  # generous OS scheduling tolerance
            self.service.invalidate_history()
        with sqlite3.connect(self.root / "history.db") as connection:
            connection.execute("PRAGMA user_version=4")
        connection.close()
        before = (self.root / "enrichment.db").read_bytes()
        self.assertEqual(self.service.cleanup_step().health.reason, "authority_unavailable")
        self.assertEqual((self.root / "enrichment.db").read_bytes(), before)

    def test_conditional_delete_does_not_remove_changed_witness(self):
        self.add()
        self.history.discard_result("A")
        original = storage._Store.remove
        def concurrent_change(store, *args, **kwargs):
            # Test-only corruption/race fixture; supported writers never rewrite a witness.
            with sqlite3.connect(self.root / "enrichment.db") as connection:
                connection.execute("UPDATE match_bindings SET parent_created_at_bits=? WHERE event_id='A'",
                                   (struct.pack(">d", 3.0),))
            connection.close()
            return original(store, *args, **kwargs)
        with patch.object(storage._Store, "remove", concurrent_change):
            self.assertEqual(self.service.cleanup_step().deleted, 0)
        self.assertEqual(self.raw(), [("A",)])
        self.assertEqual(self.service.cleanup_step().deleted, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
