"""Synthetic labels through the real detector loop; never real AC6 capture."""
from pathlib import Path
from unittest.mock import Mock, patch

def detector_sequence(recorder, optional, labels, *, stored_frames=None):
    import result_detector as rd
    from mss.screenshot import ScreenShot
    cursor, results, states, order = [0], [], [], []
    class Stop:
        def is_set(self): return cursor[0] >= len(labels)
        def wait(self, timeout): cursor[0] += 1
    def notify(kind, **payload):
        order.append(('optional', kind))
        if optional is not None: optional(kind, **payload)
    def accept(result, source):
        order.append(('accept', result))
        results.append(result)
        return True
    detector = rd.ResultDetector(Path(rd.__file__).parent,
        lambda: {'result_detector_enabled': True}, accept, Mock(), Stop(), recorder,
        on_optional_event=notify)
    shot = ScreenShot.from_size(bytearray(32), 4, 2)
    capture = Mock(identity_changed=False, discontinuity=False, status='synthetic WGC',
                   target=None, source=None, identity=None)
    def grab(_):
        capture.captured_at = 100 + cursor[0] * .75
        if stored_frames is not None and stored_frames[cursor[0]] is not None:
            raw, width, height = stored_frames[cursor[0]]
            return ScreenShot.from_size(bytearray(raw), width, height)
        return shot
    capture.grab.side_effect = grab
    detector.capture = capture
    real_classifier = detector.classifier
    detector.classifier = Mock()
    def classify(raw, width, height):
        if stored_frames is not None and stored_frames[cursor[0]] is not None:
            return real_classifier.classify_bgra(raw, width, height)
        return labels[cursor[0]], {'gameplay_activity': False}
    detector.classifier.classify_bgra.side_effect = classify
    observe = detector.state.observe
    def sampled(*args, **kwargs):
        result = observe(*args, **kwargs)
        states.append(detector.state.diagnostic_snapshot())
        return result
    detector.state.observe = sampled
    with patch('result_detector.mss.mss'), patch.object(detector, '_save_debug_result_roi'):
        detector.run()
    # Existing enable transition closes capture too; final cleanup is mandatory.
    capture.close.assert_called()
    return results, states, order
