"""Opt-in production Tk -> owned spawn -> MSS -> PNG, with isolated data."""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def fixture_worker(effect, game, allowed, deadline):
    import effect_screenshot
    import result_detector
    # Only fixture identity/output differ; production safety checks stay real.
    result_detector.TARGET_PROCESS = os.environ['AC6_TEST_TARGET_PROCESS']
    effect_screenshot.desktop_directory = lambda: Path(os.environ['AC6_TEST_SCREENSHOT_DIR'])
    effect_screenshot._screenshot_worker(effect, game, allowed, deadline)


@unittest.skipUnless(os.name == 'nt' and os.environ.get('AC6_RUN_SCREENSHOT_INTEGRATION') == '1',
                     'requires unlocked Windows desktop')
class NativeEffectScreenshotTests(unittest.TestCase):
    def test_production_layered_banner_and_owned_worker(self):
        # `origin` places the fake game client at (0, 0), which is where a real
        # borderless full-screen AC6 sits. Degenerate shell windows live at the
        # desktop origin, so an offset-only fixture cannot reach that failure.
        for milestone, opacity, obscured, origin in (
                (5, 0, False, False), (10, 10, False, False), (50, 10, False, False),
                (5, 10, True, False), (5, 10, False, True), (10, 10, True, True)):
            with self.subTest(milestone=milestone, opacity=opacity,
                              obscured=obscured, origin=origin):
                self.capture_case(milestone, opacity, obscured, origin)

    def capture_case(self, milestone, opacity, obscured, origin=False):
        with tempfile.TemporaryDirectory(prefix='AC6-screenshot-日本語-') as directory, patch.dict(
                os.environ, {'LOCALAPPDATA': directory, 'AC6_TEST_SCREENSHOT_DIR': directory,
                             'AC6_TEST_TARGET_PROCESS': Path(sys.executable).name.lower()}):
            import tkinter as tk
            import game_overlay as overlay
            import result_detector
            from effect_screenshot import screenshot_name
            api = result_detector.WinApi()
            dummy = tk.Tk()
            dummy.overrideredirect(True)
            dummy.geometry('1000x700+0+0' if origin else '1000x700+80+80')
            dummy.configure(bg='#102030')
            dummy.attributes('-topmost', True)
            dummy.update()
            hud = None
            worker_pids = set()
            import multiprocessing as mp
            # Match the installed launcher's windowless worker executable.
            mp.set_executable(str(Path(sys.executable).with_name('pythonw.exe')))
            real_tick = overlay.GameOverlay._tick
            try:
                with patch.object(overlay.GameOverlay, '_tick'), patch.object(overlay, 'read_stats', return_value=None), patch.object(
                        result_detector, 'TARGET_PROCESS', Path(sys.executable).name.lower()), patch(
                        'effect_screenshot._screenshot_worker', fixture_worker), patch.object(
                        overlay, 'load_config', return_value={'effect_screenshot_enabled': True}), patch.object(
                        overlay.GameOverlay, '_poll_server_lifecycle', return_value=False):
                    hud = overlay.GameOverlay(Path(sys.executable).name, 10, 10, 20, 30, panel_opacity=opacity)
                    if obscured:
                        blocker = tk.Toplevel(dummy)
                        blocker.overrideredirect(True)
                        blocker.geometry('200x100+20+220' if origin else '200x100+100+300')
                        blocker.attributes('-topmost', True)
                        dummy.update()
                    foreground_thread = api.user32.GetWindowThreadProcessId(api.user32.GetForegroundWindow(), None)
                    current_thread = api.kernel32.GetCurrentThreadId()
                    api.user32.AttachThreadInput(current_thread, foreground_thread, True)
                    try:
                        api.user32.SetForegroundWindow(overlay._tk_toplevel_hwnd(dummy.winfo_id()))
                        dummy.focus_force()
                        dummy.update()
                    finally:
                        api.user32.AttachThreadInput(current_thread, foreground_thread, False)
                    game = overlay.foreground_game_client(Path(sys.executable).name)
                    self.assertIsNotNone(game)
                    self.assertEqual(api.game_target()['hwnd'], game[0])
                    effect = {'effect_id': f'native-layered-{milestone}', 'created_at_ms': int(time.time()*1000),
                              'milestone': milestone, 'started': time.monotonic(),
                              'duration': 6.0 if milestone == 50 else 3.5}
                    hud._active_effect = effect
                    path = Path(directory) / screenshot_name(effect)
                    until = time.monotonic() + effect['duration'] - .1
                    while time.monotonic() < until:
                        dummy.focus_force()
                        dummy.update()
                        if obscured:
                            blocker.lift()
                        real_tick(hud)
                        if hud._screenshots.process is not None:
                            worker_pids.add(hud._screenshots.process.pid)
                        hud.root.update()
                        time.sleep(.03)
                    self.assertTrue(effect.get('screenshot_attempted'), 'parent did not spawn')
                    self.assertEqual(len(worker_pids), 1)
                    log_path = Path(directory) / 'AC6WinLossTracker' / 'diagnostics' / 'effect-screenshot.jsonl'
                    log_text = log_path.read_text(encoding='utf-8') if log_path.exists() else 'no diagnostic log'
                    self.assertEqual(path.exists(), not obscured, log_text)
                    self.assertEqual(len(list(Path(directory).glob('AC6_*.png'))), 0 if obscured else 1)
                    self.assertFalse(list(Path(directory).glob('*.pending')))
                    import json
                    rows = [json.loads(line) for line in log_text.splitlines()]
                    self.assertEqual(sum(row['status'] == 'spawn_requested' for row in rows), 1)
                    if obscured:
                        self.assertEqual(rows[-1]['reason'], 'occluded')
                        self.assertEqual(rows[-1]['blocker']['hwnd'], overlay._tk_toplevel_hwnd(blocker.winfo_id()))
                    else:
                        self.assertEqual(rows[-1]['status'], 'saved')
            finally:
                mp.set_executable(sys.executable)
                if hud:
                    hud._screenshots.close()
                    self.assertIsNone(hud._screenshots.process)
                    for pid in worker_pids:
                        self.assertFalse(overlay._process_is_alive(pid), 'screenshot worker survived cleanup')
                    hud.root.destroy()
                dummy.destroy()


if __name__ == '__main__':
    unittest.main()
