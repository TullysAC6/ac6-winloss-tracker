"""Compare what production produced with what a fixture record asserts.

Pure functions over the record and the worker's ``actual`` report, so the
comparison itself is T0-testable. Besides the per-record checks, every sequence
also has to satisfy contract checks that no record can switch off: each WIN or
LOSS detection reaches the gate exactly once, DRAW and non-results never do, the
loop never leaves its normal poll path, and run() reaches its cleanup.
"""
from __future__ import annotations

from .schema import DETECTIONS

POLL_SECONDS = 0.75
FRAME_SOURCE_CLOSES = 2  # the enable boundary and run()'s finally


def failure(stage, check, expected, actual, reason, step=None):
    return {"step": step, "stage": stage, "check": check, "expected": expected,
            "actual": actual, "reason": reason}


def same_value(expected, actual):
    if expected is None or actual is None:
        return expected is actual
    if isinstance(expected, bool) or isinstance(actual, bool):
        return type(expected) is type(actual) and expected == actual
    if type(expected) in (int, float) and type(actual) in (int, float):
        return expected == actual
    return type(expected) is type(actual) and expected == actual


def _lookup(debug, path):
    node = debug
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            raise KeyError(path)
        node = node[part]
    return node


_BOUNDS = (
    ("at_least", lambda value, bound: value >= bound),
    ("at_most", lambda value, bound: value <= bound),
    ("above", lambda value, bound: value > bound),
    ("below", lambda value, bound: value < bound),
)


def compare_image(record, actual):
    checks = record["checks"]
    failures = []
    got = actual.get("frame_class")
    if not same_value(checks["frame_class"], got):
        failures.append(failure("classify", "frame_class", checks["frame_class"], got,
                                "classifier output differs from the reviewed expectation"))
    debug = actual.get("debug")
    for check in checks.get("debug", ()):
        path = check["path"]
        try:
            value = _lookup(debug, path)
        except KeyError:
            failures.append(failure("classify", f"debug.{path}", check, None,
                                    "classifier debug output has no such value"))
            continue
        if "equals" in check:
            if not same_value(check["equals"], value):
                failures.append(failure("classify", f"debug.{path}", check["equals"], value,
                                        "classifier debug value differs"))
            continue
        if type(value) not in (int, float):
            failures.append(failure("classify", f"debug.{path}", check, value, "classifier debug value is not a number"))
            continue
        for name, test in _BOUNDS:
            if name in check and not test(value, check[name]):
                failures.append(failure("classify", f"debug.{path}", {name: check[name]}, value,
                                        f"classifier debug value is not {name.replace('_', ' ')} the bound"))
    return failures


def derive_totals(observations):
    accepted = {"win": 0, "loss": 0}
    detections = {name: 0 for name in DETECTIONS}
    rejected = frames = errors = 0
    for observation in observations:
        for call in observation["gate_calls"]:
            if call["accepted"]:
                accepted[call["result"]] = accepted.get(call["result"], 0) + 1
            else:
                rejected += 1
        for detection in observation["detections"]:
            detections[detection] = detections.get(detection, 0) + 1
        frames += 1 if observation["frames_buffered"] else 0
        errors += len(observation["errors"])
    return {"accepted": accepted, "detections": detections, "gate_rejected": rejected,
            "frames_classified": frames, "detector_errors": errors}


def compare_sequence(record, actual):
    steps = record["input"]["steps"]
    checks = record["checks"]
    observations = actual.get("observations") or []
    failures = []
    if len(observations) != len(steps):
        failures.append(failure("replay", "steps", len(steps), len(observations),
                                "the detector did not end every replay step exactly once"))
    for step, observation in zip(steps, observations):
        step_id = step["id"]
        if observation.get("id") != step_id:
            failures.append(failure("replay", "step_id", step_id, observation.get("id"),
                                    "observations are out of order", step_id))
            continue
        if not same_value(POLL_SECONDS, observation["wait_timeout"]):
            failures.append(failure("replay", "wait_timeout", POLL_SECONDS, observation["wait_timeout"],
                                    "the detector left its normal poll path", step_id))
        if observation["frames_buffered"] > 1:
            failures.append(failure("replay", "frames_buffered", 1, observation["frames_buffered"],
                                    "more than one frame was classified in one step", step_id))
        if observation["errors"]:
            failures.append(failure("detector", "errors", [], observation["errors"],
                                    "ResultDetector.run recorded a detector error", step_id))
        expect = checks["steps"][step_id]
        if not same_value(expect["frame_class"], observation["frame_class"]):
            failures.append(failure("classify", "frame_class", expect["frame_class"], observation["frame_class"],
                                    "the frame class seen by the detector loop differs", step_id))
        expected_detections = [] if expect["detection"] is None else [expect["detection"]]
        if observation["detections"] != expected_detections:
            failures.append(failure("state", "detection", expect["detection"], observation["detections"],
                                    "the state machine decision differs", step_id))
        calls = observation["gate_calls"]
        gate_results = [call["result"] for call in calls]
        contract = [name for name in observation["detections"] if name in ("win", "loss")]
        if gate_results != contract:
            failures.append(failure("gate", "gate_calls", contract, gate_results,
                                    "every WIN/LOSS detection reaches the gate once; DRAW and non-results never do",
                                    step_id))
        if any(call["source"] != "auto" for call in calls):
            failures.append(failure("gate", "source", "auto", [call["source"] for call in calls],
                                    "automatic detections must reach the gate as source 'auto'", step_id))
        if "gate" in expect:
            outcome = None if not calls else ("accepted" if calls[-1]["accepted"] else "rejected")
            if not same_value(expect["gate"], outcome):
                failures.append(failure("gate", "gate", expect["gate"], outcome, "the gate outcome differs", step_id))
        if "gameplay_activity" in expect and not same_value(expect["gameplay_activity"],
                                                             observation["gameplay_activity"]):
            failures.append(failure("motion", "gameplay_activity", expect["gameplay_activity"],
                                    observation["gameplay_activity"], "motion-confirmed activity differs", step_id))
        for group in ("state", "capture", "health"):
            for key, value in expect.get(group, {}).items():
                got = observation[group].get(key, "<missing>")
                if not same_value(value, got):
                    failures.append(failure(group, f"{group}.{key}", value, got, f"{group} differs", step_id))
    totals = derive_totals(observations)
    for key, value in checks["totals"].items():
        if totals[key] != value:
            failures.append(failure("totals", key, value, totals[key], "sequence totals differ"))
    if actual.get("stopped_flush") is not True:
        failures.append(failure("replay", "detector_stopped", True, actual.get("stopped_flush"),
                                "run() did not reach its cleanup path"))
    if record["input"]["capture"] == "frame_source" and actual.get("capture_closes") != FRAME_SOURCE_CLOSES:
        failures.append(failure("replay", "capture_closes", FRAME_SOURCE_CLOSES, actual.get("capture_closes"),
                                "capture must close at the enable boundary and in run()'s finally"))
    return failures
