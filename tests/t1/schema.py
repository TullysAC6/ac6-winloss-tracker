"""Schema for T1 fixture records and corpus registries (schema version 1).

Three things are kept apart on purpose:

* ``truth``  - what a human confirmed the pixels show (semantic, reviewed);
* ``checks`` - which production outputs are asserted, and their expected values;
* ``actual`` - what production produced. It exists only in runner reports.

Validation is strict: unknown keys, versions, adapters, enum values or check
paths are errors, never ignored. A record the harness cannot fully understand
must not become a silently weaker test.
"""
from __future__ import annotations

import re

from .strict_json import MetadataError

SCHEMA_VERSION = 1

ID_PATTERN = re.compile(r"[a-z0-9]+(?:[.-][a-z0-9]+)*")
STEP_ID_PATTERN = re.compile(r"s[0-9]{2,3}")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
TAG_PATTERN = re.compile(r"[a-z0-9_]+(?:\.[a-z0-9_]+)+")

MAX_ID = 96
MAX_TEXT = 600
MAX_SEQUENCE_STEPS = 256
MAX_AT_MS = 3_600_000
MAX_DEBUG_CHECKS = 16

FRAME_CLASSES = ("CLEAR", "PHASE", "FINAL_WIN", "FINAL_LOSS", "FINAL_DRAW", "NON_CLEAR")
RESULTS = ("win", "loss", "draw", "none", "unknown")
DETECTIONS = ("win", "loss", "draw")
SCREENS = ("final_result", "phase_result", "gameplay", "garage_menu", "rank_menu")
UNKNOWN_ONLY = ("unknown",)

IMAGE_ADAPTER = "result_classifier.v1"
SEQUENCE_ADAPTER = "result_detector_run.v1"
ADAPTERS = (IMAGE_ADAPTER, SEQUENCE_ADAPTER)
CAPTURE_MODES = ("frame_source", "wgc_boundary")
GAP_KINDS = ("capture_unavailable",)
AFTER_ACTIONS = ("external_mutation", "after_undo")

# The results family directory names. ``lose`` is the historical directory
# name; the semantic value the runtime uses is ``loss``.
RESULT_CATEGORIES = ("win", "lose", "draw", "clear", "phase", "negatives", "sequences")
CATEGORY_RESULT = {"win": "win", "lose": "loss", "draw": "draw", "clear": "none",
                   "phase": "none", "negatives": "none"}

SOURCE_TYPES_IMAGE = ("repository_asset",)
SOURCE_TYPES_SEQUENCE = ("recorded_sequence", "composed_sequence")
TEMPLATE_PARTITIONS = ("not_applicable", "unmapped", "training", "holdout")
TRUTH_REVIEW = ("legacy_label_visually_confirmed",)
# Sequence truth is a scenario over reviewed frames: either the migrated legacy
# regression itself, or a scenario whose expectations follow the documented
# production contract (tests/fixtures/README.md) step by step.
TRUTH_REVIEW_SEQUENCE = ("legacy_regression_scenario", "contract_scenario_over_reviewed_fixtures")
PRIVACY_REVIEW = ("inspected_no_personal_data",)
PRIVACY_CHECKLIST = ("player_name", "opponent_name", "steam_identifier", "notification", "overlay",
                     "desktop", "local_path", "token_or_secret", "embedded_metadata")
REDISTRIBUTION_REVIEW = ("owner_supplied_repository_asset",)
SEQUENCE_REVIEW = ("inherits_referenced_fixtures",)

METRIC_FIELDS = ("coverage", "span", "center")
CLUSTER_FIELDS = ("start", "end", "span", "center", "coverage", "density")
DEBUG_SCALARS = (
    "reason", "draw_grid_score", "draw_like", "dark_ratio", "mean_gray", "win_final_score",
    "win_phase_score", "loss_final_score", "loss_phase_score", "win_final_grid_score",
    "win_phase_grid_score", "loss_final_grid_score", "loss_phase_grid_score", "result_band_like",
    "phase_win_geom", "phase_loss_geom", "phase_color_geom", "phase_prefix_like",
    "phase_template_like", "phase_bright_like", "bright_run_fraction", "bright_run_crosses_center",
    "central_active_fraction", "max_center_gap", "central_continuous", "central_bright_density",
)
DEBUG_METRICS = ("win", "loss", "bright", "draw")
DEBUG_CLUSTERS = ("draw_cluster", "draw_y_cluster", "win_cluster", "loss_cluster", "win_y_cluster",
                  "loss_y_cluster", "bright_cluster")
DEBUG_PATHS = frozenset(
    list(DEBUG_SCALARS)
    + [f"{name}.{field}" for name in DEBUG_METRICS for field in METRIC_FIELDS]
    + [f"{name}.{field}" for name in DEBUG_CLUSTERS for field in CLUSTER_FIELDS]
)
DEBUG_BOUNDS = ("at_least", "at_most", "above", "below")

STATE_FIELDS = {"armed": bool, "post_result_lock": bool, "clear_ready": bool,
                "candidate": (str, type(None)), "candidate_hits": int,
                "last_reject_reason": (str, type(None))}
CAPTURE_FIELDS = {"shot": bool, "discontinuity": bool, "identity_changed": bool, "status": str,
                  "sample_delivered": bool}
HEALTH_FIELDS = {"status": str, "last_result": (str, type(None))}
STEP_CHECK_FIELDS = ("frame_class", "gameplay_activity", "detection", "gate", "state", "capture", "health")
GATE_OUTCOMES = ("accepted", "rejected")
TOTAL_FIELDS = ("accepted", "detections", "gate_rejected", "frames_classified", "detector_errors")


def _fail(source, message):
    raise MetadataError(f"{source}: {message}")


def _object(source, value, required, optional=()):
    if not isinstance(value, dict):
        _fail(source, "must be a JSON object")
    missing = [key for key in required if key not in value]
    if missing:
        _fail(source, f"missing required field(s): {', '.join(missing)}")
    unknown = sorted(set(value) - set(required) - set(optional))
    if unknown:
        _fail(source, f"unknown field(s): {', '.join(unknown)}")
    return value


def _text(source, value, limit=MAX_TEXT):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        _fail(source, f"must be non-empty text of at most {limit} characters")
    return value


def _enum(source, value, allowed):
    if value not in allowed:
        _fail(source, f"{value!r} is not one of {', '.join(map(repr, allowed))}")
    return value


def _int(source, value, low, high):
    if type(value) is not int or not low <= value <= high:
        _fail(source, f"must be an integer between {low} and {high}")
    return value


def _bool(source, value):
    if type(value) is not bool:
        _fail(source, "must be true or false")
    return value


def _number(source, value):
    if type(value) not in (int, float):
        _fail(source, "must be a number")
    return value


def _identifier(source, value):
    if not isinstance(value, str) or len(value) > MAX_ID or not ID_PATTERN.fullmatch(value):
        _fail(source, "must be a lowercase dotted identifier")
    return value


def validate_coverage_tags(source, tags, known_tags):
    if not isinstance(tags, list) or not tags:
        _fail(source, "coverage must be a non-empty list")
    if len(set(tags)) != len(tags):
        _fail(source, "coverage lists a tag twice")
    for tag in tags:
        if not isinstance(tag, str) or tag not in known_tags:
            _fail(source, f"unknown coverage tag {tag!r}")
    return tags


def _review(source, value, sequence):
    review = _object(source, value, ("truth", "privacy", "redistribution"))
    truth = _object(f"{source}.truth", review["truth"], ("status", "basis"))
    _enum(f"{source}.truth.status", truth["status"], TRUTH_REVIEW_SEQUENCE if sequence else TRUTH_REVIEW)
    _text(f"{source}.truth.basis", truth["basis"])
    if sequence:
        for name in ("privacy", "redistribution"):
            part = _object(f"{source}.{name}", review[name], ("status",))
            _enum(f"{source}.{name}.status", part["status"], SEQUENCE_REVIEW)
        return review
    privacy = _object(f"{source}.privacy", review["privacy"], ("status", "checked", "notes"))
    _enum(f"{source}.privacy.status", privacy["status"], PRIVACY_REVIEW)
    if privacy["checked"] != list(PRIVACY_CHECKLIST):
        _fail(f"{source}.privacy.checked", f"must list the full checklist in order: {list(PRIVACY_CHECKLIST)}")
    _text(f"{source}.privacy.notes", privacy["notes"])
    redistribution = _object(f"{source}.redistribution", review["redistribution"], ("status", "notes"))
    _enum(f"{source}.redistribution.status", redistribution["status"], REDISTRIBUTION_REVIEW)
    _text(f"{source}.redistribution.notes", redistribution["notes"])
    return review


def _debug_checks(source, checks):
    if not isinstance(checks, list) or not checks or len(checks) > MAX_DEBUG_CHECKS:
        _fail(source, f"debug checks must be a list of 1..{MAX_DEBUG_CHECKS} objects")
    for index, check in enumerate(checks):
        where = f"{source}[{index}]"
        if not isinstance(check, dict) or "path" not in check:
            _fail(where, "must be an object with a path")
        path = check["path"]
        if path not in DEBUG_PATHS:
            _fail(where, f"unknown classifier debug path {path!r}")
        operators = set(check) - {"path"}
        if not operators:
            _fail(where, "needs an assertion operator")
        unknown = operators - set(DEBUG_BOUNDS) - {"equals"}
        if unknown:
            _fail(where, f"unknown operator(s): {', '.join(sorted(unknown))}")
        if "equals" in operators:
            if len(operators) != 1:
                _fail(where, "equals cannot be combined with bounds")
            if type(check["equals"]) not in (bool, int, float, str):
                _fail(where, "equals must be a boolean, number or string")
        for bound in operators & set(DEBUG_BOUNDS):
            _number(f"{where}.{bound}", check[bound])
    return checks


def validate_image_record(source, record, category, known_tags):
    _object(source, record, ("schema_version", "id", "family", "category", "input", "truth", "checks",
                             "coverage", "provenance", "review"))
    if record["schema_version"] != SCHEMA_VERSION:
        _fail(f"{source}.schema_version", f"unsupported schema version {record['schema_version']!r}")
    _identifier(f"{source}.id", record["id"])
    if not record["id"].startswith("result."):
        _fail(f"{source}.id", "results records are named result.*")
    _enum(f"{source}.family", record["family"], ("results",))
    if record["category"] != category:
        _fail(f"{source}.category", f"record says {record['category']!r} but is stored under {category!r}")
    image = _object(f"{source}.input", record["input"], ("kind", "path", "format", "width", "height",
                                                          "bytes", "sha256"))
    _enum(f"{source}.input.kind", image["kind"], ("roi",))
    _enum(f"{source}.input.format", image["format"], ("ppm", "png"))
    if not isinstance(image["path"], str) or not image["path"].lower().endswith("." + image["format"]):
        _fail(f"{source}.input.path", "extension must match the declared format")
    _int(f"{source}.input.width", image["width"], 1, 7680)
    _int(f"{source}.input.height", image["height"], 1, 4320)
    _int(f"{source}.input.bytes", image["bytes"], 1, 64 * 1024 * 1024)
    if not isinstance(image["sha256"], str) or not SHA256_PATTERN.fullmatch(image["sha256"]):
        _fail(f"{source}.input.sha256", "must be 64 lowercase hex digits")
    truth = _object(f"{source}.truth", record["truth"], ("result", "screen", "match_type", "match_format"))
    _enum(f"{source}.truth.result", truth["result"], RESULTS)
    _enum(f"{source}.truth.screen", truth["screen"], SCREENS)
    # A result ROI carries no evidence of mode or format; never infer it.
    _enum(f"{source}.truth.match_type", truth["match_type"], UNKNOWN_ONLY)
    _enum(f"{source}.truth.match_format", truth["match_format"], UNKNOWN_ONLY)
    if truth["result"] != CATEGORY_RESULT[category]:
        _fail(f"{source}.truth.result", f"{truth['result']!r} does not belong in results/{category}")
    checks = _object(f"{source}.checks", record["checks"], ("adapter", "frame_class"), ("debug",))
    _enum(f"{source}.checks.adapter", checks["adapter"], (IMAGE_ADAPTER,))
    _enum(f"{source}.checks.frame_class", checks["frame_class"], FRAME_CLASSES)
    if "debug" in checks:
        _debug_checks(f"{source}.checks.debug", checks["debug"])
    validate_coverage_tags(f"{source}.coverage", record["coverage"], known_tags)
    provenance = _object(f"{source}.provenance", record["provenance"],
                         ("source_type", "reference", "origin", "synthetic", "template_partition", "legacy"))
    _enum(f"{source}.provenance.source_type", provenance["source_type"], SOURCE_TYPES_IMAGE)
    _text(f"{source}.provenance.reference", provenance["reference"])
    _text(f"{source}.provenance.origin", provenance["origin"])
    _bool(f"{source}.provenance.synthetic", provenance["synthetic"])
    _enum(f"{source}.provenance.template_partition", provenance["template_partition"], TEMPLATE_PARTITIONS)
    # Fixtures migrated from tests/manifest.json keep its entry; new ones have none.
    if provenance["legacy"] is not None:
        legacy = _object(f"{source}.provenance.legacy", provenance["legacy"],
                         ("manifest_key", "manifest_expected"))
        if legacy["manifest_key"] != "fixtures/" + image["path"]:
            _fail(f"{source}.provenance.legacy.manifest_key", "must name the same file as input.path")
        _enum(f"{source}.provenance.legacy.manifest_expected", legacy["manifest_expected"], FRAME_CLASSES)
        if legacy["manifest_expected"] != checks["frame_class"]:
            _fail(f"{source}.checks.frame_class", "differs from the migrated legacy expectation")
    _review(f"{source}.review", record["review"], sequence=False)
    return record


def _step_checks(source, check, step, image_ids):
    _object(source, check, ("frame_class", "detection"), [f for f in STEP_CHECK_FIELDS
                                                           if f not in ("frame_class", "detection")])
    if check["frame_class"] is not None:
        _enum(f"{source}.frame_class", check["frame_class"], FRAME_CLASSES)
    if check["detection"] is not None:
        _enum(f"{source}.detection", check["detection"], DETECTIONS)
    if "gameplay_activity" in check:
        _bool(f"{source}.gameplay_activity", check["gameplay_activity"])
    if "gate" in check and check["gate"] is not None:
        _enum(f"{source}.gate", check["gate"], GATE_OUTCOMES)
    for name, fields in (("state", STATE_FIELDS), ("capture", CAPTURE_FIELDS), ("health", HEALTH_FIELDS)):
        if name not in check:
            continue
        part = check[name]
        if not isinstance(part, dict) or not part:
            _fail(f"{source}.{name}", "must be a non-empty object")
        for key, value in part.items():
            if key not in fields:
                _fail(f"{source}.{name}", f"unknown field {key!r}")
            expected = fields[key]
            ok = isinstance(value, expected) if isinstance(expected, tuple) else type(value) is expected
            if not ok:
                _fail(f"{source}.{name}.{key}", "has the wrong type")
    return check


def validate_sequence_record(source, record, known_tags, image_records):
    _object(source, record, ("schema_version", "id", "family", "category", "input", "truth", "checks",
                             "coverage", "provenance", "review"))
    if record["schema_version"] != SCHEMA_VERSION:
        _fail(f"{source}.schema_version", f"unsupported schema version {record['schema_version']!r}")
    _identifier(f"{source}.id", record["id"])
    if not record["id"].startswith("result.sequence."):
        _fail(f"{source}.id", "sequence records are named result.sequence.*")
    _enum(f"{source}.family", record["family"], ("results",))
    _enum(f"{source}.category", record["category"], ("sequences",))
    data = record["input"]
    if not isinstance(data, dict):
        _fail(f"{source}.input", "must be a JSON object")
    mode = data.get("capture")
    _enum(f"{source}.input.capture", mode, CAPTURE_MODES)
    required = ("kind", "capture", "processing_ms", "steps") + (("targets",) if mode == "wgc_boundary" else ())
    _object(f"{source}.input", data, required)
    _enum(f"{source}.input.kind", data["kind"], ("sequence",))
    _int(f"{source}.input.processing_ms", data["processing_ms"], 0, 1000)
    targets = {}
    if mode == "wgc_boundary":
        if not isinstance(data["targets"], dict) or not 1 <= len(data["targets"]) <= 4:
            _fail(f"{source}.input.targets", "must name 1..4 capture targets")
        for name, target in data["targets"].items():
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,23}", name):
                _fail(f"{source}.input.targets", f"bad target name {name!r}")
            _object(f"{source}.input.targets.{name}", target, ("hwnd", "pid"))
            _int(f"{source}.input.targets.{name}.hwnd", target["hwnd"], 1, 2**31 - 1)
            _int(f"{source}.input.targets.{name}.pid", target["pid"], 1, 2**31 - 1)
        targets = data["targets"]
    steps = data["steps"]
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_SEQUENCE_STEPS:
        _fail(f"{source}.input.steps", f"must list 1..{MAX_SEQUENCE_STEPS} steps")
    seen = set()
    previous_at = -1
    dimensions = set()
    for index, step in enumerate(steps):
        where = f"{source}.input.steps[{index}]"
        if not isinstance(step, dict):
            _fail(where, "must be an object")
        if mode == "frame_source":
            kind_fields = [key for key in ("frame", "gap") if key in step]
            if len(kind_fields) != 1:
                _fail(where, "frame_source steps need exactly one of frame or gap")
            _object(where, step, ("id", "at_ms", kind_fields[0]), ("capture", "after"))
        else:
            _object(where, step, ("id", "at_ms", "wgc"), ("after",))
        if not isinstance(step["id"], str) or not STEP_ID_PATTERN.fullmatch(step["id"]):
            _fail(f"{where}.id", "must look like s01")
        if step["id"] in seen:
            _fail(f"{where}.id", f"duplicate step id {step['id']!r}")
        seen.add(step["id"])
        _int(f"{where}.at_ms", step["at_ms"], 0, MAX_AT_MS)
        if step["at_ms"] <= previous_at:
            _fail(f"{where}.at_ms", "sequence time must strictly increase")
        previous_at = step["at_ms"]
        if "after" in step:
            _enum(f"{where}.after", step["after"], AFTER_ACTIONS)
        referenced = None
        if mode == "frame_source":
            if "gap" in step:
                _enum(f"{where}.gap", step["gap"], GAP_KINDS)
            else:
                referenced = step["frame"]
            if "capture" in step:
                capture = _object(f"{where}.capture", step["capture"], (), ("discontinuity", "identity_changed"))
                if not capture:
                    _fail(f"{where}.capture", "must set discontinuity and/or identity_changed")
                for key, value in capture.items():
                    _bool(f"{where}.capture.{key}", value)
        else:
            wgc = _object(f"{where}.wgc", step["wgc"], ("target", "sample"))
            if wgc["target"] not in targets:
                _fail(f"{where}.wgc.target", f"unknown target {wgc['target']!r}")
            if wgc["sample"] is not None:
                sample = _object(f"{where}.wgc.sample", wgc["sample"], ("frame", "captured_ms", "timespan"))
                _int(f"{where}.wgc.sample.captured_ms", sample["captured_ms"], 0, MAX_AT_MS)
                _int(f"{where}.wgc.sample.timespan", sample["timespan"], 1, 2**62)
                referenced = sample["frame"]
        if referenced is not None:
            if referenced not in image_records:
                _fail(where, f"references unknown fixture {referenced!r}")
            image = image_records[referenced]["input"]
            dimensions.add((image["width"], image["height"]))
    if mode == "wgc_boundary":
        if len(dimensions) != 1:
            _fail(f"{source}.input.steps", "a wgc_boundary sequence replays one ROI geometry")
        width, height = next(iter(dimensions))
        if height < 40:
            _fail(f"{source}.input.steps",
                  f"{width}x{height} cannot come from production result_region (minimum height 40)")
    truth = _object(f"{source}.truth", record["truth"],
                    ("scenario", "expected_results", "match_type", "match_format"))
    _text(f"{source}.truth.scenario", truth["scenario"])
    if not isinstance(truth["expected_results"], list):
        _fail(f"{source}.truth.expected_results", "must be a list")
    for value in truth["expected_results"]:
        _enum(f"{source}.truth.expected_results", value, DETECTIONS)
    _enum(f"{source}.truth.match_type", truth["match_type"], UNKNOWN_ONLY)
    _enum(f"{source}.truth.match_format", truth["match_format"], UNKNOWN_ONLY)
    checks = _object(f"{source}.checks", record["checks"], ("adapter", "steps", "totals"))
    _enum(f"{source}.checks.adapter", checks["adapter"], (SEQUENCE_ADAPTER,))
    step_checks = checks["steps"]
    if not isinstance(step_checks, dict):
        _fail(f"{source}.checks.steps", "must be an object keyed by step id")
    if set(step_checks) != seen:
        missing = sorted(seen - set(step_checks))
        extra = sorted(set(step_checks) - seen)
        _fail(f"{source}.checks.steps", f"must assert every step exactly once (missing {missing}, unknown {extra})")
    for step in steps:
        _step_checks(f"{source}.checks.steps.{step['id']}", step_checks[step["id"]], step, image_records)
    totals = _object(f"{source}.checks.totals", checks["totals"], TOTAL_FIELDS)
    accepted = _object(f"{source}.checks.totals.accepted", totals["accepted"], ("win", "loss"))
    detections = _object(f"{source}.checks.totals.detections", totals["detections"], DETECTIONS)
    for key, value in list(accepted.items()) + list(detections.items()):
        _int(f"{source}.checks.totals.{key}", value, 0, MAX_SEQUENCE_STEPS)
    for key in ("gate_rejected", "frames_classified"):
        _int(f"{source}.checks.totals.{key}", totals[key], 0, MAX_SEQUENCE_STEPS)
    if totals["detector_errors"] != 0:
        _fail(f"{source}.checks.totals.detector_errors", "a detector error is never an expected outcome")
    # truth and checks must describe the same match outcome.
    semantic = sorted(truth["expected_results"])
    detected = sorted([name for name in DETECTIONS for _ in range(detections[name])])
    if semantic != detected:
        _fail(f"{source}.checks.totals.detections", f"disagrees with truth.expected_results {semantic}")
    stepwise = sorted(check["detection"] for check in step_checks.values() if check["detection"])
    if stepwise != detected:
        _fail(f"{source}.checks.steps", "per-step detections disagree with totals.detections")
    for result in ("win", "loss"):
        if accepted[result] > detections[result]:
            _fail(f"{source}.checks.totals.accepted", "cannot accept more results than were detected")
    validate_coverage_tags(f"{source}.coverage", record["coverage"], known_tags)
    provenance = _object(f"{source}.provenance", record["provenance"], ("source_type", "reference", "origin"))
    _enum(f"{source}.provenance.source_type", provenance["source_type"], SOURCE_TYPES_SEQUENCE)
    _text(f"{source}.provenance.reference", provenance["reference"])
    _text(f"{source}.provenance.origin", provenance["origin"])
    _review(f"{source}.review", record["review"], sequence=True)
    return record


def validate_families(source, registry):
    _object(source, registry, ("schema_version", "families"))
    if registry["schema_version"] != SCHEMA_VERSION:
        _fail(f"{source}.schema_version", "unsupported schema version")
    families = registry["families"]
    if not isinstance(families, dict) or "results" not in families:
        _fail(f"{source}.families", "must declare the results family")
    for name, family in families.items():
        if not re.fullmatch(r"[a-z][a-z_]{1,31}", name):
            _fail(f"{source}.families", f"bad family name {name!r}")
        _object(f"{source}.families.{name}", family, ("status", "categories", "note"))
        _enum(f"{source}.families.{name}.status", family["status"], ("implemented", "reserved"))
        _text(f"{source}.families.{name}.note", family["note"])
        if not isinstance(family["categories"], list):
            _fail(f"{source}.families.{name}.categories", "must be a list")
        for category in family["categories"]:
            if not isinstance(category, str) or not re.fullmatch(r"[a-z][a-z0-9_]{1,31}", category):
                _fail(f"{source}.families.{name}.categories", f"bad category {category!r}")
    if families["results"]["status"] != "implemented" or families["results"]["categories"] != list(RESULT_CATEGORIES):
        _fail(f"{source}.families.results", "results must be implemented with the canonical categories")
    return registry


def validate_required_coverage(source, registry):
    _object(source, registry, ("schema_version", "required"))
    if registry["schema_version"] != SCHEMA_VERSION:
        _fail(f"{source}.schema_version", "unsupported schema version")
    required = registry["required"]
    if not isinstance(required, dict) or not required:
        _fail(f"{source}.required", "must name at least one required coverage tag")
    for tag, description in required.items():
        if not TAG_PATTERN.fullmatch(tag):
            _fail(f"{source}.required", f"bad coverage tag {tag!r}")
        _text(f"{source}.required.{tag}", description)
    return registry
