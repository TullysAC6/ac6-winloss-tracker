import ctypes
import json
import math
import os
import threading
import time
from functools import lru_cache
from ctypes import wintypes
from pathlib import Path

try:
    import mss
    from mss.tools import to_png
except ImportError:
    mss = None
    to_png = None


CLEAR = "CLEAR"
PHASE = "PHASE"
FINAL_WIN = "FINAL_WIN"
FINAL_LOSS = "FINAL_LOSS"
FINAL_DRAW = "FINAL_DRAW"
NON_CLEAR = "NON_CLEAR"

# Public-release precision settings are intentionally not user-configurable.
TARGET_PROCESS = "armoredcore6.exe"
POLL_SECONDS = 0.75
CONFIRM_HITS = 2
CLEAR_HITS_REQUIRED = 3
COOLDOWN_SECONDS = 5.0


class TemplateError(RuntimeError):
    pass


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


def _cosine(a, b):
    if len(a) != len(b):
        raise TemplateError("template/profile length mismatch")
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if not na or not nb:
        return 0.0
    return dot / (na * nb)


def _bin_edges(length, bins):
    """Cached integer bin boundaries used by the profile helpers.

    The boundaries depend only on ROI size and bin count. Reusing them avoids
    recomputing the same int(i * length / bins) expressions every 0.75 s while
    preserving the exact v22 binning semantics.
    """
    if length <= 0 or bins <= 0:
        return ()
    return tuple(
        (
            int(i * length / bins),
            max(int(i * length / bins) + 1, int((i + 1) * length / bins)),
        )
        for i in range(bins)
    )


_bin_edges = lru_cache(maxsize=32)(_bin_edges)


def _x_profile(mask, width, height, bins=64):
    """Horizontal occupancy profile with the same numeric result as v22.

    mask is a 0/1 bytearray. bytearray.count() runs in C, so count each row
    slice there instead of executing an inner Python loop per pixel.
    """
    out = []
    for x0, x1 in _bin_edges(width, bins):
        count = 0
        span = x1 - x0
        for y in range(height):
            row = y * width
            count += mask[row + x0:row + x1].count(1)
        out.append(count / max(1, span * height))
    return out


def _y_profile(mask, width, height, bins=16):
    """Vertical occupancy profile with C-level counting of contiguous rows."""
    out = []
    for y0, y1 in _bin_edges(height, bins):
        # A vertical bin is contiguous in the flattened mask, so one C-level
        # count replaces the previous width*rows Python iterations.
        count = mask[y0 * width:y1 * width].count(1)
        out.append(count / max(1, (y1 - y0) * width))
    return out


def _profiles(mask, width, height, bx=64, by=16):
    """Combined X/Y profile, kept for template compatibility."""
    return _x_profile(mask, width, height, bx) + _y_profile(
        mask, width, height, by
    )


def _grid_profile(mask, width, height, gx=32, gy=8):
    """2-D occupancy fingerprint, numerically identical to v22.

    The detector still computes every cell and uses the same boundaries; only
    the inner per-pixel Python loop is replaced by bytearray.count() in C.
    """
    out = []
    x_edges = _bin_edges(width, gx)
    y_edges = _bin_edges(height, gy)
    for y0, y1 in y_edges:
        for x0, x1 in x_edges:
            count = 0
            span = x1 - x0
            for y in range(y0, y1):
                row = y * width
                count += mask[row + x0:row + x1].count(1)
            out.append(count / max(1, (y1 - y0) * span))
    return out

def _longest_run(values, threshold):
    best = run = 0
    start = best_start = -1
    for i, v in enumerate(values):
        if v >= threshold:
            if run == 0:
                start = i
            run += 1
            if run > best:
                best = run
                best_start = start
        else:
            run = 0
    return best_start, best


MOTION_ACTIVE_THRESHOLD = 12.0


def _motion_signature(raw, width, height, sx=32, sy=8):
    """Return a tiny luminance signature for temporal activity detection.

    Only sx*sy pixels are sampled (256 by default), so this adds negligible
    CPU compared with the full result classifier.  The signature is used only
    as evidence that a real match is active; it never decides WIN/LOSS/DRAW.
    """
    if width <= 0 or height <= 0 or len(raw) < width * height * 4:
        return None
    data = memoryview(raw)
    out = []
    for by in range(sy):
        y = min(height - 1, int((by + 0.5) * height / sy))
        row = y * width * 4
        for bx in range(sx):
            x = min(width - 1, int((bx + 0.5) * width / sx))
            i = row + x * 4
            b = int(data[i])
            g = int(data[i + 1])
            r = int(data[i + 2])
            out.append((29 * b + 150 * g + 77 * r) >> 8)
    return out


def _motion_score(previous, current):
    if previous is None or current is None or len(previous) != len(current):
        return None
    if not current:
        return None
    return sum(abs(a - b) for a, b in zip(previous, current)) / len(current)


def _is_gameplay_activity(frame_state, debug, motion_score):
    """Conservative match-activity evidence independent of scene brightness.

    v20 used CLEAR (mostly a brightness test) to arm/re-arm the result state
    machine.  Dark arenas can stay NON_CLEAR for an entire match, so a genuine
    YOU WIN may be recognized visually but discarded because the state machine
    never armed.  Here we accept strong temporal motion as an alternate
    gameplay signal, but only when the frame has no result-like signature.
    """
    if motion_score is None or motion_score < MOTION_ACTIVE_THRESHOLD:
        return False
    if frame_state not in (CLEAR, NON_CLEAR):
        return False
    if debug.get("reason") == "black_or_transition":
        return False
    if debug.get("draw_like"):
        return False
    if (
        debug.get("phase_template_like")
        or debug.get("phase_bright_like")
        or debug.get("phase_prefix_like")
    ):
        return False

    # Do not use a moving menu/garage element that already resembles a result
    # as match evidence.  Known false-garage samples are ~0.67-0.74 in the
    # 1-D WIN profile and are therefore rejected by this guard.
    profile_like = max(
        debug.get("win_final_score", 0.0),
        debug.get("loss_final_score", 0.0),
        debug.get("win_phase_score", 0.0),
        debug.get("loss_phase_score", 0.0),
    )
    grid_like = max(
        debug.get("win_final_grid_score", 0.0),
        debug.get("loss_final_grid_score", 0.0),
        debug.get("win_phase_grid_score", 0.0),
        debug.get("loss_phase_grid_score", 0.0),
    )
    return profile_like < 0.60 and grid_like < 0.65


def _center_cluster(values, threshold=0.010, max_gap=2):
    active = [i for i, v in enumerate(values) if v >= threshold]
    if not active:
        return {
            "start": -1, "end": -1, "span": 0.0,
            "center": 0.0, "coverage": 0.0, "density": 0.0,
        }

    clusters = []
    start = prev = active[0]
    for idx in active[1:]:
        if idx - prev - 1 <= max_gap:
            prev = idx
        else:
            clusters.append((start, prev))
            start = prev = idx
    clusters.append((start, prev))

    center_bin = (len(values) - 1) / 2.0

    def distance(ab):
        a, b = ab
        if a <= center_bin <= b:
            return 0.0
        return min(abs(center_bin - a), abs(center_bin - b))

    # Prefer the cluster nearest the screen center; for ties, prefer wider.
    a, b = min(clusters, key=lambda ab: (distance(ab), -(ab[1]-ab[0])))
    n = len(values)
    vals = values[a:b+1]
    return {
        "start": a,
        "end": b,
        "span": (b - a + 1) / n,
        "center": ((a + b + 1) / 2.0) / n,
        "coverage": sum(vals) / n,
        "density": sum(vals) / max(1, len(vals)),
    }


class ResultClassifier:
    REQUIRED_TEMPLATES = {
        "final_win",
        "final_loss",
        "phase_win",
        "phase_loss",
    }

    def __init__(self, template_path):
        try:
            obj = json.loads(Path(template_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise TemplateError(f"cannot read detector templates: {e}") from e

        if not isinstance(obj, dict):
            raise TemplateError("template root must be an object")
        if obj.get("version") != 3:
            raise TemplateError("unsupported template version (expected v3)")

        bx = obj.get("bins_x")
        by = obj.get("bins_y")
        gx = obj.get("grid_x")
        gy = obj.get("grid_y")
        if type(bx) is not int or bx <= 0 or bx > 256:
            raise TemplateError("invalid bins_x")
        if type(by) is not int or by <= 0 or by > 256:
            raise TemplateError("invalid bins_y")
        if type(gx) is not int or gx <= 0 or gx > 128:
            raise TemplateError("invalid grid_x")
        if type(gy) is not int or gy <= 0 or gy > 64:
            raise TemplateError("invalid grid_y")

        templates = obj.get("templates")
        grid_templates = obj.get("grid_templates")
        draw_grid_template = obj.get("draw_grid_template")
        if not isinstance(templates, dict):
            raise TemplateError("templates must be an object")
        if not isinstance(grid_templates, dict):
            raise TemplateError("grid_templates must be an object")
        if set(templates) != self.REQUIRED_TEMPLATES:
            raise TemplateError("required detector templates are missing or unknown")
        if set(grid_templates) != self.REQUIRED_TEMPLATES:
            raise TemplateError("required grid templates are missing or unknown")
        if not isinstance(draw_grid_template, list):
            raise TemplateError("draw_grid_template must be an array")

        def clean_template_map(source, expected_len, label):
            clean_map = {}
            for name in sorted(self.REQUIRED_TEMPLATES):
                arr = source[name]
                if not isinstance(arr, list) or len(arr) != expected_len:
                    raise TemplateError(f"invalid {label} length: {name}")
                vals = []
                for i, v in enumerate(arr):
                    if type(v) not in (int, float):
                        raise TemplateError(
                            f"non-numeric {label} value: {name}[{i}]"
                        )
                    v = float(v)
                    if not math.isfinite(v) or not 0.0 <= v <= 1.0:
                        raise TemplateError(
                            f"out-of-range {label} value: {name}[{i}]"
                        )
                    vals.append(v)
                clean_map[name] = vals
            return clean_map

        self.templates = clean_template_map(
            templates, bx + by, "template"
        )
        self.grid_templates = clean_template_map(
            grid_templates, gx * gy, "grid template"
        )
        if len(draw_grid_template) != gx * gy:
            raise TemplateError(
                f"invalid draw grid template length: {len(draw_grid_template)} "
                f"(expected {gx * gy})"
            )
        self.draw_grid_template = []
        for i, v in enumerate(draw_grid_template):
            if type(v) not in (int, float):
                raise TemplateError(f"non-numeric draw grid value: [{i}]")
            v = float(v)
            if not math.isfinite(v) or not 0.0 <= v <= 1.0:
                raise TemplateError(f"out-of-range draw grid value: [{i}]")
            self.draw_grid_template.append(v)
        self.bx = bx
        self.by = by
        self.gx = gx
        self.gy = gy

    def classify_bgra(self, raw, width, height):
        if width <= 0 or height <= 0 or len(raw) < width * height * 4:
            return NON_CLEAR, {"reason": "invalid_frame"}

        data = memoryview(raw)
        total = max(1, width * height)

        win_mask = bytearray(total)
        loss_mask = bytearray(total)
        bright_mask = bytearray(total)
        draw_mask = bytearray(total)

        win_n = loss_n = bright_n = draw_n = dark_n = 0
        win_min = loss_min = bright_min = draw_min = width
        win_max = loss_max = bright_max = draw_max = -1
        win_sum = loss_sum = bright_sum = draw_sum = 0
        gray_sum = 0

        for y in range(height):
            row = y * width * 4
            base = y * width
            for x in range(width):
                i = row + x * 4
                b = int(data[i])
                g = int(data[i + 1])
                r = int(data[i + 2])

                gray = (29 * b + 150 * g + 77 * r) >> 8
                gray_sum += gray
                if gray < 80:
                    dark_n += 1

                is_bright = gray > 125
                if is_bright:
                    bright_mask[base + x] = 1
                    bright_n += 1
                    bright_min = min(bright_min, x)
                    bright_max = max(bright_max, x)
                    bright_sum += x

                # DRAW is rendered as a compact, near-neutral white word on
                # the same dark result band. Detect it independently of the
                # cyan/red result masks so color cast/HDR cannot turn DRAW
                # into a WIN candidate. This mask is deliberately strict on
                # saturation and loose on luminance.
                mx = max(r, g, b)
                mn = min(r, g, b)
                is_draw_white = gray > 145 and (mx - mn) < 42
                if is_draw_white:
                    draw_mask[base + x] = 1
                    draw_n += 1
                    draw_min = min(draw_min, x)
                    draw_max = max(draw_max, x)
                    draw_sum += x

                is_win = (
                    g > 120 and b > 120
                    and (g - r) > 25
                    and (b - r) > 20
                    and abs(g - b) < 70
                )
                if is_win:
                    win_mask[base + x] = 1
                    win_n += 1
                    win_min = min(win_min, x)
                    win_max = max(win_max, x)
                    win_sum += x

                is_loss = (
                    r > 130
                    and (r - g) > 35
                    and (r - b) > 45
                    and g > 50
                )
                if is_loss:
                    loss_mask[base + x] = 1
                    loss_n += 1
                    loss_min = min(loss_min, x)
                    loss_max = max(loss_max, x)
                    loss_sum += x

        def metrics(n, mn, mx, sx):
            if n <= 0:
                return {"coverage": 0.0, "span": 0.0, "center": 0.0}
            return {
                "coverage": n / total,
                "span": (mx - mn + 1) / width,
                "center": (sx / n) / width,
            }

        win = metrics(win_n, win_min, win_max, win_sum)
        loss = metrics(loss_n, loss_min, loss_max, loss_sum)
        bright = metrics(bright_n, bright_min, bright_max, bright_sum)
        draw = metrics(draw_n, draw_min, draw_max, draw_sum)
        dark_ratio = dark_n / total
        mean_gray = gray_sum / total

        win_profile = _profiles(win_mask, width, height, self.bx, self.by)
        loss_profile = _profiles(loss_mask, width, height, self.bx, self.by)

        # v22 recomputed these exact X/Y values after _profiles(), causing four
        # redundant full mask traversals per poll (WIN X/Y + LOSS X/Y). Reuse
        # the already-computed template profile slices; values are identical.
        win_x = win_profile[:self.bx]
        win_y = win_profile[self.bx:self.bx + self.by]
        loss_x = loss_profile[:self.bx]
        loss_y = loss_profile[self.bx:self.bx + self.by]

        bright_x = _x_profile(bright_mask, width, height, 64)
        draw_x = _x_profile(draw_mask, width, height, 64)
        draw_y = _y_profile(draw_mask, width, height, 16)
        win_grid = _grid_profile(win_mask, width, height, self.gx, self.gy)
        loss_grid = _grid_profile(loss_mask, width, height, self.gx, self.gy)
        draw_grid = _grid_profile(draw_mask, width, height, self.gx, self.gy)

        win_cluster = _center_cluster(win_x, threshold=0.010, max_gap=2)
        loss_cluster = _center_cluster(loss_x, threshold=0.010, max_gap=2)
        bright_cluster = _center_cluster(bright_x, threshold=0.010, max_gap=2)
        draw_cluster = _center_cluster(draw_x, threshold=0.010, max_gap=1)
        win_y_cluster = _center_cluster(win_y, threshold=0.010, max_gap=1)
        loss_y_cluster = _center_cluster(loss_y, threshold=0.010, max_gap=1)
        draw_y_cluster = _center_cluster(draw_y, threshold=0.010, max_gap=1)

        win_final_score = _cosine(win_profile, self.templates["final_win"])
        win_phase_score = _cosine(win_profile, self.templates["phase_win"])
        loss_final_score = _cosine(loss_profile, self.templates["final_loss"])
        loss_phase_score = _cosine(loss_profile, self.templates["phase_loss"])
        win_final_grid_score = _cosine(
            win_grid, self.grid_templates["final_win"]
        )
        win_phase_grid_score = _cosine(
            win_grid, self.grid_templates["phase_win"]
        )
        loss_final_grid_score = _cosine(
            loss_grid, self.grid_templates["final_loss"]
        )
        loss_phase_grid_score = _cosine(
            loss_grid, self.grid_templates["phase_loss"]
        )
        draw_grid_score = _cosine(draw_grid, self.draw_grid_template)

        # Real AC6 result banners are drawn on a dark translucent strip.
        # Template similarity alone is too permissive during normal combat.
        result_band_like = (
            dark_ratio >= 0.72
            and 8.0 <= mean_gray <= 90.0
        )

        # DRAW is intentionally precision-first. v20/v21 used only a compact
        # neutral-white cluster, which allowed brief combat explosions/AC parts
        # to look like DRAW and prematurely lock the match.  The real DRAW word
        # has a very stable 2-D occupancy pattern, so require an independent
        # glyph fingerprint in addition to the broad geometry.  The supplied
        # true DRAW fixtures score ~1.00; the exact combat false positives from
        # the two user recordings score <=0.30, leaving a large safety margin.
        draw_like = (
            result_band_like
            and 0.12 <= draw_cluster["span"] <= 0.25
            and 0.42 <= draw_cluster["center"] <= 0.58
            and draw_cluster["coverage"] >= 0.015
            and draw_cluster["density"] >= 0.090
            and 0.20 <= draw_y_cluster["span"] <= 0.80
            and draw_y_cluster["density"] >= 0.035
            and draw_grid_score >= 0.75
        )

        # Use the central colored-text cluster, not the global min/max span.
        # Combat effects can create stray cyan/red pixels far from YOU WIN/LOSE
        # and made the old global span look like a PHASE banner.
        phase_win_geom = (
            0.42 <= win_cluster["span"] <= 0.70
            and 0.35 <= win_cluster["center"] <= 0.65
            and win_cluster["coverage"] >= 0.030
            and win_cluster["density"] >= 0.075
        )
        phase_loss_geom = (
            0.42 <= loss_cluster["span"] <= 0.72
            and 0.35 <= loss_cluster["center"] <= 0.65
            and loss_cluster["coverage"] >= 0.030
            and loss_cluster["density"] >= 0.070
        )
        phase_color_geom = phase_win_geom or phase_loss_geom

        # Final-result text has a very stable vertical footprint: the letters
        # occupy roughly the middle half of the 7%-high capture strip, leaving
        # dark margins above/below.  A cyan AC body in the garage filled nearly
        # the entire strip in the user's recording and used to pass the X-only
        # geometry.  Requiring both X and Y geometry rejects that failure mode.
        final_win_geom_early = (
            0.22 <= win_cluster["span"] <= 0.39
            and 0.38 <= win_cluster["center"] <= 0.62
            and win_cluster["coverage"] >= 0.018
            and win_cluster["density"] >= 0.075
            and 0.40 <= win_y_cluster["span"] <= 0.70
            and 0.36 <= win_y_cluster["center"] <= 0.64
            and win_y_cluster["density"] >= 0.060
        )
        final_loss_geom_early = (
            0.24 <= loss_cluster["span"] <= 0.42
            and 0.38 <= loss_cluster["center"] <= 0.62
            and loss_cluster["coverage"] >= 0.018
            and loss_cluster["density"] >= 0.070
            and 0.40 <= loss_y_cluster["span"] <= 0.70
            and 0.36 <= loss_y_cluster["center"] <= 0.64
            and loss_y_cluster["density"] >= 0.060
        )

        phase_template_like = (
            result_band_like
            and (
                (
                    phase_win_geom
                    and win_phase_score >= 0.78
                    and win_phase_grid_score >= 0.72
                    and win_phase_score >= win_final_score + 0.03
                )
                or (
                    phase_loss_geom
                    and loss_phase_score >= 0.78
                    and loss_phase_grid_score >= 0.72
                    and loss_phase_score >= loss_final_score + 0.03
                )
            )
        )

        # Bright-span PHASE veto now requires continuity through the center.
        # Two unrelated bright HUD blocks at left/right no longer qualify.
        run_start, run_len = _longest_run(bright_x, threshold=0.035)
        run_end = run_start + run_len - 1 if run_start >= 0 else -1
        center_bin = 32
        run_crosses_center = run_start <= center_bin <= run_end if run_start >= 0 else False
        run_fraction = run_len / 64.0
        central_density = sum(bright_x[24:40]) / 16.0

        # Text contains natural inter-letter gaps, so a single uninterrupted
        # run through the exact center is too strict for real PHASE samples.
        # Instead require dense, gap-limited occupancy across the central 16 bins.
        central_flags = [v >= 0.010 for v in bright_x[24:40]]
        central_active_fraction = sum(central_flags) / len(central_flags)
        max_center_gap = 0
        gap = 0
        for flag in central_flags:
            if flag:
                gap = 0
            else:
                gap += 1
                max_center_gap = max(max_center_gap, gap)
        central_continuous = (
            central_active_fraction >= 0.85
            and max_center_gap <= 1
        )

        colored_span = max(win_cluster["span"], loss_cluster["span"])

        # Some PHASE banners have a white/gray prefix while YOU WIN/LOSE is
        # colored. In that case the total bright text is much wider than the
        # colored final-like portion.
        phase_prefix_like = (
            result_band_like
            and (final_win_geom_early or final_loss_geom_early)
            and bright["coverage"] >= 0.055
            and 0.40 <= bright_cluster["span"] <= 0.75
            and (bright_cluster["span"] - colored_span) >= 0.10
            and 0.30 <= bright_cluster["center"] <= 0.70
            and central_continuous
            and central_density >= 0.045
        )

        phase_bright_like = (
            result_band_like
            and phase_color_geom
            and bright["coverage"] >= 0.04
            and bright_cluster["span"] >= 0.40
            and 0.32 <= bright_cluster["center"] <= 0.68
            and central_continuous
            and central_density >= 0.045
        )

        debug = {
            "win": win,
            "loss": loss,
            "bright": bright,
            "draw": draw,
            "draw_cluster": draw_cluster,
            "draw_y_cluster": draw_y_cluster,
            "draw_grid_score": draw_grid_score,
            "draw_like": draw_like,
            "dark_ratio": dark_ratio,
            "mean_gray": mean_gray,
            "win_final_score": win_final_score,
            "win_phase_score": win_phase_score,
            "loss_final_score": loss_final_score,
            "loss_phase_score": loss_phase_score,
            "win_final_grid_score": win_final_grid_score,
            "win_phase_grid_score": win_phase_grid_score,
            "loss_final_grid_score": loss_final_grid_score,
            "loss_phase_grid_score": loss_phase_grid_score,
            "result_band_like": result_band_like,
            "win_cluster": win_cluster,
            "loss_cluster": loss_cluster,
            "win_y_cluster": win_y_cluster,
            "loss_y_cluster": loss_y_cluster,
            "bright_cluster": bright_cluster,
            "phase_win_geom": phase_win_geom,
            "phase_loss_geom": phase_loss_geom,
            "phase_color_geom": phase_color_geom,
            "phase_prefix_like": phase_prefix_like,
            "phase_template_like": phase_template_like,
            "phase_bright_like": phase_bright_like,
            "bright_run_fraction": run_fraction,
            "bright_run_crosses_center": run_crosses_center,
            "central_active_fraction": central_active_fraction,
            "max_center_gap": max_center_gap,
            "central_continuous": central_continuous,
            "central_bright_density": central_density,
        }

        if draw_like:
            return FINAL_DRAW, debug

        if phase_template_like or phase_bright_like or phase_prefix_like:
            return PHASE, debug

        win_geom = final_win_geom_early
        loss_geom = final_loss_geom_early

        # Precision-first final decision.  A final must agree in three
        # independent ways: dark result band, 2-D geometry, and both the old
        # 1-D profile plus the new 2-D glyph fingerprint.  The previous 0.50
        # profile threshold was intentionally permissive and is the reason a
        # cyan garage model could become FINAL_WIN.
        win_ok = (
            result_band_like
            and win_geom
            and win_final_score >= 0.78
            and win_final_grid_score >= 0.82
            and win_final_grid_score >= win_phase_grid_score + 0.12
        )
        loss_ok = (
            result_band_like
            and loss_geom
            and loss_final_score >= 0.78
            and loss_final_grid_score >= 0.82
            and loss_final_grid_score >= loss_phase_grid_score + 0.12
        )

        if win_ok and not loss_ok:
            return FINAL_WIN, debug
        if loss_ok and not win_ok:
            return FINAL_LOSS, debug

        # Black/transition frames are NON_CLEAR. CLEAR requires actual gameplay
        # image information, not merely absence of colored result pixels.
        if mean_gray < 8.0 and bright["coverage"] < 0.005:
            return NON_CLEAR, {**debug, "reason": "black_or_transition"}

        if dark_ratio < 0.78:
            return CLEAR, debug

        # Very dark result-band-like frames without a valid result signature are
        # intentionally NON_CLEAR so they can never re-arm the detector.
        return NON_CLEAR, debug


class ResultStateMachine:
    # After a result/manual mutation, do not re-arm merely because a few
    # transient frames look CLEAR. AC6 result animations can briefly classify
    # as CLEAR and then show another result-like frame, which previously let
    # one match be counted twice (or a LOSS be followed by a false WIN).
    # Require a continuous CLEAR period *after* the normal cooldown has fully
    # elapsed before the detector may arm for the next match.
    POST_RESULT_CLEAR_SECONDS = 5.0
    # Dark arenas may never satisfy CLEAR. Two temporally separated, safe
    # high-motion samples provide an alternate low-cost proof that a match is
    # actually active. This is deliberately not a result classifier.
    ACTIVITY_HITS_REQUIRED = 2
    ACTIVITY_GAP_MAX_SECONDS = 3.0
    # A genuine final banner must follow either CLEAR gameplay or high-motion
    # gameplay evidence recently.
    FINAL_AFTER_GAMEPLAY_MAX_SECONDS = 5.0
    # DRAW must be confirmed on separate polls just like WIN/LOSS. A single
    # visual DRAW hit can be a combat effect and must never terminate a match.
    DRAW_CONFIRM_HITS = 2

    def __init__(self):
        self.lock = threading.RLock()
        self.reset_unarmed()

    def reset_unarmed(self):
        with self.lock:
            self.armed = False
            self.candidate = None
            self.candidate_hits = 0
            self.clear_hits = 0
            self.clear_ready = False
            self.last_clear_at = None
            self.last_activity_at = None
            self.activity_hits = 0
            self.last_counted_at = 0.0
            self.post_result_lock = False
            self.post_result_clear_since = None
            self.last_reject_reason = None

    def external_mutation(self, now=None):
        now = time.monotonic() if now is None else now
        with self.lock:
            self.armed = False
            self.candidate = None
            self.candidate_hits = 0
            self.clear_hits = 0
            self.clear_ready = False
            self.last_clear_at = None
            self.last_activity_at = None
            self.activity_hits = 0
            self.last_counted_at = now
            self.post_result_lock = True
            self.post_result_clear_since = None
            self.last_reject_reason = None

    def after_undo(self, now=None):
        self.external_mutation(now)

    def snapshot(self):
        with self.lock:
            return {
                "armed": self.armed,
                "candidate": self.candidate,
                "candidate_hits": self.candidate_hits,
                "clear_hits": self.clear_hits,
                "clear_ready": self.clear_ready,
                "post_result_lock": self.post_result_lock,
                "last_reject_reason": self.last_reject_reason,
            }

    def note_foreground(self, is_foreground):
        with self.lock:
            if not is_foreground:
                self.candidate = None
                self.candidate_hits = 0
                self.clear_hits = 0
                self.clear_ready = False
                self.last_clear_at = None
                self.last_activity_at = None
                self.activity_hits = 0
                self.post_result_clear_since = None
                self.last_reject_reason = None

    def _observe_gameplay_activity(self, gameplay_activity, cooldown_seconds, now):
        """Use motion-confirmed gameplay as an alternate arm/re-arm path.

        CLEAR remains as a fallback, but dark gameplay no longer depends on
        brightness. Activity observed during the ordinary result cooldown is
        discarded so result-animation motion can never be banked for re-arm.
        Returns True only when this frame releases a post-result lock; that
        releasing frame is consumed and can never also become a result.
        """
        if not gameplay_activity:
            if (
                self.last_activity_at is not None
                and now - self.last_activity_at > self.ACTIVITY_GAP_MAX_SECONDS
            ):
                self.activity_hits = 0
            return False

        # Strict public mode: activity may help initial/startup arming, but it
        # can NEVER release a post-result lock. Only stable CLEAR can do that.
        if self.post_result_lock:
            self.last_activity_at = None
            self.activity_hits = 0
            return False

        if (
            self.last_activity_at is None
            or now - self.last_activity_at > self.ACTIVITY_GAP_MAX_SECONDS
        ):
            self.activity_hits = 1
        else:
            self.activity_hits += 1
        self.last_activity_at = now

        if self.activity_hits < self.ACTIVITY_HITS_REQUIRED:
            return False

        if not self.armed and now - self.last_counted_at >= cooldown_seconds:
            self.armed = True
        return False

    def _arm_if_ready(self, now, cooldown_seconds):
        if self.post_result_lock:
            return
        if (
            not self.armed
            and self.clear_ready
            and now - self.last_counted_at >= cooldown_seconds
        ):
            self.armed = True

    def _observe_post_result_lock(self, frame_state, cooldown_seconds, now):
        """Return True while the post-result lock owns this frame.

        CLEAR frames seen during the normal cooldown are intentionally ignored.
        Once cooldown has elapsed, only an uninterrupted CLEAR interval of
        POST_RESULT_CLEAR_SECONDS releases the lock. Any PHASE/NON_CLEAR/FINAL
        frame resets that interval. The releasing CLEAR frame arms the detector,
        but is never itself treated as a result candidate.
        """
        if not self.post_result_lock:
            return False

        # Result/phase/transition frames prove we are still inside the old
        # result sequence. They must break any tentative re-arm interval.
        if frame_state != CLEAR:
            self.post_result_clear_since = None
            self.candidate = None
            self.candidate_hits = 0
            self.clear_hits = 0
            self.clear_ready = False
            self.last_clear_at = None
            return True

        # Never bank CLEAR time while the ordinary cooldown is still active.
        if now - self.last_counted_at < cooldown_seconds:
            self.post_result_clear_since = None
            return True

        if self.post_result_clear_since is None:
            self.post_result_clear_since = now
            return True

        if now - self.post_result_clear_since < self.POST_RESULT_CLEAR_SECONDS:
            return True

        # We have now seen a stable non-result period long enough to consider
        # the previous match finished. Arm for the *next* result only.
        self.post_result_lock = False
        self.post_result_clear_since = None
        self.clear_hits = 0
        self.clear_ready = True
        self.last_clear_at = now
        self.armed = True
        return True

    def observe(
        self,
        frame_state,
        confirm_hits,
        clear_hits_required,
        cooldown_seconds,
        now=None,
        gameplay_activity=False,
    ):
        now = time.monotonic() if now is None else now

        with self.lock:
            self.last_reject_reason = None

            # Activity can arm startup/dark gameplay, but strict public mode
            # never allows activity to release a post-result lock.
            self._observe_gameplay_activity(gameplay_activity, cooldown_seconds, now)

            if self._observe_post_result_lock(frame_state, cooldown_seconds, now):
                self.last_reject_reason = "post_result_lock"
                return None

            if frame_state in (PHASE, NON_CLEAR):
                self.candidate = None
                self.candidate_hits = 0
                self.clear_hits = 0
                self.clear_ready = False
                return None

            if frame_state == CLEAR:
                self.candidate = None
                self.candidate_hits = 0
                self.last_clear_at = now
                self.clear_hits += 1
                if self.clear_hits >= clear_hits_required:
                    self.clear_ready = True
                self._arm_if_ready(now, cooldown_seconds)
                return None

            self._arm_if_ready(now, cooldown_seconds)
            self.clear_hits = 0
            if not self.armed:
                self.last_reject_reason = "not_armed"
                return None

            # A real final must directly follow observed gameplay.  Use the
            # newest of brightness-safe CLEAR and motion-confirmed activity.
            recent_gameplay = self.last_clear_at
            if self.last_activity_at is not None:
                if recent_gameplay is None or self.last_activity_at > recent_gameplay:
                    recent_gameplay = self.last_activity_at
            if (
                recent_gameplay is None
                or now - recent_gameplay > self.FINAL_AFTER_GAMEPLAY_MAX_SECONDS
            ):
                self.candidate = None
                self.candidate_hits = 0
                self.last_reject_reason = "no_recent_gameplay"
                return None

            result = (
                "win" if frame_state == FINAL_WIN
                else "loss" if frame_state == FINAL_LOSS
                else "draw" if frame_state == FINAL_DRAW
                else None
            )
            if result is None:
                return None

            if self.candidate == result:
                self.candidate_hits += 1
            else:
                self.candidate = result
                self.candidate_hits = 1

            required_hits = (
                max(self.DRAW_CONFIRM_HITS, confirm_hits)
                if result == "draw"
                else confirm_hits
            )
            if self.candidate_hits >= required_hits:
                self.armed = False
                self.last_counted_at = now
                self.candidate = None
                self.candidate_hits = 0
                self.clear_hits = 0
                self.clear_ready = False
                self.last_clear_at = None
                self.last_activity_at = None
                self.activity_hits = 0
                if result == "draw":
                    # DRAW has no stats mutation, so unlike WIN/LOSS there is no
                    # server-side external_mutation() call to install the lock.
                    # Install it here only after a confirmed DRAW.
                    self.post_result_lock = True
                    self.post_result_clear_since = None
                return result

            return None


class WinApi:
    def __init__(self):
        if os.name != "nt":
            return

        self.user32 = ctypes.windll.user32
        self.kernel32 = ctypes.windll.kernel32

        try:
            self.user32.SetProcessDPIAware()
        except Exception:
            pass

        self.user32.GetForegroundWindow.restype = wintypes.HWND
        self.user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
        self.user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(POINT)]
        self.user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND, ctypes.POINTER(wintypes.DWORD)
        ]
        self.user32.GetWindowThreadProcessId.restype = wintypes.DWORD

        self.kernel32.OpenProcess.argtypes = [
            wintypes.DWORD, wintypes.BOOL, wintypes.DWORD
        ]
        self.kernel32.OpenProcess.restype = wintypes.HANDLE
        self.kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self.kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        self.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel32.CloseHandle.restype = wintypes.BOOL

    def foreground_process_and_client(self):
        if os.name != "nt":
            return None, None

        hwnd = self.user32.GetForegroundWindow()
        if not hwnd:
            return None, None

        pid = wintypes.DWORD()
        self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return None, None

        handle = self.kernel32.OpenProcess(0x1000, False, pid.value)
        if not handle:
            return None, None

        try:
            size = wintypes.DWORD(32768)
            buf = ctypes.create_unicode_buffer(size.value)
            ok = self.kernel32.QueryFullProcessImageNameW(
                handle, 0, buf, ctypes.byref(size)
            )
            process = os.path.basename(buf.value).lower() if ok else None
        finally:
            self.kernel32.CloseHandle(handle)

        rect = RECT()
        if not self.user32.GetClientRect(hwnd, ctypes.byref(rect)):
            return process, None

        p1 = POINT(rect.left, rect.top)
        p2 = POINT(rect.right, rect.bottom)
        if not self.user32.ClientToScreen(hwnd, ctypes.byref(p1)):
            return process, None
        if not self.user32.ClientToScreen(hwnd, ctypes.byref(p2)):
            return process, None

        width = p2.x - p1.x
        height = p2.y - p1.y
        if width < 640 or height < 360:
            return process, None

        return process, {
            "left": p1.x,
            "top": p1.y,
            "width": width,
            "height": height,
        }


class DetectorHealth:
    def __init__(self, callback):
        self.lock = threading.Lock()
        self.callback = callback
        self.data = {
            "system": True,
            "kind": "detector",
            "status": "starting",
            "error": None,
            "last_capture": None,
            "last_result": None,
        }

    def update(self, publish=True, **kwargs):
        with self.lock:
            old = dict(self.data)
            self.data.update(kwargs)
            changed = any(old.get(k) != self.data.get(k) for k in kwargs)
            snapshot = dict(self.data)
        if publish and changed and self.callback:
            self.callback("detector", snapshot, remember=False)

    def touch_capture(self, ts):
        # Internal freshness only; do not emit a heartbeat SSE every poll.
        self.update(publish=False, last_capture=ts)

    def snapshot(self):
        with self.lock:
            return dict(self.data)


class ResultDetector:
    def __init__(self, root, config_loader, on_result, event_callback, stop_event, diagnostic_recorder=None):
        self.root = Path(root)
        self.config_loader = config_loader
        self.on_result = on_result
        self.event_callback = event_callback
        self.stop_event = stop_event
        self.diagnostics = diagnostic_recorder
        self.classifier = ResultClassifier(self.root / "detector_templates.json")
        self.state = ResultStateMachine()
        self.health = DetectorHealth(event_callback)
        self.winapi = WinApi()
        self._last_mismatch_log = 0.0
        self._client_missing_since = None
        self._was_enabled = None
        self._last_phase_log = 0.0
        self._last_reject_log = 0.0
        self._last_debug_capture = 0.0
        self._last_debug_reject_capture = 0.0
        self._last_motion_signature = None
        self._last_gate_reject_log = 0.0
        self._last_diagnostic_visual = None

    def external_mutation(self):
        self.state.external_mutation()

    def after_undo(self):
        self.state.after_undo()

    def _save_debug_result_roi(self, shot, frame_state):
        if to_png is None:
            return
        now = time.monotonic()
        if now - self._last_debug_capture < 2.0:
            return
        self._last_debug_capture = now
        try:
            out = (self.diagnostics.root / "debug_result_latest.png") if self.diagnostics else (self.root / "debug_result_latest.png")
            to_png(shot.rgb, shot.size, output=str(out))
        except Exception as e:
            print(f"[result] debug capture failed: {e}")

    def _save_debug_rejected_roi(self, shot):
        if to_png is None:
            return
        now = time.monotonic()
        if now - self._last_debug_reject_capture < 2.0:
            return
        self._last_debug_reject_capture = now
        try:
            out = (self.diagnostics.root / "debug_result_rejected_latest.png") if self.diagnostics else (self.root / "debug_result_rejected_latest.png")
            to_png(shot.rgb, shot.size, output=str(out))
        except Exception as e:
            print(f"[result] rejected debug capture failed: {e}")

    def run(self):
        if mss is None:
            msg = "mss is not installed"
            print(f"[result] detector init failed: {msg}")
            self.health.update(status="error", error=msg)
            return

        try:
            with mss.mss() as sct:
                while not self.stop_event.is_set():
                    try:
                        c = self.config_loader()
                        poll = POLL_SECONDS
                        enabled = c["result_detector_enabled"]

                        if enabled != self._was_enabled:
                            self.state.reset_unarmed()
                            self._client_missing_since = None
                            self._was_enabled = enabled
                            self.health.update(
                                status="starting" if enabled else "disabled",
                                error=None,
                            )

                        if not enabled:
                            self.stop_event.wait(min(1.0, poll))
                            continue

                        proc, client = self.winapi.foreground_process_and_client()
                        expected = TARGET_PROCESS

                        if proc != expected:
                            self.state.note_foreground(False)
                            self._last_motion_signature = None
                            self._client_missing_since = None
                            now = time.monotonic()
                            if now - self._last_mismatch_log >= 10.0:
                                print(
                                    f"[result] foreground waiting: "
                                    f"{proc or 'unknown'} (expected {expected})"
                                )
                                self._last_mismatch_log = now
                            self.health.update(status="waiting", error=None)
                            self.stop_event.wait(poll)
                            continue

                        if client is None:
                            self.state.note_foreground(False)
                            self._last_motion_signature = None
                            now = time.monotonic()
                            if self._client_missing_since is None:
                                self._client_missing_since = now
                            if now - self._client_missing_since >= 5.0:
                                self.health.update(
                                    status="degraded",
                                    error="AC6 is foreground but client rectangle is unavailable",
                                )
                            else:
                                self.health.update(status="waiting", error=None)
                            self.stop_event.wait(poll)
                            continue

                        self._client_missing_since = None

                        region = {
                            "left": client["left"] + int(client["width"] * 0.20),
                            "top": client["top"] + int(client["height"] * 0.43),
                            "width": max(100, int(client["width"] * 0.60)),
                            "height": max(40, int(client["height"] * 0.07)),
                        }

                        shot = sct.grab(region)
                        frame_state, debug = self.classifier.classify_bgra(
                            shot.raw, shot.width, shot.height
                        )

                        motion_sig = _motion_signature(
                            shot.raw, shot.width, shot.height
                        )
                        motion_score = _motion_score(
                            self._last_motion_signature, motion_sig
                        )
                        self._last_motion_signature = motion_sig
                        gameplay_activity = _is_gameplay_activity(
                            frame_state, debug, motion_score
                        )
                        debug["motion_score"] = motion_score
                        debug["gameplay_activity"] = gameplay_activity

                        self.health.touch_capture(time.time())
                        self.health.update(status="active", error=None)

                        if self.diagnostics:
                            compact = {
                                "frame_state": frame_state,
                                "motion_score": None if motion_score is None else round(float(motion_score), 4),
                                "gameplay_activity": bool(gameplay_activity),
                                "dark_ratio": round(float(debug.get("dark_ratio", 0.0)), 4),
                                "mean_gray": round(float(debug.get("mean_gray", 0.0)), 2),
                                "win_1d": round(float(debug.get("win_final_score", 0.0)), 4),
                                "win_2d": round(float(debug.get("win_final_grid_score", 0.0)), 4),
                                "loss_1d": round(float(debug.get("loss_final_score", 0.0)), 4),
                                "loss_2d": round(float(debug.get("loss_final_grid_score", 0.0)), 4),
                                "state_before": self.state.snapshot(),
                            }
                            self.diagnostics.buffer_frame(**compact)

                        if frame_state in (PHASE, FINAL_WIN, FINAL_LOSS, FINAL_DRAW):
                            self._save_debug_result_roi(shot, frame_state)
                            if (
                                self.diagnostics
                                and frame_state in (FINAL_WIN, FINAL_LOSS, FINAL_DRAW)
                                and frame_state != self._last_diagnostic_visual
                            ):
                                self.diagnostics.flush_frame_context(
                                    f"visual_{frame_state.lower()}"
                                )
                                image = self.diagnostics.capture_roi(shot, frame_state)
                                self.diagnostics.record("result_visual", frame_state=frame_state, roi=image)
                            self._last_diagnostic_visual = frame_state
                        else:
                            self._last_diagnostic_visual = None

                        suspicious_reject = (
                            frame_state == NON_CLEAR
                            and debug.get("result_band_like")
                            and max(
                                debug.get("win_final_score", 0.0),
                                debug.get("loss_final_score", 0.0),
                            ) >= 0.55
                        )
                        if suspicious_reject:
                            self._save_debug_rejected_roi(shot)
                            now = time.monotonic()
                            if now - self._last_reject_log >= 2.0:
                                if self.diagnostics:
                                    self.diagnostics.flush_frame_context("suspicious_reject")
                                    image = self.diagnostics.capture_roi(shot, "rejected")
                                    self.diagnostics.record("suspicious_reject", roi=image, win_1d=debug.get("win_final_score", 0.0), win_2d=debug.get("win_final_grid_score", 0.0), loss_1d=debug.get("loss_final_score", 0.0), loss_2d=debug.get("loss_final_grid_score", 0.0))
                                print(
                                    "[result] result-like frame rejected "
                                    f"(win1d={debug.get('win_final_score', 0):.2f}, "
                                    f"win2d={debug.get('win_final_grid_score', 0):.2f}, "
                                    f"win_y={debug.get('win_y_cluster', {}).get('span', 0):.2f}, "
                                    f"loss1d={debug.get('loss_final_score', 0):.2f}, "
                                    f"loss2d={debug.get('loss_final_grid_score', 0):.2f}, "
                                    f"loss_y={debug.get('loss_y_cluster', {}).get('span', 0):.2f})"
                                )
                                self._last_reject_log = now

                        result = self.state.observe(
                            frame_state,
                            CONFIRM_HITS,
                            CLEAR_HITS_REQUIRED,
                            COOLDOWN_SECONDS,
                            gameplay_activity=gameplay_activity,
                        )

                        if self.diagnostics and result is not None:
                            self.diagnostics.flush_frame_context("state_decision")
                            image = self.diagnostics.capture_roi(
                                shot, f"confirmed_{result}"
                            )
                            self.diagnostics.record(
                                "result_confirmed_visual",
                                result=result,
                                roi=image,
                            )
                            self.diagnostics.record(
                                "state_decision",
                                result=result,
                                reject_reason=self.state.last_reject_reason,
                                state_after=self.state.snapshot(),
                            )

                        if frame_state == PHASE:
                            now = time.monotonic()
                            if now - self._last_phase_log >= 2.0:
                                if debug.get("phase_prefix_like"):
                                    reason = "prefix"
                                elif debug.get("phase_template_like"):
                                    reason = "template"
                                else:
                                    reason = "bright"
                                print(
                                    "[result] PHASE result ignored "
                                    f"(reason={reason}, "
                                    f"dark={debug.get('dark_ratio', 0):.2f}, "
                                    f"win_span={debug.get('win', {}).get('span', 0):.2f}, "
                                    f"win_cluster={debug.get('win_cluster', {}).get('span', 0):.2f}, "
                                    f"loss_span={debug.get('loss', {}).get('span', 0):.2f}, "
                                    f"loss_cluster={debug.get('loss_cluster', {}).get('span', 0):.2f})"
                                )
                                self._last_phase_log = now

                        if frame_state == FINAL_WIN:
                            print(
                                "[result] FINAL_WIN detected "
                                f"(profile={debug.get('win_final_score', 0):.2f}, "
                                f"glyph={debug.get('win_final_grid_score', 0):.2f})"
                            )
                        elif frame_state == FINAL_LOSS:
                            print(
                                "[result] FINAL_LOSS detected "
                                f"(profile={debug.get('loss_final_score', 0):.2f}, "
                                f"glyph={debug.get('loss_final_grid_score', 0):.2f})"
                            )
                        elif frame_state == FINAL_DRAW:
                            print(
                                "[result] FINAL_DRAW candidate "
                                f"(span={debug.get('draw_cluster', {}).get('span', 0):.2f}, "
                                f"density={debug.get('draw_cluster', {}).get('density', 0):.2f}, "
                                f"glyph={debug.get('draw_grid_score', 0):.2f})"
                            )

                        if (
                            frame_state in (FINAL_WIN, FINAL_LOSS)
                            and result is None
                            and self.state.last_reject_reason
                        ):
                            now = time.monotonic()
                            if now - self._last_gate_reject_log >= 2.0:
                                print(
                                    "[result] visual final not accepted "
                                    f"(gate={self.state.last_reject_reason}, "
                                    f"motion={motion_score if motion_score is not None else -1:.1f})"
                                )
                                self._last_gate_reject_log = now

                        if result == "draw":
                            # DRAW intentionally leaves WIN/LOSE/streak unchanged.
                            self.health.update(
                                last_result="draw", status="active", error=None
                            )
                        elif result:
                            accepted = self.on_result(result, "auto")
                            if accepted:
                                self.health.update(
                                    last_result=result,
                                    status="active",
                                    error=None,
                                )

                        self.stop_event.wait(poll)

                    except Exception as e:
                        msg = f"{type(e).__name__}: {e}"
                        print(f"[result] detector error: {msg}")
                        if self.diagnostics:
                            self.diagnostics.flush_frame_context("detector_error")
                            self.diagnostics.record("detector_error", error=msg)
                        self.state.reset_unarmed()
                        self.health.update(status="error", error=msg)
                        self.stop_event.wait(2.0)

        except Exception as e:
            msg = f"{type(e).__name__}: {e}"
            print(f"[result] detector init failed: {msg}")
            if self.diagnostics:
                self.diagnostics.flush_frame_context("detector_init_error")
                self.diagnostics.record("detector_init_error", error=msg)
            self.health.update(status="error", error=msg)
