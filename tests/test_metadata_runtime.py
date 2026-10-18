"""T0: deterministic assisted association and real checked sidecar writes; no process/socket."""
import ast
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from metadata_runtime import MetadataRuntime, VERSION, feature_enabled
from history_store import HistoryStore
from optional_enrichment import OptionalEnrichmentService, MatchSnapshot
from enrichment_test_clock import fixture_clock
from lobby_capture import header_geometry
import preferences

TARGET = {"hwnd": 10, "pid": 20, "birth": 30,
          "client": {"left": -1920, "top": 10, "width": 1920, "height": 1080},
          "bounds": [-1920, 10, 0, 1090]}


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ac6-metadata-unit-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = 100.0
        self.on = True
        self.target = dict(TARGET)
        self.worker = Mock(side_effect=self.positive)
        self.runtime = MetadataRuntime(self.root, enabled=lambda: self.on,
            target=lambda: self.target, worker=self.worker, clock=lambda: self.now)
        self.addCleanup(self.runtime.shutdown)

    def positive(self, target, cancel):
        return {"status": "recognized", "match_type": "ranked", "match_format": "single",
                "version": VERSION, "source": "direct_header", "captured_at": self.now, "cleaned": True}

    def finish(self):
        task = self.runtime.task
        if task:
            task.join(3)  # Test lifecycle guard only; feature TTL uses the fake clock.
            self.assertFalse(task.is_alive())

    def acquire(self):
        reply = self.runtime.acquire()
        self.finish()
        self.assertEqual(self.runtime.status()["state"], "pending")
        return reply["request_id"]

    def result(self, event="one", result="win"):
        self.runtime.notify("result", result=result, event_id=event, decided_at=self.now, witness=(event,2,result))

    def history(self, result="win"):
        store = HistoryStore(self.root)
        store.start_session(started_at=1)
        store.record_result("one", result, "auto", {}, created_at=2)
        return store

    def test_off_and_invalid_preferences_do_no_optional_work(self):
        self.on = False
        self.assertEqual(self.runtime.acquire()["state"], "disabled")
        self.worker.assert_not_called()
        self.assertFalse((self.root / "enrichment.db").exists())
        self.assertFalse(preferences.DEFAULTS["match_metadata_detection"])
        for error in (OSError("bad"), ValueError("bad")):
            with patch.object(preferences, "load", side_effect=error):
                self.assertFalse(feature_enabled())
        with patch.object(preferences, "load", return_value={}):
            self.assertFalse(feature_enabled())

    def test_first_win_and_loss_consume_once_second_never_inherits(self):
        for outcome in ("win", "loss"):
            request = self.acquire()
            self.result(result=outcome)
            item = self.runtime.status()
            self.assertEqual((item["event_id"], item["result"], item["request_id"]), ("one", outcome, request))
            self.assertIsNone(self.runtime.pending)
            self.result("two")
            self.assertIsNone(self.runtime.candidate)
            self.assertEqual(self.runtime.confirm(request, "one", True)["state"], "rejected")

    def test_draw_without_row_consumes_and_cannot_leak_to_win(self):
        request = self.acquire()
        self.runtime.notify("result", result="draw")
        self.assertIsNone(self.runtime.pending)
        self.result()
        self.assertIsNone(self.runtime.candidate)
        self.assertEqual(self.runtime.confirm(request, "one", True)["state"], "rejected")
        self.assertFalse((self.root / "enrichment.db").exists())

    def test_ttls_are_monotonic_and_exact_boundaries(self):
        self.acquire()
        self.now = 700
        self.result()
        self.assertIsNone(self.runtime.candidate)
        request = self.acquire()
        self.result()
        self.now += 60
        self.assertEqual(self.runtime.confirm(request, "one", True)["state"], "rejected")
        self.now = 1000
        self.acquire()
        self.now = 1590
        self.result()
        self.assertEqual(self.runtime.candidate["expires"], 1600)

    def test_replacement_cancel_off_restart_and_identity_revoke(self):
        old = self.acquire()
        fresh = self.acquire()
        self.assertNotEqual(old, fresh)
        self.result()
        self.assertEqual(self.runtime.confirm(old, "one", True)["state"], "rejected")
        self.runtime.cancel()
        self.assertIsNone(self.runtime.candidate)
        self.acquire()
        self.target = {**TARGET, "birth": 31}  # Same PID/HWND reused by another process.
        self.assertEqual(self.runtime.status()["reason"], "target_changed")
        self.target = dict(TARGET)
        self.acquire()
        self.on = False
        self.assertEqual(self.runtime.status()["state"], "disabled")
        self.assertIsNone(self.runtime.pending)
        another = MetadataRuntime(self.root, enabled=lambda: True)
        self.assertIsNone(another.pending)

    def test_result_or_cancel_while_acquiring_rejects_late_completion(self):
        for action in (lambda: self.result(), self.runtime.cancel,
                       lambda: self.runtime.notify("result", result="draw")):
            started, release = threading.Event(), threading.Event()
            def blocked(target, cancel):
                started.set()
                release.wait(3)
                return self.positive(target, cancel)
            self.worker.side_effect = blocked
            self.runtime.acquire()
            self.assertTrue(started.wait(3))
            self.assertEqual(self.runtime.acquire()["state"], "busy")
            action()
            release.set()
            self.finish()
            self.assertIsNone(self.runtime.pending)
            self.assertIsNone(self.runtime.candidate)

    def test_acquisition_gap_requires_capture_strictly_after_latest_gap(self):
        # Barrier controls completion; native capture and gap use the fake
        # monotonic clock. No sleep/headroom decides which evidence is valid.
        for captured, gaps, expected in (
                (101.0, (100.0,), "pending"),
                (99.5, (100.0,), "unknown"),
                (100.0, (100.0,), "unknown"),
                (100.5, (100.0, 101.0), "unknown"),
                (101.5, (100.0, 101.0), "pending")):
            with self.subTest(captured=captured, gaps=gaps):
                self.now = 100.0
                started, release = threading.Event(), threading.Event()
                def blocked(target, cancel):
                    started.set()
                    self.assertTrue(release.wait(3))
                    return {**self.positive(target, cancel), "captured_at": captured}
                self.worker.side_effect = blocked
                self.runtime.acquire()
                try:
                    self.assertTrue(started.wait(3))
                    for gap in gaps:
                        self.now = gap
                        self.runtime.notify("capture_gap")
                        self.assertEqual(self.runtime.state, "acquiring")
                        self.assertFalse(self.runtime.cancelled.is_set())
                    self.now = max(captured, gaps[-1]) + .1
                finally:
                    release.set()
                    self.finish()
                self.assertEqual(self.runtime.status()["state"], expected)
                if expected == "pending":
                    self.assertEqual(self.runtime.pending["expires"], captured + 600)
                else:
                    self.assertEqual(self.runtime.reason, "capture_gap")
                    self.assertIsNone(self.runtime.pending)
                self.assertFalse((self.root / "enrichment.db").exists())

    def test_gap_after_pending_or_confirmation_discards_and_never_leaks(self):
        for confirmation in (False, True):
            self.acquire()
            if confirmation:
                self.result()
            self.runtime.notify("capture_gap")
            self.assertEqual(self.runtime.reason, "capture_gap")
            self.assertIsNone(self.runtime.pending)
            self.assertIsNone(self.runtime.candidate)
            self.result("later")
            self.assertIsNone(self.runtime.candidate)
            self.assertFalse((self.root / "enrichment.db").exists())

    def test_gap_does_not_clear_result_cancel_off_identity_or_loss_revocation(self):
        for kind in ("win", "draw", "decision", "cancel", "off", "target", "lost", "shutdown"):
            with self.subTest(kind=kind):
                self.now, self.on, self.target = 100.0, True, dict(TARGET)
                runtime = MetadataRuntime(self.root, enabled=lambda: self.on,
                    target=lambda: self.target, worker=lambda t,c: blocked(t,c), clock=lambda: self.now)
                started, release = threading.Event(), threading.Event()
                def blocked(target, cancel):
                    started.set()
                    self.assertTrue(release.wait(3))
                    return self.positive(target, cancel)
                runtime.acquire()
                try:
                    self.assertTrue(started.wait(3))
                    runtime.notify("capture_gap")
                    self.now = 101.0
                    if kind in ("win", "draw"):
                        runtime.notify("result", result=kind, event_id="one", witness=("one", 2, kind))
                    elif kind == "decision": runtime.notify("decision")
                    elif kind == "cancel": runtime.cancel()
                    elif kind == "off": self.on = False; runtime.cancel("disabled")
                    elif kind == "target": self.target = {**TARGET, "birth": 31}
                    elif kind == "lost":
                        with runtime.lock: runtime.notify("capture_gap")
                    else:
                        revoked = threading.Event()
                        original_cancel = runtime.cancel
                        def cancel(reason="cancelled"):
                            reply = original_cancel(reason)
                            revoked.set()
                            return reply
                        runtime.cancel = cancel
                        stopping = threading.Thread(target=runtime.shutdown)
                        stopping.start()
                        self.assertTrue(revoked.wait(3))
                        release.set()
                        stopping.join(3)
                        self.assertFalse(stopping.is_alive())
                finally:
                    release.set()
                    task = runtime.task
                    if task: task.join(3); self.assertFalse(task.is_alive())
                self.assertIsNone(runtime.pending)
                self.assertIsNone(runtime.candidate)
                runtime.shutdown()

    def test_receipt_before_cleanup_cannot_attach_a_late_capture(self):
        self.acquire()
        self.runtime.notify("result", result="win", event_id="one", decided_at=99)
        self.assertIsNone(self.runtime.candidate)

    def test_off_cancel_result_and_shutdown_during_target_discovery_never_dispatch(self):
        for kind in ("off", "cancel", "draw", "shutdown", "lost"):
            entered, release = threading.Event(), threading.Event()
            enabled = [True]
            worker = Mock(side_effect=self.positive)
            def target():
                entered.set()
                self.assertTrue(release.wait(3))
                return TARGET
            runtime = MetadataRuntime(self.root, enabled=lambda: enabled[0], target=target,
                worker=worker, clock=lambda: self.now)
            reply = []
            request = threading.Thread(target=lambda: reply.append(runtime.acquire()))
            request.start()
            try:
                self.assertTrue(entered.wait(3))
                if kind == "off": enabled[0] = False; runtime.cancel("disabled")
                elif kind == "draw": runtime.notify("result", result="draw")
                elif kind == "shutdown": runtime.shutdown()
                elif kind == "lost": runtime.lost.set()
                else: runtime.cancel()
            finally:
                release.set(); request.join(3)
            self.assertFalse(request.is_alive())
            self.assertNotEqual(reply[0]["state"], "acquiring")
            worker.assert_not_called()
            self.assertIsNone(runtime.task)
            self.assertIsNone(runtime.pending)
            runtime.shutdown()

    def test_notification_never_waits_and_loss_revokes(self):
        self.acquire()
        with self.runtime.lock:
            self.result()
        self.assertTrue(self.runtime.lost.is_set())
        self.assertEqual(self.runtime.status()["reason"], "notification_lost")
        self.result("two")
        self.assertIsNone(self.runtime.candidate)

    def test_unknown_wrong_provenance_crash_and_unreaped_are_no_write(self):
        for changes in ({"status": "failed"}, {"version": "other"}, {"source": "guess"},
                        {"match_format": "team"}, {"captured_at": 0}, {"cleaned": False}):
            self.worker.side_effect = lambda t,c: {**self.positive(t,c), **changes}
            self.runtime.acquire()
            self.finish()
            self.assertIsNone(self.runtime.pending)
            self.assertFalse((self.root / "enrichment.db").exists())
        self.assertTrue(self.runtime.closed)

    def test_checked_snapshot_exact_parent_and_no_rank_carry_forward(self):
        self.history()
        request = self.acquire()
        self.result()
        self.assertEqual(self.runtime.confirm(request, "wrong", True)["state"], "rejected")
        self.assertEqual(self.runtime.confirm(request, "one", False)["state"], "rejected")
        with fixture_clock():
            self.assertEqual(self.runtime.confirm(request, "one", True)["state"], "writing")
            self.finish()
            service = OptionalEnrichmentService(self.root, active=True)
            snapshot = service.lookup_snapshot("one").snapshots[0]
        self.assertEqual(snapshot, MatchSnapshot("one", "ranked", "single", None, None, "recognized", VERSION, 1))
        self.assertEqual(self.runtime.confirm(request, "one", True)["state"], "rejected")

    def test_existing_independent_snapshot_conflicts_without_relabel(self):
        self.history()
        service = OptionalEnrichmentService(self.root, active=True)
        prior = MatchSnapshot("one", "ranked", "single", "A4", "S", "recognized", "rank-other")
        with fixture_clock():
            service.save_snapshot(service.prepare_binding("one").ticket, prior,
                                  expected_revision=0, create_missing=True)
            before = (self.root / "enrichment.db").read_bytes()
            request = self.acquire()
            self.result()
            self.runtime.confirm(request, "one", True)
            self.finish()
            self.assertEqual(self.runtime.last["state"], "conflict")
            self.assertEqual((self.root / "enrichment.db").read_bytes(), before)

    def test_authority_removed_or_wrong_result_or_unavailable_never_writes(self):
        store = self.history()
        request = self.acquire()
        self.result()
        store.discard_result("one")
        with fixture_clock():
            self.runtime.confirm(request, "one", True)
            self.finish()
        self.assertFalse((self.root / "enrichment.db").exists())
        self.runtime.service_factory = Mock(side_effect=OSError("full disk"))
        request = self.acquire()
        self.result()
        self.runtime.confirm(request, "one", True)
        self.finish()
        self.assertEqual(self.runtime.last["state"], "unavailable")

    def test_cancel_while_sidecar_prepare_blocked_and_cas_conflict(self):
        self.history()
        entered, release = threading.Event(), threading.Event()
        real = OptionalEnrichmentService(self.root, active=True)
        original = real.prepare_binding
        def block(event):
            entered.set()
            release.wait(3)
            return original(event)
        real.prepare_binding = block
        self.runtime.service_factory = lambda root: real
        request = self.acquire()
        self.result()
        with fixture_clock():
            self.runtime.confirm(request, "one", True)
            self.assertTrue(entered.wait(3))
            self.runtime.cancel()
            release.set()
            self.finish()
        self.assertFalse((self.root / "enrichment.db").exists())

    def test_same_event_recreated_with_different_witness_is_rejected(self):
        store = self.history()
        request = self.acquire()
        self.result()
        store.discard_result("one")
        store.record_result("one", "win", "fixture", {}, created_at=3)
        with fixture_clock():
            self.runtime.confirm(request, "one", True)
            self.finish()
        self.assertEqual(self.runtime.last["reason"], "invalid_authority")
        self.assertFalse((self.root / "enrichment.db").exists())

    def test_real_cas_race_keeps_other_snapshot_and_reports_conflict(self):
        self.history()
        real = OptionalEnrichmentService(self.root, active=True)
        other = OptionalEnrichmentService(self.root, active=True)
        original = real.save_snapshot
        def race(ticket, snapshot, **kwargs):
            prior = MatchSnapshot("one", "custom", "single", None, None, "recognized", "other-version")
            other.save_snapshot(other.prepare_binding("one").ticket, prior,
                                expected_revision=0, create_missing=True)
            return original(ticket, snapshot, **kwargs)
        real.save_snapshot = race
        self.runtime.service_factory = lambda root: real
        request = self.acquire()
        self.result()
        with fixture_clock():
            self.runtime.confirm(request, "one", True)
            self.finish()
            self.assertEqual(self.runtime.last["reason"], "snapshot_conflict")
            self.assertEqual(other.lookup_snapshot("one").snapshots[0].recognition_version,"other-version")

    def test_worker_exception_degrades_and_allows_fresh_explicit_acquisition(self):
        self.worker.side_effect = RuntimeError("crash")
        self.runtime.acquire()
        self.finish()
        self.assertEqual(self.runtime.status()["reason"], "acquisition_failed")
        self.worker.side_effect = self.positive
        self.acquire()


class BoundaryTests(unittest.TestCase):
    def test_pre_cancel_never_spawns_and_response_requires_normal_exit(self):
        import lobby_capture
        cancel=threading.Event(); cancel.set()
        with patch.object(lobby_capture,"spawn_python") as spawn:
            self.assertTrue(lobby_capture.acquire(TARGET,cancel)["cleaned"])
            spawn.assert_not_called()
        cancel.clear()
        for mode, exit_code in (("normal",0),("crash",23),("forced_zero",0)):
            process=Mock(pid=900,ac6_launch_nonce="a"*64)
            process.poll.side_effect=lambda: process.returncode
            process.returncode=None
            def wait(timeout):
                if mode == "forced_zero" and process.returncode is None:
                    raise lobby_capture.subprocess.TimeoutExpired("owned",timeout)
                process.returncode=exit_code
            process.wait.side_effect=wait
            positive={"status":"recognized","match_type":"ranked","match_format":"single",
                      "version":VERSION,"source":"direct_header","captured_at":100.0}
            with patch.object(lobby_capture,"spawn_python",return_value=process), \
                 patch.object(lobby_capture,"process_birth",return_value=1), \
                 patch.object(lobby_capture,"KillOnCloseJob") as job, \
                 patch.object(lobby_capture,"PipeReader") as reader:
                if mode == "forced_zero":
                    job.return_value.close.side_effect=lambda: setattr(process,"returncode",0)
                reader.return_value.read.side_effect=[{"pid":900,"launch_nonce":"a"*64},positive]
                response=lobby_capture.acquire(TARGET,cancel)
            self.assertTrue(response["cleaned"])
            self.assertEqual(response["status"],"recognized" if mode=="normal" else "failed")

    def test_native_adapter_fresh_geometry_repeat_stale_and_target_change(self):
        import types
        import numpy as np
        import lobby_capture
        import match_header
        for kind in ("fresh", "repeated", "stale", "geometry", "target"):
            control = Mock()
            control.is_finished.side_effect = [False,True]
            roi = np.full((60,480,4),255,np.uint8)
            buffer = Mock()
            buffer.__getitem__ = Mock(return_value=types.SimpleNamespace(copy=lambda: roi))
            frame = types.SimpleNamespace(width=1920 if kind!="geometry" else 1280,
                height=1080,timespan=int((98 if kind=="stale" else 100)*10_000_000),frame_buffer=buffer)
            class Native:
                def __init__(self,**kw): self.options=kw
                def event(self,f): setattr(self,f.__name__,f); return f
                def start_free_threaded(self):
                    self.on_frame_arrived(frame,Mock())
                    if kind=="repeated": self.on_frame_arrived(frame,Mock())
                    return control
            values = [TARGET,TARGET,None,None] if kind=="target" else None
            with patch.dict(sys.modules,{"windows_capture":types.SimpleNamespace(WindowsCapture=Native)}), \
                    patch.object(lobby_capture,"current_target",side_effect=values,return_value=TARGET), \
                    patch.object(lobby_capture,"time",types.SimpleNamespace(monotonic=lambda:100.0)), \
                    patch.object(match_header,"recognize_header",return_value=match_header.HeaderRecognition(
                        "ranked","single","recognized")):
                result=lobby_capture.capture_header(TARGET,105)
            self.assertEqual(result["status"],"recognized" if kind=="fresh" else "failed")
            control.stop.assert_called_once()
            if kind=="fresh":
                buffer.__getitem__.assert_called_once_with((slice(40,100),slice(80,560),slice(None)))

    def test_header_geometry_translates_only_the_accepted_native_roi(self):
        self.assertEqual(header_geometry(TARGET, 1920, 1080), (80,40,480,60))
        decorated = {**TARGET, "bounds": [-1928, -20, 8, 1098]}
        self.assertEqual(header_geometry(decorated, 1936,1118), (88,70,480,60))
        self.assertIsNone(header_geometry(TARGET, 1280,720))
        wrong = {**TARGET, "client": {**TARGET["client"], "width": 1280}}
        self.assertIsNone(header_geometry(wrong, 1280,1080))

    def test_worker_native_imports_are_after_owned_entry_ready_gate(self):
        tree = ast.parse((ROOT / "lobby_worker.py").read_text())
        top_imports = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
        names = {n.module for n in top_imports if isinstance(n, ast.ImportFrom)}
        names |= {a.name for n in top_imports if isinstance(n, ast.Import) for a in n.names}
        self.assertLessEqual(names, {"ctypes", "json", "os", "sys", "time", "owned_worker"})
        code = (ROOT / "lobby_worker.py").read_text()
        self.assertIn("owned_entry(ready, native_work", code)
        self.assertNotIn("server", names)
        self.assertNotIn("CreateProcess", (ROOT / "lobby_capture.py").read_text())


if __name__ == "__main__":
    unittest.main(verbosity=2)
