"""Replay adapters: stored pixels through the production recognition path.

Real production objects: ResultClassifier (with the shipped templates), the
ResultDetector.run loop, its motion helpers, ResultStateMachine, ResultGate and,
in ``wgc_boundary`` mode, GameCapture.grab with its freshness, repeat, identity
and geometry checks. Replaced: native capture only - the WinApi instance, the
mss desktop context and, in ``wgc_boundary`` mode, the capture worker process,
its pipe and its job object - plus the ``time`` name inside the replayed modules
(see clock.py) and a diagnostics recorder that keeps rows in memory.

The accepted-result callback does what ``server.record_result`` does with the
detector: ResultGate.try_accept with the server's 5 s cooldown and, when
accepted, ``detector.external_mutation()``. Persistence is not replayed here;
T0 and T2 own it.
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path

from .clock import ModuleTime, VirtualClock
from .images import decode_image
from .paths import resolve_fixture_file
from .schema import MANUAL_AFTER_ACTIONS

# server.record_result: ``cooldown = 5.0``. A T0 check keeps these in step.
SERVER_COOLDOWN_SECONDS = 5.0
STATUS_FRAME = "replay frame source"
STATUS_GAP = "replay capture unavailable"


class ReplayError(RuntimeError):
    """The replay could not faithfully drive production; the case fails."""


def load_verified_image(fixtures_root, image_input):
    path = resolve_fixture_file(fixtures_root, image_input["path"])
    data = path.read_bytes()
    if len(data) != image_input["bytes"]:
        raise ReplayError(f"{image_input['path']} changed size after validation")
    if hashlib.sha256(data).hexdigest() != image_input["sha256"]:
        raise ReplayError(f"{image_input['path']} changed content after validation")
    decoded = decode_image(data, image_input["format"])
    if (decoded.width, decoded.height) != (image_input["width"], image_input["height"]):
        raise ReplayError(f"{image_input['path']} decoded to unexpected dimensions")
    return decoded


def json_safe(value):
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return repr(value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return repr(value)


def classify_image(result_detector, templates_path, decoded):
    classifier = result_detector.ResultClassifier(templates_path)
    frame_class, debug = classifier.classify_bgra(decoded.bgra, decoded.width, decoded.height)
    return {"frame_class": frame_class, "debug": json_safe(debug)}


class Cursor:
    def __init__(self):
        self.index = 0


class ReplayRecorder:
    """The DiagnosticRecorder surface ResultDetector.run uses, kept in memory.

    ``capture_roi`` names an image instead of writing one; that is the only way
    it differs from the production recorder's effect on the loop.
    """

    def __init__(self, root, cursor):
        self.root = Path(root)
        self.cursor = cursor
        self.frames = []
        self.rows = []
        self.flushes = []

    def buffer_frame(self, **payload):
        self.frames.append((self.cursor.index, payload))

    def flush_frame_context(self, reason):
        self.flushes.append((self.cursor.index, str(reason)))
        return 0

    def capture_roi(self, shot, label):
        return f"replay-{label}.png"

    def record(self, kind, **payload):
        self.rows.append((self.cursor.index, kind, payload))


class GateSink:
    """The detector-facing half of server.record_result, with the real gate."""

    def __init__(self, gate, clock, cursor):
        self.gate = gate
        self.clock = clock
        self.cursor = cursor
        self.detector = None
        self.calls = []
        self.manual_calls = []
        self.events = []

    def on_result(self, result, source):
        now = self.clock.processing_now()
        accepted = self.gate.try_accept(SERVER_COOLDOWN_SECONDS, now=now)
        if accepted:
            self.detector.external_mutation()
        self.calls.append((self.cursor.index, result, source, accepted))
        return accepted

    def manual_result(self, result):
        """server.record_result for the gate's other source.

        record_result is source-agnostic: an automatic detection and a manual
        result share one ResultGate, and a refused duplicate must not touch the
        detector or extend the cooldown. Only that arbitration is replayed here;
        history and stats belong to T0/T2.
        """
        now = self.clock.processing_now()
        accepted = self.gate.try_accept(SERVER_COOLDOWN_SECONDS, now=now)
        if accepted:
            self.detector.external_mutation()
        self.manual_calls.append((self.cursor.index, result, "manual", accepted))
        return accepted

    def event(self, kind, payload, remember=True):
        self.events.append((self.cursor.index, kind, json_safe(payload)))

    def after_undo(self):
        # server.undo_result: the gate reservation is cleared and the detector
        # is told, so a still-visible final cannot immediately count again.
        self.gate.clear_for_manual_correction()
        self.detector.after_undo()

    def reset(self):
        # server.reset_stats: the gate is locked at "now" and the detector told.
        self.gate.lock_now(now=self.clock.processing_now())
        self.detector.external_mutation()


class ReplayStop:
    """The stop event run() polls; each wait() ends exactly one step."""

    def __init__(self, steps, clock, cursor, on_step_end, before_step):
        self.steps = steps
        self.clock = clock
        self.cursor = cursor
        self.on_step_end = on_step_end
        self.before_step = before_step
        self.timeouts = []

    def is_set(self):
        return self.cursor.index >= len(self.steps)

    def wait(self, timeout=None):
        if self.is_set():
            raise ReplayError("the detector waited after the last replay step")
        self.timeouts.append(timeout)
        self.on_step_end(self.cursor.index, timeout)
        self.cursor.index += 1
        if not self.is_set():
            self.clock.set_step(self.steps[self.cursor.index]["at_ms"])
            self.before_step(self.cursor.index)
        return self.is_set()


class ReplayCapture:
    """frame_source mode: GameCapture's interface, fed from validated fixtures."""

    def __init__(self, steps, images, cursor, clock, screenshot_type):
        self.steps = steps
        self.images = images
        self.cursor = cursor
        self.clock = clock
        self.screenshot_type = screenshot_type
        self.status = "replay capture not started"
        self.discontinuity = False
        self.identity_changed = False
        self.captured_at = None
        self.grabs = []
        self.closes = 0

    def grab(self, desktop):
        step = self.steps[self.cursor.index]
        self.grabs.append(self.cursor.index)
        flags = step.get("capture", {})
        self.discontinuity = bool(flags.get("discontinuity"))
        self.identity_changed = bool(flags.get("identity_changed"))
        if "gap" in step:
            self.status = STATUS_GAP
            return None
        image = self.images[step["frame"]]
        self.captured_at = self.clock.capture_seconds
        self.status = STATUS_FRAME
        return self.screenshot_type.from_size(bytearray(image.bgra), image.width, image.height)

    def close(self):
        self.closes += 1


class NullWinApi:
    """frame_source mode never needs window queries; any use is a replay bug."""

    def __getattr__(self, name):
        raise ReplayError(f"frame_source replay does not answer WinApi.{name}")


class FakeWinApi:
    """wgc_boundary mode: the capture target for the current step, no desktop."""

    def __init__(self, steps, targets, geometry, cursor):
        self.steps = steps
        self.targets = targets
        self.client, self.bounds = geometry
        self.cursor = cursor
        self.fallback_checks = 0

    def game_target(self):
        step = self.steps[min(self.cursor.index, len(self.steps) - 1)]
        target = self.targets[step["wgc"]["target"]]
        return {"hwnd": target["hwnd"], "pid": target["pid"], "client": dict(self.client),
                "bounds": tuple(self.bounds)}

    def region_unobscured(self, hwnd, region, allowed=()):
        # The guarded desktop fallback is refused: replay has no desktop pixels.
        self.fallback_checks += 1
        return False


class FakeEvent:
    def __init__(self):
        self.flag = False

    def set(self):
        self.flag = True

    def is_set(self):
        return self.flag

    def wait(self, timeout=None):
        return self.flag


class FakeProcess:
    """Stands in for the WGC worker process; its target never runs."""

    next_pid = 60000

    def __init__(self, target=None, args=(), name=None, daemon=None):
        FakeProcess.next_pid += 1
        self.pid = FakeProcess.next_pid
        self.name = name
        self.alive = False
        self.closed = False

    def start(self):
        self.alive = True

    def is_alive(self):
        return self.alive

    def join(self, timeout=None):
        return None

    def terminate(self):
        self.alive = False

    def kill(self):
        self.alive = False

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, context, role):
        self.context = context
        self.role = role
        self.closed = False

    def poll(self, timeout=0):
        context = self.context
        return (self is context.parent and not self.closed and context.request_pending
                and context.sample is not None)

    def recv(self):
        if not self.poll():
            raise ReplayError("GameCapture received from the replay worker without a delivered sample")
        sample, self.context.sample = self.context.sample, None
        self.context.request_pending = False
        self.context.delivered = True
        return sample

    def send(self, message):
        if message != "frame" or self.closed:
            raise ReplayError(f"unexpected capture worker request {message!r}")
        self.context.request_pending = True
        self.context.requests += 1

    def close(self):
        self.closed = True


class FakeContext:
    """The multiprocessing context GameCapture is constructed with."""

    def __init__(self):
        self.parent = None
        self.request_pending = False
        self.sample = None
        self.delivered = False
        self.requests = 0
        self.processes = []

    def Pipe(self):
        parent, child = FakeConnection(self, "parent"), FakeConnection(self, "child")
        self.parent = parent
        self.request_pending = False
        return parent, child

    def Event(self):
        return FakeEvent()

    def Process(self, target=None, args=(), name=None, daemon=None):
        process = FakeProcess(target, args, name, daemon)
        self.processes.append(process)
        return process


class FakeJob:
    def __init__(self, pid):
        self.pid = pid
        self.closed = False

    def close(self):
        self.closed = True


def fake_stop_process(process):
    process.alive = False
    process.close()


def client_geometry_for_roi(game_capture, width, height):
    """A borderless client whose production result_region is exactly the ROI."""
    for client_width in range(max(640, math.floor(width / 0.60) - 2), math.floor(width / 0.60) + 4):
        if int(client_width * 0.60) == width:
            break
    else:
        raise ReplayError(f"no client width yields a {width}-pixel result ROI")
    for client_height in range(max(360, math.floor(height / 0.07) - 2), math.floor(height / 0.07) + 30):
        if max(40, int(client_height * 0.07)) == height:
            break
    else:
        raise ReplayError(f"no client height yields a {height}-pixel result ROI")
    client = {"left": 0, "top": 0, "width": client_width, "height": client_height}
    region = game_capture.result_region(client)
    if (region["width"], region["height"]) != (width, height):
        raise ReplayError("derived client geometry does not reproduce the fixture ROI")
    return client, (0, 0, client_width, client_height)


class FakeDesktopContext:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def grab(self, region):
        raise ReplayError("replay never grabs desktop pixels")


class FakeMss:
    @staticmethod
    def mss():
        return FakeDesktopContext()


def run_sequence(modules, record_input, images, templates_path, case_root):
    result_detector = modules["result_detector"]
    result_gate = modules["result_gate"]
    game_capture = modules["game_capture"]
    screenshot_type = modules["screenshot_type"]
    steps = record_input["steps"]
    clock = VirtualClock(record_input["processing_ms"])
    cursor = Cursor()
    module_time = ModuleTime(clock)

    result_detector.time = module_time
    game_capture.time = module_time
    result_detector.mss = FakeMss
    mode = record_input["capture"]
    context = None
    winapi = None
    if mode == "frame_source":
        capture = ReplayCapture(steps, images, cursor, clock, screenshot_type)
        result_detector.WinApi = NullWinApi
        game_capture.GameCapture = lambda api: capture
    else:
        dimensions = {(images[s["wgc"]["sample"]["frame"]].width, images[s["wgc"]["sample"]["frame"]].height)
                      for s in steps if s["wgc"]["sample"] is not None}
        if len(dimensions) != 1:
            raise ReplayError("wgc_boundary replay needs exactly one ROI geometry")
        geometry = client_geometry_for_roi(game_capture, *next(iter(dimensions)))
        context = FakeContext()
        winapi = FakeWinApi(steps, record_input["targets"], geometry, cursor)
        game_capture.KillOnCloseJob = FakeJob
        game_capture.stop_process = fake_stop_process
        real_capture_type = modules["game_capture_type"]
        captures = []

        def capture_factory(api):
            instance = real_capture_type(api, context=context)
            captures.append(instance)
            return instance

        result_detector.WinApi = lambda: winapi
        game_capture.GameCapture = capture_factory

    root = Path(case_root) / "detector-root"
    root.mkdir()
    template_bytes = Path(templates_path).read_bytes()
    (root / "detector_templates.json").write_bytes(template_bytes)
    diagnostics_root = Path(case_root) / "diagnostics"
    diagnostics_root.mkdir()
    recorder = ReplayRecorder(diagnostics_root, cursor)
    sink = GateSink(result_gate.ResultGate(), clock, cursor)
    observations = []

    def before_step(index):
        if context is None:
            return
        context.delivered = False
        sample = steps[index]["wgc"]["sample"]
        if sample is None:
            context.sample = None
            return
        image = images[sample["frame"]]
        context.sample = (clock.seconds(sample["captured_ms"]), sample["timespan"],
                          image.width, image.height, bytes(image.bgra))

    detector_holder = {}

    def on_step_end(index, timeout):
        detector = detector_holder["detector"]
        live_capture = detector.capture
        frames = [payload for step, payload in recorder.frames if step == index]
        decisions = [payload.get("result") for step, kind, payload in recorder.rows
                     if step == index and kind == "state_decision"]
        calls = [call for call in sink.calls if call[0] == index]
        errors = [kind for step, kind, payload in recorder.rows
                  if step == index and kind in ("detector_error", "detector_init_error")]
        health = detector.health.snapshot()
        observation = {
            "id": steps[index]["id"],
            "at_ms": steps[index]["at_ms"],
            "wait_timeout": timeout,
            "frames_buffered": len(frames),
            "frame_class": frames[-1]["frame_state"] if frames else None,
            "gameplay_activity": frames[-1]["gameplay_activity"] if frames else None,
            "motion_score": frames[-1]["motion_score"] if frames else None,
            "detections": decisions,
            "gate_calls": [{"result": call[1], "source": call[2], "accepted": call[3]} for call in calls],
            "state": json_safe(detector.state.snapshot()),
            "capture": {
                "shot": bool(frames),
                "status": live_capture.status,
                "discontinuity": bool(live_capture.discontinuity),
                "identity_changed": bool(live_capture.identity_changed),
            },
            "health": {"status": health.get("status"), "last_result": health.get("last_result"),
                       "error": health.get("error")},
            "errors": errors,
        }
        if context is not None:
            observation["capture"]["sample_delivered"] = context.delivered
            if steps[index]["wgc"]["sample"] is not None and not context.delivered:
                context.sample = None
        after = steps[index].get("after")
        if after == "external_mutation":
            sink.reset()
        elif after == "after_undo":
            sink.after_undo()
        elif after in MANUAL_AFTER_ACTIONS:
            manual = MANUAL_AFTER_ACTIONS[after]
            observation["after_gate"] = {"result": manual, "source": "manual",
                                         "accepted": sink.manual_result(manual)}
        observation["after"] = after
        if after:
            observation["state_after_action"] = json_safe(detector.state.snapshot())
        observations.append(observation)

    stop = ReplayStop(steps, clock, cursor, on_step_end, before_step)
    clock.set_step(steps[0]["at_ms"])
    before_step(0)
    detector = result_detector.ResultDetector(
        root, lambda: {"result_detector_enabled": True}, sink.on_result, sink.event, stop,
        diagnostic_recorder=recorder)
    detector_holder["detector"] = detector
    sink.detector = detector
    detector.run()
    if cursor.index != len(steps):
        raise ReplayError(f"ResultDetector.run returned after {cursor.index} of {len(steps)} steps")
    grabs = capture.grabs if mode == "frame_source" else None
    if grabs is not None and grabs != list(range(len(steps))):
        raise ReplayError(f"expected exactly one grab per step, saw {grabs}")
    return {
        "observations": observations,
        "flush_reasons": sorted({reason for _, reason in recorder.flushes}),
        "record_kinds": sorted({kind for _, kind, _ in recorder.rows}),
        "stopped_flush": any(reason == "detector_stopped" for _, reason in recorder.flushes),
        "capture_closes": capture.closes if mode == "frame_source" else None,
        "worker_requests": context.requests if context is not None else None,
        "desktop_fallback_checks": winapi.fallback_checks if winapi is not None else None,
        "virtual_base_seconds": VirtualClock.BASE_SECONDS,
    }
