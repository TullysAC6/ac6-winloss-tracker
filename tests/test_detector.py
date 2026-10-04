"""T0 classifier regressions on inputs constructed in code, and template validation.

Stored-pixel expectations that used to live here - tests/manifest.json and the
user-video regressions (DRAW positives, false DRAW combat frames, garage/menu
false WINs, the dark-gameplay missed WIN) - are T1 cases now, under
tests/fixtures/results/. tests/fixtures/legacy-coverage.json maps each one to
the T1 check that replaced it, and tests/test_t1_legacy_coverage.py keeps that
mapping honest. What stays here is what T1 metadata cannot express: frames that
are generated or transformed in code.
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from result_detector import (
    FINAL_WIN,
    FINAL_LOSS,
    FINAL_DRAW,
    NON_CLEAR,
    PHASE,
    ResultClassifier,
    TemplateError,
)


def read_ppm(path):
    with Path(path).open("rb") as f:
        magic = f.readline().strip()
        if magic != b"P6":
            raise ValueError("not P6 ppm")
        line = f.readline()
        while line.startswith(b"#"):
            line = f.readline()
        width, height = map(int, line.split())
        maxv = int(f.readline())
        if maxv != 255:
            raise ValueError("unsupported max value")
        rgb = f.read(width * height * 3)

    bgra = bytearray(width * height * 4)
    for i in range(width * height):
        r = rgb[i * 3]
        g = rgb[i * 3 + 1]
        b = rgb[i * 3 + 2]
        j = i * 4
        bgra[j] = b
        bgra[j + 1] = g
        bgra[j + 2] = r
        bgra[j + 3] = 255
    return bytes(bgra), width, height


classifier = ResultClassifier(ROOT / "detector_templates.json")
failed = []

# Regression: pure black/transition frame must NOT re-arm the state machine.
_, w, h = read_ppm(Path(__file__).parent / "fixtures" / "normal_01.ppm")
black = bytes((0, 0, 0, 255)) * (w * h)
got, debug = classifier.classify_bgra(black, w, h)
print(f"{'black_synthetic':32s} expected={NON_CLEAR:10s} got={got:10s} {'OK' if got == NON_CLEAR else 'NG'}")
if got != NON_CLEAR:
    failed.append(("black_synthetic", NON_CLEAR, got, debug))

# Regression: left/right bright blocks with a dark center must not be PHASE.
bgra = bytearray(bytes((0, 0, 0, 255)) * (w * h))
def rect(x0, y0, x1, y1, rgb):
    r, g, b = rgb
    for y in range(y0, y1):
        for x in range(x0, x1):
            i = (y * w + x) * 4
            bgra[i:i+4] = bytes((b, g, r, 255))
rect(int(w*.08), int(h*.2), int(w*.18), int(h*.8), (220,220,220))
rect(int(w*.82), int(h*.2), int(w*.92), int(h*.8), (220,220,220))
got, debug = classifier.classify_bgra(bytes(bgra), w, h)
ok = got != PHASE
print(f"{'disconnected_bright_blocks':32s} expected=NOT_PHASE  got={got:10s} {'OK' if ok else 'NG'}")
if not ok:
    failed.append(("disconnected_bright_blocks", "NOT_PHASE", got, debug))


# Regression: high bright coverage/span with a deliberate center gap must not
# become PHASE merely from end-to-end span + central average density.
bgra = bytearray(bytes((0,0,0,255))*(w*h))
# broad left/right blocks plus near-center side blocks, but leave bin ~32 dark
rect(int(w*.10),int(h*.15),int(w*.43),int(h*.85),(220,220,220))
rect(int(w*.57),int(h*.15),int(w*.90),int(h*.85),(220,220,220))
got,debug=classifier.classify_bgra(bytes(bgra),w,h)
ok=got!=PHASE
print(f"{'bright_center_gap':32s} expected=NOT_PHASE  got={got:10s} {'OK' if ok else 'NG'}")
if not ok: failed.append(("bright_center_gap","NOT_PHASE",got,debug))


# A PHASE-like cyan shape on a bright combat-like background must not be PHASE.
phase_raw, pw, ph = read_ppm(
    Path(__file__).parent / "fixtures" / "phase_win_01.ppm"
)
bright = bytearray(phase_raw)
for i in range(pw * ph):
    j = i * 4
    b, g, r = bright[j], bright[j+1], bright[j+2]
    is_cyan = (
        g > 120 and b > 120
        and (g-r) > 25 and (b-r) > 20
        and abs(g-b) < 70
    )
    if not is_cyan:
        bright[j:j+4] = bytes((120,120,120,255))
got, debug = classifier.classify_bgra(bytes(bright), pw, ph)
ok = got != PHASE
print(
    f"{'phase_shape_on_bright_gameplay':32s} "
    f"expected=NOT_PHASE  got={got:10s} {'OK' if ok else 'NG'}"
)
if not ok:
    failed.append((
        "phase_shape_on_bright_gameplay", "NOT_PHASE", got, debug
    ))

# Regression for the real v17 failure: final YOU WIN plus stray cyan pixels
# far left/right must remain FINAL_WIN. Global min/max span becomes PHASE-like,
# but the centered colored-text cluster remains final-sized.
raw, rw, rh = read_ppm(Path(__file__).parent / "fixtures" / "final_win_01.ppm")
noisy = bytearray(raw)
for yy in range(max(1, rh//3), min(rh, rh//3 + max(2, rh//6))):
    for xx in list(range(max(0, rw//8), min(rw, rw//8+8))) + list(range(max(0, rw*7//8-8), min(rw, rw*7//8))):
        j=(yy*rw+xx)*4
        noisy[j:j+4]=bytes((220,220,40,255))
got, debug = classifier.classify_bgra(bytes(noisy), rw, rh)
ok = got == FINAL_WIN
print(f"{'final_win_with_side_cyan_noise':32s} expected=FINAL_WIN  got={got:10s} {'OK' if ok else 'NG'}")
if not ok:
    failed.append(("final_win_with_side_cyan_noise", FINAL_WIN, got, debug))

# Keep glyphs fixed while arena lighting changes. These transformed controls
# exercise both colours, the dark margin requirement and the PHASE veto.
def lit_background(source, width, height, lower_gray=30, peripheral=False):
    changed = bytearray(source)
    for y in range(height):
        for x in range(width):
            j = (y * width + x) * 4
            b, g, r = changed[j:j+3]
            cyan = g > 120 and b > 120 and g-r > 25 and b-r > 20 and abs(g-b) < 70
            red = r > 130 and r-g > 35 and r-b > 45 and g > 50
            if not (cyan or red):
                gray = lower_gray if y >= height * 3 // 4 else 90
                if peripheral and x < width * 66 // 100:
                    gray = lower_gray
                changed[j:j+4] = bytes((gray, gray, gray, 255))
    return bytes(changed)

for name, expected in (("final_win_01.ppm", FINAL_WIN), ("final_loss_01.ppm", FINAL_LOSS)):
    pixels, width, height = read_ppm(Path(__file__).parent / "fixtures" / name)
    lit = lit_background(pixels, width, height, peripheral=True)
    got, debug = classifier.classify_bgra(lit, width, height)
    assert not debug["result_band_like"] and debug["lower_band_like"], debug
    assert got == expected, (name, got, debug)
    # Even a dark lower margin cannot validate an otherwise bright field.
    got, debug = classifier.classify_bgra(lit_background(pixels, width, height), width, height)
    assert got not in (FINAL_WIN, FINAL_LOSS, FINAL_DRAW), (name, got, debug)
    # An all-bright background cannot pass merely because the letters match.
    got, debug = classifier.classify_bgra(lit_background(pixels, width, height, 90), width, height)
    assert got not in (FINAL_WIN, FINAL_LOSS, FINAL_DRAW), (name, got, debug)
    # A black lower margin carries no visible band information.
    got, debug = classifier.classify_bgra(lit_background(pixels, width, height, 0), width, height)
    assert got not in (FINAL_WIN, FINAL_LOSS, FINAL_DRAW), (name, got, debug)

for name in ("phase_win_01.ppm", "phase_loss_01.ppm"):
    pixels, width, height = read_ppm(Path(__file__).parent / "fixtures" / name)
    got, debug = classifier.classify_bgra(lit_background(pixels, width, height), width, height)
    assert got not in (FINAL_WIN, FINAL_LOSS, FINAL_DRAW), (name, got, debug)

print("Lighting recovery: WIN/LOSS, missing/black margin and PHASE controls passed.")

# A neutral PHASE prefix must still veto a near-perfect final-like substring
# even when the global dark-band test fails. Preserve the white prefix while
# brightening the background, rather than deleting the evidence in the test.
prefix, width, height = read_ppm(Path(__file__).parent / "fixtures" / "phase_white_prefix_synthetic.ppm")
for level in (0, 40, 79, 80, 81, 120, 125, 126, 220):
    for peripheral, backdrop in [(False, 90)] + [(True, v) for v in (30, 34, 35, 40, 50, 60)]:
        if peripheral and level == backdrop:
            # Equal paint erases the prefix against its own background. This
            # combination cannot serve as evidence of a visible PHASE word.
            continue
        lit = bytearray(lit_background(prefix, width, height, peripheral=peripheral))
        if peripheral:
            for y in range(height * 3 // 4):
                for x in range(width * 18 // 100, width * 34 // 100):
                    j = (y * width + x) * 4
                    lit[j:j+4] = bytes((backdrop, backdrop, backdrop, 255))
        for j in range(0, len(prefix), 4):
            b, g, r = prefix[j:j+3]
            if min(b, g, r) > 125 and max(b, g, r)-min(b, g, r) < 42:
                lit[j:j+4] = bytes((level, level, level, 255))
        got, debug = classifier.classify_bgra(bytes(lit), width, height)
        if not debug["result_band_like"]:
            assert got not in (FINAL_WIN, FINAL_LOSS, FINAL_DRAW), (level, peripheral, backdrop, got, debug)

# Leading texture cannot swallow preserved PHASE text in a taller profile.
# Move full-height narrow stripes across the leading region, and also cover
# the whole region; repaint the original prefix last so it stays visible.
for stripe_fraction in (0.01, 0.05, 0.10, 0.20, 0.50, 1.0):
    for stripe_position in (0.0, 0.25, 0.50, 0.75):
        lit = bytearray(lit_background(prefix, width, height, peripheral=True))
        x0, x1 = width * 18 // 100, width * 34 // 100
        stripe_start = x0 + int((x1-x0) * stripe_position)
        stripe_end = min(x1, stripe_start + max(1, int((x1-x0) * stripe_fraction)))
        for y in range(height):
            for x in range(x0, x1):
                gray = 40 if y < height * 3 // 4 else 30
                if stripe_start <= x < stripe_end:
                    gray += 10
                j = (y * width + x) * 4
                lit[j:j+4] = bytes((gray, gray, gray, 255))
        for j in range(0, len(prefix), 4):
            b, g, r = prefix[j:j+3]
            if min(b, g, r) > 125 and max(b, g, r)-min(b, g, r) < 42:
                lit[j:j+4] = bytes((79, 79, 79, 255))
        got, debug = classifier.classify_bgra(bytes(lit), width, height)
        assert not debug['result_band_like'], debug
        assert got not in (FINAL_WIN, FINAL_LOSS, FINAL_DRAW), (stripe_fraction, stripe_position, got, debug)

# Crossing lighting stripes may suppress relative contrast on a few rows.
# The existing normalized gap handling must retain independent text support.
for band_gray, variation in ((gray, variant) for gray in (74, 75, 79)
                             for variant in ('uniform', 'rows', 'checker')):
    for band_height in (1, 2, 4, 6, 8, 10):
        lit = bytearray(lit_background(prefix, width, height, peripheral=True))
        x0, x1 = width * 18 // 100, width * 34 // 100
        for y in range(height):
            for x in range(x0, x1):
                gray = 40 if y < height * 3 // 4 else 30
                if x < x0 + max(1, (x1-x0) * 5 // 100):
                    gray += 10
                if height // 2 <= y < height // 2 + band_height:
                    gray = band_gray
                j = (y * width + x) * 4
                lit[j:j+4] = bytes((gray, gray, gray, 255))
        for j in range(0, len(prefix), 4):
            b, g, r = prefix[j:j+3]
            if min(b, g, r) > 125 and max(b, g, r)-min(b, g, r) < 42:
                py, px = divmod(j // 4, width)
                gray = 79 if variation == 'uniform' else 77 + ((py + (px if variation == 'checker' else 0)) % 2)
                lit[j:j+4] = bytes((gray, gray, gray, 255))
        got, debug = classifier.classify_bgra(bytes(lit), width, height)
        assert got not in (FINAL_WIN, FINAL_LOSS, FINAL_DRAW), (band_gray, band_height, variation, got, debug)

# A same-luminance stripe may connect the prefix to full-height background.
# Slight colour cast must not make the still-visible prefix disappear either.
for prefix_bgr in ((79, 79, 79), (100, 70, 58), (104, 70, 54)):
    for band_gray in (79, 80, 90):
        for band_height in (1, 2, 4):
            lit = bytearray(lit_background(prefix, width, height, peripheral=True))
            x0, x1 = width * 18 // 100, width * 34 // 100
            for y in range(height):
                for x in range(x0, x1):
                    gray = 40 if y < height * 3 // 4 else 30
                    if x < x0 + max(1, (x1-x0) * 5 // 100):
                        gray = 79
                    if height // 2 <= y < height // 2 + band_height:
                        gray = band_gray
                    j = (y * width + x) * 4
                    lit[j:j+4] = bytes((gray, gray, gray, 255))
            for j in range(0, len(prefix), 4):
                b, g, r = prefix[j:j+3]
                if min(b, g, r) > 125 and max(b, g, r)-min(b, g, r) < 42:
                    lit[j:j+4] = bytes((*prefix_bgr, 255))
            got, debug = classifier.classify_bgra(bytes(lit), width, height)
            assert got not in (FINAL_WIN, FINAL_LOSS, FINAL_DRAW), (prefix_bgr, band_gray, band_height, got, debug)

# Multiple stripes can fragment horizontal support without removing the
# visible prefix. Area support must retain both equal and unequal luminances.
for foreground in (77, 79):
    for period in (8, 12, 18, 24, 27, 30, 36, 48):
        for shift in (0, period//4, period//2, 3*period//4):
            lit = bytearray(lit_background(prefix, width, height, peripheral=True))
            x0, x1 = width * 18 // 100, width * 34 // 100
            for y in range(height):
                for x in range(x0, x1):
                    gray = (40 if y < height * 3 // 4 else 30) + 39 * int((x-x0+shift) % period < period//2)
                    j = (y * width + x) * 4
                    lit[j:j+4] = bytes((gray, gray, gray, 255))
            for j in range(0, len(prefix), 4):
                b, g, r = prefix[j:j+3]
                if min(b, g, r) > 125 and max(b, g, r)-min(b, g, r) < 42:
                    lit[j:j+4] = bytes((foreground, foreground, foreground, 255))
            got, debug = classifier.classify_bgra(bytes(lit), width, height)
            assert got not in (FINAL_WIN, FINAL_LOSS, FINAL_DRAW), (foreground, period, shift, got, debug)
            striped = bytes(lit)

            # Four unrelated pixels must not dilute the unchanged PHASE text.
            # The independent review reproduced false WIN for period24/shift12
            # and period30/shift7 with these sparse outlying valley columns.
            prefix_columns = {j // 4 % width for j in range(0, len(prefix), 4)
                              if min(prefix[j:j+3]) > 125
                              and max(prefix[j:j+3])-min(prefix[j:j+3]) < 42}
            valleys = [x for x in range(x0, x1) if x not in prefix_columns
                       and (x-x0+shift) % period >= period//2]
            assert len(valleys) >= 2
            for x, ys in ((valleys[0], (3, 32)), (valleys[-1], (17, 46))):
                for y in ys:
                    j = (y * width + x) * 4
                    lit[j:j+4] = bytes((foreground, foreground, foreground, 255))
            got, debug = classifier.classify_bgra(bytes(lit), width, height)
            assert got not in (FINAL_WIN, FINAL_LOSS, FINAL_DRAW), ('outliers', foreground, period, shift, got, debug)

            # Independent review also reproduced false WIN after two thin
            # crossbars outside every original prefix stroke stretched all
            # column extents. The visible prefix itself is unchanged.
            for background in (striped, bytes(lit)):
                bars = bytearray(background)
                for y in (0, height-1):
                    for x in range(x0, x1):
                        j = (y * width + x) * 4
                        bars[j:j+4] = bytes((foreground, foreground, foreground, 255))
                got, debug = classifier.classify_bgra(bytes(bars), width, height)
                assert got not in (FINAL_WIN, FINAL_LOSS, FINAL_DRAW), ('outer crossbars', foreground, period, shift, got, debug)

# Red mask pixels can themselves be dark. Background density removes the
# same pixels from numerator and denominator; do not inflate the band score.
pixels, width, height = read_ppm(Path(__file__).parent / "fixtures" / "final_loss_01.ppm")
lit = bytearray(lit_background(pixels, width, height, peripheral=True))
for j in range(0, len(lit), 4):
    b, g, r = lit[j:j+3]
    if r > 130 and r-g > 35 and r-b > 45 and g > 50 and (j // 4 // width) % 2 == 0:
        lit[j:j+4] = bytes((0, 51, 131, 255))
got, debug = classifier.classify_bgra(bytes(lit), width, height)
background = []
for j in range(0, len(lit), 4):
    b, g, r = lit[j:j+3]
    if not (r > 130 and r-g > 35 and r-b > 45 and g > 50):
        background.append((29*b + 150*g + 77*r) >> 8)
assert abs(debug["loss_background_dark_ratio"] - sum(v < 80 for v in background)/len(background)) < 1e-12
assert got == FINAL_LOSS, (got, debug)

for width, height in ((1, 1), (2, 2), (4, 1)):
    got, _ = classifier.classify_bgra(bytes((220, 220, 40, 255))*(width*height), width, height)
    assert got not in (FINAL_WIN, FINAL_LOSS)

if failed:
    print("\nFAILED")
    for item in failed:
        print(item)
    raise SystemExit(1)

print("\nAll synthetic-input classifier tests passed.")

# Template schema regression. The bad template file lives in a temporary
# directory that is removed afterwards; a failed removal fails the run.
with tempfile.TemporaryDirectory() as bad_dir:
    bad_path = Path(bad_dir) / "bad_templates.json"
    bad_path.write_text(json.dumps({
        "version": 3,
        "bins_x": 64,
        "bins_y": 16,
        "grid_x": 32,
        "grid_y": 8,
        "templates": {
            "final_win": [0.0],
            "final_loss": [0.0] * 80,
            "phase_win": [0.0] * 80,
            "phase_loss": [0.0] * 80,
        },
        "grid_templates": {
            "final_win": [0.0] * 256,
            "final_loss": [0.0] * 256,
            "phase_win": [0.0] * 256,
            "phase_loss": [0.0] * 256,
        },
        "draw_grid_template": [0.0] * 256,
    }), encoding="utf-8")
    try:
        ResultClassifier(bad_path)
    except TemplateError:
        print("template schema validation: OK")
    else:
        raise AssertionError("invalid template length was accepted")
