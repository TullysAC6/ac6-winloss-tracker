"""T0 contract tests for the T1 fixture replay harness (issue #14).

Nothing here replays a fixture or starts a worker; T1 itself does that. These
tests pin the harness's fail-closed behaviour so it cannot quietly weaken:
strict metadata, path confinement, exact image decoding, schema and corpus
rejection, comparison, reports, guards and the seams T1 relies on in production
code. No network, port, live data or native capture is used.
"""
import ast
import copy
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import types
import unittest
import xml.etree.ElementTree as ElementTree
import zlib
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from t1 import compare, guards, images, paths, replay, report, schema  # noqa: E402
from t1.clock import ModuleTime, VirtualClock  # noqa: E402
from t1.corpus import CorpusError, load_corpus  # noqa: E402
from t1.strict_json import MetadataError, loads_strict  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"
IMAGE_RECORD = FIXTURES / "results" / "win" / "final-win.01.json"
SEQUENCE_RECORD = FIXTURES / "results" / "sequences" / "two-match-win-then-loss.json"
WGC_RECORD = FIXTURES / "results" / "sequences" / "wgc-stale-and-repeated-frames.json"


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def ppm(width, height, pixels, header=None):
    head = header if header is not None else f"P6\n{width} {height}\n255\n".encode("ascii")
    return head + bytes(pixels)


def paeth(left, up, up_left):
    estimate = left + up - up_left
    pa, pb, pc = abs(estimate - left), abs(estimate - up), abs(estimate - up_left)
    return left if pa <= pb and pa <= pc else (up if pb <= pc else up_left)


def png(width, height, rows, filters=None, extra=(), ihdr=None, trailing=b"", idat=None):
    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)

    raw = bytearray()
    previous = bytes(width * 3)
    for index, row in enumerate(rows):
        method = filters[index] if filters else 0
        out = bytearray(len(row))
        for i, value in enumerate(row):
            left = row[i - 3] if i >= 3 else 0
            up = previous[i]
            up_left = previous[i - 3] if i >= 3 else 0
            predictor = (0, left, up, (left + up) >> 1, paeth(left, up, up_left))[method]
            out[i] = (value - predictor) & 0xFF
        raw += bytes([method]) + out
        previous = row
    header = ihdr if ihdr is not None else struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    data = zlib.compress(bytes(raw)) if idat is None else idat
    body = chunk(b"IHDR", header) + b"".join(chunk(kind, content) for kind, content in extra)
    return images.PNG_SIGNATURE + body + chunk(b"IDAT", data) + chunk(b"IEND", b"") + trailing


class StrictJsonTests(unittest.TestCase):
    def test_duplicate_keys_are_refused_at_any_depth(self):
        for text in (b'{"a": 1, "a": 2}', b'{"outer": {"k": 1, "k": 1}}'):
            with self.assertRaisesRegex(MetadataError, "duplicate JSON key"):
                loads_strict(text)

    def test_non_finite_numbers_are_refused(self):
        for text in (b'{"v": NaN}', b'{"v": Infinity}', b'{"v": -Infinity}', b'{"v": 1e999}'):
            with self.assertRaisesRegex(MetadataError, "non-finite"):
                loads_strict(text)

    def test_encoding_size_and_syntax(self):
        with self.assertRaisesRegex(MetadataError, "byte order mark"):
            loads_strict(b'\xef\xbb\xbf{}')
        with self.assertRaisesRegex(MetadataError, "not UTF-8"):
            loads_strict(b'{"v": "\xff"}')
        with self.assertRaisesRegex(MetadataError, "exceeds"):
            loads_strict(b" " * (256 * 1024 + 1))
        with self.assertRaisesRegex(MetadataError, "malformed"):
            loads_strict(b'{"v": ')
        self.assertEqual(loads_strict(b'{"v": [1, 2.5, true, null]}'), {"v": [1, 2.5, True, None]})


class PathTests(unittest.TestCase):
    def test_hostile_relative_paths_are_refused(self):
        for value in ("../final_win_01.ppm", "results/../x.ppm", "/abs.ppm", "C:/x.ppm", "c:x.ppm",
                      "//server/share/x.ppm", "\\\\server\\share\\x.ppm", "a\\b.ppm", "", ".", "a//b.ppm",
                      "con.ppm", "NUL", "x.ppm:stream", "trailing.", "sp ace.ppm", "x\x00.ppm", "a" * 241):
            with self.assertRaises(paths.FixturePathError, msg=value):
                paths.validate_relative_path(value)

    def test_resolution_requires_a_regular_file_inside_the_root(self):
        with tempfile.TemporaryDirectory(prefix="ac6-t1-paths-") as name:
            root = Path(name) / "fixtures"
            (root / "results").mkdir(parents=True)
            (root / "ok.ppm").write_bytes(b"x")
            self.assertEqual(paths.resolve_fixture_file(root, "ok.ppm"), Path(os.path.realpath(root / "ok.ppm")))
            with self.assertRaisesRegex(paths.FixturePathError, "does not exist"):
                paths.resolve_fixture_file(root, "missing.ppm")
            with self.assertRaisesRegex(paths.FixturePathError, "not a regular file"):
                paths.resolve_fixture_file(root, "results")

    @unittest.skipUnless(os.name == "nt", "junctions are a Windows reparse point")
    def test_a_junction_out_of_the_root_is_refused(self):
        import _winapi
        with tempfile.TemporaryDirectory(prefix="ac6-t1-junction-") as name:
            root = Path(name) / "fixtures"
            outside = Path(name) / "outside"
            root.mkdir()
            outside.mkdir()
            (outside / "x.ppm").write_bytes(b"x")
            _winapi.CreateJunction(str(outside), str(root / "linked"))
            try:
                with self.assertRaisesRegex(paths.FixturePathError, "symlink or reparse point"):
                    paths.resolve_fixture_file(root, "linked/x.ppm")
            finally:
                os.rmdir(root / "linked")

    def test_a_symlink_is_refused(self):
        with tempfile.TemporaryDirectory(prefix="ac6-t1-symlink-") as name:
            root = Path(name) / "fixtures"
            root.mkdir()
            (Path(name) / "outside.ppm").write_bytes(b"x")
            try:
                os.symlink(Path(name) / "outside.ppm", root / "link.ppm")
            except (OSError, NotImplementedError) as error:
                self.skipTest(f"symlinks are not creatable here: {error}")
            with self.assertRaisesRegex(paths.FixturePathError, "symlink or reparse point"):
                paths.resolve_fixture_file(root, "link.ppm")


class ImageTests(unittest.TestCase):
    def test_ppm_is_copied_exactly_to_bgra(self):
        decoded = images.decode_ppm(ppm(2, 1, [1, 2, 3, 250, 128, 0]))
        self.assertEqual((decoded.width, decoded.height), (2, 1))
        self.assertEqual(decoded.bgra, bytes([3, 2, 1, 255, 0, 128, 250, 255]))

    def test_malformed_ppm_is_refused(self):
        good = ppm(2, 1, [10, 13, 10, 10, 20, 30])
        cases = {
            "comment": good.replace(b"P6\n", b"P6\n# note\n"),
            "trailing": good + b"\n",
            "truncated": good[:-1],
            "maxval": ppm(2, 1, [0] * 6, b"P6\n2 1\n65535\n"),
            "ascii": b"P3\n2 1\n255\n0 0 0 0 0 0",
            "crlf checkout": good.replace(b"\n", b"\r\n"),
            "zero size": ppm(0, 1, [], b"P6\n0 1\n255\n"),
        }
        for label, data in cases.items():
            with self.assertRaises(images.ImageError, msg=label):
                images.decode_ppm(data)

    def test_ppm_and_png_limits(self):
        with patch.object(images, "MAX_PIXELS", 3):
            with self.assertRaisesRegex(images.ImageError, "pixels"):
                images.decode_ppm(ppm(2, 2, [0] * 12))
        with patch.object(images, "MAX_IMAGE_BYTES", 10):
            with self.assertRaisesRegex(images.ImageError, "exceeds"):
                images.decode_ppm(ppm(2, 2, [0] * 12))
            with self.assertRaisesRegex(images.ImageError, "exceeds"):
                images.decode_png(png(1, 1, [bytes(3)]))

    def test_png_every_filter_decodes_exactly(self):
        width, height = 4, 5
        rows = [bytes((x * 37 + y * 11 + c * 5) % 256 for x in range(width) for c in range(3)) for y in range(height)]
        decoded = images.decode_png(png(width, height, rows, filters=[0, 1, 2, 3, 4]))
        expected = images.rgb_to_bgra(b"".join(rows), width, height)
        self.assertEqual(decoded.bgra, expected)

    def test_png_matches_pillow_when_available(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow is not installed on this interpreter")
        import io
        pixels = bytes((i * 7) % 256 for i in range(9 * 6 * 3))
        buffer = io.BytesIO()
        Image.frombytes("RGB", (9, 6), pixels).save(buffer, format="PNG", optimize=True)
        decoded = images.decode_png(buffer.getvalue())
        self.assertEqual(decoded.bgra, images.rgb_to_bgra(pixels, 9, 6))

    def test_malformed_png_is_refused(self):
        row = [bytes(range(6))]
        good = png(2, 1, row)
        bad_crc = bytearray(good)
        bad_crc[-5] ^= 0xFF
        cases = {
            "crc": bytes(bad_crc),
            "text chunk": png(2, 1, row, extra=[(b"tEXt", b"Author\x00someone")]),
            "exif chunk": png(2, 1, row, extra=[(b"eXIf", b"MM\x00*")]),
            "gamma chunk": png(2, 1, row, extra=[(b"gAMA", struct.pack(">I", 45455))]),
            "interlaced": png(2, 1, row, ihdr=struct.pack(">IIBBBBB", 2, 1, 8, 2, 0, 0, 1)),
            "rgba": png(2, 1, row, ihdr=struct.pack(">IIBBBBB", 2, 1, 8, 6, 0, 0, 0)),
            "16 bit": png(2, 1, row, ihdr=struct.pack(">IIBBBBB", 2, 1, 16, 2, 0, 0, 0)),
            "trailing": png(2, 1, row, trailing=b"x"),
            "short data": png(2, 1, row, idat=zlib.compress(b"\x00" + bytes(5))),
            "long data": png(2, 1, row, idat=zlib.compress(b"\x00" + bytes(7))),
            "filter 5": png(2, 1, row, idat=zlib.compress(b"\x05" + bytes(6))),
            "not zlib": png(2, 1, row, idat=b"not zlib"),
            "no iend": good[:-12],
            "signature": b"GIF89a" + good[8:],
        }
        for label, data in cases.items():
            with self.assertRaises(images.ImageError, msg=label):
                images.decode_png(data)
        with self.assertRaisesRegex(images.ImageError, "unsupported"):
            images.decode_image(good, "jpeg")


class SchemaTests(unittest.TestCase):
    def setUp(self):
        self.tags = set(load(FIXTURES / "required-coverage.json")["required"])
        self.image = load(IMAGE_RECORD)
        self.sequence = load(SEQUENCE_RECORD)
        self.images = {}
        for path in (FIXTURES / "results").glob("*/*.json"):
            record = load(path)
            if record["category"] != "sequences":
                self.images[record["id"]] = record

    def reject_image(self, mutate, pattern):
        record = copy.deepcopy(self.image)
        mutate(record)
        with self.assertRaisesRegex(MetadataError, pattern):
            schema.validate_image_record("record", record, "win", self.tags)

    def reject_sequence(self, mutate, pattern, base=None):
        record = copy.deepcopy(base or self.sequence)
        mutate(record)
        with self.assertRaisesRegex(MetadataError, pattern):
            schema.validate_sequence_record("record", record, self.tags, self.images)

    def test_the_committed_records_validate(self):
        schema.validate_image_record("record", copy.deepcopy(self.image), "win", self.tags)
        schema.validate_sequence_record("record", copy.deepcopy(self.sequence), self.tags, self.images)

    def test_image_record_rejections(self):
        self.reject_image(lambda r: r.update(extra=True), "unknown field")
        self.reject_image(lambda r: r.update(schema_version=2), "unsupported schema version")
        self.reject_image(lambda r: r["checks"].update(adapter="result_classifier.v2"), "adapter")
        self.reject_image(lambda r: r["checks"].update(debug=[{"path": "win.nonexistent", "equals": 1}]),
                          "unknown classifier debug path")
        self.reject_image(lambda r: r["checks"].update(debug=[{"path": "draw_like", "matches": ".*"}]),
                          "unknown operator")
        self.reject_image(lambda r: r["truth"].update(match_type="ranked"), "match_type")
        self.reject_image(lambda r: r["truth"].update(result="loss"), "does not belong")
        self.reject_image(lambda r: r["checks"].update(frame_class="FINAL_LOSS"), "legacy expectation")
        self.reject_image(lambda r: r["review"]["privacy"]["checked"].pop(), "full checklist")
        self.reject_image(lambda r: r["review"]["truth"].update(status="pending"), "status")
        self.reject_image(lambda r: r["input"].update(sha256="A" * 64), "lowercase hex")
        self.reject_image(lambda r: r["coverage"].append("result.not.a.tag"), "unknown coverage tag")
        with self.assertRaisesRegex(MetadataError, "stored under"):
            schema.validate_image_record("record", copy.deepcopy(self.image), "lose", self.tags)

    def test_sequence_metadata_cannot_carry_actions_or_code(self):
        self.reject_sequence(lambda r: r["input"]["steps"][0].update(after="os.system"), "after")
        self.reject_sequence(lambda r: r["input"]["steps"][0].update(exec="print(1)"), "unknown field")
        self.reject_sequence(lambda r: r["input"]["steps"][0].update(gap="launch_process"), "exactly one of")
        self.reject_sequence(lambda r: r["input"].update(command="cmd /c calc"), "unknown field")

    def test_sequence_record_rejections(self):
        self.reject_sequence(lambda r: r["input"]["steps"][1].update(frame="result.unknown"), "unknown fixture")
        self.reject_sequence(lambda r: r["checks"]["steps"].pop("s05"), "every step")
        self.reject_sequence(lambda r: r["input"]["steps"][1].update(at_ms=750), "strictly increase")
        self.reject_sequence(lambda r: r["truth"].update(expected_results=["win"]), "disagrees with truth")
        self.reject_sequence(lambda r: r["checks"]["totals"].update(detector_errors=1), "never an expected")
        self.reject_sequence(lambda r: r["checks"]["steps"]["s05"].update(detection=None), "disagree")
        self.reject_sequence(lambda r: r["input"].update(steps=r["input"]["steps"] * 12), "steps")
        self.reject_sequence(lambda r: r["checks"]["steps"]["s01"]["state"].update(armed="no"), "wrong type")

    def test_wgc_boundary_needs_a_production_producible_roi(self):
        base = load(WGC_RECORD)

        def small(record):
            for step in record["input"]["steps"]:
                if step["wgc"]["sample"] is not None:
                    step["wgc"]["sample"]["frame"] = "result.final-win.video-5-9"

        self.reject_sequence(small, "minimum height 40", base)
        self.reject_sequence(lambda r: r["input"]["steps"][1]["wgc"].update(target="elsewhere"), "unknown target", base)


class CorpusTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="ac6-t1-corpus-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name) / "fixtures"
        shutil.copytree(FIXTURES, self.root)

    def assertCorpusError(self, pattern):
        with self.assertRaisesRegex(CorpusError, pattern):
            load_corpus(self.root)

    def test_the_committed_corpus_loads(self):
        corpus = load_corpus(self.root)
        self.assertEqual(len(corpus.images), 25)
        self.assertGreaterEqual(len(corpus.sequences), 12)
        self.assertEqual([case.id for case in corpus.cases], sorted(case.id for case in corpus.cases))
        self.assertEqual(corpus.summary()["families"]["results"], "implemented")

    def test_a_record_in_a_reserved_family_fails_instead_of_skipping(self):
        target = self.root / "ranks" / "pre_s" / "a4.json"
        target.write_text(IMAGE_RECORD.read_text(encoding="utf-8"), encoding="utf-8")
        self.assertCorpusError("reserved and has no replay adapter")

    def test_orphaned_duplicate_and_tampered_pixels_fail(self):
        shutil.copy(self.root / "normal_01.ppm", self.root / "orphan.ppm")
        self.assertCorpusError("without a fixture record")
        (self.root / "orphan.ppm").unlink()
        shutil.copy(IMAGE_RECORD, self.root / "results" / "win" / "copy.json")
        self.assertCorpusError("duplicate fixture id|already described")
        (self.root / "results" / "win" / "copy.json").unlink()
        data = bytearray((self.root / "final_win_01.ppm").read_bytes())
        data[-1] ^= 0xFF
        (self.root / "final_win_01.ppm").write_bytes(bytes(data))
        self.assertCorpusError("SHA-256")
        (self.root / "final_win_01.ppm").write_bytes(bytes(data) + b"\x00")
        self.assertCorpusError("bytes, metadata says")

    def test_dimension_mismatch_fails(self):
        path = self.root / "results" / "win" / "final-win.01.json"
        record = load(path)
        record["input"]["width"] = 756
        path.write_text(json.dumps(record), encoding="utf-8")
        self.assertCorpusError("metadata says 756x50")

    def test_empty_corpus_missing_coverage_and_unexpected_files_fail(self):
        stale = self.root / "results" / "sequences" / "wgc-stale-and-repeated-frames.json"
        stale.unlink()
        self.assertCorpusError("required coverage has no fixture: sequence.stale_frame")
        (self.root / "diagnostics.zip").write_bytes(b"PK")
        self.assertCorpusError("file type is not allowed")
        (self.root / "diagnostics.zip").unlink()
        (self.root / "results" / "win" / "deeper").mkdir()
        (self.root / "results" / "win" / "deeper" / "x.json").write_text("{}", encoding="utf-8")
        self.assertCorpusError("<family>/<category>")
        shutil.rmtree(self.root / "results")
        for image in self.root.glob("*.ppm"):
            image.unlink()
        self.assertCorpusError("empty")

    def test_duplicate_keys_in_a_record_fail_the_corpus(self):
        path = self.root / "results" / "win" / "final-win.01.json"
        text = path.read_text(encoding="utf-8").replace('"id": "result.final-win.01",',
                                                        '"id": "result.final-win.01", "id": "result.other",', 1)
        path.write_text(text, encoding="utf-8")
        self.assertCorpusError("duplicate JSON key")

    @unittest.skipUnless(os.name == "nt", "junctions are a Windows reparse point")
    def test_a_linked_fixture_directory_fails(self):
        import _winapi
        outside = Path(self.directory.name) / "outside"
        outside.mkdir()
        _winapi.CreateJunction(str(outside), str(self.root / "results" / "linked"))
        try:
            self.assertCorpusError("symlinks or reparse points|must not be links")
        finally:
            os.rmdir(self.root / "results" / "linked")


class CompareTests(unittest.TestCase):
    def observation(self, **overrides):
        base = {"id": "s01", "at_ms": 750, "wait_timeout": 0.75, "frames_buffered": 1, "frame_class": "FINAL_DRAW",
                "gameplay_activity": False, "motion_score": None, "detections": [], "gate_calls": [],
                "state": {"armed": True}, "capture": {"shot": True}, "health": {"status": "active"}, "errors": []}
        base.update(overrides)
        return base

    def sequence(self, detection=None, gate=None):
        check = {"frame_class": "FINAL_DRAW", "detection": detection}
        if gate is not None:
            check["gate"] = gate
        detections = {"win": 0, "loss": 0, "draw": 1 if detection == "draw" else 0}
        return {"input": {"capture": "frame_source", "steps": [{"id": "s01", "at_ms": 750, "frame": "x"}]},
                "checks": {"steps": {"s01": check},
                           "totals": {"accepted": {"win": 0, "loss": 0}, "detections": detections,
                                      "gate_rejected": 0, "frames_classified": 1, "detector_errors": 0}}}

    def actual(self, observation, closes=2):
        return {"observations": [observation], "stopped_flush": True, "capture_closes": closes}

    def test_image_comparison_is_exact(self):
        record = {"checks": {"frame_class": "FINAL_WIN",
                             "debug": [{"path": "draw_like", "equals": True},
                                       {"path": "draw_cluster.span", "at_least": 0.12, "at_most": 0.25}]}}
        good = {"frame_class": "FINAL_WIN", "debug": {"draw_like": True, "draw_cluster": {"span": 0.2}}}
        self.assertEqual(compare.compare_image(record, good), [])
        failures = compare.compare_image(record, {"frame_class": "CLEAR",
                                                  "debug": {"draw_like": 1, "draw_cluster": {"span": 0.3}}})
        self.assertEqual([item["check"] for item in failures],
                         ["frame_class", "debug.draw_like", "debug.draw_cluster.span"])
        missing = compare.compare_image(record, {"frame_class": "FINAL_WIN", "debug": {}})
        self.assertTrue(all("no such value" in item["reason"] for item in missing))

    def test_a_draw_reaching_the_gate_breaks_the_contract(self):
        record = self.sequence(detection="draw")
        honest = self.observation(detections=["draw"])
        self.assertEqual(compare.compare_sequence(record, self.actual(honest)), [])
        leaked = self.observation(detections=["draw"], gate_calls=[{"result": "draw", "source": "auto",
                                                                    "accepted": True}])
        checks = [item["check"] for item in compare.compare_sequence(record, self.actual(leaked))]
        self.assertIn("gate_calls", checks)

    def test_error_paths_and_cleanup_are_failures(self):
        record = self.sequence()
        failures = compare.compare_sequence(record, self.actual(self.observation(wait_timeout=2.0,
                                                                                 errors=["detector_error"]), 1))
        self.assertEqual({item["check"] for item in failures}, {"wait_timeout", "errors", "capture_closes",
                                                                  "detector_errors"})
        failures = compare.compare_sequence(record, {"observations": [], "stopped_flush": False, "capture_closes": 2})
        self.assertIn("steps", [item["check"] for item in failures])
        self.assertIn("detector_stopped", [item["check"] for item in failures])

    def test_strict_value_comparison(self):
        self.assertFalse(compare.same_value(True, 1))
        self.assertFalse(compare.same_value(None, False))
        self.assertTrue(compare.same_value(1, 1.0))
        self.assertFalse(compare.same_value("1", 1))


class ReportTests(unittest.TestCase):
    def test_reports_carry_no_absolute_user_path(self):
        repo = ROOT
        replacements = report.placeholders(repo)
        text = f"failed at {repo / 'tests' / 'x.py'} and {str(Path.home()).upper()}\\secret"
        cleaned = report.sanitize({"reason": text, "items": [str(Path.home())]}, replacements)
        flat = json.dumps(cleaned)
        self.assertNotIn(str(repo).replace("\\", "\\\\"), flat)
        self.assertNotIn(str(Path.home()).replace("\\", "\\\\").lower(), flat.lower())
        self.assertIn("<repo>", flat)

    def test_junit_and_summary_reflect_failures(self):
        result = {"status": "FAIL", "canonical_corpus": True, "infrastructure_failures": [{"stage": "coverage",
                                                                                            "reason": "missing"}],
                  "summary": {"total": 1, "passed": 0, "failed": 1, "skipped": 0, "duration_s": 1.5},
                  "cases": [{"id": "result.x", "kind": "image", "status": "FAIL", "duration_s": 0.5,
                             "failures": [compare.failure("classify", "frame_class", "A", "B", "differs")]}],
                  "coverage": {"missing": ["result.win.positive"]}}
        with tempfile.TemporaryDirectory(prefix="ac6-t1-report-") as name:
            path = Path(name) / "junit.xml"
            report.write_junit(path, result)
            tree = ElementTree.parse(path).getroot()
        self.assertEqual(len(tree.findall(".//testcase")), 2)
        self.assertEqual(len(tree.findall(".//failure")), 2)
        lines = "\n".join(report.summary_lines(result))
        self.assertIn("T1 status: FAIL", lines)
        self.assertIn("REQUIRED COVERAGE NOT PROVEN", lines)


class ClockTests(unittest.TestCase):
    def test_capture_processing_and_sequence_times_are_distinct(self):
        clock = VirtualClock(30)
        clock.set_step(750)
        self.assertEqual(clock.capture_seconds, 100.75)
        self.assertAlmostEqual(clock.processing_now(), 100.78)
        self.assertEqual(clock.seconds(2250), 102.25)
        shim = ModuleTime(clock)
        self.assertAlmostEqual(shim.monotonic(), 100.78)
        for name in ("sleep", "perf_counter", "strftime"):
            self.assertFalse(hasattr(shim, name), name)
        with self.assertRaises(ValueError):
            VirtualClock(1001)


class GuardTests(unittest.TestCase):
    def test_guards_refuse_and_restore(self):
        import ctypes
        import multiprocessing
        originals = (socket.socket, subprocess.Popen, open, os.open, ctypes.CDLL, list(sys.meta_path))
        with tempfile.TemporaryDirectory(prefix="ac6-t1-guard-") as name:
            owned = Path(name) / "owned"
            forbidden = Path(name) / "AC6WinLossTracker"
            owned.mkdir()
            forbidden.mkdir()
            (forbidden / "history.db").write_bytes(b"live")
            restore = guards.install(owned, forbidden_roots=[forbidden])
            try:
                attempts = {
                    "socket": lambda: socket.socket(),
                    "dns": lambda: socket.getaddrinfo("example.invalid", 80),
                    "connect": lambda: socket.create_connection(("127.0.0.1", 8765)),
                    "popen": lambda: subprocess.Popen([sys.executable, "-c", "pass"]),
                    "system": lambda: os.system("echo guard"),
                    "spawn": lambda: multiprocessing.get_context("spawn").Process(target=print).start(),
                    "write outside": lambda: open(Path(name) / "outside.txt", "w"),
                    "os.open outside": lambda: os.open(str(Path(name) / "outside2.txt"), os.O_CREAT | os.O_WRONLY),
                    "replace outside": lambda: os.replace(owned / "a", Path(name) / "b"),
                    "read live data": lambda: open(forbidden / "history.db", "rb"),
                    "import server": lambda: __import__("server"),
                    "import windows_capture": lambda: __import__("windows_capture"),
                }
                if os.name == "nt":
                    import _winapi
                    attempts["user32"] = lambda: ctypes.WinDLL("user32")
                    attempts["CreateProcess"] = lambda: _winapi.CreateProcess(None, "cmd", None, None, 0, 0, None,
                                                                              None, None)
                for label, attempt in attempts.items():
                    with self.assertRaises(guards.GuardViolation, msg=label):
                        attempt()
                with open(owned / "inside.txt", "w") as handle:
                    handle.write("allowed")
                probes = guards.probe(owned, Path(name) / "probe.txt")
                self.assertTrue(all(value is True for value in probes.values()), probes)
            finally:
                restore()
        self.assertEqual((socket.socket, subprocess.Popen, open, os.open, ctypes.CDLL, list(sys.meta_path)),
                         originals)

    def test_worker_refuses_production_imported_before_isolation(self):
        from t1 import worker
        sentinel = "pending_history"
        already = sentinel in sys.modules
        if not already:
            sys.modules[sentinel] = types.ModuleType(sentinel)
        try:
            with tempfile.TemporaryDirectory(prefix="ac6-t1-isolation-") as name:
                with self.assertRaisesRegex(RuntimeError, "imported before isolation"):
                    worker.prove_isolation(Path(name))
        finally:
            if not already:
                del sys.modules[sentinel]

    def test_worker_refuses_an_environment_outside_its_root(self):
        from t1 import worker
        fake_sys = types.SimpleNamespace(modules={}, flags=types.SimpleNamespace(dont_write_bytecode=1,
                                                                                  ignore_environment=1))
        with tempfile.TemporaryDirectory(prefix="ac6-t1-isolation-") as name:
            owned = Path(name) / "case"
            owned.mkdir()
            inside = {"LOCALAPPDATA": str(owned), "TEMP": str(owned), "TMP": str(owned)}
            with patch.object(worker, "sys", fake_sys), patch.dict(os.environ, inside), \
                    patch("os.getcwd", return_value=str(owned)), \
                    patch("tempfile.gettempdir", return_value=str(owned)):
                self.assertEqual(worker.prove_isolation(owned)["LOCALAPPDATA"], "owned")
            for variable in ("LOCALAPPDATA", "TEMP", "TMP"):
                outside = dict(inside, **{variable: name})
                with patch.object(worker, "sys", fake_sys), patch.dict(os.environ, outside), \
                        patch("os.getcwd", return_value=str(owned)), \
                        patch("tempfile.gettempdir", return_value=str(owned)):
                    with self.assertRaisesRegex(RuntimeError, variable):
                        worker.prove_isolation(owned)
            unflagged = types.SimpleNamespace(modules={}, flags=types.SimpleNamespace(dont_write_bytecode=0,
                                                                                       ignore_environment=1))
            with patch.object(worker, "sys", unflagged):
                with self.assertRaisesRegex(RuntimeError, "-B and -E"):
                    worker.prove_isolation(owned)


def _attributes_used(source, owner):
    """Attribute names accessed as ``<owner>.<name>`` anywhere in ``source``."""
    names = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Attribute):
            value = node.value
            chain = []
            while isinstance(value, ast.Attribute):
                chain.append(value.attr)
                value = value.value
            if isinstance(value, ast.Name):
                chain.append(value.id)
                if ".".join(reversed(chain)) == owner:
                    names.add(node.attr)
    return names


class ProductionSeamTests(unittest.TestCase):
    """The replay replaces a few collaborators; fail if production outgrows them."""

    detector_source = (ROOT / "result_detector.py").read_text(encoding="utf-8")
    capture_source = (ROOT / "game_capture.py").read_text(encoding="utf-8")

    def test_recorder_capture_winapi_and_clock_surfaces_cover_production_use(self):
        recorder = _attributes_used(self.detector_source, "self.diagnostics")
        self.assertTrue(recorder)
        self.assertLessEqual(recorder, {name for name in dir(replay.ReplayRecorder) if not name.startswith("_")}
                             | {"root"})
        capture = _attributes_used(self.detector_source, "self.capture")
        replay_capture = replay.ReplayCapture([], {}, replay.Cursor(), VirtualClock(0), None)
        self.assertLessEqual(capture, set(vars(replay_capture)) | set(dir(replay.ReplayCapture)))
        api = _attributes_used(self.capture_source, "self.api")
        self.assertLessEqual(api, set(dir(replay.FakeWinApi)))
        clock = VirtualClock(0)
        shim = ModuleTime(clock)
        for source in (self.detector_source, self.capture_source):
            for name in _attributes_used(source, "time"):
                self.assertTrue(hasattr(shim, name), f"time.{name} is not replayed")

    def test_the_accepted_result_callback_mirrors_the_server(self):
        server = (ROOT / "server.py").read_text(encoding="utf-8")
        body = server[server.index("def record_result("):server.index("class UndoFailed")]
        self.assertIn("cooldown = 5.0", body)
        self.assertIn("result_gate.try_accept(cooldown, now=now)", body)
        self.assertIn("current.external_mutation()", body)
        self.assertEqual(replay.SERVER_COOLDOWN_SECONDS, 5.0)
        undo = server[server.index("def undo_result("):server.index("def reset_stats(")]
        self.assertIn("result_gate.clear_for_manual_correction()", undo)
        self.assertIn("current.after_undo()", undo)
        reset = server[server.index("def reset_stats("):]
        reset = reset[:reset.index("\ndef ", 1)]
        self.assertIn("result_gate.lock_now()", reset)
        self.assertIn("current.external_mutation()", reset)

    def test_every_asserted_debug_path_exists_in_real_classifier_output(self):
        from result_detector import ResultClassifier
        classifier = ResultClassifier(ROOT / "detector_templates.json")
        keys = set()
        for pixel in ((0, 0, 0, 255), (60, 60, 60, 255)):
            _, debug = classifier.classify_bgra(bytes(pixel) * (757 * 50), 757, 50)
            for key, value in debug.items():
                keys.add(key)
                if isinstance(value, dict):
                    keys.update(f"{key}.{field}" for field in value)
        self.assertLessEqual(schema.DEBUG_PATHS, keys)

    def test_the_runner_side_never_imports_production(self):
        production = {path.stem for path in ROOT.glob("*.py")} | {"launcher"}
        for name in ("corpus", "schema", "compare", "report", "process", "paths", "images", "strict_json",
                     "runner", "clock", "guards"):
            tree = ast.parse((ROOT / "tests" / "t1" / f"{name}.py").read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported = {alias.name.split(".")[0] for alias in node.names}
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    imported = {(node.module or "").split(".")[0]}
                else:
                    continue
                self.assertFalse(imported & production, f"tests/t1/{name}.py imports {imported & production}")

    def test_the_worker_installs_guards_before_importing_production(self):
        source = (ROOT / "tests" / "t1" / "worker.py").read_text(encoding="utf-8")
        install = source.index("guards.install(")
        for module in ("import game_capture", "import result_detector", "import result_gate"):
            self.assertLess(install, source.index(module), module)
        self.assertLess(source.index("prove_isolation(case_root)"), install)


if __name__ == "__main__":
    unittest.main(verbosity=2)
