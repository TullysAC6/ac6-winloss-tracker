"""T0: every pre-#14 pixel assertion is still enforced, by T1 or by T0.

tests/fixtures/legacy-coverage.json lists each assertion the old
tests/test_detector.py and tests/manifest.json made. An assertion migrated to T1
must be encoded, at least as strictly, in the named fixture record; one retained
in T0 must still be present in tests/test_detector.py. Nothing may be dropped,
and the old manifest must not come back as a second truth registry.
"""
import ast
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
sys.path.insert(0, str(ROOT / "tests"))

from t1.strict_json import loads_strict  # noqa: E402

RETAINED_MARKERS = {
    "synthetic.black_frame": "black_synthetic",
    "synthetic.disconnected_bright_blocks": "disconnected_bright_blocks",
    "synthetic.bright_center_gap": "bright_center_gap",
    "synthetic.phase_shape_on_bright_gameplay": "phase_shape_on_bright_gameplay",
    "synthetic.final_win_with_side_cyan_noise": "final_win_with_side_cyan_noise",
    "templates.invalid_length_rejected": "template schema validation",
}


def records():
    found = {}
    for path in (FIXTURES / "results").glob("*/*.json"):
        record = loads_strict(path.read_bytes(), str(path))
        found[record["id"]] = record
    return found


class LegacyCoverageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mapping = loads_strict((FIXTURES / "legacy-coverage.json").read_bytes(), "legacy-coverage.json")
        cls.records = records()
        cls.entries = cls.mapping["assertions"]

    def test_the_mapping_is_complete_and_unique(self):
        self.assertEqual(self.mapping["schema_version"], 1)
        ids = [entry["legacy_id"] for entry in self.entries]
        self.assertEqual(len(ids), len(set(ids)), "a legacy assertion is listed twice")
        for entry in self.entries:
            self.assertIn(entry["disposition"], ("t1", "t0_retained"), entry["legacy_id"])
        manifest = [entry for entry in self.entries if entry["legacy_id"].startswith("manifest.")]
        self.assertEqual(len(manifest), 25, "all 25 manifest entries must be mapped")
        legacy_files = sorted(path.name for path in FIXTURES.glob("*.ppm"))
        self.assertEqual(sorted(entry["manifest_key"].split("/", 1)[1] for entry in manifest), legacy_files)
        retained = {entry["legacy_id"] for entry in self.entries if entry["disposition"] == "t0_retained"}
        self.assertEqual(retained, set(RETAINED_MARKERS))

    def test_migrated_assertions_are_encoded_in_their_t1_case(self):
        for entry in self.entries:
            if entry["disposition"] != "t1":
                continue
            with self.subTest(entry["legacy_id"]):
                record = self.records.get(entry["case"])
                self.assertIsNotNone(record, f"{entry['case']} does not exist")
                expected = entry["t1_check"]
                if "steps" in expected:
                    for step_id, step_check in expected["steps"].items():
                        actual = record["checks"]["steps"][step_id]
                        for key, value in step_check.items():
                            if isinstance(value, dict):
                                for sub_key, sub_value in value.items():
                                    self.assertEqual(actual[key][sub_key], sub_value, f"{step_id}.{key}.{sub_key}")
                            else:
                                self.assertEqual(actual[key], value, f"{step_id}.{key}")
                    continue
                self.assertEqual(record["checks"]["frame_class"], expected["frame_class"])
                if "manifest_key" in entry:
                    self.assertEqual(record["provenance"]["legacy"],
                                     {"manifest_key": entry["manifest_key"], "manifest_expected": entry["manifest_expected"]})
                    self.assertEqual(record["checks"]["frame_class"], entry["manifest_expected"])
                for debug_check in expected.get("debug", []):
                    self.assertIn(debug_check, record["checks"].get("debug", []))

    def test_retained_assertions_are_still_in_t0(self):
        source = (ROOT / "tests" / "test_detector.py").read_text(encoding="utf-8")
        for entry in self.entries:
            if entry["disposition"] == "t0_retained":
                with self.subTest(entry["legacy_id"]):
                    self.assertEqual(entry["location"], "tests/test_detector.py")
                    self.assertIn(RETAINED_MARKERS[entry["legacy_id"]], source)

    def test_stored_pixel_truth_has_one_registry(self):
        self.assertFalse((ROOT / "tests" / "manifest.json").exists(),
                         "tests/manifest.json would be a second truth registry beside the T1 records")
        # Judge what the code uses, not what its docstring explains.
        tree = ast.parse((ROOT / "tests" / "test_detector.py").read_text(encoding="utf-8"))
        docstring = ast.get_docstring(tree, clean=False)
        literals = [node.value for node in ast.walk(tree)
                    if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value != docstring]
        self.assertFalse(any("manifest.json" in literal for literal in literals))
        for migrated in ("video_draw_1_2", "video_false_draw_combat", "video_false_garage", "video_dark_gameplay",
                         "video_missed_final_win"):
            self.assertFalse(any(migrated in literal for literal in literals),
                             f"{migrated} is replayed by T1; T0 must not assert it again")


if __name__ == "__main__":
    unittest.main(verbosity=2)
