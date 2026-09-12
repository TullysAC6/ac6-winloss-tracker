import sys
import os
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image, ImageDraw
from effect_screenshot import EffectScreenshots, _save_visible_effect, contains_effect_banner, save_once, screenshot_name
from config_utils import DEFAULT_CONFIG, validate_config


def effect(milestone=5):
    return {"effect_id": "event/id:unsafe", "created_at_ms": 1700000000000,
            "milestone": milestone, "started": 100.,
            "duration": 6. if milestone == 50 else 3.5, "render_key": "banner"}


class ScreenshotTests(unittest.TestCase):
    def test_diagnostics_without_stdout_and_io_failure_isolation(self):
        from effect_screenshot import _record
        with tempfile.TemporaryDirectory() as directory, patch("app_paths.diagnostics_dir", return_value=Path(directory)):
            with patch("sys.stdout", None):
                _record(effect(), "skipped", reason="deadline")
            path = Path(directory) / "effect-screenshot.jsonl"
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["reason"], "deadline")
            path.write_text("x" * (256 * 1024), encoding="utf-8")
            _record(effect(), "saved")
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["status"], "saved")
            self.assertTrue(path.with_name(path.name + ".1").exists())
            with patch("effect_screenshot.RotatingFileHandler", side_effect=OSError("disk full")):
                _record(effect(), "saved")  # Optional telemetry never raises.

    @unittest.skipUnless(os.name == "nt", "Windows pythonw")
    def test_real_pythonw_worker_has_durable_skip_reason(self):
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        with tempfile.TemporaryDirectory() as directory:
            environment = dict(os.environ, LOCALAPPDATA=directory)
            subprocess.run([str(pythonw), "-c", "from effect_screenshot import _screenshot_worker; "
                            "_screenshot_worker({'effect_id':'headless','milestone':5}, (0,0,0,640,360), (), 0)"],
                           cwd=Path(__file__).resolve().parents[1], env=environment,
                           creationflags=subprocess.CREATE_NO_WINDOW, check=True, timeout=15)
            path = Path(directory) / "AC6WinLossTracker" / "diagnostics" / "effect-screenshot.jsonl"
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([row["status"] for row in rows], ["worker_started", "skipped"])
            self.assertEqual(rows[-1]["reason"], "target_changed")

    def test_filename_and_dedup_and_write_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / screenshot_name(effect())
            self.assertIn("05-WIN-STREAK", target.name)
            self.assertNotIn("unsafe", target.name)
            image = Image.new("RGB", (4, 3), "gold")
            self.assertTrue(save_once(image, target))
            original = target.read_bytes()
            self.assertFalse(save_once(Image.new("RGB", (4, 3), "black"), target))
            self.assertEqual(original, target.read_bytes())
            with Image.open(target) as saved:
                self.assertEqual(saved.tobytes(), image.tobytes())
            failed = Path(directory) / "failed.png"
            image.save = Mock(side_effect=OSError("disk full"))
            with self.assertRaises(OSError):
                save_once(image, failed)
            self.assertFalse(failed.exists())
            self.assertFalse(list(Path(directory).glob("*.pending")))
            with patch("effect_screenshot.os.replace", side_effect=PermissionError("rename denied")):
                with self.assertRaises(PermissionError):
                    save_once(Image.new("RGB", (4, 3)), failed)
            self.assertFalse(failed.exists())
            self.assertFalse(list(Path(directory).glob("*.pending")))

    def test_default_off_and_strict_boolean(self):
        self.assertFalse(DEFAULT_CONFIG["effect_screenshot_enabled"])
        legacy = dict(DEFAULT_CONFIG)
        legacy.pop("effect_screenshot_enabled")
        self.assertFalse(validate_config(legacy)["effect_screenshot_enabled"])
        self.assertTrue(validate_config(dict(DEFAULT_CONFIG, effect_screenshot_enabled=True))["effect_screenshot_enabled"])
        for value in ("true", 1, None):
            with self.assertRaises(ValueError):
                validate_config(dict(DEFAULT_CONFIG, effect_screenshot_enabled=value))

    @patch("effect_screenshot.KillOnCloseJob", side_effect=OSError("job denied"))
    @patch("effect_screenshot.time.monotonic")
    def test_job_failure_and_worker_timeout(self, clock, job):
        context = Mock()
        context.Process.return_value.is_alive.return_value = False
        manager = EffectScreenshots(context)
        current = effect()
        game = (1, 0, 0, 1920, 1080)
        clock.return_value = 101
        manager.tick(current, game, (), True)
        clock.return_value = 101.5
        with self.assertRaises(OSError):
            manager.tick(current, game, (), True)
        context.Event.return_value.set.assert_not_called()
        self.assertIsNone(manager.process)
        manager.tick(current, game, (), True)
        self.assertEqual(context.Process.call_count, 1)
        # A timed-out child is reaped even while its effect remains active.
        process = Mock()
        process.is_alive.side_effect = [True, False, False, False]
        manager.process = process
        manager.worker_started = 90
        manager.worker_effect_id = current["effect_id"]
        manager.tick(current, game, (), True)
        process.close.assert_called_once()
        self.assertIsNone(manager.process)

    @patch("effect_screenshot.stop_process", side_effect=RuntimeError("cannot reap"))
    @patch("effect_screenshot.time.monotonic", return_value=110)
    def test_cleanup_failure_never_spawns_another_child(self, clock, stop):
        context = Mock()
        manager = EffectScreenshots(context)
        process = manager.process = Mock()
        for _ in range(3):
            with self.assertRaises(RuntimeError):
                manager.tick(effect(), (1, 0, 0, 1920, 1080), (), True)
            self.assertIs(manager.process, process)
        context.Process.assert_not_called()

    @patch("effect_screenshot.KillOnCloseJob")
    @patch("effect_screenshot.time.monotonic")
    def test_once_after_visible_paint_and_fifty_flash(self, clock, job):
        context = Mock()
        manager = EffectScreenshots(context)
        current = effect()
        game = (1, 0, 0, 1920, 1080)
        clock.return_value = 100.6
        manager.tick(current, game, (2, 3, 4), True)
        context.Process.assert_not_called()
        clock.return_value = 100.9
        manager.tick(current, game, (2, 3, 4), True)
        manager.tick(current, game, (2, 3, 4), True)
        self.assertEqual(context.Process.call_count, 1)
        self.assertTrue(current["screenshot_attempted"])
        context.Process.return_value.is_alive.return_value = False
        manager.close()
        job.return_value.close.assert_called_once()
        manager.tick(current, game, (2, 3, 4), True)
        self.assertEqual(context.Process.call_count, 1)

        manager = EffectScreenshots(context)
        current = effect(50)
        clock.return_value = 101
        manager.tick(current, game, (2, 3, 4), True)
        clock.return_value = 101.5
        manager.tick(current, game, (2, 3, 4), True)
        self.assertNotIn("screenshot_attempted", current)
        clock.return_value = 102.1
        manager.tick(current, game, (2, 3, 4), True)
        self.assertTrue(current["screenshot_attempted"])

    @patch("effect_screenshot.time.monotonic")
    def test_disabled_hidden_expired_spawn_failure(self, clock):
        context = Mock()
        manager = EffectScreenshots(context)
        current = effect()
        clock.return_value = 101
        manager.tick(current, (1, 0, 0, 1920, 1080), (2, 3, 4), False)
        manager.tick(current, None, (2, 3, 4), True)
        clock.return_value = 104
        manager.tick(current, (1, 0, 0, 1920, 1080), (2, 3, 4), True)
        clock.return_value = 104.5
        manager.tick(current, (1, 0, 0, 1920, 1080), (2, 3, 4), True)
        context.Process.assert_not_called()
        manager = EffectScreenshots(context)
        context.Process.return_value.pid = None
        context.Process.return_value.start.side_effect = OSError("cannot spawn")
        clock.return_value = 101
        manager.tick(current, (1, 0, 0, 1920, 1080), (2, 3, 4), True)
        clock.return_value = 101.5
        with self.assertRaises(OSError):
            manager.tick(current, (1, 0, 0, 1920, 1080), (2, 3, 4), True)
        self.assertTrue(current["screenshot_attempted"])
        self.assertIsNone(manager.process)

    @patch("effect_screenshot.desktop_directory")
    @patch("effect_screenshot.time.monotonic", return_value=101)
    @patch("result_detector.WinApi")
    @patch("mss.mss")
    def test_exact_desktop_pixels_and_alt_tab_rejection(self, mss_factory, api_factory, clock, desktop_path):
        client = {"left": -640, "top": 0, "width": 640, "height": 360}
        target = {"hwnd": 1, "pid": 10, "client": client, "bounds": (-640, 0, 0, 360)}
        api = api_factory.return_value
        api.game_target.return_value = target
        api.user32.IsWindowVisible.return_value = True
        api.user32.GetForegroundWindow.return_value = 1
        desktop = mss_factory.return_value.__enter__.return_value
        desktop.monitors = [client]
        # This represents the final compositor pixels, including a gold effect.
        pixels = Image.new("RGB", (640, 360), "navy")
        draw = ImageDraw.Draw(pixels)
        draw.rectangle((70, 168, 570, 278), fill=(43, 33, 8), outline=(255, 216, 74), width=3)
        self.assertTrue(contains_effect_banner(pixels, 5))
        desktop.grab.return_value = Mock(size=(640, 360), rgb=pixels.tobytes())
        with tempfile.TemporaryDirectory() as directory:
            desktop_path.return_value = Path(directory)
            _save_visible_effect(effect(), (1, -640, 0, 640, 360), (2, 3, 4), 103)
            path = Path(directory) / screenshot_name(effect())
            with Image.open(path) as saved:
                self.assertEqual(saved.tobytes(), pixels.tobytes())
            desktop.grab.assert_called_once_with(client)
            path.unlink()
            # A painted window elsewhere over the AC6 client is part of the
            # user's visible composition and must not reject the screenshot.
            api.region_unobscured.return_value = False
            _save_visible_effect(effect(), (1, -640, 0, 640, 360), (2, 3, 4), 103)
            self.assertTrue(path.exists())
            api.region_unobscured.assert_not_called()
            path.unlink()
            api.user32.GetForegroundWindow.return_value = 0
            _save_visible_effect(effect(), (1, -640, 0, 640, 360), (2, 3, 4), 103)
            self.assertFalse(path.exists())
            api.user32.GetForegroundWindow.return_value = 1
            desktop.grab.return_value = Mock(size=(640, 360), rgb=bytes(640 * 360 * 3))
            _save_visible_effect(effect(), (1, -640, 0, 640, 360), (2, 3, 4), 103)
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
