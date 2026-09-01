from __future__ import annotations

import hashlib
import threading
import time
from copy import deepcopy
from datetime import datetime, timezone


RECOGNITION_VERSION = "self-identity-lobby-v1"

# Normalized against the AC6 client area.  These are intentionally separate
# from the result detector ROI and never include the sortie AC image.
LOBBY_HEADER_ROI = (0.030, 0.035, 0.305, 0.095)
LOBBY_MENU_ROI = (0.045, 0.130, 0.280, 0.550)
LOBBY_MATCH_PANEL_ROI = (0.300, 0.130, 0.615, 0.580)
LOBBY_SORTIE_PANEL_ROI = (0.640, 0.130, 0.950, 0.900)
PLAYER_CARD_ROI = (0.318, 0.273, 0.548, 0.353)
PLAYER_EMBLEM_ROI = (0.318, 0.273, 0.365, 0.353)
PLAYER_NAME_ROI = (0.365, 0.273, 0.548, 0.353)


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
        self.channels = 4 if self.pixel_format in ("bgra", "rgba") else 3
        expected = self.width * self.height * self.channels
        if self.width <= 0 or self.height <= 0 or len(self.raw) < expected:
            raise ValueError("invalid frame buffer")

    def rgb(self, x, y):
        i = (int(y) * self.width + int(x)) * self.channels
        if self.pixel_format == "bgra":
            return self.raw[i + 2], self.raw[i + 1], self.raw[i]
        if self.pixel_format == "rgba":
            return self.raw[i], self.raw[i + 1], self.raw[i + 2]
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
            if previous is not None and abs(gray - previous) >= 22:
                horizontal_edges += 1
            previous = gray
    for col in range(cols):
        x = left + min(right - left - 1, (col * (right - left)) // cols)
        previous = None
        for row in range(rows):
            y = top + min(bottom - top - 1, (row * (bottom - top)) // rows)
            gray = pixels.gray(x, y)
            if previous is not None and abs(gray - previous) >= 22:
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


def lobby_confidence(raw, width, height, pixel_format="bgra"):
    """Score the stable SINGLE lobby layout without OCR or AC-image input."""
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
        y = top + min(bottom - top - 1, ((row * 2 + 1) * (bottom - top)) // (rows * 2))
        line = []
        for col in range(columns):
            x = left + min(right - left - 1, ((col * 2 + 1) * (right - left)) // (columns * 2))
            line.append(pixels.gray(x, y))
        samples.append(line)
    bits = 0
    for line in samples:
        for left_value, right_value in zip(line, line[1:]):
            bits = (bits << 1) | int(left_value > right_value)
    return f"{bits:032x}"


class SelfIdentityTracker:
    """Capture one privacy-minimized self reference from a confirmed lobby.

    This component is observation-only.  It has no dependency on ResultGate or
    StatsManager and therefore cannot change a match result.
    """

    def __init__(
        self,
        diagnostics=None,
        clock=None,
        observation_seconds=30.0,
        probe_interval=2.0,
        max_probes=15,
    ):
        self._lock = threading.Lock()
        self._diagnostics = diagnostics
        self._clock = clock or time.monotonic
        self._observation_seconds = max(1.0, float(observation_seconds))
        self._probe_interval = max(0.1, float(probe_interval))
        self._max_probes = max(1, int(max_probes))
        self._reference = None
        self._last_reference_digest = None
        self.reset_runtime_state()

    def reset_runtime_state(self):
        now = self._clock()
        with getattr(self, "_lock", threading.Lock()):
            self._window_started = now
            self._last_probe = None
            self._probe_count = 0
            self._window_closed = False

    def note_gameplay_activity(self):
        with self._lock:
            self._window_closed = True

    def should_observe(self, now=None):
        now = self._clock() if now is None else float(now)
        with self._lock:
            if self._reference is not None or self._window_closed:
                return False
            if self._probe_count >= self._max_probes:
                return False
            if now - self._window_started > self._observation_seconds:
                return False
            return self._last_probe is None or now - self._last_probe >= self._probe_interval

    def has_reference(self):
        with self._lock:
            return self._reference is not None

    def get_reference(self):
        with self._lock:
            return deepcopy(self._reference)

    def health_snapshot(self):
        with self._lock:
            return {
                "available": self._reference is not None,
                "recognition_version": RECOGNITION_VERSION,
                "observation_active": not self._window_closed,
                "probes": self._probe_count,
            }

    def observe_frame(self, raw, width, height, pixel_format="bgra", captured_at=None):
        now = self._clock()
        with self._lock:
            self._last_probe = now
            self._probe_count += 1
        confidence, _details = lobby_confidence(raw, width, height, pixel_format)
        # Lobby confirmation is structural: five of six independent anchors
        # must agree.  This is not a future self-match similarity threshold.
        if confidence < (5.0 / 6.0):
            return None

        card_fingerprint = perceptual_fingerprint(
            raw, width, height, PLAYER_EMBLEM_ROI, pixel_format
        )
        image_fingerprint = perceptual_fingerprint(
            raw, width, height, PLAYER_CARD_ROI, pixel_format
        )
        captured_at = captured_at or datetime.now(timezone.utc).isoformat()
        reference = {
            "captured_at": str(captured_at),
            "screen": {"width": int(width), "height": int(height)},
            "lobby_confidence": round(float(confidence), 4),
            "player_card_roi": list(PLAYER_CARD_ROI),
            "player_name_roi": list(PLAYER_NAME_ROI),
            "card_fingerprint": card_fingerprint,
            "image_fingerprint": image_fingerprint,
            "display_name": None,
            "display_name_confidence": None,
            "recognition_version": RECOGNITION_VERSION,
        }
        digest = hashlib.sha256(
            (card_fingerprint + ":" + image_fingerprint).encode("ascii")
        ).hexdigest()
        with self._lock:
            if digest == self._last_reference_digest:
                return deepcopy(self._reference)
            self._reference = reference
            self._last_reference_digest = digest
            self._window_closed = True
        if self._diagnostics is not None:
            self._diagnostics.record(
                "self_identity_reference",
                lobby_confidence=reference["lobby_confidence"],
                recognition_version=RECOGNITION_VERSION,
                # Only non-reversible hashes are recorded; no player name/image.
                card_fingerprint=card_fingerprint,
            )
        return deepcopy(reference)
