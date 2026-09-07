"""Opt-in real DWM/WGC test: AC6_RUN_WGC_INTEGRATION=1 on an unlocked desktop.

Only a synthetic Tk window is captured. No game or user Desktop file is written.
"""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import result_detector
from game_capture import GameCapture, _capture_worker
from effect_screenshot import _save_visible_effect, desktop_directory, screenshot_name


def dummy_worker(connection, target):
    import game_capture
    original_time = game_capture.native_frame_time
    original_geometry = game_capture.crop_geometry
    reported = set()
    def check_time(stamp, now):
        if "time" not in reported:
            print(f"[native-test] timestamp={stamp} monotonic={now} delta={now-stamp/10_000_000}", flush=True)
            reported.add("time")
        return original_time(stamp, now)
    def check_geometry(target, width, height):
        if "geometry" not in reported:
            print(f"[native-test] frame={width}x{height} target={target}", flush=True)
            reported.add("geometry")
        return original_geometry(target, width, height)
    game_capture.native_frame_time = check_time
    game_capture.crop_geometry = check_geometry
    result_detector.TARGET_PROCESS = Path(sys.executable).name.lower()
    _capture_worker(connection, target)


@unittest.skipUnless(os.name == "nt" and os.environ.get("AC6_RUN_WGC_INTEGRATION") == "1",
                     "requires opt-in unlocked Windows desktop with WGC")
class WindowsCaptureIntegration(unittest.TestCase):
    def test_native_target_wgc_composition_and_cleanup(self):
        import tkinter as tk
        import mss
        from PIL import Image
        api = result_detector.WinApi()  # DPI awareness before creating Tk.
        root = tk.Tk()
        root.overrideredirect(True)
        root.geometry("640x360+80+80")
        canvas = tk.Canvas(root, width=640, height=360, highlightthickness=0, bg="#102030")
        canvas.pack()
        root.attributes("-topmost", True)
        capture = GameCapture(api)
        try:
            with patch.object(result_detector, "TARGET_PROCESS", Path(sys.executable).name.lower()):
                root.update()
                root.focus_force()
                root.update()
                target = api.game_target()
                self.assertIsNotNone(target, "dummy Python window was not uniquely identified")
                self.assertEqual(target["pid"], os.getpid())
                self.assertTrue(api.region_unobscured(target["hwnd"], target["client"]))
                # Patching only the spawn entry allows the child to identify the
                # test executable; production HWND/PID/rect checks remain real.
                with patch("game_capture._capture_worker", dummy_worker), mss.mss() as desktop:
                    deadline = time.monotonic() + 15
                    shot = None
                    while time.monotonic() < deadline:
                        canvas.configure(bg="#102030" if int(time.monotonic() * 5) % 2 else "#304050")
                        root.update()
                        candidate = capture.grab(desktop)
                        if candidate is not None and capture.status == "WGC window capture":
                            shot = candidate
                            break
                        time.sleep(.2)
                    self.assertIsNotNone(shot, capture.status)
                    self.assertLess(time.monotonic() - capture.captured_at, capture.MAX_FRAME_AGE)
                    self.assertIn(tuple(shot.rgb[:3]), ((16, 32, 48), (48, 64, 80)))
                    pid = capture.process.pid
                    capture.close()
                    self.assertIsNone(capture.process)
                    self.assertNotIn(pid, [child.pid for child in __import__("multiprocessing").active_children()])

                # Draw the production banner geometry in the synthetic target.
                canvas.create_rectangle(70, 168, 570, 278, fill="#2b2108", outline="#ffd84a", width=3)
                root.update()
                time.sleep(.3)
                effect = {"effect_id": "native-dwm", "milestone": 5, "created_at_ms": 1700000000000}
                client = target["client"]
                game = (target["hwnd"], *(client[k] for k in ("left", "top", "width", "height")))
                self.assertTrue(desktop_directory().is_absolute())  # Real Known Folder API.
                with tempfile.TemporaryDirectory(prefix="AC6-日本語-") as directory:
                    destination = Path(directory) / "OneDrive" / "デスクトップ"
                    destination.mkdir(parents=True)
                    with patch("effect_screenshot.desktop_directory", return_value=destination):
                        _save_visible_effect(effect, game, (), time.monotonic() + 5)
                        path = destination / screenshot_name(effect)
                        self.assertTrue(path.exists(), "real compositor screenshot was not saved")
                        with Image.open(path) as image:
                            self.assertEqual(image.size, (640, 360))
                        # An actual opaque topmost window must prevent saving.
                        blocker = tk.Toplevel(root)
                        blocker.overrideredirect(True)
                        blocker.geometry("200x160+80+80")
                        blocker.attributes("-topmost", True)
                        root.update()
                        self.assertFalse(api.region_unobscured(target["hwnd"], client))
                        path.unlink()
                        _save_visible_effect(effect, game, (), time.monotonic() + 5)
                        self.assertFalse(path.exists())
                        blocker.destroy()
                root.overrideredirect(False)
                root.update()
                root.iconify()
                root.update()
                self.assertIsNone(api.game_target())
        finally:
            capture.close()
            root.destroy()


if __name__ == "__main__":
    unittest.main()
