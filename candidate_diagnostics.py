"""Bounded, passive result-candidate evidence; no capture/decision ownership."""
from __future__ import annotations

from collections import deque
import copy
import hashlib
import json
import math
from pathlib import Path
import queue
import threading
import uuid
import zipfile

FRAME_COUNT = 3  # previous frame, candidate, decisive reset (current two-hit rule)
MAX_ROI_BYTES = 2 * 1024 * 1024
MAX_CONTEXT_BYTES = 128 * 1024
MAX_BUNDLES = 8


def classifier_fingerprint(classifier):
    return hashlib.sha256(json.dumps({name: getattr(classifier, name) for name in (
        'templates', 'grid_templates', 'draw_grid_template', 'bx', 'by', 'gx', 'gy')},
        sort_keys=True, allow_nan=False).encode('utf-8')).hexdigest()


class CandidateEvidence:
    """One detector's three immutable ROI/context slots. All failures fail open.

    Only the detector thread owns this ring. Lifecycle notices from other
    threads are reflected by the next sampled state; no state is mutated here.
    """
    def __init__(self, submit):
        self.submit = submit
        self.frames = deque(maxlen=FRAME_COUNT)

    def clear(self):
        self.frames.clear()

    def boundary(self, reason, context):
        try:
            previous = self.frames[-1]['context']['state_after'] if self.frames else {}
            if previous.get('candidate') in ('win', 'loss') and previous.get('candidate_hits', 0) > 0:
                self.submit({'schema_version': 1, 'trigger': reason,
                             'boundary': copy.deepcopy(context), 'frames': list(self.frames)})
        except Exception:
            pass
        finally:
            self.clear()

    def observe(self, raw, width, height, context):
        try:
            size = width * height * 4
            if not (0 < size <= MAX_ROI_BYTES and len(raw) == size):
                self.clear()
                return
            previous = self.frames[-1]['context']['state_after'] if self.frames else {}
            # Copy only already available result-strip pixels, after the decision.
            self.frames.append({'raw': bytes(raw), 'width': width, 'height': height,
                                'context': copy.deepcopy(context)})
            candidate = previous.get('candidate')
            after = context['state_after']
            if context['result'] is not None:
                self.clear()  # successful finals never flush this feature
            elif (candidate in ('win', 'loss') and previous.get('candidate_hits', 0) > 0
                  and (after.get('candidate') != candidate
                       or after.get('candidate_hits', 0) <= previous['candidate_hits']
                       or after.get('last_reject_reason'))):
                self.submit({'schema_version': 1, 'trigger': 'candidate_reset',
                             'boundary': None, 'frames': list(self.frames)})
                self.clear()
        except Exception:
            self.clear()


class CandidateBundleWriter:
    """One daemon writer, one waiting bundle, eight atomic stored ZIPs.

    No detector-thread disk I/O/encoding/queue wait. Queue saturation drops
    optional evidence. Shutdown drains at most the existing detector join
    budget supplied by its caller; a blocked filesystem never blocks results.
    """
    def __init__(self, root):
        self.root = Path(root) / 'candidate-bundles'
        self.pending = queue.Queue(maxsize=1)
        self.closed = threading.Event()
        self.thread = threading.Thread(target=self._run, name='ac6-candidate-diagnostics', daemon=True)
        self.thread.start()

    def submit(self, bundle):
        try:
            if not self.closed.is_set():
                self.pending.put_nowait(bundle)
        except Exception:
            pass

    def close(self, timeout=0):
        self.closed.set()
        try:
            self.pending.put_nowait(None)  # Wake an idle writer, no periodic polls.
        except queue.Full:
            pass
        self.thread.join(timeout=timeout)
        return not self.thread.is_alive()

    def _run(self):
        while True:
            bundle = self.pending.get()
            try:
                if bundle is not None:
                    self._write(bundle)
            except Exception:
                pass  # diagnostic errors are isolated from the detector
            finally:
                self.pending.task_done()
                bundle = None  # Do not retain the last ROI while waiting idle.
            if self.closed.is_set() and self.pending.empty():
                break

    def _write(self, bundle):
        # Serialize/validate before creating any partial artifact.
        frames = bundle['frames']
        if not 0 < len(frames) <= FRAME_COUNT:
            raise ValueError('invalid evidence frame count')
        rows = []
        for index, frame in enumerate(frames):
            raw = frame['raw']
            if not 0 < len(raw) == frame['width'] * frame['height'] * 4 <= MAX_ROI_BYTES:
                raise ValueError('invalid evidence pixels')
            rows.append({'pixels': f'{index}.bgra', 'width': frame['width'], 'height': frame['height'],
                         'sha256': hashlib.sha256(raw).hexdigest(), 'context': frame['context']})
        manifest = {key: value for key, value in bundle.items() if key != 'frames'}
        manifest['frames'] = rows
        encoded = json.dumps(manifest, allow_nan=False, ensure_ascii=False).encode('utf-8')
        if len(encoded) > MAX_CONTEXT_BYTES:
            raise ValueError('evidence context too large')
        self.root.mkdir(parents=True, exist_ok=True)
        # Old interrupted writes cannot accumulate across sessions.
        for path in self.root.glob('*.partial'):
            path.unlink(missing_ok=True)
        for path in sorted(self.root.glob('*.zip'), key=lambda p: p.stat().st_mtime_ns)[:-(MAX_BUNDLES - 1)]:
            path.unlink()
        final = self.root / f'{uuid.uuid4().hex}.zip'
        partial = final.with_suffix('.partial')
        try:
            with zipfile.ZipFile(partial, 'w', compression=zipfile.ZIP_STORED) as archive:
                archive.writestr('manifest.json', encoded)
                for row, frame in zip(rows, frames):
                    archive.writestr(row['pixels'], frame['raw'])
            partial.replace(final)
        finally:
            partial.unlink(missing_ok=True)


def replay_bundle(path, classifier):
    """Read bounded data only; replay exact BGRA through the supplied classifier.

    State analysis is per-frame from the sampled pre-observe state, rather than
    claiming unsampled concurrent manual mutations were reconstructed.
    """
    from result_detector import ResultStateMachine, CONFIRM_HITS, CLEAR_HITS_REQUIRED, COOLDOWN_SECONDS
    output = []
    with zipfile.ZipFile(path) as archive:
        info = archive.getinfo('manifest.json')
        if info.file_size > MAX_CONTEXT_BYTES:
            raise ValueError('manifest too large')
        manifest = json.loads(archive.read(info))
        if manifest['schema_version'] != 1 or not 0 < len(manifest['frames']) <= FRAME_COUNT:
            raise ValueError('unsupported evidence')
        for index, row in enumerate(manifest['frames']):
            if row['pixels'] != f'{index}.bgra':
                raise ValueError('unexpected pixel member')
            info = archive.getinfo(row['pixels'])
            expected = row['width'] * row['height'] * 4
            if not 0 < info.file_size == expected <= MAX_ROI_BYTES:
                raise ValueError('pixel size invalid')
            raw = archive.read(info)
            if hashlib.sha256(raw).hexdigest() != row['sha256']:
                raise ValueError('pixel digest mismatch')
            decision, debug = classifier.classify_bgra(raw, row['width'], row['height'])
            context = row['context']
            if type(context['captured_at']) not in (int, float) or not math.isfinite(context['captured_at']):
                raise ValueError('missing/nonfinite native monotonic timestamp')
            state = ResultStateMachine()
            for key, value in context['state_before'].items():
                if key not in state.diagnostic_snapshot():
                    raise ValueError('unknown state field')
                setattr(state, key, value)
            result = state.observe(decision, CONFIRM_HITS, CLEAR_HITS_REQUIRED, COOLDOWN_SECONDS,
                now=context['captured_at'], gameplay_activity=context['gameplay_activity'])
            output.append({'frame': index, 'recorded_decision': context['frame_state'],
                'replayed_decision': decision, 'decision_matches': decision == context['frame_state'],
                'debug': debug, 'recorded_debug': context['debug'], 'state_result': result,
                'state_after': state.diagnostic_snapshot(), 'recorded_state_after': context['state_after']})
    return {'trigger': manifest['trigger'], 'boundary': manifest['boundary'], 'frames': output,
            'replay_classifier_fingerprint': classifier_fingerprint(classifier),
            'state_analysis': 'sampled pre-observe state; concurrent external mutations are not replayed'}
