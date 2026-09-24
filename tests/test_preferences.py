"""preferences.json: additive settings with one-generation rollback safety."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config_utils  # noqa: E402
import preferences  # noqa: E402
import settings_window  # noqa: E402

KEY = "player_streak_status_enabled"
# The key set the previous build (main 89f5f4a, and v1.2.0) accepts in
# config.json.  That build refuses to start on any other key, so this set is
# frozen: additive settings belong in preferences.json.
FROZEN_V18_CONFIG_KEYS = {
    "config_version", "port", "stats_enabled", "result_detector_enabled",
    "effect_screenshot_enabled", "effect_enabled", "overlay_stats_scope",
}


class Isolated(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="ac6-preferences-")
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.config = root / "config.json"
        self.config.write_text(json.dumps(dict(config_utils.DEFAULT_CONFIG, port=9123)), encoding="utf-8")
        self.path = root / "preferences.json"
        patcher = patch.object(config_utils, "CONFIG_PATH", self.config)
        patcher.start()
        self.addCleanup(patcher.stop)
        config_utils._last_good = config_utils._last_good_signature = None
        preferences._cache = None
        self.addCleanup(setattr, preferences, "_cache", None)

    def write(self, raw):
        text = raw if isinstance(raw, str) else json.dumps(raw)
        self.path.write_text(text, encoding="utf-8")
        preferences._cache = None


class FrozenConfigTests(unittest.TestCase):
    def test_config_json_schema_stays_what_the_previous_build_accepts(self):
        self.assertEqual(set(config_utils.DEFAULT_CONFIG), FROZEN_V18_CONFIG_KEYS,
                         "add new settings to preferences.DEFAULTS, never to config.json")
        self.assertEqual(config_utils.CONFIG_VERSION, 18)
        self.assertNotIn(KEY, config_utils.DEFAULT_CONFIG)
        with self.assertRaises(ValueError):
            config_utils.validate_config(dict(config_utils.DEFAULT_CONFIG, **{KEY: False}))

    def test_preference_keys_never_overlap_config_keys(self):
        self.assertFalse(set(preferences.DEFAULTS) & FROZEN_V18_CONFIG_KEYS)
        self.assertEqual(settings_window.PREFERENCE_KEYS, tuple(preferences.DEFAULTS))
        self.assertEqual(set(settings_window.CONFIG_KEYS) & set(settings_window.PREFERENCE_KEYS), set())


class ValidationTests(Isolated):
    def test_missing_file_is_every_default_and_is_never_created_by_reading(self):
        self.assertEqual(preferences.load(), {KEY: True})
        self.assertFalse(self.path.exists())

    def test_current_version_is_strict(self):
        self.write({"preferences_version": 1, KEY: False})
        self.assertEqual(preferences.load(), {KEY: False})
        for bad in ({"preferences_version": 1, KEY: "false"},
                    {"preferences_version": 1, KEY: 0},
                    {"preferences_version": 1, "surprise": True},
                    {KEY: False},
                    {"preferences_version": "1"},
                    {"preferences_version": 0},
                    {"preferences_version": True},
                    [],
                    "{not json"):
            with self.subTest(bad=bad):
                self.write(bad)
                with self.assertRaises(ValueError):
                    preferences.load()

    def test_one_version_newer_is_accepted_with_known_keys_still_checked(self):
        newer = {"preferences_version": 2, KEY: False, "broadcast_show_best_streak": True,
                 "broadcast_anchor": "top-right", "broadcast_scale": 1.25, "note": None}
        self.write(newer)
        self.assertEqual(preferences.load(), {KEY: False}, "unknown newer keys are never interpreted")
        for bad in ({"preferences_version": 2, KEY: "no"},
                    {"preferences_version": 2, "Bad-Name": 1},
                    {"preferences_version": 2, "nested": {"a": 1}},
                    {"preferences_version": 2, "listed": [1]},
                    {"preferences_version": 2, "x" * 65: 1}):
            with self.subTest(bad=bad):
                self.write(bad)
                with self.assertRaises(ValueError):
                    preferences.load()

    def test_more_than_one_version_newer_is_refused(self):
        self.write({"preferences_version": 3, KEY: False})
        with self.assertRaisesRegex(ValueError, "more than one version newer"):
            preferences.load()

    def test_oversized_file_is_refused(self):
        self.write('{"preferences_version": 1, "%s": true}' % KEY + " " * preferences.MAX_BYTES)
        with self.assertRaises(ValueError):
            preferences.load()

    def test_renderer_keeps_the_last_value_on_a_bad_file_and_reports_once(self):
        self.write({"preferences_version": 1, KEY: False})
        self.assertIs(preferences.effective(KEY, True), False)
        self.write("{broken")
        with patch("builtins.print") as printed:
            self.assertIs(preferences.effective(KEY, False), False)
            self.assertIs(preferences.effective(KEY, False), False)
        self.assertEqual(printed.call_count, 1)
        self.path.unlink()
        preferences._cache = None
        self.assertIs(preferences.effective(KEY, False), True, "no file = default")


class SaveTests(Isolated):
    def test_saving_the_default_writes_nothing(self):
        self.assertFalse(preferences.save({KEY: True}))
        self.assertFalse(self.path.exists())

    def test_save_is_atomic_versioned_and_round_trips(self):
        self.assertTrue(preferences.save({KEY: False}))
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")),
                         {"preferences_version": 1, KEY: False})
        self.assertEqual(preferences.load(), {KEY: False})
        self.assertTrue(preferences.save({KEY: True}))
        self.assertEqual(preferences.load(), {KEY: True})
        self.assertEqual(list(self.path.parent.glob(".preferences-*.tmp")), [])

    def test_save_preserves_what_a_newer_build_wrote(self):
        newer = {"preferences_version": 2, KEY: True, "broadcast_show_best_streak": False}
        self.write(newer)
        self.assertTrue(preferences.save({KEY: False}))
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")),
                         dict(newer, **{KEY: False}), "rolling forward again keeps its settings")

    def test_invalid_or_too_new_files_are_never_overwritten(self):
        for bad in ("{broken", json.dumps({"preferences_version": 3, KEY: True}),
                    json.dumps({"preferences_version": 1, "surprise": 1})):
            with self.subTest(bad=bad):
                self.write(bad)
                before = self.path.read_bytes()
                with self.assertRaises(ValueError):
                    preferences.save({KEY: False})
                self.assertEqual(self.path.read_bytes(), before)

    def test_bad_values_and_keys_are_refused_before_any_write(self):
        for values in ({KEY: "off"}, {KEY: 0}, {"port": 1}, {"surprise": True}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                preferences.save(values)
        self.assertFalse(self.path.exists())

    def test_concurrent_edit_is_reported_and_preserved(self):
        self.write({"preferences_version": 1, KEY: True})
        changed = json.dumps({"preferences_version": 1, KEY: True}, indent=4)

        def race(descriptor):
            self.path.write_text(changed, encoding="utf-8")

        with patch("preferences.os.fsync", side_effect=race):
            with self.assertRaises(OSError):
                preferences.save({KEY: False})
        self.assertEqual(self.path.read_text(encoding="utf-8"), changed)


class SettingsRoutingTests(Isolated):
    def test_settings_write_the_preference_outside_config_json(self):
        config_before = self.config.read_bytes()
        settings_window.save_settings({KEY: False})
        self.assertEqual(self.config.read_bytes(), config_before, "config.json untouched")
        self.assertIs(settings_window.read_settings()[KEY], False)
        settings_window.save_settings({"effect_enabled": False, "overlay_stats_scope": "lifetime",
                                       "effect_screenshot_enabled": False, KEY: False})
        stored = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertEqual(set(stored), FROZEN_V18_CONFIG_KEYS)
        self.assertNotIn(KEY, stored)
        config_utils.validate_config(stored)

    def test_a_refused_preference_leaves_config_json_unchanged(self):
        self.write("{broken")
        config_before = self.config.read_bytes()
        with self.assertRaises(ValueError):
            settings_window.save_settings({"effect_enabled": False, KEY: False})
        self.assertEqual(self.config.read_bytes(), config_before)

    def test_an_invalid_config_leaves_preferences_unchanged(self):
        self.config.write_text('{"config_version": 18, "unexpected": 1}', encoding="utf-8")
        with self.assertRaises(ValueError):
            settings_window.save_settings({"effect_enabled": False, KEY: False})
        self.assertFalse(self.path.exists())

    def test_read_settings_refuses_an_invalid_preferences_file(self):
        self.write({"preferences_version": 1, KEY: "yes"})
        with self.assertRaises(ValueError):
            settings_window.read_settings()


if __name__ == "__main__":
    unittest.main()
