"""#66: deterministic passive evidence and isolated real writer lifecycle."""
import ast
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
from candidate_diagnostics import CandidateEvidence, CandidateBundleWriter, FRAME_COUNT, MAX_ROI_BYTES, MAX_BUNDLES, replay_bundle
import result_detector as rd
from t1.images import decode_image

BASE_CORE_DIGESTS = {
    '_leading_text_contrast': 'b6a6acd4b62d48f179145dc33523570f74b979c7fa9dcd57a15d2876b4145e51',
    # Owner-authorized bright-result-band repair advances ONLY the classifier
    # baseline and pins its new prefix helper. State/run remain pinned to the
    # original #66 exact base.
    'ResultClassifier.classify_bgra': 'c5c2bacd01d9e940bb0706474cbcd6c7d73211e3be32acc27bd465538647bdfe',
    'ResultStateMachine': '93eb802002513bdeecea900f1ff7633b01151ef0f3ee57962172843a9aaf7122',
    'ResultDetector.run': 'f88eee3619d97bb14684a7edd9880b0a62a4c0efe0f8d1a8cd350835211f0eae',
}


def frame_context(state, label, now, result):
    return {'captured_at': now, 'observed_at': now + .01, 'frame_state': label,
            'debug': {'final_win_geom': True, 'win_final_score': .99},
            'gameplay_activity': False, 'state_before': state[0], 'state_after': state[1], 'result': result}


def sequence(labels, submit):
    state, evidence = rd.ResultStateMachine(), CandidateEvidence(submit)
    snapshots, results = [], []
    for index, label in enumerate(labels):
        before = state.diagnostic_snapshot()
        result = state.observe(label, rd.CONFIRM_HITS, rd.CLEAR_HITS_REQUIRED, rd.COOLDOWN_SECONDS, now=100 + index * .75)
        after = state.diagnostic_snapshot()
        context = frame_context((before, after), label, 100 + index * .75, result)
        evidence.observe(bytearray(32), 4, 2, context)
        assert state.diagnostic_snapshot() == after
        snapshots.append(after)
        results.append(result)
    return evidence, snapshots, results


def core_digests(source):
    """Remove only the named #66 observers/debug fields, pin the entire core AST."""
    tree = ast.parse(source)
    class StripObservers(ast.NodeTransformer):
        def visit_Dict(self, node):
            node = self.generic_visit(node)
            pairs = [(k, v) for k, v in zip(node.keys, node.values)
                     if not isinstance(k, ast.Constant) or k.value not in ('final_win_geom', 'final_loss_geom')]
            node.keys, node.values = [p[0] for p in pairs], [p[1] for p in pairs]
            return node
        def visit_Assign(self, node):
            if any(isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name)
                   and t.value.id == 'debug' and isinstance(t.slice, ast.Constant)
                   and t.slice.value in ('win_ok', 'loss_ok') for t in node.targets): return None
            if any(isinstance(t, ast.Name) and t.id in ('candidate_before', 'candidate_after') for t in node.targets): return None
            return self.generic_visit(node)
        def visit_Expr(self, node):
            if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Attribute) and node.value.func.attr in ('_candidate_boundary', '_candidate_observe'):
                return None
            return self.generic_visit(node)
        def visit_Try(self, node):
            if any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                   and n.func.attr == 'close_candidate_bundles' for n in ast.walk(node)) and len(node.body) == 1 and isinstance(node.body[0], ast.If):
                return None
            return self.generic_visit(node)
    output = {}
    for cls in tree.body:
        if isinstance(cls, ast.FunctionDef) and cls.name == '_leading_text_contrast':
            output[cls.name] = hashlib.sha256(ast.dump(cls, include_attributes=False).encode()).hexdigest()
        if not isinstance(cls, ast.ClassDef): continue
        methods = [n for n in cls.body if isinstance(n, ast.FunctionDef)]
        for method in methods:
            if cls.name == 'ResultClassifier' and method.name == 'classify_bgra' or cls.name == 'ResultDetector' and method.name == 'run':
                node = StripObservers().visit(method)
                output[f'{cls.name}.{method.name}'] = hashlib.sha256(ast.dump(node, include_attributes=False).encode()).hexdigest()
        if cls.name == 'ResultStateMachine':
            nodes = [n for n in cls.body if not isinstance(n, ast.FunctionDef) or n.name != 'diagnostic_snapshot']
            output[cls.name] = hashlib.sha256(''.join(ast.dump(n, include_attributes=False) for n in nodes).encode()).hexdigest()
    return output


class CandidateEvidenceTests(unittest.TestCase):
    def test_core_ast_and_public_constants_match_approved_baseline(self):
        self.assertEqual(core_digests((ROOT / 'result_detector.py').read_text()), BASE_CORE_DIGESTS)
        self.assertEqual((rd.POLL_SECONDS, rd.CONFIRM_HITS, rd.CLEAR_HITS_REQUIRED, rd.COOLDOWN_SECONDS), (.75, 2, 3, 5.0))
    def test_bounded_long_normal_sequence_and_input_ownership(self):
        sink = Mock()
        evidence, _, _ = sequence([rd.CLEAR] * 10000, sink)
        self.assertEqual(len(evidence.frames), FRAME_COUNT)
        self.assertEqual(sum(len(f['raw']) for f in evidence.frames), 96)
        sink.assert_not_called()
        raw = bytearray(32)
        context = frame_context(({}, {}), rd.CLEAR, 1, None)
        evidence.observe(raw, 4, 2, context)
        raw[0] = 255
        context['debug']['final_win_geom'] = False
        self.assertEqual(evidence.frames[-1]['raw'][0], 0)
        self.assertTrue(evidence.frames[-1]['context']['debug']['final_win_geom'])

    def test_win_and_loss_clear_flush_exact_decisive_frame(self):
        for final in (rd.FINAL_WIN, rd.FINAL_LOSS):
            bundles = []
            evidence, _, results = sequence([rd.CLEAR]*3 + [final, rd.CLEAR], bundles.append)
            self.assertEqual(results, [None]*5)
            self.assertEqual(len(bundles), 1)
            frames = bundles[0]['frames']
            self.assertEqual([f['context']['frame_state'] for f in frames], [rd.CLEAR, final, rd.CLEAR])
            self.assertEqual(frames[-1]['context']['state_before']['candidate_hits'], 1)
            self.assertEqual(frames[-1]['context']['state_after']['candidate_hits'], 0)
            self.assertIn('final_win_geom', frames[-1]['context']['debug'])
            self.assertFalse(evidence.frames)

    def test_success_never_flushes_and_resets(self):
        for final, expected in ((rd.FINAL_WIN, 'win'), (rd.FINAL_LOSS, 'loss'), (rd.FINAL_DRAW, 'draw')):
            sink = Mock()
            evidence, _, results = sequence([rd.CLEAR]*3 + [final, final], sink)
            self.assertEqual(results[-1], expected)
            self.assertFalse(evidence.frames)
            sink.assert_not_called()

    def test_switched_candidate_then_clear_or_success(self):
        for first, second in ((rd.FINAL_WIN, rd.FINAL_LOSS), (rd.FINAL_LOSS, rd.FINAL_WIN)):
            for last in (rd.CLEAR, second):
                bundles = []
                evidence, _, results = sequence([rd.CLEAR]*3 + [first, second, last], bundles.append)
                if last == rd.CLEAR:
                    self.assertEqual(len(bundles), 2)
                    self.assertEqual([f['context']['frame_state'] for f in bundles[-1]['frames']], [second, rd.CLEAR])
                    self.assertEqual(bundles[-1]['frames'][-1]['context']['state_before']['candidate_hits'], 1)
                    self.assertIsNone(results[-1])
                else:
                    self.assertEqual(len(bundles), 1)  # switch only; confirmed new candidate adds no bundle
                    self.assertEqual(results[-1], 'win' if second == rd.FINAL_WIN else 'loss')
                self.assertFalse(evidence.frames)

    def test_rejection_and_gap_identity_boundaries(self):
        for label in (rd.PHASE, rd.NON_CLEAR, rd.FINAL_LOSS):
            sink = Mock()
            sequence([rd.CLEAR]*3 + [rd.FINAL_WIN, label], sink)
            sink.assert_called_once()
        for reason in ('capture_gap', 'identity_changed', 'discontinuity', 'detector_error'):
            sink = Mock()
            evidence, _, _ = sequence([rd.CLEAR]*3 + [rd.FINAL_WIN], sink)
            evidence.boundary(reason, {'state': {'candidate': 'win'}})
            self.assertEqual(sink.call_args[0][0]['trigger'], reason)
            self.assertFalse(evidence.frames)

    def test_allocation_callback_failure_and_oversize_fail_open(self):
        sink = Mock(side_effect=MemoryError('diagnostic allocation'))
        evidence, snapshots, results = sequence([rd.CLEAR]*3 + [rd.FINAL_WIN, rd.CLEAR], sink)
        baseline, expected, baseline_results = sequence([rd.CLEAR]*3 + [rd.FINAL_WIN, rd.CLEAR], lambda _: None)
        self.assertEqual(snapshots, expected)
        self.assertEqual(results, baseline_results)
        self.assertFalse(evidence.frames)
        with patch('candidate_diagnostics.copy.deepcopy', side_effect=MemoryError):
            evidence.observe(bytes(32), 4, 2, {})
        self.assertFalse(evidence.frames)
        evidence.observe(bytes(MAX_ROI_BYTES + 4), MAX_ROI_BYTES // 4 + 1, 1, {})
        self.assertFalse(evidence.frames)
        baseline.clear()

    def test_instrumented_real_loop_core_failure_is_not_hidden(self):
        from mss.screenshot import ScreenShot
        labels = [rd.CLEAR]*3 + [rd.FINAL_WIN, rd.CLEAR, rd.FINAL_LOSS, rd.FINAL_LOSS]
        for broken in (False, True):
            cursor, calls, bundles = [0], [], []
            class Stop:
                def is_set(self): return cursor[0] >= len(labels)
                def wait(self, timeout): cursor[0] += 1
            recorder = Mock()
            recorder.submit_candidate_bundle = Mock(side_effect=OSError('write failed')) if broken else bundles.append
            detector = rd.ResultDetector(ROOT, lambda: {'result_detector_enabled': True},
                lambda result, source: calls.append(result) or True, Mock(), Stop(), recorder)
            shot = ScreenShot.from_size(bytearray(32), 4, 2)
            capture = Mock(identity_changed=False, discontinuity=False, status='test WGC', target=None, source=None, identity=None)
            def grab(_):
                capture.captured_at = 100 + cursor[0] * .75
                return shot
            capture.grab.side_effect = grab
            detector.capture = capture
            detector.classifier = Mock()
            detector.classifier.classify_bgra.side_effect = lambda *_: (labels[cursor[0]], {'gameplay_activity': False})
            with patch('result_detector.mss.mss'), patch.object(detector, '_save_debug_result_roi'):
                detector.run()
            self.assertEqual(calls, ['loss'])
            capture.close.assert_called()
            if not broken:
                self.assertEqual(len(bundles), 1)
                self.assertEqual(bundles[0]['frames'][-1]['context']['frame_state'], rd.CLEAR)
        # A genuine classifier exception still reaches the existing error path.
        cursor[0] = 0
        detector.classifier.classify_bgra.side_effect = ValueError('core failure')
        with patch('result_detector.mss.mss'):
            detector.run()
        self.assertTrue(any(c.args[0] == 'detector_error' for c in recorder.record.call_args_list))


class CandidateRuntimeTests(unittest.TestCase):
    def bundle(self):
        sink = []
        sequence([rd.CLEAR]*3 + [rd.FINAL_WIN, rd.CLEAR], sink.append)
        return sink[0]

    def test_actual_writer_isolated_root_retention_shutdown_export(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'LOCALAPPDATA': directory}):
            import diagnostics
            recorder = diagnostics.DiagnosticRecorder()
            for _ in range(MAX_BUNDLES + 3):
                recorder.submit_candidate_bundle(self.bundle())
                recorder._candidate_writer.pending.join()
            writer = recorder._candidate_writer
            self.assertTrue(writer.close(timeout=2))
            self.assertFalse(writer.thread.is_alive())
            self.assertEqual(len(list(writer.root.glob('*.zip'))), MAX_BUNDLES)
            self.assertFalse(list(writer.root.glob('*.partial')))
            self.assertTrue(writer.root.is_relative_to(Path(directory)))
            archive_path = recorder.export(destination_dir=directory)
            with zipfile.ZipFile(archive_path) as archive:
                self.assertEqual(len([n for n in archive.namelist() if n.startswith('candidate-bundles/')]), MAX_BUNDLES)
            self.assertFalse(list(Path(directory).rglob('.runtime.json')))

    def test_real_writer_failures_drain_and_no_partial(self):
        for failure in ('mkdir', 'json', 'pixels'):
            with tempfile.TemporaryDirectory() as directory:
                writer = CandidateBundleWriter(directory)
                if failure == 'mkdir':
                    injection = patch.object(Path, 'mkdir', side_effect=OSError('denied'))
                elif failure == 'json':
                    injection = patch('candidate_diagnostics.json.dumps', side_effect=TypeError('cannot encode'))
                else:
                    injection = patch('candidate_diagnostics.zipfile.ZipFile.writestr', side_effect=OSError('disk full'))
                with injection:
                    writer.submit(self.bundle())
                    writer.pending.join()
                self.assertTrue(writer.close(timeout=2))
                self.assertFalse(list(Path(directory).rglob('*.partial')))
                self.assertFalse(list(Path(directory).rglob('*.zip')))

    def test_blocked_disk_never_waits_in_detector_submit_and_queue_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            writer = CandidateBundleWriter(directory)
            entered, release = threading.Event(), threading.Event()
            def blocked(_):
                entered.set()
                release.wait(3)
            try:
                with patch.object(writer, '_write', side_effect=blocked):
                    writer.submit(self.bundle())
                    self.assertTrue(entered.wait(2))
                    for _ in range(100): writer.submit(self.bundle())
                    self.assertEqual(writer.pending.qsize(), 1)
                    self.assertFalse(writer.close(timeout=0))
                    release.set()
                    self.assertTrue(writer.close(timeout=2))
            finally:
                release.set()
                writer.close(timeout=2)

    def test_canonical_pixels_exact_offline_classifier_replay_and_hash_rejection(self):
        # Existing canonical image is reused without relabelling or new truth.
        fixture = json.loads((ROOT / 'tests/fixtures/results/win/final-win.01.json').read_text())
        image = fixture['input']
        data = (ROOT / 'tests/fixtures' / image['path']).read_bytes()
        decoded = decode_image(data, image['format'])
        classifier = rd.ResultClassifier(ROOT / 'detector_templates.json')
        label, debug = classifier.classify_bgra(decoded.bgra, decoded.width, decoded.height)
        self.assertEqual(label, rd.FINAL_WIN)
        state = rd.ResultStateMachine()
        state.armed, state.last_clear_at = True, 100
        before = state.diagnostic_snapshot()
        result = state.observe(label, 2, 3, 5, now=101)
        context = frame_context((before, state.diagnostic_snapshot()), label, 101, result)
        context['debug'] = debug
        bundle = {'schema_version': 1, 'trigger': 'test', 'boundary': None,
                  'frames': [{'raw': decoded.bgra, 'width': decoded.width, 'height': decoded.height, 'context': context}]}
        with tempfile.TemporaryDirectory() as directory:
            writer = CandidateBundleWriter(directory)
            writer.submit(bundle)
            writer.pending.join()
            self.assertTrue(writer.close(timeout=2))
            path, = writer.root.glob('*.zip')
            replay = replay_bundle(path, classifier)['frames'][0]
            self.assertTrue(replay['decision_matches'])
            self.assertEqual(replay['debug'], debug)
            self.assertEqual(replay['state_after'], context['state_after'])
            for key in ('final_win_geom', 'result_band_like', 'phase_prefix_like', 'win_ok', 'loss_ok'):
                self.assertIn(key, replay['debug'])
            with zipfile.ZipFile(path) as archive:
                manifest = json.loads(archive.read('manifest.json'))
                pixels = archive.read('0.bgra')
            manifest['frames'][0]['sha256'] = '0'*64
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('manifest.json', json.dumps(manifest))
                archive.writestr('0.bgra', pixels)
            with self.assertRaises(ValueError): replay_bundle(path, classifier)


if __name__ == '__main__':
    unittest.main()
