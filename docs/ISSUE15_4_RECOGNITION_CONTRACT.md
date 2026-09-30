# #15-4 offline recognition evidence / fixture foundation

Date: 2026-10-01 JST. Authority: [owner authorization](https://github.com/TullysAC6/ac6-winloss-tracker/issues/15#issuecomment-5921021683).
The [pre-code target checkpoint](https://github.com/TullysAC6/ac6-winloss-tracker/issues/15#issuecomment-5921185412)
was recorded on verified base `d20741fd2903cd75daf9e05b3b8e5f4bdae77c33`.

## Supported boundary

`match_header.recognize_header(roi)` is a dormant, pure positive-only decoder of
the complete English **RANK MATCH: SINGLE** lobby heading. The caller supplies
the native `(80,40,560,100)` crop of the evidenced 1920x1080 layout, as a uint8
BGR or opaque BGRA array, exactly 480x60. It returns immutable category evidence:
`ranked / single / recognized`, version `rank-single-header.v1`, source
`direct_header`. Every other input returns `unknown / unknown / failed` with an
explicit reason. No automatic crop selection or rescaling is claimed.

This does not implement general Ranked/Custom or Single/Team discrimination,
localization, arbitrary resolutions, self Rank, Rating, observation timestamps,
or full-match classification. A negative label means **the crop supplies no
complete supported evidence**, not that the underlying match was not Ranked.
Failed evidence never becomes a Rating change or a result. There is no caller
from startup, capture, detector, server, enrichment or UI; no clock, I/O, worker,
network, persistent state or writes. Future persistence belongs to the Tracker
server and requires separately authorized event identity/time and ownership.

## Actual evidence inspected

The owner's local project is `Desktop/AC6auto/ac6-vision-agent-poc-easy`.
Its actual `learning_data_root.json` points at `C:/AC6_AI_DATA/learning_data`.
Inspected current Analyzer orchestration, `learning/hud/decoder.py`, report and
match JSON for four videos, tactical/shadow reports, `hud_labels.json`,
`hud_review_corrections.json`, reviewed tactical evidence, an existing original
combat frame, and bounded frames extracted directly from the four original MP4s.
No Analyzer or agent was run; source artifacts were not modified.

| Original local video | Existing metadata / findings from pixels |
|---|---|
| `aRnhg9vs-zs` | Native Ranked Single lobby at 225.25 s, S badge, PLACE 14 and RATING 2150; sortie and combat negatives. Title says PS while user metadata says Steam: platform metadata is not truth. |
| `xJ86EUS510w` | Ranked Single lobbies at 414.00 and 718.00 s; S/PLACE/RATING displays 8/1907 and 6/1916. Garage ARENA RANK 01/S is not PvP self Rank. |
| `y86wDex-EQI` | Loading/combat negatives; separate positive at 110.00 s, S/PLACE 1/RATING 2259. Opening media-player UI cannot be treated as game evidence. |
| `WabYwwGakEQ` | Assembly negative at 498.75 s; early heuristic match segments include assembly/AC-test context. A segmentation label does not prove a real PvP match. |

Four original videos and four corresponding Analyzer reports exist; 128 reviewed
tactical documents and zero approved knowledge documents were found. These are
AP/Expansion/tactical labels, not canonical Tracker category/rank/Rating truth.
Actual reports disclose heuristic/circular labels. Existing correction evidence
also shows wrongly transcribed AP digits in downscaled evidence. None was adopted
as expected category truth; title, subtitles, rank_class and inferred battle
context cannot supply it. Original MP4 source IDs/URLs, native frame timestamps,
frame hashes and crop coordinates are recorded per fixture.

## Bounded canonical corpus and truth

Formal T1 contains **11 metadata images**: 4 complete header positives, 6 genuine
unrelated negatives (assembly, sortie, combat, garage, loading), and 1 genuinely
truncated header. These add to the unchanged 25 result images + 17 sequences.
Only tiny lossless PNG crops are committed; no player names, opponent names,
handles, desktop, chat, raw video, credentials or embedded source paths remain.
Original full frames remain in the owner's local evidence directory for review.

Truth was transcribed by Codex visual inspection of original full frames and
unmodified crops before predictions; this is human-verifiable pixel truth,
**not a claim that the owner performed Acceptance**. Records explicitly name the
visual verifier/method, date, direct visible text, screen context, original-frame
SHA-256, source dimensions/time/crop and split. Expected truth and actual outputs
remain separate. Strict loader validation checks schema, input size/hash/decode,
category/truth/check agreement, provenance and correction format. Unknown keys,
versions, fabricated labels/synthetic canonical input and split leakage fail.

Source videos are disjoint between dev (`aRnhg9vs-zs`, `WabYwwGakEQ`) and
validation (`xJ86EUS510w`, `y86wDex-EQI`). Only `metadata.dev-header` supplies
the template. One repeated lobby in validation checks Rating-independent header
stability, not independent statistical sample count. Four positive images are
too small for a population accuracy claim.

Initial exact-bit matching passed dev and negatives but abstained on the first
two validation positives (global scores ~0.938/0.940). These failures are retained
in local `t1-initial` evidence. A fixed one-pixel mutual ink-proximity mechanism
corrected antialiasing sensitivity, with unchanged 0.95 global / 0.90 per-glyph
thresholds and controlled morphology probes. The initial validation positives
were inspected during diagnosis: **they are regression checks, not untouched
holdout measurements after that correction**. A new third-source positive at
110.00 s in `y86wDex-EQI` was first scored after the mechanism was fixed. No
expected label was changed; no accuracy, confidence calibration or broad
generalization is claimed.

Corrections start as an empty bounded list. Any future truth correction requires
original visual evidence, previous truth, reason, verifier/date and evidence hash;
preserve the Git diff and rerun affected gates. Never change truth to suit a
prediction. New fixtures require direct visual review and privacy checks.

## Algorithm and sequence decision

White ink: every channel >=180, channel spread <=50. The reviewed dev crop's
binary mask is packed and embedded as a read-only constant, with an integrity
test that reproduces the extraction from that exact dev asset. No validation
image contributes to the model. Compare nine bounded displacement positions
(-1..1 px), using symmetric precision/recall for ink within one pixel. Require
global agreement >=0.95 **and every separated glyph >=0.90**, including colon
and SINGLE; the numeric scores are not probabilistic confidence. Missing glyphs,
partial crops, garbage, nonopaque BGRA and unsupported geometry abstain.

The Autopilot's multi-frame AP/Expansion decoder uses nonincreasing values,
reset penalties, correlated-frame tempering and max-marginal UNKNOWN. Those
constraints are not justified for categories or Rating. Coarse scan/refinement
helped locate candidate frames only; no temporal carry-forward, monotonic Rating,
default mode, action controller or full-video recognizer was adopted. There is
no sequence decoding in this slice.

## Exact missing-fixture plan

Before extending support, visually verify from genuine originals at least two
distinct sessions/sources per proposed screen/class where available, with one
kept for validation and explicit near-negative/partial evidence:

- **Custom/Team:** actual Custom Single, Custom Team and Ranked Team lobby/menu
  headings, their exact layouts/locales and normal surrounding screens. Gameplay
  with two players alone is not proof of Ranked or Single.
- **Self Rank:** directly identified self panels for UNRANKED and letter tiers,
  especially separate A and A4, plus S. Include opponent/ARENA RANK badges and
  ambiguous/occluded identity as abstention negatives. Never turn S PLACE into a
  new rank tier.
- **Rating:** self-linked pre-S/non-S displays (UNRANKED through A4) and S displays
  separately, original exact value text and contexts (current vs delta/place),
  unreadable/partial values, and genuine transitions only if sequence behavior is
  proposed. A4 is pre-S. The seen S/2150/1907/1916/2259 examples are candidates,
  not a complete value-domain or pre-S contract.
- **Layout breadth:** genuine other resolutions/scales/locales and overlays
  before claiming them. Do not synthesize unavailable Custom/Team/pre-S truth.

## Gates and state

T0 owns pure logic/schema/malformed/UNKNOWN/determinism/dormant-boundary checks.
Formal T1 owns all genuine pixel recognition, in the existing guarded, bounded,
job-owned workers; each worker knows input/adapter but not expected labels.
The existing result corpus/assertions remain. T2 runner ownership/lifecycle is
relevant because the test replay adapter changed; recognition-specific runtime
T2 and real-game T3 are N/A. Full existing CI/source-install gates still apply.
No timing correctness assertion or production timeout/budget was changed.

Resolve Issue #15 / the implementation PR for live review, exact-head/main CI,
merge SHA and gate evidence; this document does not predict those outcomes.
#15-4 Acceptance remains **PENDING**, Released **NO**; stable remains **v1.2.0**.
STOP after #15-4. Production capture/event hooks, short-lived recognizer execution
boundary, server-owned writes, live failure isolation and real-game acceptance
remain separately authorized future work. Season, UI, analytics, opponent-build,
DRAW, ResultGate/detector/CLEAR and release are outside this slice.
