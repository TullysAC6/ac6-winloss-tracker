# Owner-confirmed bright-result-band WIN miss — 2026-10-03

The owner authorized product repair after the real #15-5 T3 failed. This
supersedes the earlier diagnostic-only restriction for this narrowly scoped
classifier repair. It does not authorize merge, Acceptance, release or #15-6.
Branch from main `02e0de101df0b5289c3057ae1ea84b8bb24f5eb7`; preserve PR65
exact `057d5532c2737f07853127d015f134bad04395ec` unchanged and OPEN/DRAFT.

## Root cause and retained evidence

Two consecutive natural result ROIs show YOU WIN. First global dark ratio
0.89773, final profile/grid 0.99462/0.99227: FINAL_WIN, one hit. Next global
dark ratio 0.689965, profile/grid 0.99433/0.98981, valid final X/Y geometry:
the global >=0.72 band test fails, returns CLEAR and erases that candidate.
No result callback or history rejection occurred. Main and PR65 reproduce the
same decisions. Capture-unavailable arrived approximately 12 seconds later.
The upstream lighting mechanism is not established; earlier misses without
decisive pixels remain UNKNOWN. The failed T3 remains FAIL.

Three exact full production 1152×75 ROIs are now T1 assets. Only BGRA→RGB
channel copying occurred; no crop beyond the original production ROI, resizing
or colour correction. No full diagnostic archive, user database or full-screen
capture is committed. The final pair is consecutive real evidence; arming and
gap/CLEAR/repetition/UNDO test sequences are explicitly composed controls.

## Repair boundary

Keep the original global band path unchanged. An additional WIN/LOSS path
requires all existing final X/Y geometry, color masks, template thresholds,
final-versus-PHASE grid margin and exclusive result agreement. It additionally
requires profile AND grid >=0.95, a lower-quarter margin >=90% dark at the
existing gray<80 definition, visible margin mean 8..90 and global mean 8..90,
with no structurally matching white PHASE prefix. Also require >=90% dark
leading space (ROI X18..34%, Y25..75%) and >=72% dark full-scene background
after removing the candidate's own colour pixels from both numerator and
denominator. Red glyph pixels can be dark and are explicitly subtracted;
at least half the ROI must remain background, and empty regions abstain.
The failed sample's background ratio is 73.15%, leading space97.30%, and lower
margin is 91.61% dark; the pre-result gameplay is 35.89% dark there. This spatial
check targets the visible dark strip, rather than lowering global thresholds
to fit 68.99%. All-bright and black lower margins are rejected even with exact
WIN/LOSS letters. Existing PHASE and DRAW classification predicates are kept.

Independent review rejected the first lower-margin-only implementation: a
neutral PHASE prefix at gray120/125 fell below the old bright-mask threshold
and admitted a false WIN. That NO-GO is preserved. The stronger background and
leading-space checks reject those cases. A recovery-only relative-contrast
check also rejects text-height neutral leading clusters even below gray80:
compare each leading column to its lower-margin luminance, require contrast
>=5, and apply the existing normalized final-text vertical geometry. Sweeps
include gray0/40/79/80/81/120/125/126/220 on both broad-bright and peripheral-lit
backgrounds; the clearly visible preserved prefix is never recovered as WIN.
Conservative abstention on legitimate textured frames is a T3 sensitivity
limitation, not justification to lower the guards.

The existing pixel pass collects bounded scalar counters. Only otherwise
strong recovery candidates inspect the narrow leading strip again, allocating
a mask16% of the ROI size plus one column-baseline list. No extra capture,
I/O, process, thread, dependency or polling change. The AST-pinned classifier
and new helper baseline are intentionally advanced for this repair; the original
state-machine and detector-loop AST pins and public timing constants remain.
ResultGate, CLEAR re-arming, two consecutive hits, capture-gap/freshness,
authoritative persistence, WGC, installer and #15-5 runtime are untouched.

## Automated and real-machine evidence

Formal T1 adds three ROI and five sequence cases to the unchanged original
53-case corpus: 61 total. Dedicated required tags retain the bright WIN and
two-hit exactly-once regression. T0 constructs WIN/LOSS lighting positives,
missing/black margin negatives, cyan/red PHASE negatives and a preserved white
PHASE-prefix negative. Full T0, T1 and isolated T2 evidence, independent review
and exact-head CI belong to the repair PR; do not inherit PR65's old green CI.

An offline alternating CPU-clock microbenchmark of the same three ROIs after
the stronger guards gave baseline and repair median83.33ms/ROI on the coarse
process clock. This measurement does not establish zero overhead. It is a
bounded local measurement, not AC6 FPS/GPU or real-session performance
acceptance. No new work occurs between detector polls.

T3 is required before any merge because this changes recorded-result eligibility.
After reviewed exact-head CI is green, start a new clean isolated data root on
that exact repair head, confirm ordinary Launcher/Tracker startup, keep metadata
OFF, verify visible lobby/gameplay readiness, play naturally and compare owner
WIN/LOSS outcomes to saved history and result ROIs. Specifically verify WIN
through the bright arena transition, ordinary LOSS, one result per match,
next-match re-arming, startup on an already visible result without counting it,
UNDO without recounting the same visible result, and unavailable/minimized
capture without stale counting. Never change AC6 focus automatically; restore
the game to a visible lobby before establishing capture readiness. Retain
diagnostics for any mismatch and finish by authenticated owned shutdown and
zero child/port/runtime/lock residue. Do not fabricate missing historical rows.

This shared-core repair T3 does not complete #15-5's metadata-ON acquisition,
confirmation, cancel/OFF/DRAW/shutdown and same-match gates. Those require a
separately synced, reviewed and CI-green PR65 candidate followed by its FULL
real-machine T3. #15-5 remains Accepted PENDING.
