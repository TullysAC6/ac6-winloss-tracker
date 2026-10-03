# Owner-confirmed bright-result-band WIN miss — 2026-10-03

The owner authorized product repair after the real #15-5 T3 failed. The
2026-10-04 continuation specifically authorizes Issue #68 PR publication,
review/fixes/CI, pinned merge, exact-main CI and closure. Issue #68 alone may
use T3 N/A if a fresh independent review agrees that retained real pixels,
canonical negatives and runtime gates establish the repair deterministically.
If specific native evidence is still needed, STOP before separate gameplay.
This supersedes the earlier diagnostic-only/merge restrictions for this repair
only. It does not authorize PR65 sync/T3/merge, Acceptance, release or #15-6.
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
compare each leading column to its lower-margin luminance and independently
compare each row to its own median, require contrast >=5, and apply the
existing normalized final-text vertical geometry. Never merge the two masks:
a uniform upper/lower background change must not swallow the shorter prefix.
The second independent NO-GO established that exact ambiguity in the initial
column-only contrast check; its report and reproduction are retained. Sweeps
include gray0/40/79/80/81/120/125/126/220 on both broad-bright and peripheral-lit
backgrounds including leading backdrops30/34/35/40/50/60; the visibly preserved
prefix is never recovered as WIN. Equal foreground/background paint is not
claimed as visible PHASE evidence.
Conservative abstention on legitimate textured frames is a T3 sensitivity
limitation, not justification to lower the guards.

The existing pixel pass collects bounded scalar counters. Only otherwise
strong recovery candidates inspect the narrow leading strip again, allocating
two masks approximately32% of the ROI size plus bounded column/row lists. No extra capture,
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
the row-relative guard gave baseline72.92ms and repair83.33ms/ROI (+14.3%) on
the coarse process clock; about10.4ms per frame for this result-heavy sample.
The additional strip scans happen only for strong recovery candidates, not
ordinary gameplay. This measurement does not establish zero overhead. It is a
bounded local measurement, not AC6 FPS/GPU or real-session performance
acceptance. No new work occurs between detector polls.

The earlier implementation proposed a separate mandatory #68 gameplay T3 under
the default project rule. The owner's 2026-10-04 instructions supersede that
proposal with the conditional N/A decision above, not automatic N/A. A fresh
review must state whether any specific native behavior remains unverified.
Do not start separate #68 T3 or fabricate historical rows. Retain every old
failure/review and finish all isolated tests with zero owned resource residue.

This shared-core repair T3 does not complete #15-5's metadata-ON acquisition,
confirmation, cancel/OFF/DRAW/shutdown and same-match gates. Those require a
separately synced, reviewed and CI-green PR65 candidate followed by its FULL
real-machine T3. #15-5 remains Accepted PENDING.
