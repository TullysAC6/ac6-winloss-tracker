import tempfile
import threading
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from self_identity import (
    PLAYER_CARD_ROI,
    SelfIdentityTracker,
    lobby_confidence,
    perceptual_fingerprint,
    scale_normalized_roi,
)
from stats_manager import StatsManager


HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"


def load_ppm(name):
    data = (FIXTURES / name).read_bytes()
    parts = data.split(None, 4)
    assert parts[0] == b"P6" and int(parts[3]) == 255
    width, height = int(parts[1]), int(parts[2])
    raw = parts[4]
    assert len(raw) == width * height * 3
    return raw, width, height


class RecorderSpy:
    def __init__(self):
        self.records = []

    def record(self, kind, **details):
        self.records.append((kind, details))


lobby = load_ppm("self_identity_lobby_anonymized.ppm")
result = load_ppm("self_identity_result_anonymized.ppm")
detail = load_ppm("self_identity_detail_anonymized.ppm")

# Real-layout positive and result/detail negatives from privacy-sanitized user
# screenshots.  No OCR, player name, or AC image is used as self identity.
assert lobby_confidence(*lobby, pixel_format="rgb")[0] >= 5.0 / 6.0
assert lobby_confidence(*result, pixel_format="rgb")[0] < 5.0 / 6.0
assert lobby_confidence(*detail, pixel_format="rgb")[0] < 5.0 / 6.0

# An existing gameplay/result-detector ROI is also a negative even though its
# aspect ratio differs from a normal full client frame.
gameplay = load_ppm("live_gameplay_6349.ppm")
assert lobby_confidence(*gameplay, pixel_format="rgb")[0] < 5.0 / 6.0

# 1920x1080 reference coordinates and resolution-independent scaling.
assert scale_normalized_roi(PLAYER_CARD_ROI, 1920, 1080) == (611, 295, 1052, 381)
assert scale_normalized_roi(PLAYER_CARD_ROI, 1280, 720) == (407, 197, 701, 254)

# Fingerprints are deterministic and limited to the configured card ROI.
fingerprint_a = perceptual_fingerprint(*lobby, PLAYER_CARD_ROI, pixel_format="rgb")
fingerprint_b = perceptual_fingerprint(*lobby, PLAYER_CARD_ROI, pixel_format="rgb")
assert fingerprint_a == fingerprint_b
assert len(fingerprint_a) == 32

recorder = RecorderSpy()
tracker = SelfIdentityTracker(diagnostics=recorder, probe_interval=0.1)
reference = tracker.observe_frame(*lobby, pixel_format="rgb", captured_at="2026-09-01T00:00:00Z")
assert tracker.has_reference()
assert tracker.get_reference() == reference
assert reference["screen"] == {"width": 480, "height": 270}
assert reference["display_name"] is None
assert reference["recognition_version"] == "self-identity-lobby-v1"
assert len(recorder.records) == 1

# Observation is rate-limited/bounded and gameplay closes the window, so a
# battle never introduces continuous full-client capture.
clock_value = [100.0]
bounded = SelfIdentityTracker(
    clock=lambda: clock_value[0],
    observation_seconds=5.0,
    probe_interval=2.0,
    max_probes=2,
)
assert bounded.should_observe()
assert bounded.observe_frame(*result, pixel_format="rgb") is None
assert not bounded.should_observe()
clock_value[0] += 2.0
assert bounded.should_observe()
bounded.note_gameplay_activity()
assert not bounded.should_observe()

# A small unrelated UI change does not alter the card fingerprint/reference.
raw, width, height = lobby
changed = bytearray(raw)
for y in range(5, 18):
    for x in range(width - 45, width - 5):
        i = (y * width + x) * 3
        changed[i:i + 3] = b"\x20\x30\x40"
tracker.reset_runtime_state()
same = tracker.observe_frame(changed, width, height, pixel_format="rgb")
assert same["card_fingerprint"] == reference["card_fingerprint"]
# Identical identity never causes another diagnostics/disk-facing write.
assert len(recorder.records) == 1

# Identity failure is observation-only and cannot mutate WIN/LOSE state.
with tempfile.TemporaryDirectory() as temp_dir:
    stats = StatsManager(Path(temp_dir))
    before = stats.snapshot()
    negative_tracker = SelfIdentityTracker()
    assert negative_tracker.observe_frame(*result, pixel_format="rgb") is None
    assert stats.snapshot() == before

# The component creates no thread/process, including duplicate construction and
# shutdown-style runtime resets.
threads_before = {thread.ident for thread in threading.enumerate()}
duplicate_a = SelfIdentityTracker()
duplicate_b = SelfIdentityTracker()
duplicate_a.reset_runtime_state()
duplicate_b.reset_runtime_state()
threads_after = {thread.ident for thread in threading.enumerate()}
assert threads_after == threads_before

print("self identity lobby reference tests: OK")
