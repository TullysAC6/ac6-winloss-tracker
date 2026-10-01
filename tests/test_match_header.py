"""T0: malformed evidence, deterministic abstention and canonical data contract.

Recognition of genuine stored pixels is owned exclusively by formal T1.
"""
import ast
import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
import match_header as m
from t1 import schema, compare
from t1.images import decode_image
from t1.strict_json import MetadataError

RECORD = ROOT / "tests/fixtures/match_metadata/ranked_single/dev-header.json"


class HeaderTests(unittest.TestCase):
    def test_malformed_partial_and_nonopaque_abstain(self):
        values = [None, [], b"pixels", np.zeros((60, 480), np.uint8),
                  np.zeros((59, 480, 3), np.uint8), np.zeros((60, 479, 3), np.uint8),
                  np.zeros((60, 480, 3), float), np.zeros((60, 480, 5), np.uint8),
                  np.zeros((60, 480, 4), np.uint8)]
        for value in values:
            with self.subTest(type=type(value)):
                out = m.recognize_header(value)
                self.assertEqual((out.match_type, out.match_format, out.status), ("unknown", "unknown", "failed"))

    def test_garbage_is_unknown_and_input_is_unchanged(self):
        rng = np.random.default_rng(15)
        for pixels in [np.zeros((60, 480, 3), np.uint8), np.full((60, 480, 3), 255, np.uint8),
                       rng.integers(0, 256, (60, 480, 3), dtype=np.uint8)]:
            before = pixels.copy()
            first = m.recognize_header(pixels)
            self.assertEqual(first, m.recognize_header(pixels))
            self.assertEqual(first.match_type, "unknown")
            np.testing.assert_array_equal(pixels, before)

    def test_every_glyph_is_required_even_if_global_score_would_pass(self):
        self.assertEqual(len(m._GLYPHS), 16)  # RANK(4) MATCH(5) colon(1) SINGLE(6)
        # Constructed white-ink model inputs, not original-image fixture replay.
        for left, right in m._GLYPHS:
            pixels = np.repeat((m._REFERENCE.astype(np.uint8) * 255)[:, :, None], 3, axis=2)
            pixels[:, left:right] = 0
            self.assertEqual(m.recognize_header(pixels).status, "failed")

    def test_border_noise_cannot_merge_letters_or_hide_a_partial_letter(self):
        pixels = np.repeat((m._REFERENCE.astype(np.uint8) * 255)[:, :, None], 3, axis=2)
        pixels[4, 131:149] = 255  # the bright UI border that previously joined M/A
        pixels[:, 178:192] = 0  # independently reproduced partial-A false positive
        self.assertEqual(m.recognize_header(pixels).status, "failed")

    def test_one_pixel_edge_variation_has_bounded_tolerance(self):
        # Constructed morphology probes fix the edge mechanism; no holdout tuning.
        self.assertEqual(m._f1(np.array([[False, True, False]]),
                               np.array([[True, False, False]])), 1.0)
        self.assertEqual(m._f1(np.array([[True, False, False, False]]),
                               np.array([[False, False, False, True]])), 0.0)
        self.assertEqual(m._f1(np.ones((2,2), bool), np.zeros((2,2), bool)), 0.0)

    def test_no_clock_state_io_or_runtime_import_boundary(self):
        tree = ast.parse((ROOT / "match_header.py").read_text())
        imports = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        imports |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        self.assertEqual(imports, {"__future__", "base64", "dataclasses", "zlib", "numpy"})
        for name in ("server.py", "result_detector.py", "game_capture.py", "launcher.pyw", "optional_enrichment.py"):
            self.assertNotIn("import match_header", (ROOT / name).read_text(encoding="utf-8"))
        self.assertEqual(m.VERSION, "rank-single-header.v1")
        self.assertEqual(m.SOURCE, "direct_header")


class FixtureContractTests(unittest.TestCase):
    def setUp(self):
        self.record = json.loads(RECORD.read_text())
        self.tags = set(json.loads((ROOT / 'tests/fixtures/required-coverage.json').read_text())["required"])

    def validate(self, r):
        return schema.validate_image_record('fixture', r, r['category'], self.tags)

    def test_strict_schema_refuses_invented_truth_bad_provenance_and_keys(self):
        mutations = [lambda r: r.update(schema_version=True), lambda r: r.update(extra=1),
                     lambda r: r['truth'].update(match_type='custom'),
                     lambda r: r['checks'].update(match_type='unknown'),
                     lambda r: r['provenance'].update(synthetic=True),
                     lambda r: r['provenance'].update(split='validation'),
                     lambda r: r['provenance'].update(timestamp_ms=True),
                     lambda r: r['provenance'].update(crop_xyxy=[0,0,480,60]),
                     lambda r: r['review'].update(corrections=[{}])]
        self.validate(self.record)
        for change in mutations:
            r = copy.deepcopy(self.record); change(r)
            with self.assertRaises(MetadataError): self.validate(r)

    def test_embedded_model_comes_only_from_pinned_dev_pixels(self):
        # Asset/model integrity, not a recognition-output assertion.
        path = ROOT / 'tests/fixtures' / self.record['input']['path']
        data = path.read_bytes()
        self.assertEqual(hashlib.sha256(data).hexdigest(), self.record['input']['sha256'])
        d = decode_image(data, 'png')
        bgra = np.frombuffer(d.bgra, np.uint8).reshape(d.height, d.width, 4)
        np.testing.assert_array_equal(m._REFERENCE, m._ink(bgra[:, :, :3]))

    def test_comparator_refuses_wrong_status_version_source_and_class(self):
        actual = {k:v for k,v in self.record['checks'].items() if k != 'adapter'}
        self.assertEqual(compare.compare_image(self.record, actual), [])
        for key in actual:
            bad = dict(actual); bad[key] = 'incorrect'
            self.assertTrue(compare.compare_image(self.record, bad))


if __name__ == '__main__':
    unittest.main(verbosity=2)
