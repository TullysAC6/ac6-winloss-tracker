import copy
import ctypes
import multiprocessing as mp
import os
import sys
import time
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from game_capture import GameCapture, _capture_worker, crop_geometry, result_region, native_frame_time
from owned_worker import KillOnCloseJob, stop_process
from result_detector import CLEAR, FINAL_WIN, FINAL_LOSS, FINAL_DRAW, ResultDetector, ResultStateMachine

TARGET = {"hwnd": 123, "pid": 456,
          "client": {"left": -1920, "top": 0, "width": 1920, "height": 1080},
          "bounds": (-1920, 0, 0, 1080)}


def blocked_worker():
    time.sleep(60)


def dying_owner(connection):
    child = mp.get_context("spawn").Process(target=blocked_worker, daemon=True)
    child.start()
    job = KillOnCloseJob(child.pid)
    connection.send(child.pid)
    connection.recv()  # The test holds a process handle before our hard exit.
    os._exit(0)  # Deliberately bypass finally/atexit; the OS must close the job.


class CaptureTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows handle/child endurance")
    def test_repeated_owned_children_do_not_accumulate(self):
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        kernel.GetProcessHandleCount.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        samples = []
        existing = {p.pid for p in mp.active_children()}
        for _ in range(12):
            child = mp.get_context("spawn").Process(target=blocked_worker, daemon=True)
            child.start()
            job = None
            try:
                job = KillOnCloseJob(child.pid)
            finally:
                if job:
                    job.close()
                stop_process(child)
            count = wintypes.DWORD()
            self.assertTrue(kernel.GetProcessHandleCount(kernel.GetCurrentProcess(), ctypes.byref(count)))
            samples.append(count.value)
            self.assertEqual({p.pid for p in mp.active_children()}, existing)
        self.assertLessEqual(max(samples[2:]), samples[1] + 4, samples)

    @unittest.skipUnless(os.name == "nt", "Windows Job Object parent-death integration")
    def test_abnormal_parent_exit_reaps_child(self):
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        context = mp.get_context("spawn")
        parent_end, child_end = context.Pipe()
        owner = context.Process(target=dying_owner, args=(child_end,))
        owner.start()
        child_end.close()
        handle = None
        try:
            self.assertTrue(parent_end.poll(10), "owner did not start child")
            handle = kernel.OpenProcess(0x100000, False, parent_end.recv())
            self.assertTrue(handle)
            parent_end.send("exit")
            owner.join(timeout=5)
            self.assertFalse(owner.is_alive())
            self.assertEqual(kernel.WaitForSingleObject(handle, 5000), 0)
        finally:
            parent_end.close()
            if handle:
                kernel.CloseHandle(handle)
            stop_process(owner)

    def test_native_callback_roi_slot_consumed_once_and_closed(self):
        control = Mock()
        control.is_finished.return_value = False
        raw = b"\x10" * (1152 * 75 * 4)
        frame = Mock(width=1920, height=1080, timespan=int(time.monotonic() * 10_000_000))
        class Buffer:
            def __getitem__(self, key):
                self.key = key
                return self
            def tobytes(self):
                return raw
        frame.frame_buffer = Buffer()
        instances = []
        class Native:
            def __init__(self, **kwargs):
                self.options = kwargs
                instances.append(self)
            def event(self, function):
                setattr(self, function.__name__, function)
                return function
            def start_free_threaded(self):
                self.on_frame_arrived(frame, Mock())
                self.on_frame_arrived(frame, Mock())  # same native timestamp
                return control
        connection = Mock()
        connection.poll.return_value = True
        connection.recv.side_effect = ["frame", "frame", "stop"]
        with patch.dict(sys.modules, {"windows_capture": types.SimpleNamespace(WindowsCapture=Native)}), \
                patch("result_detector.WinApi") as api, patch("game_capture.mp.parent_process") as owner:
            api.return_value.game_target.return_value = TARGET
            owner.return_value.is_alive.return_value = True
            _capture_worker(connection, TARGET)
        self.assertEqual(instances[0].options["window_hwnd"], 123)
        self.assertFalse(instances[0].options["secondary_window"])
        sent = connection.send.call_args_list
        self.assertEqual(sent[0].args[0][2:], (1152, 75, raw))
        self.assertIsNone(sent[1].args[0])
        control.stop.assert_called_once()
        connection.close.assert_called_once()

    def setup_capture(self):
        api = Mock()
        api.game_target.return_value = copy.deepcopy(TARGET)
        api.region_unobscured.return_value = False
        capture = GameCapture(api)
        capture.target = copy.deepcopy(TARGET)
        capture.process = Mock()
        capture.process.is_alive.return_value = True
        capture.connection = Mock()
        capture.connection.poll.return_value = True
        capture.pending_at = 99.5
        return capture

    def test_native_time_does_not_refresh_delayed_frames(self):
        self.assertEqual(native_frame_time(998_000_000, 100), 99.8)
        self.assertEqual(native_frame_time(1_000_200_000, 100), 100)
        for stamp in (970_000_000, 1_010_000_000, 0):
            self.assertIsNone(native_frame_time(stamp, 100))

    @patch("game_capture.KillOnCloseJob", side_effect=OSError("job denied"))
    def test_job_failure_never_releases_child(self, job):
        context = Mock()
        parent, child = Mock(), Mock()
        context.Pipe.return_value = (parent, child)
        context.Process.return_value.is_alive.return_value = False
        capture = GameCapture(Mock(), context)
        with self.assertRaises(OSError):
            capture._start(TARGET)
        context.Event.return_value.set.assert_not_called()
        self.assertIsNone(capture.process)
        context.Process.return_value.close.assert_called_once()
        child.close.assert_called_once()

    @patch("game_capture.stop_process", side_effect=RuntimeError("cannot reap"))
    def test_unreaped_worker_prevents_replacement(self, stop):
        capture = self.setup_capture()
        process = capture.process
        capture.api.game_target.return_value = dict(TARGET, pid=999)
        capture._start = Mock()
        for _ in range(3):
            with self.assertRaises(RuntimeError):
                capture.grab(Mock())
            self.assertIs(capture.process, process)
        capture._start.assert_not_called()

    @patch("game_capture.time.monotonic", return_value=100)
    def test_responsive_worker_without_frames_is_reaped(self, clock):
        capture = self.setup_capture()
        capture.last_frame_at = 94
        capture.connection.recv.return_value = None
        capture.process.is_alive.side_effect = [True, False, False, False]
        self.assertIsNone(capture.grab(Mock()))
        self.assertIsNone(capture.process)
        self.assertEqual(capture.retry_at, 110)

    @patch("game_capture.time.monotonic", return_value=100)
    def test_desktop_resize_breaks_consecutive_evidence_during_retry(self, clock):
        capture = self.setup_capture()
        capture.process = None
        capture.retry_at = 110
        capture.api.region_unobscured.return_value = True
        capture.grab(Mock())
        capture.grab(Mock())
        self.assertFalse(capture.discontinuity)
        capture.api.game_target.return_value["client"]["left"] += 10
        capture.grab(Mock())
        self.assertTrue(capture.discontinuity)

    @patch("game_capture.time.monotonic", return_value=100)
    def test_background_new_frames_only(self, clock):
        capture = self.setup_capture()
        desktop = Mock()
        capture.connection.recv.return_value = (99.8, 10, 1152, 75, b"\x01" * (1152 * 75 * 4))
        shot = capture.grab(desktop)
        self.assertEqual(shot.raw, b"\x01" * (1152 * 75 * 4))
        self.assertEqual(capture.captured_at, 99.8)
        self.assertIsNone(capture.grab(desktop))  # repeated native timestamp
        capture.connection.recv.return_value = (99.9, 9, 1152, 75, b"\x01" * (1152 * 75 * 4))
        self.assertIsNone(capture.grab(desktop))  # out of order
        capture.connection.recv.return_value = (97, 11, 1152, 75, b"\x01" * (1152 * 75 * 4))
        self.assertIsNone(capture.grab(desktop))  # expired buffer
        desktop.grab.assert_not_called()

    @patch("game_capture.time.monotonic", return_value=100)
    def test_foreground_fallback_rechecks_identity_and_occlusion(self, clock):
        capture = self.setup_capture()
        capture.connection.recv.return_value = None
        desktop = Mock()
        capture.api.region_unobscured.side_effect = [True, False]
        self.assertIsNone(capture.grab(desktop))  # Alt+Tab during grab
        capture.api.region_unobscured.side_effect = None
        capture.api.region_unobscured.return_value = True
        capture.api.game_target.side_effect = [TARGET, dict(TARGET, pid=999)]
        self.assertIsNone(capture.grab(desktop))
        capture.api.game_target.side_effect = None
        self.assertIs(capture.grab(desktop), desktop.grab.return_value)

    @patch("game_capture.time.monotonic", return_value=100)
    def test_minimized_and_changed_target(self, clock):
        capture = self.setup_capture()
        capture.api.game_target.return_value = None
        capture.close = Mock()
        self.assertIsNone(capture.grab(Mock()))
        capture.close.assert_called_once()
        capture = self.setup_capture()
        capture.identity = (123, 999)
        capture.connection.recv.return_value = None
        capture.grab(Mock())
        self.assertTrue(capture.identity_changed)

    @patch("game_capture.time.monotonic", return_value=100)
    def test_hung_worker_closed_before_retry(self, clock):
        capture = self.setup_capture()
        capture.pending_at = 90
        capture.process.is_alive.side_effect = [True, False, False, False]
        process = capture.process
        self.assertIsNone(capture.grab(Mock()))
        self.assertIsNone(capture.process)
        self.assertEqual(capture.retry_at, 110)
        process.close.assert_called_once()

    def test_geometry_borderless_decorated_resize(self):
        self.assertEqual(crop_geometry(TARGET, 1920, 1080), (384, 464, 1152, 75))
        decorated = dict(TARGET, bounds=(-1928, -31, 8, 1088))
        self.assertEqual(crop_geometry(decorated, 1936, 1119), (392, 495, 1152, 75))
        self.assertIsNone(crop_geometry(TARGET, 1280, 720))
        self.assertEqual(result_region(TARGET["client"])["left"], -1536)

    def test_actual_spawn_shutdown_is_bounded(self):
        process = mp.get_context("spawn").Process(target=blocked_worker, daemon=True)
        process.start()
        job = KillOnCloseJob(process.pid)
        pid = process.pid
        start = time.monotonic()
        job.close()
        stop_process(process)
        self.assertLess(time.monotonic() - start, 3)
        self.assertNotIn(pid, [p.pid for p in mp.active_children()])


class CaptureGapTests(unittest.TestCase):
    @patch("result_detector.mss.mss")
    def test_diagnostic_flush_failure_never_blocks_cleanup(self, desktop_factory):
        stopped = Mock()
        stopped.is_set.return_value = True
        recorder = Mock()
        recorder.flush_frame_context.side_effect = OSError("disk full")
        detector = ResultDetector(Path(__file__).resolve().parents[1], lambda: {}, Mock(), Mock(), stopped,
                                  diagnostic_recorder=recorder)
        detector.capture = Mock()
        detector.run()
        detector.capture.close.assert_called_once()

    @patch("result_detector.mss.mss")
    def test_detector_loop_gap_and_cleanup(self, desktop_factory):
        from mss.screenshot import ScreenShot
        for final, result in ((FINAL_WIN, "win"), (FINAL_LOSS, "loss"), (FINAL_DRAW, "draw")):
            sequence = [CLEAR, CLEAR, CLEAR, final, None, final, final, final]
            cursor = [0]
            class Stop:
                def is_set(self):
                    return cursor[0] >= len(sequence)
                def wait(self, timeout):
                    cursor[0] += 1
            detector = ResultDetector(Path(__file__).resolve().parents[1],
                lambda: {"result_detector_enabled": True}, Mock(return_value=True), Mock(), Stop(), diagnostic_recorder=Mock())
            capture = Mock(discontinuity=False, identity_changed=False, status="WGC")
            frame = ScreenShot.from_size(bytearray(128 * 40 * 4), 128, 40)
            def grab(desktop):
                capture.captured_at = 100 + cursor[0] * .5
                return frame if sequence[cursor[0]] else None
            capture.grab.side_effect = grab
            detector.capture = capture
            detector.classifier = Mock()
            detector.classifier.classify_bgra.side_effect = lambda *args: (sequence[cursor[0]], {})
            detector._save_debug_result_roi = Mock()
            def accept(*args):
                detector.state.external_mutation(now=capture.captured_at)
                return True
            detector.on_result.side_effect = accept
            detector.run()
            detector.diagnostics.flush_frame_context.assert_any_call("detector_stopped")
            self.assertEqual(detector.health.snapshot()["last_result"], result)
            self.assertTrue(detector.state.post_result_lock)
            self.assertEqual(detector.on_result.call_count, 0 if result == "draw" else 1)
            self.assertEqual(capture.close.call_count, 2)  # enable boundary + finally
            self.assertEqual(detector.classifier.classify_bgra.call_count, 7)
        desktop_factory.return_value.__enter__.return_value.grab.assert_not_called()

    def observe(self, state, frame, now):
        return state.observe(frame, 2, 3, 5, now=now)

    def armed(self):
        state = ResultStateMachine()
        for now in (100, 100.8, 101.6):
            self.observe(state, CLEAR, now)
        return state

    def test_short_alt_tab_recovers_win_loss_draw(self):
        for final, result in ((FINAL_WIN, "win"), (FINAL_LOSS, "loss"), (FINAL_DRAW, "draw")):
            state = self.armed()
            state.note_capture_gap()
            self.assertIsNone(self.observe(state, final, 103))
            self.assertEqual(self.observe(state, final, 103.8), result)
            if result != "draw":
                state.external_mutation(now=103.8)
            state.note_capture_gap()
            self.assertIsNone(self.observe(state, final, 110))
            self.assertTrue(state.post_result_lock)

    def test_no_bank_or_stale_confirmation(self):
        state = self.armed()
        self.observe(state, FINAL_WIN, 102)
        state.note_capture_gap()
        self.assertIsNone(self.observe(state, FINAL_WIN, 103))
        self.assertEqual(state.candidate_hits, 1)
        state.note_capture_gap()
        self.assertIsNone(self.observe(state, FINAL_WIN, 107))
        self.assertEqual(state.last_reject_reason, "no_recent_gameplay")
        state = ResultStateMachine()
        self.observe(state, CLEAR, 100)
        self.observe(state, CLEAR, 101)
        state.note_capture_gap()
        self.observe(state, CLEAR, 102)
        self.assertFalse(state.armed)

    def test_missing_time_never_rearms(self):
        state = ResultStateMachine()
        state.external_mutation(now=100)
        for now in (105, 106, 107, 108):
            self.observe(state, CLEAR, now)
        state.note_capture_gap()
        self.observe(state, CLEAR, 111)
        self.assertTrue(state.post_result_lock)
        self.assertIsNone(self.observe(state, FINAL_WIN, 112))
        state.after_undo(now=113)
        state.note_capture_gap()
        self.assertIsNone(self.observe(state, FINAL_WIN, 119))


if __name__ == "__main__":
    unittest.main()
