from __future__ import annotations

import hashlib
import threading
import time
from copy import deepcopy
from datetime import datetime, timezone


RECOGNITION_VERSION = "self-identity-lobby-v2"

SEARCHING_LOBBY = "SEARCHING_LOBBY"
LOBBY_CANDIDATE = "LOBBY_CANDIDATE"
LOBBY_CONFIRMED = "LOBBY_CONFIRMED"
REFERENCE_READY = "REFERENCE_READY"
BATTLE = "BATTLE"
RESULT = "RESULT"
ERROR_OPTIONAL = "ERROR_OPTIONAL"

# Full-client ROIs, normalized against the AC6 client area. The sortie panel
# is only a lobby-layout anchor. Its machine image is never fingerprinted or
# used as self identity.
LOBBY_HEADER_ROI = (0.030, 0.035, 0.305, 0.095)
LOBBY_MENU_ROI = (0.045, 0.130, 0.280, 0.550)
LOBBY_MATCH_PANEL_ROI = (0.300, 0.130, 0.615, 0.580)
LOBBY_SORTIE_PANEL_ROI = (0.640, 0.130, 0.950, 0.900)
PLAYER_CARD_ROI = (0.318, 0.273, 0.548, 0.353)
PLAYER_EMBLEM_ROI = (0.318, 0.273, 0.365, 0.353)
PLAYER_NAME_ROI = (0.365, 0.273, 0.548, 0.353)

# Stage 1 subregions are relative to the existing ResultDetector frame
# (client x=20..80%, y=43..50%).
CANDIDATE_LEFT_ROI = (0.00, 0.00, 0.20, 1.00)
CANDIDATE_LEFT_MIDDLE_ROI = (0.20, 0.00, 0.45, 1.00)
CANDIDATE_CENTER_ROI = (0.45, 0.00, 0.70, 1.00)
CANDIDATE_RIGHT_ROI = (0.70, 0.00, 1.00, 1.00)


def scale_normalized_roi(roi, width, height):
    """Return a clamped integer (left, top, right, bottom) rectangle."""
    width = max(1, int(width))
    height = max(1, int(height))
    left = max(0, min(width - 1, int(round(float(roi[0]) * width))))
    top = max(0, min(height - 1, int(round(float(roi[1]) * height))))
    right = max(left + 1, min(width, int(round(float(roi[2]) * width))))
    bottom = max(top + 1, min(height, int(round(float(roi[3]) * height))))
    return left, top, right, bottom


class _Pixels:
    def __init__(self, raw, width, height, pixel_format):
        self.raw = memoryview(raw)
        self.width = int(width)
        self.height = int(height)
        self.pixel_format = str(pixel_format).lower()
        if self.pixel_format not in ("bgra", "rgba", "rgb"):
            raise ValueError("unsupported pixel format")
        self.channels = 4 if self.pixel_format in ("bgra", "rgba") else 3
        expected = self.width * self.height * self.channels
        if self.width <= 0 or self.height <= 0 or len(self.raw) < expected:
            raise ValueError("invalid frame buffer")

    def rgb(self, x, y):
        i = (int(y) * self.width + int(x)) * self.channels
        if self.pixel_format == "bgra":
            return self.raw[i + 2], self.raw[i + 1], self.raw[i]
        return self.raw[i], self.raw[i + 1], self.raw[i + 2]

    def gray(self, x, y):
        r, g, b = self.rgb(x, y)
        return (int(r) * 77 + int(g) * 150 + int(b) * 29) >> 8


def _region_features(pixels, roi, sample_columns=32, sample_rows=16):
    left, top, right, bottom = scale_normalized_roi(
        roi, pixels.width, pixels.height
    )
    cols = max(3, min(sample_columns, right - left))
    rows = max(3, min(sample_rows, bottom - top))
    values = []
    horizontal_edges = 0
    vertical_edges = 0
    chroma = 0
    for row in range(rows):
        y = top + min(bottom - top - 1, (row * (bottom - top)) // rows)
        previous = None
        for col in range(cols):
            x = left + min(right - left - 1, (col * (right - left)) // cols)
            r, g, b = pixels.rgb(x, y)
            gray = (int(r) * 77 + int(g) * 150 + int(b) * 29) >> 8
            values.append(gray)
            chroma += max(r, g, b) - min(r, g, b)
            if previous is not None and abs(gray - previous) >= 18:
                horizontal_edges += 1
            previous = gray
    for col in range(cols):
        x = left + min(right - left - 1, (col * (right - left)) // cols)
        previous = None
        for row in range(rows):
            y = top + min(bottom - top - 1, (row * (bottom - top)) // rows)
            gray = pixels.gray(x, y)
            if previous is not None and abs(gray - previous) >= 18:
                vertical_edges += 1
            previous = gray
    count = max(1, len(values))
    edge_denominator = max(1, rows * (cols - 1) + cols * (rows - 1))
    return {
        "mean": sum(values) / count,
        "bright_ratio": sum(1 for value in values if value >= 135) / count,
        "dark_ratio": sum(1 for value in values if value <= 35) / count,
        "edge_ratio": (horizontal_edges + vertical_edges) / edge_denominator,
        "chroma": chroma / count,
    }


def cheap_lobby_candidate(raw, width, height, pixel_format="bgra"):
    """Stage 1: classify only the already-captured narrow detector frame."""
    pixels = _Pixels(raw, width, height, pixel_format)
    left = _region_features(pixels, CANDIDATE_LEFT_ROI, 12, 8)
    left_middle = _region_features(
        pixels, CANDIDATE_LEFT_MIDDLE_ROI, 16, 8
    )
    center = _region_features(pixels, CANDIDATE_CENTER_ROI, 16, 8)
    right = _region_features(pixels, CANDIDATE_RIGHT_ROI, 18, 8)
    checks = (
        20.0 <= left["mean"] <= 80.0,
        center["mean"] <= 38.0 and center["dark_ratio"] >= 0.82,
        left_middle["edge_ratio"] >= 0.035,
        right["mean"] >= center["mean"] + 12.0,
        right["dark_ratio"] <= 0.48,
    )
    return all(checks), {
        "left": left,
        "left_middle": left_middle,
        "center": center,
        "right": right,
        "checks": checks,
    }


def lobby_confidence(raw, width, height, pixel_format="bgra"):
    """Stage 2: confirm the full stable SINGLE lobby layout without OCR."""
    pixels = _Pixels(raw, width, height, pixel_format)
    header = _region_features(pixels, LOBBY_HEADER_ROI, 36, 10)
    menu = _region_features(pixels, LOBBY_MENU_ROI, 24, 28)
    match = _region_features(pixels, LOBBY_MATCH_PANEL_ROI, 28, 24)
    sortie = _region_features(pixels, LOBBY_SORTIE_PANEL_ROI, 28, 28)
    card = _region_features(pixels, PLAYER_CARD_ROI, 36, 14)
    checks = (
        header["bright_ratio"] >= 0.08 and header["edge_ratio"] >= 0.06,
        35.0 <= menu["mean"] <= 145.0 and menu["edge_ratio"] >= 0.045,
        20.0 <= match["mean"] <= 125.0 and match["edge_ratio"] >= 0.025,
        25.0 <= sortie["mean"] <= 155.0 and sortie["edge_ratio"] >= 0.025,
        card["dark_ratio"] >= 0.20 and card["edge_ratio"] >= 0.08,
        abs(match["mean"] - sortie["mean"]) <= 65.0,
    )
    confidence = sum(1.0 for passed in checks if passed) / len(checks)
    return confidence, {
        "header": header,
        "menu": menu,
        "match_panel": match,
        "sortie_panel": sortie,
        "player_card": card,
        "checks": checks,
    }


def perceptual_fingerprint(raw, width, height, roi, pixel_format="bgra"):
    """Return a deterministic 128-bit difference hash for one normalized ROI."""
    pixels = _Pixels(raw, width, height, pixel_format)
    left, top, right, bottom = scale_normalized_roi(roi, width, height)
    columns = 17
    rows = 8
    samples = []
    for row in range(rows):
        y = top + min(
            bottom - top - 1,
            ((row * 2 + 1) * (bottom - top)) // (rows * 2),
        )
        line = []
        for col in range(columns):
            x = left + min(
                right - left - 1,
                ((col * 2 + 1) * (right - left)) // (columns * 2),
            )
            line.append(pixels.gray(x, y))
        samples.append(line)
    bits = 0
    for line in samples:
        for left_value, right_value in zip(line, line[1:]):
            bits = (bits << 1) | int(left_value > right_value)
    return f"{bits:032x}"


class SelfIdentityTracker:
    """Lobby-driven, observation-only self identity state machine."""

    def __init__(
        self,
        diagnostics=None,
        monotonic_clock=None,
        wall_clock=None,
        confirmation_hits=2,
        confirmation_gap_seconds=2.5,
    ):
        self._lock = threading.Lock()
        self._diagnostics = diagnostics
        self._monotonic = monotonic_clock or time.monotonic
        self._wall_clock = wall_clock or time.time
        self._confirmation_hits = max(2, int(confirmation_hits))
        self._confirmation_gap = max(0.75, float(confirmation_gap_seconds))
        self._reference = None
        self._last_reference_digest = None
        self._reference_writes = 0
        self.reset_runtime_state()

    def reset_runtime_state(self):
        with self._lock:
            self._state = SEARCHING_LOBBY
            self._candidate_hits = 0
            self._last_candidate_monotonic = None
            self._lobby_latched = False
            self._probes = 0
            self._last_lobby_candidate_at = None
            self._last_lobby_confirmed_at = None
            self._last_probe_reason = "startup"
            self._last_error = None

    def has_reference(self):
        with self._lock:
            return self._reference is not None

    def get_reference(self):
        with self._lock:
            return deepcopy(self._reference)

    def health_snapshot(self):
        with self._lock:
            reference = self._reference
            return {
                "available": reference is not None,
                "recognition_version": RECOGNITION_VERSION,
                "observation_active": self._state in (
                    SEARCHING_LOBBY, LOBBY_CANDIDATE
                ),
                "probes": self._probes,
                "state": self._state,
                "last_lobby_candidate_at": self._last_lobby_candidate_at,
                "last_lobby_confirmed_at": self._last_lobby_confirmed_at,
                "reference_captured_at": (
                    reference.get("captured_at") if reference else None
                ),
                "reference_id": (
                    reference.get("reference_id") if reference else None
                ),
                "last_error": self._last_error,
                "last_probe_reason": self._last_probe_reason,
            }

    def note_optional_error(self, error):
        with self._lock:
            self._state = ERROR_OPTIONAL
            self._candidate_hits = 0
            self._last_error = f"{type(error).__name__}: {error}"
            self._last_probe_reason = "optional_error"

    def observe_detector_frame(
        self,
        raw,
        width,
        height,
        frame_state,
        gameplay_activity,
        pixel_format="bgra",
    ):
        """Return True only when Stage 2 full-client confirmation is needed."""
        monotonic_now = self._monotonic()
        wall_now = self._wall_clock()
        with self._lock:
            self._probes += 1
            if gameplay_activity:
                self._state = BATTLE
                self._candidate_hits = 0
                self._lobby_latched = False
                self._last_probe_reason = "gameplay"
                return False
            if str(frame_state) in (
                "PHASE", "FINAL_WIN", "FINAL_LOSS", "FINAL_DRAW"
            ):
                self._state = RESULT
                self._candidate_hits = 0
                self._lobby_latched = False
                self._last_probe_reason = "result"
                return False

        candidate, _details = cheap_lobby_candidate(
            raw, width, height, pixel_format
        )
        with self._lock:
            if not candidate:
                self._candidate_hits = 0
                self._last_candidate_monotonic = None
                self._lobby_latched = False
                self._state = SEARCHING_LOBBY
                self._last_probe_reason = "not_lobby_candidate"
                return False
            self._last_lobby_candidate_at = wall_now
            if self._lobby_latched:
                self._state = REFERENCE_READY
                self._last_probe_reason = "lobby_already_observed"
                return False
            if (
                self._last_candidate_monotonic is None
                or monotonic_now - self._last_candidate_monotonic
                > self._confirmation_gap
            ):
                self._candidate_hits = 1
            else:
                self._candidate_hits += 1
            self._last_candidate_monotonic = monotonic_now
            if self._candidate_hits < self._confirmation_hits:
                self._state = LOBBY_CANDIDATE
                self._last_probe_reason = "candidate_waiting_confirmation"
                return False
            self._state = LOBBY_CONFIRMED
            self._last_lobby_confirmed_at = wall_now
            self._last_probe_reason = "candidate_confirmed"
            return True

    def observe_frame(
        self, raw, width, height, pixel_format="bgra", captured_at=None
    ):
        """Stage 2 confirmation and reference capture for a requested frame."""
        with self._lock:
            if self._state != LOBBY_CONFIRMED:
                self._last_probe_reason = "full_frame_not_requested"
                return None
        confidence, _details = lobby_confidence(
            raw, width, height, pixel_format
        )
        if confidence < (5.0 / 6.0):
            with self._lock:
                self._state = SEARCHING_LOBBY
                self._candidate_hits = 0
                self._last_probe_reason = "full_frame_not_lobby"
            return None

        card_fingerprint = perceptual_fingerprint(
            raw, width, height, PLAYER_CARD_ROI, pixel_format
        )
        emblem_fingerprint = perceptual_fingerprint(
            raw, width, height, PLAYER_EMBLEM_ROI, pixel_format
        )
        name_region_fingerprint = perceptual_fingerprint(
            raw, width, height, PLAYER_NAME_ROI, pixel_format
        )
        digest = hashlib.sha256(
            (
                card_fingerprint
                + ":"
                + emblem_fingerprint
                + ":"
                + name_region_fingerprint
            ).encode("ascii")
        ).hexdigest()
        captured_at = captured_at or datetime.now(timezone.utc).isoformat()
        reference = {
            "reference_id": digest[:20],
            "captured_at": str(captured_at),
            "screen_width": int(width),
            "screen_height": int(height),
            "screen": {"width": int(width), "height": int(height)},
            "lobby_confidence": round(float(confidence), 4),
            "player_card_roi": list(PLAYER_CARD_ROI),
            "player_name_roi": list(PLAYER_NAME_ROI),
            "card_fingerprint": card_fingerprint,
            "emblem_fingerprint": emblem_fingerprint,
            "name_region_fingerprint": name_region_fingerprint,
            "display_name": None,
            "display_name_confidence": None,
            "recognition_version": RECOGNITION_VERSION,
        }
        with self._lock:
            unchanged = digest == self._last_reference_digest
            if not unchanged:
                self._reference = reference
                self._last_reference_digest = digest
                self._reference_writes += 1
            current = deepcopy(self._reference)
            self._state = REFERENCE_READY
            self._candidate_hits = 0
            self._lobby_latched = True
            self._last_error = None
            self._last_probe_reason = (
                "reference_unchanged" if unchanged else "reference_updated"
            )
        if not unchanged and self._diagnostics is not None:
            self._diagnostics.record(
                "self_identity_reference",
                reference_id=current["reference_id"],
                lobby_confidence=current["lobby_confidence"],
                recognition_version=RECOGNITION_VERSION,
                # Only a non-reversible primary hash is persisted.
                card_fingerprint=card_fingerprint,
            )
        return current
