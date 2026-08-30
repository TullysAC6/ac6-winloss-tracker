import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from result_detector import (
    CLEAR,
    PHASE,
    NON_CLEAR,
    FINAL_WIN,
    FINAL_LOSS,
    FINAL_DRAW,
    ResultStateMachine,
)


def obs(sm, state, now):
    return sm.observe(
        state,
        confirm_hits=2,
        clear_hits_required=3,
        cooldown_seconds=5.0,
        now=now,
    )


def assert_false(x, msg):
    if x:
        raise AssertionError(msg)


def assert_true(x, msg):
    if not x:
        raise AssertionError(msg)


# Startup on a still-visible final must not count; CLEAR x3 required.
sm = ResultStateMachine()
assert_false(sm.armed, "startup must be disarmed")
assert obs(sm, FINAL_WIN, 100.0) is None
assert obs(sm, FINAL_WIN, 100.8) is None
assert_false(sm.armed, "old final must not arm")
assert obs(sm, CLEAR, 101.6) is None
assert obs(sm, CLEAR, 102.4) is None
assert_false(sm.armed, "only two CLEAR frames")
assert obs(sm, CLEAR, 103.2) is None
assert_true(sm.armed, "third CLEAR should arm")
assert obs(sm, FINAL_WIN, 104.0) is None
assert obs(sm, FINAL_WIN, 104.8) == "win"
print("startup / initial CLEAR gate: OK")

# PHASE and NON_CLEAR can never re-arm.
sm.external_mutation(now=110.0)
for i in range(8):
    assert obs(sm, PHASE, 116.0 + i) is None
assert_false(sm.armed, "PHASE re-armed detector")
for i in range(8):
    assert obs(sm, NON_CLEAR, 126.0 + i) is None
assert_false(sm.armed, "NON_CLEAR re-armed detector")
print("PHASE/NON_CLEAR never re-arm: OK")

# Foreground loss resets a partial startup CLEAR sequence.
sm.reset_unarmed()
obs(sm, CLEAR, 200.0)
obs(sm, CLEAR, 200.8)
sm.note_foreground(False)
obs(sm, CLEAR, 201.6)
assert_false(sm.armed, "foreground loss did not reset clear_hits")
obs(sm, CLEAR, 202.4)
obs(sm, CLEAR, 203.2)
assert_true(sm.armed, "fresh three CLEAR frames should arm")
print("foreground loss resets CLEAR sequence: OK")

# Disable -> enable is represented by reset_unarmed: all candidate/lock state vanishes.
sm.candidate = "win"
sm.candidate_hits = 1
sm.clear_hits = 2
sm.armed = True
sm.post_result_lock = True
sm.post_result_clear_since = 123.0
sm.reset_unarmed()
assert_false(sm.armed, "disable/enable reset must be disarmed")
assert sm.candidate is None and sm.candidate_hits == 0 and sm.clear_hits == 0
assert_false(sm.post_result_lock, "disable/enable reset left post-result lock set")
assert sm.post_result_clear_since is None
print("disable/enable reset: OK")

# Regression: one accepted WIN must not be counted a second time just because
# the result animation produces several CLEAR frames during cooldown and then
# shows FINAL_WIN again.
sm = ResultStateMachine()
for t in (300.0, 300.8, 301.6):
    obs(sm, CLEAR, t)
assert_true(sm.armed, "setup failed to arm")
assert obs(sm, FINAL_WIN, 302.4) is None
assert obs(sm, FINAL_WIN, 303.2) == "win"
sm.external_mutation(now=303.2)
# Old implementation banked these CLEAR frames and could re-arm at t>=308.2.
for t in (304.0, 304.8, 305.6, 306.4, 307.2):
    assert obs(sm, CLEAR, t) is None
for t in (308.8, 309.6, 310.4):
    assert obs(sm, FINAL_WIN, t) is None
assert_false(sm.armed, "same WIN banner re-armed after transient CLEAR frames")
print("same-result duplicate suppression: OK")

# Regression: a LOSS sequence must not later count a spurious WIN from the same
# result animation / menu transition.
sm = ResultStateMachine()
for t in (400.0, 400.8, 401.6):
    obs(sm, CLEAR, t)
assert obs(sm, FINAL_LOSS, 402.4) is None
assert obs(sm, FINAL_LOSS, 403.2) == "loss"
sm.external_mutation(now=403.2)
for t in (404.0, 404.8, 405.6, 406.4, 407.2, 408.0):
    assert obs(sm, CLEAR, t) is None
assert obs(sm, FINAL_WIN, 409.0) is None
assert obs(sm, FINAL_WIN, 409.8) is None
assert_false(sm.armed, "LOSS sequence was allowed to add a WIN")
print("LOSS -> false WIN conflict suppression: OK")

# A genuine next match must become detectable after cooldown + a continuous
# stable CLEAR interval. CLEAR time before cooldown expiry does not count.
sm = ResultStateMachine()
sm.external_mutation(now=500.0)
for t in (501.0, 502.0, 503.0, 504.0):
    assert obs(sm, CLEAR, t) is None
assert_false(sm.armed, "CLEAR during cooldown must not arm")
# Start stable CLEAR after cooldown. At least 5 seconds are required.
for t in (505.0, 506.0, 507.0, 508.0, 509.0):
    assert obs(sm, CLEAR, t) is None
assert_false(sm.armed, "post-result stable CLEAR interval was too short")
assert obs(sm, CLEAR, 510.0) is None
assert_true(sm.armed, "stable next-match CLEAR period did not re-arm")
assert obs(sm, FINAL_WIN, 511.0) is None
assert obs(sm, FINAL_WIN, 511.8) == "win"
print("next-match re-arm after stable CLEAR: OK")

# Any non-CLEAR frame breaks the post-result stable-clear timer.
sm = ResultStateMachine()
sm.external_mutation(now=600.0)
for t in (605.0, 606.0, 607.0, 608.0):
    obs(sm, CLEAR, t)
assert obs(sm, PHASE, 608.5) is None
for t in (609.0, 610.0, 611.0, 612.0, 613.0):
    obs(sm, CLEAR, t)
assert_false(sm.armed, "PHASE did not reset post-result clear timer")
assert obs(sm, CLEAR, 614.0) is None
assert_true(sm.armed, "fresh stable CLEAR after PHASE did not re-arm")
print("post-result CLEAR must be continuous: OK")

# Undo/manual correction: still-visible final cannot immediately re-count.
sm.after_undo(now=700.0)
assert obs(sm, FINAL_WIN, 706.0) is None
assert obs(sm, FINAL_WIN, 706.8) is None
assert_false(sm.armed, "undo exposed stale final")
print("undo stale-final protection: OK")



# Precision guard: even if some earlier menu frames happened to arm the
# detector, a result-looking frame that appears long after the last gameplay
# CLEAR must not be accepted.
sm = ResultStateMachine()
for t in (800.0, 800.8, 801.6):
    obs(sm, CLEAR, t)
assert_true(sm.armed, "setup failed to arm stale-final test")
# No gameplay-safe CLEAR for >4 seconds, then a false result-like menu frame.
assert obs(sm, FINAL_WIN, 806.0) is None
assert obs(sm, FINAL_WIN, 806.8) is None
assert_false(sm.candidate_hits, "stale result became a candidate")
print("recent-gameplay final guard: OK")


# DRAW is a terminal no-count result, but v22 requires confirmation. A single
# DRAW-looking combat frame must not terminate the match; two confirmed DRAW
# polls do, after which later false WIN-like frames remain locked out.
sm = ResultStateMachine()
for t in (900.0, 900.8, 901.6):
    obs(sm, CLEAR, t)
assert_true(sm.armed, "setup failed to arm DRAW test")
assert obs(sm, FINAL_DRAW, 902.4) is None
assert_true(sm.armed, "single DRAW candidate incorrectly terminated match")
assert_false(sm.post_result_lock, "single DRAW candidate installed terminal lock")
# A normal combat/non-result frame cancels the one-off DRAW candidate.
assert obs(sm, NON_CLEAR, 903.2) is None
assert sm.candidate is None and sm.candidate_hits == 0
# Genuine DRAW persists and is confirmed on two polls.
assert obs(sm, FINAL_DRAW, 904.0) is None
assert obs(sm, FINAL_DRAW, 904.8) == "draw"
assert_false(sm.armed, "confirmed DRAW did not lock detector")
assert_true(sm.post_result_lock, "confirmed DRAW did not enter terminal lock")
for t in (905.6, 906.4, 907.2, 908.0, 908.8, 909.6):
    assert obs(sm, FINAL_WIN, t) is None
assert_false(sm.armed, "false WIN after confirmed DRAW re-armed detector")
print("DRAW confirmation + terminal no-count lock: OK")

# Startup/stale DRAW must not lock an unarmed detector.
sm = ResultStateMachine()
assert obs(sm, FINAL_DRAW, 920.0) is None
assert obs(sm, FINAL_DRAW, 920.8) is None
assert_false(sm.post_result_lock, "stale startup DRAW installed lock")
assert_false(sm.armed, "stale startup DRAW armed detector")
print("stale DRAW cannot lock unarmed detector: OK")



# Public precision mode: activity may arm initial dark gameplay, but after a
# counted result it must NEVER release the terminal lock. Stable CLEAR is required.
sm = ResultStateMachine()
# Initial dark gameplay can still arm to preserve the missed-WIN fix.
assert sm.observe(NON_CLEAR, 2, 3, 5.0, now=1006.0, gameplay_activity=True) is None
assert_false(sm.armed, "one gameplay-activity hit must not arm")
assert sm.observe(NON_CLEAR, 2, 3, 5.0, now=1006.8, gameplay_activity=True) is None
assert_true(sm.armed, "initial dark gameplay activity did not arm")
# Once a result mutation locks the detector, activity alone cannot re-arm.
sm.external_mutation(now=1010.0)
for t in (1016.0, 1016.8, 1017.6, 1018.4):
    assert sm.observe(NON_CLEAR, 2, 3, 5.0, now=t, gameplay_activity=True) is None
    assert_false(sm.armed, "activity released post-result lock")
    assert_true(sm.post_result_lock, "post-result lock disappeared without CLEAR")
print("activity cannot bypass post-result CLEAR lock: OK")

print("\nAll state-machine tests passed.")
