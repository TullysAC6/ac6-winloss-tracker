import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from result_detector import (
    CLEAR,
    PHASE,
    FINAL_WIN,
    FINAL_LOSS,
    FINAL_DRAW,
    NON_CLEAR,
    ResultClassifier,
    ResultStateMachine,
    _motion_signature,
    _motion_score,
    _is_gameplay_activity,
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


manifest = json.loads(
    (Path(__file__).parent / "manifest.json").read_text(encoding="utf-8")
)
classifier = ResultClassifier(ROOT / "detector_templates.json")

mapping = {
    "CLEAR": CLEAR,
    "PHASE": PHASE,
    "FINAL_WIN": FINAL_WIN,
    "FINAL_LOSS": FINAL_LOSS,
    "FINAL_DRAW": FINAL_DRAW,
    "NON_CLEAR": NON_CLEAR,
}

failed = []
for rel, expected_name in manifest.items():
    raw, w, h = read_ppm(Path(__file__).parent / rel)
    got, debug = classifier.classify_bgra(raw, w, h)
    expected = mapping[expected_name]
    ok = got == expected
    print(
        f"{Path(rel).name:32s} expected={expected:10s} "
        f"got={got:10s} {'OK' if ok else 'NG'}"
    )
    if not ok:
        failed.append((rel, expected, got, debug))

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

print("\nAll detector classifier tests passed.")

# Template schema regression.
import tempfile
from result_detector import TemplateError
bad_path = Path(tempfile.mkdtemp()) / "bad_templates.json"
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



# User-video DRAW regression: the exact supplied DRAW frames must be a
# terminal no-count class, never FINAL_WIN.
for fixture in (
    "video_draw_1_2.ppm",
    "video_draw_1_6.ppm",
    "video_draw_2_5.ppm",
):
    raw, w, h = read_ppm(Path(__file__).parent / "fixtures" / fixture)
    got, debug = classifier.classify_bgra(raw, w, h)
    if got != FINAL_DRAW:
        raise AssertionError((fixture, FINAL_DRAW, got, debug))
    assert debug["draw_like"]
    assert 0.12 <= debug["draw_cluster"]["span"] <= 0.25
print("user-video DRAW positive-class regression: OK")

# v22 regression: exact combat frames from the two user recordings that v21
# misclassified as FINAL_DRAW must be rejected. Their neutral-white geometry
# can look DRAW-like in 1-D, but their 2-D glyph fingerprint is very different.
for fixture in (
    "video_false_draw_combat_1_4667.ppm",
    "video_false_draw_combat_1_6667.ppm",
    "video_false_draw_combat_1_7667.ppm",
):
    raw, w, h = read_ppm(Path(__file__).parent / "fixtures" / fixture)
    got, debug = classifier.classify_bgra(raw, w, h)
    if got == FINAL_DRAW:
        raise AssertionError((fixture, "NOT_FINAL_DRAW", got, debug))
    assert debug["draw_grid_score"] < 0.75
print("user-video false-DRAW combat regression: OK")

# User-video regression: preserve the real YOU WIN while rejecting the exact
# garage/menu frames that caused the observed second WIN count.  This is the
# primary precision regression for the v19 redesign.
for fixture, expected in (
    ("video_true_final_win_5_9.ppm", FINAL_WIN),
    ("video_false_garage_20_1.ppm", NON_CLEAR),
    ("video_false_garage_menu_21_4.ppm", NON_CLEAR),
    ("video_rank_menu_23_267.ppm", NON_CLEAR),
):
    raw, w, h = read_ppm(Path(__file__).parent / "fixtures" / fixture)
    got, debug = classifier.classify_bgra(raw, w, h)
    if got != expected:
        raise AssertionError((fixture, expected, got, debug))

raw, w, h = read_ppm(
    Path(__file__).parent / "fixtures" / "video_true_final_win_5_9.ppm"
)
_, true_debug = classifier.classify_bgra(raw, w, h)
raw, w, h = read_ppm(
    Path(__file__).parent / "fixtures" / "video_false_garage_20_1.ppm"
)
_, false_debug = classifier.classify_bgra(raw, w, h)
assert true_debug["win_final_grid_score"] >= 0.82
assert false_debug["win_final_grid_score"] < 0.82
assert false_debug["win_y_cluster"]["span"] > 0.70
print("user-video false-WIN regression: OK")


# v21 regression from the user's 2026-08-26 missed-WIN recording.
# The real YOU WIN is classified correctly in v20; the miss happened because
# the preceding dark combat frames were NON_CLEAR, so the state machine never
# armed. Motion-confirmed gameplay must now arm without weakening final-result
# thresholds.
seq = []
prev_sig = None
for fixture in (
    "video_dark_gameplay_0_75.ppm",
    "video_dark_gameplay_1_50.ppm",
    "video_dark_gameplay_2_25.ppm",
):
    raw, w, h = read_ppm(Path(__file__).parent / "fixtures" / fixture)
    state, debug = classifier.classify_bgra(raw, w, h)
    sig = _motion_signature(raw, w, h)
    motion = _motion_score(prev_sig, sig)
    active = _is_gameplay_activity(state, debug, motion)
    seq.append((state, motion, active))
    prev_sig = sig

assert seq[0][0] == NON_CLEAR and not seq[0][2]
assert seq[1][0] == NON_CLEAR and seq[1][2]
assert seq[2][0] == NON_CLEAR and seq[2][2]

sm = ResultStateMachine()
# Baseline sample has no previous motion signature.
sm.observe(NON_CLEAR, 2, 3, 5.0, now=100.75, gameplay_activity=False)
sm.observe(NON_CLEAR, 2, 3, 5.0, now=101.50, gameplay_activity=True)
sm.observe(NON_CLEAR, 2, 3, 5.0, now=102.25, gameplay_activity=True)
assert sm.armed, "dark gameplay activity did not arm detector"
raw, w, h = read_ppm(
    Path(__file__).parent / "fixtures" / "video_missed_final_win_6_00.ppm"
)
state, debug = classifier.classify_bgra(raw, w, h)
assert state == FINAL_WIN
assert sm.observe(state, 2, 3, 5.0, now=106.00, gameplay_activity=False) is None
assert sm.observe(state, 2, 3, 5.0, now=106.75, gameplay_activity=False) == "win"
print("user-video dark-gameplay missed-WIN regression: OK")
