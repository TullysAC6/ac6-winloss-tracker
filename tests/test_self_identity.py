import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import self_identity
from result_detector import ResultDetector
from self_identity import (
    BATTLE,
    LOBBY_CANDIDATE,
    REFERENCE_READY,
    RESULT,
    SEARCHING_LOBBY,
    PLAYER_CARD_ROI,
    SelfIdentityTracker,
    cheap_lobby_candidate,
    lobby_confidence,
    scale_normalized_roi,
)
from stats_manager import StatsManager


FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load_ppm(name):
    data = (FIXTURES / name).read_bytes()
    parts = data.split(None, 4)
    assert parts[0] == b"P6" and int(parts[3]) == 255
    width, height = int(parts[1]), int(parts[2])
    raw = parts[4]
    assert len(raw) == width * height * 3
    return raw, width, height


def detector_crop(frame):
    raw, width, height = frame
    left, top = int(width * 0.20), int(height * 0.43)
    right, bottom = int(width * 0.80), int(height * 0.50)
    cropped = bytearray()
    for y in range(top, bottom):
        cropped.extend(raw[(y * width + left) * 3:(y * width + right) * 3])
    return bytes(cropped), right - left, bottom - top


def rgb_to_bgra(frame):
    raw, width, height = frame
    converted = bytearray()
    for i in range(0, len(raw), 3):
        converted.extend((raw[i + 2], raw[i + 1], raw[i], 255))
    return bytes(converted), width, height


def mutate_rect(frame, roi, pattern):
    raw, width, height = frame
    changed = bytearray(raw)
    left, top, right, bottom = scale_normalized_roi(roi, width, height)
    for y in range(top, bottom):
        for x in range(left, right):
            i = (y * width + x) * 3
            changed[i:i + 3] = pattern(x - left, y - top)
    return bytes(changed), width, height


def erase_rois(frame, rois, color):
    for roi in rois:
        frame = mutate_rect(frame, roi, lambda x, y, value=color: value)
    return frame


class RecorderSpy:
    def __init__(self):
        self.records = []

    def record(self, kind, **details):
        self.records.append((kind, details))


class FakeClock:
    def __init__(self):
        self.monotonic = 100.0
        self.wall = 2_000_000_000.0

    def advance(self, seconds=0.75):
        self.monotonic += seconds
        self.wall += seconds


def new_tracker(recorder=None):
    clock = FakeClock()
    tracker = SelfIdentityTracker(
        diagnostics=recorder,
        monotonic_clock=lambda: clock.monotonic,
        wall_clock=lambda: clock.wall,
    )
    return tracker, clock


def drive_candidate(tracker, clock, candidate_frame, hits=2):
    requested = False
    for _ in range(hits):
        requested = tracker.observe_detector_frame(
            *candidate_frame,
            frame_state="NON_CLEAR",
            gameplay_activity=False,
            pixel_format="rgb",
        )
        clock.advance()
    return requested


lobby = load_ppm("self_identity_lobby_anonymized.ppm")
lobby_detector = detector_crop(lobby)
result_summary = load_ppm("self_identity_result_anonymized.ppm")
result_detail = load_ppm("self_identity_detail_anonymized.ppm")
black = (bytes(len(lobby_detector[0])), lobby_detector[1], lobby_detector[2])

# Stage 1 is a multi-region, cheap classifier over the existing detector ROI.
assert cheap_lobby_candidate(*lobby_detector, pixel_format="rgb")[0]
stage1_negatives = {
    "SYSTEM/loading": black,
    "GARAGE": load_ppm("video_false_garage_20_1.ppm"),
    "NEST": load_ppm("video_false_garage_menu_21_4.ppm"),
    "RANK MATCH mode selection": load_ppm("video_rank_menu_23_267.ppm"),
    "Gameplay": load_ppm("live_gameplay_6349.ppm"),
}
for label, frame in stage1_negatives.items():
    assert not cheap_lobby_candidate(*frame, pixel_format="rgb")[0], label

# Stage 2 keeps actual anonymized lobby geometry positive and real result
# summary/detail fixtures negative.
assert lobby_confidence(*lobby, pixel_format="rgb")[0] >= 5.0 / 6.0
assert lobby_confidence(*result_summary, pixel_format="rgb")[0] < 5.0 / 6.0
assert lobby_confidence(*result_detail, pixel_format="rgb")[0] < 5.0 / 6.0

# Additional UI negatives remove independent lobby anchors. They model SYSTEM,
# NEST, mode selection, ASSEMBLY, AC DATA and LEADERBOARD layouts; none can
# generate a reference even if Stage 1 is deliberately forced to confirmation.
other_ui_negatives = {
    "SYSTEM": erase_rois(
        lobby, ((0.0, 0.0, 0.32, 0.12), (0.0, 0.1, 0.30, 0.62)), b"\x08\x0a\x0c"
    ),
    "GARAGE": erase_rois(
        lobby, ((0.0, 0.1, 0.30, 0.62), (0.30, 0.1, 0.62, 0.62)), b"\x0a\x0c\x0e"
    ),
    "NEST": erase_rois(
        lobby, ((0.30, 0.1, 0.62, 0.62), PLAYER_CARD_ROI), b"\x08\x08\x0a"
    ),
    "RANK MATCH mode selection": erase_rois(
        lobby, ((0.0, 0.0, 0.32, 0.12), PLAYER_CARD_ROI), b"\x90\x90\x90"
    ),
    "ASSEMBLY": erase_rois(
        lobby, ((0.0, 0.1, 0.30, 0.62), PLAYER_CARD_ROI), b"\x10\x12\x14"
    ),
    "AC DATA": erase_rois(
        lobby, ((0.0, 0.0, 0.32, 0.12), (0.30, 0.12, 0.62, 0.60)), b"\x15\x18\x1b"
    ),
    "LEADERBOARD": erase_rois(
        lobby, ((0.30, 0.12, 0.62, 0.60), PLAYER_CARD_ROI), b"\xb0\xb0\xb0"
    ),
    "LEADERBOARD detail": erase_rois(
        lobby, ((0.0, 0.0, 0.32, 0.12), (0.0, 0.1, 0.30, 0.62)), b"\x06\x08\x0a"
    ),
    "RESULT summary": result_summary,
    "RESULT player selection": result_summary,
    "RESULT opponent/self detail": result_detail,
    "black transition": (bytes(lobby[1] * lobby[2] * 3), lobby[1], lobby[2]),
}
for label, frame in other_ui_negatives.items():
    tracker, clock = new_tracker()
    assert drive_candidate(tracker, clock, lobby_detector)
    assert tracker.observe_frame(*frame, pixel_format="rgb") is None, label
    assert not tracker.has_reference(), label

# A single candidate is not enough. A second within the normal detector cadence
# requests one Stage 2 frame and then creates the reference.
recorder = RecorderSpy()
tracker, clock = new_tracker(recorder)
assert not drive_candidate(tracker, clock, lobby_detector, hits=1)
assert tracker.health_snapshot()["state"] == LOBBY_CANDIDATE
assert not tracker.has_reference()
assert drive_candidate(tracker, clock, lobby_detector, hits=1)
reference = tracker.observe_frame(
    *lobby, pixel_format="rgb", captured_at="2026-09-01T00:00:00Z"
)
assert tracker.has_reference()
assert tracker.health_snapshot()["state"] == REFERENCE_READY
assert reference["reference_id"] == tracker.health_snapshot()["reference_id"]
assert reference["screen_width"] == 480 and reference["screen_height"] == 270
assert len(reference["card_fingerprint"]) == 32
assert len(reference["emblem_fingerprint"]) == 32
assert len(reference["name_region_fingerprint"]) == 32
assert len(recorder.records) == 1

# No time/probe lifetime exists: after several minutes of unrelated screens the
# tracker remains able to capture the first confirmed lobby.
late_recorder = RecorderSpy()
late_tracker, late_clock = new_tracker(late_recorder)
for _ in range(500):
    assert not late_tracker.observe_detector_frame(
        *black,
        frame_state="NON_CLEAR",
        gameplay_activity=False,
        pixel_format="rgb",
    )
    late_clock.advance()
assert late_clock.monotonic > 400.0
assert late_tracker.health_snapshot()["state"] == SEARCHING_LOBBY
assert late_tracker.health_snapshot()["observation_active"]
assert late_recorder.records == []
assert drive_candidate(late_tracker, late_clock, lobby_detector)
assert late_tracker.observe_frame(*lobby, pixel_format="rgb") is not None

# Same lobby after leaving/re-entering is unchanged and causes no diagnostics
# write. A player-card change refreshes; an AC-image-only change does not.
assert not tracker.observe_detector_frame(
    *black, "NON_CLEAR", False, pixel_format="rgb"
)
clock.advance()
assert drive_candidate(tracker, clock, lobby_detector)
unchanged = tracker.observe_frame(*lobby, pixel_format="rgb")
assert unchanged["reference_id"] == reference["reference_id"]
assert len(recorder.records) == 1

card_changed = mutate_rect(
    lobby,
    PLAYER_CARD_ROI,
    lambda x, y: bytes((20 + (x % 5) * 35, 25 + (y % 3) * 45, 40)),
)
tracker.observe_detector_frame(*black, "NON_CLEAR", False, pixel_format="rgb")
clock.advance()
assert drive_candidate(tracker, clock, lobby_detector)
refreshed = tracker.observe_frame(*card_changed, pixel_format="rgb")
assert refreshed["reference_id"] != reference["reference_id"]
assert len(recorder.records) == 2

machine_changed = mutate_rect(
    card_changed,
    (0.70, 0.25, 0.90, 0.70),
    lambda x, y: bytes((45 + x % 20, 60 + y % 20, 75)),
)
tracker.observe_detector_frame(*black, "NON_CLEAR", False, pixel_format="rgb")
clock.advance()
assert drive_candidate(tracker, clock, lobby_detector)
machine_reference = tracker.observe_frame(*machine_changed, pixel_format="rgb")
assert machine_reference["reference_id"] == refreshed["reference_id"]
assert len(recorder.records) == 2

# Result only changes state. It cannot request capture or generate identity;
# the following confirmed lobby remains discoverable.
result_tracker, result_clock = new_tracker()
assert not result_tracker.observe_detector_frame(
    *lobby_detector,
    frame_state="FINAL_WIN",
    gameplay_activity=False,
    pixel_format="rgb",
)
assert result_tracker.health_snapshot()["state"] == RESULT
assert not result_tracker.has_reference()
result_clock.advance()
assert drive_candidate(result_tracker, result_clock, lobby_detector)
assert result_tracker.observe_frame(*lobby, pixel_format="rgb") is not None

# Gameplay performs cheap state handling only: no identity hash and no capture.
hash_calls = [0]
original_hash = self_identity.perceptual_fingerprint


def counted_hash(*args, **kwargs):
    hash_calls[0] += 1
    return original_hash(*args, **kwargs)


self_identity.perceptual_fingerprint = counted_hash
try:
    gameplay_tracker, gameplay_clock = new_tracker()
    for _ in range(100):
        assert not gameplay_tracker.observe_detector_frame(
            *lobby_detector,
            frame_state="CLEAR",
            gameplay_activity=True,
            pixel_format="rgb",
        )
        gameplay_clock.advance()
    assert gameplay_tracker.health_snapshot()["state"] == BATTLE
    assert hash_calls[0] == 0
finally:
    self_identity.perceptual_fingerprint = original_hash


class Shot:
    def __init__(self, frame):
        self.raw, self.width, self.height = rgb_to_bgra(frame)


class CaptureSpy:
    def __init__(self, full_frame):
        self.full_frame = full_frame
        self.calls = 0

    def grab(self, _region):
        self.calls += 1
        return Shot(self.full_frame)


# Verify the actual ResultDetector integration: 100 gameplay frames trigger
# zero additional grabs; two lobby candidates trigger exactly one.
integration_tracker, integration_clock = new_tracker()
detector = object.__new__(ResultDetector)
detector.identity_tracker = integration_tracker
sct = CaptureSpy(lobby)
candidate_shot = Shot(lobby_detector)
for _ in range(100):
    assert not detector._observe_self_identity(
        sct, {}, candidate_shot, "CLEAR", True
    )
    integration_clock.advance()
assert sct.calls == 0
assert not detector._observe_self_identity(
    sct, {}, candidate_shot, "NON_CLEAR", False
)
integration_clock.advance()
assert detector._observe_self_identity(
    sct, {}, candidate_shot, "NON_CLEAR", False
)
assert sct.calls == 1

# Optional identity failure cannot mutate WIN/LOSE and is contained by the
# ResultDetector helper.
with tempfile.TemporaryDirectory() as temp_dir:
    stats = StatsManager(Path(temp_dir))
    before = stats.snapshot()

    class BrokenIdentity:
        def __init__(self):
            self.error = None

        def observe_detector_frame(self, *args, **kwargs):
            raise OSError("test identity failure")

        def note_optional_error(self, error):
            self.error = error

    broken = BrokenIdentity()
    detector.identity_tracker = broken
    assert not detector._observe_self_identity(
        sct, {}, candidate_shot, "NON_CLEAR", False
    )
    assert isinstance(broken.error, OSError)
    assert stats.snapshot() == before

# ROI scaling, health diagnostics and lifecycle/thread safety.
assert scale_normalized_roi(PLAYER_CARD_ROI, 1920, 1080) == (611, 295, 1052, 381)
health = tracker.health_snapshot()
for key in (
    "state", "last_lobby_candidate_at", "last_lobby_confirmed_at",
    "reference_captured_at", "reference_id", "last_error", "last_probe_reason",
):
    assert key in health
threads_before = {thread.ident for thread in threading.enumerate()}
duplicate_a = SelfIdentityTracker()
duplicate_b = SelfIdentityTracker()
duplicate_a.reset_runtime_state()
duplicate_b.reset_runtime_state()
assert {thread.ident for thread in threading.enumerate()} == threads_before

print("self identity lobby-driven lifetime/two-stage tests: OK")
