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
