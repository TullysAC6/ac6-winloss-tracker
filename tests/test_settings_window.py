import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config_utils
import settings_window as settings


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "config.json"
        self.raw = dict(config_utils.DEFAULT_CONFIG, port=9123, stats_enabled=False)
        self.write(self.raw)
        self.path_patch = patch.object(config_utils, "CONFIG_PATH", self.path)
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)
        config_utils._last_good = config_utils._last_good_signature = None

    def write(self, raw):
        self.path.write_text(json.dumps(raw), encoding="utf-8")

    def test_current_value_and_preserve_latest_other_values(self):
        self.assertFalse(settings.read_screenshot_setting())
        self.write(dict(self.raw, effect_screenshot_enabled=True))
        self.assertTrue(settings.read_screenshot_setting())
        # Another change made after the settings window opened must survive.
        updated = dict(self.raw, port=9345, result_detector_enabled=False)
        self.write(updated)
        settings.save_screenshot_setting(True)
        self.assertEqual(json.loads(self.path.read_text()), dict(updated, effect_screenshot_enabled=True))

    def test_old_config_without_setting(self):
        self.raw.pop("effect_screenshot_enabled")
        self.write(self.raw)
        self.assertFalse(settings.read_screenshot_setting())
        settings.save_screenshot_setting(True)
        self.assertTrue(config_utils.load_config()["effect_screenshot_enabled"])

    def test_write_and_rename_failures_leave_original_intact(self):
        original = self.path.read_bytes()
        for operation in ("os.fsync", "os.replace"):
            with self.subTest(operation=operation), patch("settings_window." + operation, side_effect=OSError("denied")):
                with self.assertRaises(OSError):
                    settings.save_screenshot_setting(True)
            self.assertEqual(self.path.read_bytes(), original)
            self.assertEqual(list(self.path.parent.glob(".config-*.tmp")), [])

    def test_invalid_config_is_never_replaced(self):
        for raw in ("{", '[]', '{"config_version":99}', '{"config_version":18,"unexpected":1}'):
            self.path.write_text(raw)
            with self.assertRaises(ValueError):
                settings.save_screenshot_setting(True)
            self.assertEqual(self.path.read_text(), raw)

    def test_concurrent_edit_is_reported_and_preserved(self):
        changed = dict(self.raw, port=9999)
        with patch("settings_window.os.fsync", side_effect=lambda _: self.write(changed)):
            with self.assertRaises(OSError):
                settings.save_screenshot_setting(True)
        self.assertEqual(json.loads(self.path.read_text()), changed)

    def test_existing_overlay_tick_observes_both_changes_without_restart(self):
        import game_overlay
        overlay = Mock(always_show=False, debug=False, _effect_visible=False, _active_effect=None,
                       panel_opacity=10, panel_hwnd=1, text_hwnd=2, effect_hwnd=3)
        overlay._sse_connected.is_set.return_value = True
        config_utils.load_config()  # Prime the existing process-local config cache.
        with patch("game_overlay.foreground_game_client", return_value=None), \
             patch("subprocess.Popen", side_effect=AssertionError("unexpected spawn")):
            for enabled in (False, True, False):
                settings.save_screenshot_setting(enabled)
                game_overlay.GameOverlay._tick(overlay)
                self.assertIs(overlay._screenshots.tick.call_args.args[3], enabled)
                self.assertEqual(overlay._screenshots.tick.call_args.args[2], (1, 2, 3))
            overlay.panel_opacity = 0
            game_overlay.GameOverlay._tick(overlay)
            self.assertEqual(overlay._screenshots.tick.call_args.args[2], (2, 3))

    @unittest.skipUnless(os.name == "nt", "real Tk controls on Windows")
    def test_running_launcher_opens_settings_without_spawning(self):
        import importlib.machinery
        import importlib.util
        import tkinter as tk
        loader = importlib.machinery.SourceFileLoader("settings_test_launcher", str(Path(settings.__file__).with_name("launcher.pyw")))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        launcher = importlib.util.module_from_spec(spec)
        loader.exec_module(launcher)
        root = tk.Tk()
        errors = []
        opened = []
        def inspect():
            try:
                def descendants(widget):
                    for child in widget.winfo_children():
                        yield child
                        yield from descendants(child)
                button = next(w for w in descendants(root) if isinstance(w, tk.Button) and w.cget("text") == "設定")
                self.assertTrue(button.winfo_viewable())
                button.invoke()
                root.update_idletasks()
                opened.append(root._tracker_settings)
                self.assertTrue(root._tracker_settings.window.winfo_viewable())
                self.assertLessEqual(button.winfo_rooty() + button.winfo_height(), root.winfo_rooty() + root.winfo_height())
            except Exception as error:
                errors.append(error)
            finally:
                root.destroy()
        root.after(800, inspect)
        with patch("tkinter.Tk", return_value=root), \
             patch.object(launcher, "launch_once", return_value="already_running") as launch, \
             patch("subprocess.Popen", side_effect=AssertionError("unexpected spawn")):
            launcher.main()
        self.assertEqual(errors, [])
        self.assertEqual(len(opened), 1)
        launch.assert_called_once()

    @unittest.skipUnless(os.name == "nt", "real Tk controls on Windows")
    def test_real_gui_initial_save_reuse_and_async_close(self):
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        self.addCleanup(root.destroy)
        gate = threading.Event()
        self.addCleanup(gate.set)
        with patch("subprocess.Popen", side_effect=AssertionError("unexpected spawn")):
            window = settings.open_settings(root)
            root.update()
            self.assertFalse(window.enabled.get())
            self.assertIs(settings.open_settings(root), window)
            window.toggle.invoke()
            window.save_button.invoke()
            self.assertTrue(settings.read_screenshot_setting())
            window.window.withdraw()
            settings.save_screenshot_setting(False)
            settings.open_settings(root)
            self.assertFalse(window.enabled.get())
            def delayed_check():
                gate.wait(3)
                return "test result"
            with patch.object(settings, "check_latest_release", side_effect=delayed_check) as check:
                window.check_button.invoke()
                window.check()
                root.update()
                self.assertTrue(window.checking)
                self.assertEqual(check.call_count, 1)
                window.window.withdraw()
                self.assertIs(settings.open_settings(root), window)
                gate.set()
                deadline = time.monotonic() + 3
                while window.checking and time.monotonic() < deadline:
                    root.update()
                    time.sleep(.02)
                self.assertFalse(window.checking)
                self.assertEqual(window.update_status.cget("text"), "test result")
                # Destroy during another request: worker must never call Tk.
                window.check()
                window.window.destroy()
                root.update()


class ReleaseTests(unittest.TestCase):
    @patch("settings_window.urllib.request.urlopen")
    def test_numeric_comparison_and_metadata_only(self, urlopen):
        response = urlopen.return_value.__enter__.return_value
        for latest, current, expected in (("v1.10.0", "1.9.0", "新しいバージョン"),
                                          ("v1.1.0", "1.1.0", "最新の公開バージョンです"),
                                          ("v1.0.1", "1.1.0", "より新しいバージョンです")):
            response.read.return_value = json.dumps({"tag_name": latest, "draft": False, "prerelease": False}).encode()
            self.assertIn(expected, settings.check_latest_release(current))
        self.assertEqual(urlopen.call_count, 3)
        for call in urlopen.call_args_list:
            self.assertEqual(call.args[0].full_url, settings.LATEST_RELEASE_URL)
            self.assertEqual(call.args[0].get_method(), "GET")
            self.assertEqual(call.kwargs["timeout"], 10)

    @patch("settings_window.urllib.request.urlopen")
    def test_http_timeout_and_malformed_metadata(self, urlopen):
        for code in (403, 404, 429, 500):
            urlopen.side_effect = urllib.error.HTTPError(settings.LATEST_RELEASE_URL, code, "error", {}, None)
            self.assertNotIn("最新の公開バージョンです", settings.check_latest_release())
        urlopen.side_effect = TimeoutError("timeout")
        self.assertIn("確認できません", settings.check_latest_release())
        urlopen.side_effect = None
        for body in (b"broken", b"[]", b"{}", b'{"tag_name":"v1.2.0-rc1"}',
                     b'{"tag_name":"v1.2.0","prerelease":true}', b"x" * (1024 * 1024 + 1)):
            urlopen.return_value.__enter__.return_value.read.return_value = body
            self.assertIn("確認できません", settings.check_latest_release())


if __name__ == "__main__":
    unittest.main()
