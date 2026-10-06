"""T0: self-rank S abstention, determinism, dormant boundary and canonical data contract.

Recognition of genuine stored pixel pairs is owned exclusively by formal T1. Tests
here that pair crops from different frames, blank regions or shift pixels are
constructed stage probes over dev assets: they are never canonical truth.
"""
import ast
import copy
import dataclasses
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
import self_rank as s
from t1 import compare, schema
from t1.corpus import CorpusError, load_corpus
from t1.images import decode_image
from t1.runner import _spec
from t1.strict_json import MetadataError

FIXTURES = ROOT / "tests" / "fixtures"
RANKS = FIXTURES / "ranks"
TEMPLATE = RANKS / "s_rank" / "dev-s-matching.json"
FIELDS = ("self_rank", "status", "version", "source", "reason")


def crop(path):
    decoded = decode_image(path.read_bytes(), "png")
    return np.frombuffer(decoded.bgra, np.uint8).reshape(decoded.height, decoded.width, 4).copy()


def dev_pair(name, category="s_rank"):
    return crop(RANKS / category / f"{name}-header.png"), crop(RANKS / category / f"{name}-panel.png")


def moved(pixels, dy, dx):
    """Constructed probe: move content by (dy, dx), repeating the edge pixels."""
    height, width = pixels.shape[:2]
    ys = np.clip(np.arange(height) - dy, 0, height - 1)
    xs = np.clip(np.arange(width) - dx, 0, width - 1)
    return np.ascontiguousarray(pixels[ys][:, xs])


class RecognizerTests(unittest.TestCase):
    def setUp(self):
        self.header, self.panel = dev_pair("dev-s-matching")

    def blank(self, box):
        panel = self.panel.copy()
        y0, y1, x0, x1 = box
        panel[y0:y1, x0:x1, :3] = (72, 49, 40)  # BGR panel background colour, opaque
        return panel

    def test_dev_template_pair_is_recognized_only_as_s(self):
        out = s.recognize_self_rank(self.header, self.panel)
        self.assertEqual((out.self_rank, out.status, out.reason), ("S", "recognized", "self_rank_s"))
        self.assertEqual((out.version, out.source), ("self-rank-s-lobby.v1", "direct_lobby_rank_panel"))

    def test_malformed_partial_and_nonopaque_abstain(self):
        bad = [None, [], b"pixels", np.zeros((168, 142), np.uint8), np.zeros((167, 142, 3), np.uint8),
               np.zeros((168, 143, 3), np.uint8), np.zeros((168, 142, 3), float), np.zeros((168, 142, 5), np.uint8),
               self.panel[:98], self.panel[:, :, :2]]
        for panel in bad:
            with self.subTest(panel=type(panel)):
                out = s.recognize_self_rank(self.header, panel)
                self.assertEqual((out.self_rank, out.status, out.reason), (None, "failed", "invalid_geometry_or_format"))
        for header in (None, np.zeros((60, 479, 3), np.uint8), self.header[:, :, :2]):
            self.assertEqual(s.recognize_self_rank(header, self.panel).reason, "invalid_geometry_or_format")
        transparent = self.panel.copy()
        transparent[0, 0, 3] = 254
        self.assertEqual(s.recognize_self_rank(self.header, transparent).reason, "nonopaque_evidence")
        transparent = self.header.copy()
        transparent[0, 0, 3] = 0
        self.assertEqual(s.recognize_self_rank(transparent, self.panel).reason, "nonopaque_evidence")

    def test_garbage_is_failed_deterministic_and_inputs_are_unchanged(self):
        rng = np.random.default_rng(156)
        for panel in (np.zeros((168, 142, 3), np.uint8), np.full((168, 142, 3), 255, np.uint8),
                      rng.integers(0, 256, (168, 142, 3), dtype=np.uint8), self.panel):
            header_before, panel_before = self.header.copy(), panel.copy()
            first = s.recognize_self_rank(self.header, panel)
            self.assertEqual(first, s.recognize_self_rank(self.header, panel))
            np.testing.assert_array_equal(self.header, header_before)
            np.testing.assert_array_equal(panel, panel_before)
            if panel is not self.panel:
                self.assertEqual((first.self_rank, first.status), (None, "failed"))
        bgr = s.recognize_self_rank(self.header[:, :, :3].copy(), self.panel[:, :, :3].copy())
        self.assertEqual(bgr, s.recognize_self_rank(self.header, self.panel))

    def test_complete_header_context_is_necessary_and_never_enough(self):
        # Constructed pairing: a genuine S panel with genuine non-supported headers.
        for name in ("dev-system-menu", "dev-garage-license", "dev-custom-room", "dev-custom-result",
                     "dev-intro-opponent", "dev-dimmed-start"):
            header, _ = dev_pair(name, "negatives")
            with self.subTest(context=name):
                self.assertEqual(s.recognize_self_rank(header, self.panel).reason, "unsupported_context")
        # ...and the genuine header with genuine non-badge panels never yields S.
        for name in ("dev-system-menu", "dev-garage-license", "dev-custom-room", "dev-custom-result",
                     "dev-intro-opponent", "dev-dimmed-start"):
            _, panel = dev_pair(name, "negatives")
            with self.subTest(panel=name):
                out = s.recognize_self_rank(self.header, panel)
                self.assertEqual((out.self_rank, out.status), (None, "failed"))
                self.assertIn(out.reason, ("badge_frame_absent", "rank_label_absent", "s_glyph_absent"))

    def test_every_panel_condition_is_required(self):
        # Constructed probes on the pinned dev pixels.
        self.assertEqual(s.recognize_self_rank(self.header, self.blank(s.LABEL_BOX)).reason, "rank_label_absent")
        self.assertEqual(s.recognize_self_rank(self.header, self.blank(s.S_BOX)).reason, "s_glyph_absent")
        # No genuine numberless (outside top-100) S exists, so that presentation abstains.
        self.assertEqual(s.recognize_self_rank(self.header, self.blank(s.DIGIT_BOX)).reason, "place_number_absent")
        y0, y1, x0, x1 = s.FRAME_BOX
        no_frame = self.panel.copy()
        no_frame[y0 - 4:y1 + 4, x0 - 4:x1 + 4, :3] = (72, 49, 40)
        self.assertEqual(s.recognize_self_rank(self.header, no_frame).reason, "badge_frame_absent")
        mirrored = self.panel.copy()
        y0, y1, x0, x1 = s.S_BOX
        mirrored[y0:y1, x0:x1] = mirrored[y0:y1, x0:x1][:, ::-1]
        self.assertEqual(s.recognize_self_rank(self.header, mirrored).reason, "s_glyph_absent")

    def test_registration_window_is_bounded(self):
        # Constructed displacement probes: inside the fixed window passes, outside abstains.
        for dy, dx in ((0, 2), (2, 0), (-2, -2), (1, 3), (3, -3)):
            with self.subTest(inside=(dy, dx)):
                self.assertEqual(s.recognize_self_rank(self.header, moved(self.panel, dy, dx)).status, "recognized")
        for dy, dx in ((0, 5), (5, 0), (-6, 0), (0, -6)):
            with self.subTest(outside=(dy, dx)):
                self.assertEqual(s.recognize_self_rank(self.header, moved(self.panel, dy, dx)).status, "failed")

    def test_glyph_decision_ignores_a_source_tone_curve(self):
        # Constructed probe: an affine tone change of the S box (as between video
        # sources) keeps the glyph decision; brightness is judged by header and label.
        y0, y1, x0, x1 = s.S_BOX
        for gain, offset in ((0.8, -10), (0.6, 0), (1.1, 5)):
            panel = self.panel.copy()
            box = panel[y0:y1, x0:x1, :3].astype(np.float64) * gain + offset
            panel[y0:y1, x0:x1, :3] = np.clip(np.rint(box), 0, 255).astype(np.uint8)
            with self.subTest(gain=gain, offset=offset):
                self.assertEqual(s.recognize_self_rank(self.header, panel).status, "recognized")
        self.assertEqual(s._correlation(np.zeros((3, 3)), np.ones((3, 3))), 0.0)

    def test_dimmed_s_pixels_are_not_a_supported_presentation(self):
        # Constructed pairing: the lit header with the genuine dimmed dev panel.
        _, dimmed = dev_pair("dev-dimmed-start", "negatives")
        out = s.recognize_self_rank(self.header, dimmed)
        self.assertEqual((out.self_rank, out.status), (None, "failed"))
        self.assertIn(out.reason, ("rank_label_absent", "s_glyph_absent"))

    def test_output_contract_has_no_overclaim(self):
        self.assertEqual(tuple(f.name for f in dataclasses.fields(s.SelfRankRecognition)), FIELDS)
        self.assertEqual(s.SelfRankRecognition(), s.SelfRankRecognition(None, "failed", s.VERSION, s.SOURCE,
                                                                          "insufficient_evidence"))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            s.SelfRankRecognition().self_rank = "S"
        source = (ROOT / "self_rank.py").read_text(encoding="utf-8")
        for forbidden in ("match_type", "match_format", "rating", "place", "opponent", "confidence", "probability"):
            self.assertNotIn(forbidden + " =", source.lower())
        self.assertEqual(source.count('SelfRankRecognition("S", "recognized"'), 1)

    def test_no_clock_state_io_or_runtime_import_boundary(self):
        tree = ast.parse((ROOT / "self_rank.py").read_text(encoding="utf-8"))
        imports = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        imports |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        self.assertEqual(imports, {"__future__", "base64", "dataclasses", "zlib", "numpy", "match_header"})
        calls = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        self.assertFalse(calls & {"open", "print", "input", "exec", "eval", "__import__"})
        production = [p for p in ROOT.iterdir() if p.suffix in (".py", ".pyw") and p.name != "self_rank.py"]
        self.assertTrue(production)
        for path in production:
            text = path.read_text(encoding="utf-8")
            for needle in ("import self_rank", "from self_rank", "recognize_self_rank"):
                self.assertNotIn(needle, text, f"{path.name} must not call the dormant recognizer")
        self.assertEqual((s.VERSION, s.SOURCE), ("self-rank-s-lobby.v1", "direct_lobby_rank_panel"))

    def test_embedded_model_comes_only_from_pinned_dev_pixels(self):
        # Asset/model integrity, not a recognition-output assertion.
        record = json.loads(TEMPLATE.read_text(encoding="utf-8"))
        self.assertTrue(record["provenance"]["template_source"])
        self.assertEqual(record["provenance"]["split"], "dev")
        bgr = self.panel[:, :, :3]
        y0, y1, x0, x1 = s.S_BOX
        np.testing.assert_array_equal(s._S_RED, bgr[y0:y1, x0:x1, 2])
        y0, y1, x0, x1 = s.LABEL_BOX
        np.testing.assert_array_equal(s._LABEL, s._label_ink(bgr)[y0:y1, x0:x1])

    def test_thresholds_are_fixed_values(self):
        self.assertEqual((s.WINDOW, s.MIN_FRAME_CONTRAST, s.MAX_FRAME_INNER, s.MIN_LABEL, s.MIN_GLYPH,
                          s.MIN_DIGIT_INK), (3, 120.0, 40.0, 0.90, 0.95, 200))
        self.assertEqual(s.PANEL_SHAPE, (168, 142))
        self.assertEqual(s.HEADER_SHAPE, (60, 480))


class FixtureContractTests(unittest.TestCase):
    def setUp(self):
        self.record = json.loads(TEMPLATE.read_text(encoding="utf-8"))
        self.negative = json.loads((RANKS / "negatives" / "dev-custom-room.json").read_text(encoding="utf-8"))
        self.tags = set(json.loads((FIXTURES / "required-coverage.json").read_text(encoding="utf-8"))["required"])

    def validate(self, record):
        return schema.validate_image_record("fixture", record, record["category"], self.tags)

    def test_strict_schema_refuses_invented_truth_bad_provenance_and_keys(self):
        mutations = [
            lambda r: r.update(extra=1), lambda r: r.pop("context"),
            lambda r: r["truth"].update(self_rank="A4"), lambda r: r["truth"].update(self_rank="unknown"),
            lambda r: r["truth"].update(screen="custom_room"), lambda r: r["truth"].update(match_type="ranked"),
            lambda r: r["checks"].update(self_rank=None), lambda r: r["checks"].update(status="failed"),
            lambda r: r["checks"].update(version="rank-single-header.v1"),
            lambda r: r["checks"].update(adapter="match_header.v1"), lambda r: r["checks"].update(rating="2150"),
            lambda r: r["provenance"].update(synthetic=True), lambda r: r["provenance"].update(split="test"),
            lambda r: r["provenance"].update(timestamp_ms=True),
            lambda r: r["provenance"].update(crop_xyxy=[602, 432, 744, 600]),
            lambda r: r["provenance"].update(context_crop_xyxy=[0, 0, 480, 60]),
            lambda r: r["provenance"].update(source_dimensions=[2560, 1440]),
            lambda r: r["provenance"].update(reference="https://example.invalid/"),
            lambda r: r["input"].update(width=140), lambda r: r["context"].update(height=59),
            lambda r: r["context"].update(path="ranks/s_rank/dev-s-idle-header.png"),
            lambda r: r.update(category="negatives"), lambda r: r.update(id="metadata.dev-s-matching"),
            lambda r: r["review"].update(corrections=[{}]),
            lambda r: r["context"].update(width=480.0), lambda r: r["context"].update(height=60.0),
            lambda r: r["checks"].update(abstention="panel"), lambda r: r["checks"].pop("abstention"),
        ]
        self.validate(copy.deepcopy(self.record))
        for index, change in enumerate(mutations):
            record = copy.deepcopy(self.record)
            change(record)
            with self.subTest(mutation=index), self.assertRaises(MetadataError):
                self.validate(record)

    def test_negative_records_cannot_claim_a_rank_or_the_template(self):
        self.validate(copy.deepcopy(self.negative))
        for change in (lambda r: r["truth"].update(self_rank="S"), lambda r: r["checks"].update(self_rank="S"),
                       lambda r: r["checks"].update(status="recognized"),
                       lambda r: r["provenance"].update(template_source=True),
                       lambda r: r["truth"].update(screen="lobby_matching"),
                       lambda r: r["checks"].update(abstention=None), lambda r: r["checks"].update(abstention="panel"),
                       lambda r: r["checks"].update(abstention="header")):
            record = copy.deepcopy(self.negative)
            change(record)
            with self.assertRaises(MetadataError):
                self.validate(record)

    def test_comparator_requires_exact_fields_and_values(self):
        actual = {key: value for key, value in self.record["checks"].items() if key not in ("adapter", "abstention")}
        actual["reason"] = "self_rank_s"
        self.assertEqual(compare.compare_image(self.record, actual), [])
        for key in ("self_rank", "status", "version", "source"):
            bad = dict(actual)
            bad[key] = "incorrect"
            self.assertTrue(compare.compare_image(self.record, bad))
        overclaim = dict(actual, match_type="ranked")
        self.assertTrue(compare.compare_image(self.record, overclaim))
        missing = {key: value for key, value in actual.items() if key != "self_rank"}
        self.assertTrue(compare.compare_image(self.record, missing))
        negative_actual = {"self_rank": None, "status": "failed", "version": s.VERSION, "source": s.SOURCE,
                           "reason": "unsupported_context"}
        self.assertEqual(compare.compare_image(self.negative, negative_actual), [])
        # The abstention stage must match the reviewed truth, and a positive never abstains.
        for reason in ("badge_frame_absent", "invalid_geometry_or_format", "unknown_reason", None):
            self.assertTrue(compare.compare_image(self.negative, dict(negative_actual, reason=reason)))
        panel_record = json.loads((RANKS / "negatives" / "dev-crossfade-system.json").read_text(encoding="utf-8"))
        self.assertEqual(compare.compare_image(panel_record, dict(negative_actual, reason="badge_frame_absent")), [])
        self.assertTrue(compare.compare_image(panel_record, negative_actual))
        self.assertTrue(compare.compare_image(self.record, dict(actual, reason="badge_frame_absent")))
        self.assertTrue(compare.compare_image(self.negative, {k: v for k, v in negative_actual.items()
                                                              if k != "self_rank"}))

    def test_worker_spec_carries_both_crops_but_never_truth(self):
        corpus = load_corpus(FIXTURES)
        case = next(c for c in corpus.cases if c.id == "ranks.val-s-one")
        spec = _spec(case, corpus, ROOT, FIXTURES, [])
        self.assertEqual(set(spec["images"]), {"ranks.val-s-one", "ranks.val-s-one#context"})
        self.assertEqual(spec["adapter"], "self_rank.v1")
        text = json.dumps(spec)
        for leaked in ('"truth"', '"checks"', '"self_identity"', '"recognized"', '"split"'):
            self.assertNotIn(leaked, text)


class CorpusTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="ac6-t0-ranks-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name) / "fixtures"
        shutil.copytree(FIXTURES, self.root)

    def rewrite(self, relative, change):
        path = self.root / relative
        record = json.loads(path.read_text(encoding="utf-8"))
        change(record)
        path.write_text(json.dumps(record), encoding="utf-8")

    def test_committed_rank_corpus_is_bounded_and_split_by_video(self):
        corpus = load_corpus(self.root)
        ranks = [r for r in corpus.images.values() if r["family"] == "ranks"]
        self.assertEqual(len(ranks), 22)
        self.assertEqual(sum(r["category"] == "s_rank" for r in ranks), 10)
        videos = {}
        for record in corpus.images.values():
            if record["family"] in ("ranks", "match_metadata"):
                videos.setdefault(record["provenance"]["video_id"], set()).add(record["provenance"]["split"])
        self.assertEqual(videos, {"aRnhg9vs-zs": {"dev"}, "WabYwwGakEQ": {"dev"},
                                  "xJ86EUS510w": {"validation"}, "y86wDex-EQI": {"validation"}})
        positives = {r["provenance"]["video_id"] for r in ranks if r["category"] == "s_rank"
                     and r["provenance"]["split"] == "validation"}
        self.assertEqual(len(positives), 2, "two distinct validation sessions")

    def test_a_video_cannot_cross_splits_even_across_families(self):
        self.rewrite("ranks/negatives/val-mode-select.json", lambda r: r["provenance"].update(split="dev"))
        with self.assertRaisesRegex(CorpusError, "must not cross dev/validation"):
            load_corpus(self.root)

    def test_exactly_one_pinned_template(self):
        self.rewrite("ranks/s_rank/dev-s-matching.json", lambda r: r["provenance"].update(template_source=False))
        with self.assertRaisesRegex(CorpusError, "pinned dev template"):
            load_corpus(self.root)

    def test_missing_context_crop_fails(self):
        (self.root / "ranks/s_rank/val-s-two-header.png").unlink()
        with self.assertRaises(CorpusError):
            load_corpus(self.root)

    def test_shared_context_image_fails(self):
        def share(record):
            other = json.loads((self.root / "ranks/s_rank/dev-s-idle.json").read_text(encoding="utf-8"))
            record["context"] = other["context"]
        self.rewrite("ranks/s_rank/dev-s-place-fifteen.json", share)
        (self.root / "ranks/s_rank/dev-s-place-fifteen-header.png").unlink()
        with self.assertRaisesRegex(CorpusError, "pair|already described"):
            load_corpus(self.root)

    def test_reserved_rank_categories_still_refuse_records(self):
        target = self.root / "ranks" / "pre_s" / "a4.json"
        shutil.copy(RANKS / "s_rank" / "dev-s-idle.json", target)
        with self.assertRaisesRegex(CorpusError, "category"):
            load_corpus(self.root)


if __name__ == "__main__":
    unittest.main(verbosity=2)
