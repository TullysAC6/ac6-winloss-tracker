# #15-6 offline self-rank S evidence / fixture foundation

Date: 2026-10-06 JST. Authority: [owner authorization](https://github.com/TullysAC6/ac6-winloss-tracker/issues/15#issuecomment-6005202373).
The [pre-code evidence checkpoint](https://github.com/TullysAC6/ac6-winloss-tracker/issues/15#issuecomment-6006194783)
was recorded on verified base `ef4af555a5187b370dafea334bac6ab1bac81df4`, with the gate PASS for the boundary below.
Live review, CI, merge and acceptance evidence belongs in the slice PR and [Issue #6](https://github.com/TullysAC6/ac6-winloss-tracker/issues/6).
Public v1.2.0 does not contain this slice. No following slice is authorized.

## Supported boundary

`self_rank.recognize_self_rank(header_roi, panel_roi)` is a dormant, pure, positive-only recognizer of the
**player's own S rank**. It takes two unscaled crops of the **same** frame of the native 1920x1080 English
Ranked Single waiting screen, each a uint8 BGR or opaque BGRA array:

- the #15-4 header ROI `(80,40,560,100)`, 480x60;
- the lobby RANK panel ROI `(600,432,742,600)`, 142x168. It holds the RANK label and the badge only: no
  card, name, or PLACE/RATING field. The badge itself contains the top-100 place number as pixels, which is
  never read.

It returns `self_rank = "S"`, status `recognized`, version `self-rank-s-lobby.v1`, source
`direct_lobby_rank_panel`. Every other input returns `self_rank = None`, status `failed`, with an explicit reason.
A failed result means the crops supply no supported evidence, never that the player is not S. There is no
confidence probability, and no match type/format, Rating, PLACE, opponent rank, pre-S rank or Season output.

The complete `RANK MATCH: SINGLE` header, judged by the accepted #15-4 decoder on the given pixels, is a
**necessary context precondition only**. It is the only evidenced way to exclude Ranked Team lobbies, custom
rooms and every other screen. It is never rank evidence: the #15-4 category output is neither returned nor
used to infer a rank. Rank evidence comes only from the panel.

There is no caller from startup, capture, detector, server, enrichment or UI; no clock, I/O, worker, network,
persistent state or writes. A future acquisition caller must supply both crops from one frame. Persisting the
result (#15-2 `self_rank` with recognition status/version) needs separately authorized runtime work.

## Evidence inspected

Read-only, against the owner's local Analyzer project (`Desktop/AC6auto/ac6-vision-agent-poc-easy`) and its
learning-data root (`C:/AC6_AI_DATA/learning_data`). The four original MP4s `aRnhg9vs-zs` (VP9),
`xJ86EUS510w`, `y86wDex-EQI` and `WabYwwGakEQ` (AV1) are all native 1920x1080 at 60 fps. Frames were decoded
with OpenCV millisecond seeks; all five #15-4 frame hashes reproduce byte-for-byte. Ranked Single lobbies were
located across each whole video with the accepted header decoder as a **candidate locator only**, then
visually verified: 22 segments in `xJ86`, 27 in `y86w`, 18 seek hits in `aRnh`, none in `WabY`. No Analyzer or
agent ran; no source artifact was modified. Analyzer labels, titles, subtitles and platform metadata were not
used as truth. The owner's two reference images (A, UNRANKED) have unknown provenance and are not used.

## Why each positive is the player's own rank

The accepted surface is the RANK panel under MATCH INFO on the screen owner's own Ranked Single waiting screen.
Three distinct sessions, each a different player, show it.

1. **Same frame.** The idle screen (start-matching menu) or the matching screen (matching status and cancel
   prompt) shows the screen owner's own menu, NP and sortie AC, and exactly one card in MATCH INFO. In these
   states no opponent exists.
2. **Lifecycle.** Dense sampling of every source through idle → matching → match found (dimmed) → loading
   shows that the card and panel never change and no opponent identity appears in the lobby.
3. **Intro link.** The lobby card later appears in the intro beside the AC identical to SORTIE AC INFO, and
   the combat HUD AP equals the lobby AC SPEC AP. The opponent appears only on a different intro card. The
   ALPHA/BETA slot is not a self signal: the player is ALPHA in one source and BETA in the other two.

Not used as evidence: timing or adjacency alone, Analyzer output, metadata, card badges, PLACE or RATING
values, and (per owner instruction) the story-mode mercenary license's ARENA RANK and HUNTER CLASS.

## Canonical corpus and truth

Formal T1 adds **22 rank records** (44 lossless PNGs, 1,161,591 bytes) to the unchanged 61 cases:
**10 positives** under `ranks/s_rank/` and **12 genuine negatives** under `ranks/negatives/`.

| Split | Sources | Positives | Negatives |
|---|---|---|---|
| dev | `aRnhg9vs-zs`, `WabYwwGakEQ` | 4: idle and matching, places 14/15/08, one after the player changed card and AC | 8: dimmed match start, system menu, garage license, opponent intro card, custom room, custom result, a truncated crop of a genuine panel, a lit-header cross-fade into SYSTEM |
| validation | `xJ86EUS510w`, `y86wDex-EQI` | 6: idle and matching, places 08/05/01, two sessions | 4: dimmed match start, cross-faded lobby, Ranked mode select, opponent intro |

- Each record names the original video, timestamp, both crop rectangles, the original-frame SHA-256, crop
  hashes, visible truth, the self-identity reasoning, verifier/method/date, split and corrections.
- Truth `S` is transcribed from visible UI; truth `unknown` means no supported evidence in these crops.
  Expected truth and actual outputs stay separate; the worker receives pixels and the adapter only.
- Every record also declares where a negative must abstain, from what the pixels show: `input` (crop of the
  wrong geometry), `context` (no complete lit header) or `panel` (complete lit header over a panel that is
  not a clean RANK panel). The comparator enforces it, so a panel-targeted negative cannot pass at the
  header gate.
- Source videos never cross splits, within or across families (`match_metadata` uses the same split).
  `WabYwwGakEQ` contributes negatives only. The player cards that are screen owners in `xJ86` and `y86w`
  (validation) also appear on `WabY` custom screens (dev), outside the crops; one dev panel crop shows the
  edge of the `xJ86` player's nameplate decoration. No S pixels come from `WabY`.
- Exactly one pinned dev positive (`ranks.dev-s-matching`) is the template source. The truncated negative
  is a constructed truncated crop of that genuine frame; its header crop is byte-identical to the template's
  (the #15-4 `dev-partial` precedent).
- Privacy: minimal game-UI crops only; no player or opponent name, handle, emblem, AC name, NP, match ID,
  date, chat, desktop or full frame. Original full frames remain local for review.

Truth was transcribed by Claude Code (Opus 5.5) visual inspection of original full frames and crops. This is
human-verifiable pixel truth, **not owner Acceptance**. Corrections follow the #15-4 rule: original evidence,
previous truth, reason, verifier/date and evidence hash; never change truth to suit a prediction.

## Initial validation failure and correction

The first formal T1 run (78/80) was retained as local evidence. Dev, `xJ86` and every negative passed; both
`y86w` positives abstained at the glyph stage. Diagnosis found a **dev-side defect**: the original glyph ink
(`R >= 150`) captured only the lower bowl of the S, because the glyph is shaded top to bottom. `y86w` uses a
different tone curve (crushed blacks, darker upper strokes), and its lit strokes overlap the dev source's
*dimmed* lobby levels, so no absolute-intensity glyph threshold can serve all sources.

The glyph stage now uses zero-mean normalized correlation (NCC) of the red channel inside the registered S
box against the pinned dev crop; it is invariant to gain and offset. **The 0.95 constant now applies to a
different statistic**: the original was a one-pixel-proximity F1 of ink masks. In effect it is a new
threshold, chosen after diagnosing the `y86w` validation frames. Every other stage, threshold and expected
label is unchanged.

**v1 has no untouched validation source.** The four earlier validation positives were inspected during
diagnosis: they are regression checks. The two "fresh" validation positives (`val-s-fresh-idle`,
`val-s-third-source-fresh`) were visually verified and declared before they were first scored, after the
correction, and both pass. But they re-sample the same two diagnosed sessions, so they add little independent
evidence that the corrected stage generalizes. The `xJ86` layout offset (+2 px x, +1–2 px y) was also observed
during truth verification, before the registration window was fixed. Ten positives support no accuracy claim.

## Lit-header transitions, recall and the panel-stage negative

The independent reviewer scanned `aRnh`, `xJ86` and `y86w` end to end at 2 Hz. Every sample whose header
the gate recognized was a Ranked Single lobby showing the screen owner's S. No other screen passed the gate,
and no genuine lit-header panel in any source shows anything but the player's S badge. Header-lit samples:

| Source | Recognized S | Abstained |
|---|---|---|
| `aRnh` | 1,175 | 2 |
| `xJ86` | 1,649 | 1 |
| `y86w` | 734 | 30 |

These abstentions are **recall misses on the true S**, not saves. They cluster at lobby fades and cuts. On
`y86w` some near-steady frames also fall just below the absolute frame-contrast or label thresholds, because
of its tone curve. Lit-header pure fades may be recognized (still the true S) or abstain. Transitions are
therefore **not** guaranteed to abstain. Only these are:

- dimmed or cross-faded lobbies whose header is not complete and lit (context gate);
- one genuine lit-header **cross-fade into another screen** whose content visibly overlays the lobby
  (`ranks.dev-crossfade-system`, panel stage).

That record is a **post-hoc addition** after independent review, chosen by an objective rule (the last
lit-header frame before the SYSTEM screen) and labelled from the visible overlay. The recognizer had been run
on nearby frames during diagnosis. The validation sources contain no lit-header frame with foreign content:
their lobby exits are hard cuts, and the `xJ86` cross-fade completes its header only after the overlay has
cleared. So **no validation panel-stage negative exists**, and that is recorded as an evidence gap.

Per-stage margins on the T1 positives:

| Stage | Threshold | `aRnh` (dev) | `xJ86` | `y86w` |
|---|---|---|---|---|
| Frame contrast (ring − inner) | ≥ 120, inner ≤ 40 | 232–234 | 217–219 | **135–137** |
| RANK label F1 | ≥ 0.90 | 1.000 | 1.000 | **0.953–0.965** |
| Glyph NCC | ≥ 0.95 | 0.996–1.000 | 0.981–0.982 | 0.978–0.980 |
| Digit-row ink (px) | ≥ 200 | 527–809 | 800–878 | 539–550 |

The frame, label and digit stages use absolute intensity. That is the same class as the corrected glyph
stage, and it affects **recall only**. It is deliberately unchanged in v1: making those stages tone-invariant
would move the whole lit-presentation requirement onto the header and void the validation again. A source
with a third tone curve may abstain broadly.

## Algorithm

All stages run on the given crops in order; the first failure abstains with its reason.

1. Shape, dtype and opacity of both crops (`invalid_geometry_or_format`, `nonopaque_evidence`).
2. Complete header by the accepted #15-4 decoder (`unsupported_context`).
3. Badge-frame registration: the 2 px light frame around a near-black interior, searched within ±3 px; ring
   minus inner luminance ≥ 120 and inner ≤ 40 (`badge_frame_absent`).
4. RANK label: light-grey ink, one-pixel mutual-proximity F1 ≥ 0.90 against the dev mask (`rank_label_absent`).
5. S glyph: normalized red-channel correlation ≥ 0.95, best of nine ±1 px positions (`s_glyph_absent`).
6. Place-number row present: at least 200 bright red pixels (`R ≥ 150`, `R − max(G,B) ≥ 90`). The number is
   never read (`place_number_absent`).

Both templates are embedded, read-only constants, mechanically extracted from the pinned dev crop; T0
reproduces them from that asset. Constructed glyph probes on the dev crop:

| Probe | Glyph NCC |
|---|---|
| Mirrored S | 0.819 |
| S shifted 4 rows | 0.711 |
| S stroke pixels replaced by the box's background median | 0.358 |
| Flat box | 0 |

Numberless S (outside the ranking top 100, per owner note) is unevidenced. It is expected to abstain at
stage 6, but that is **unverified**: a numberless S drawn a few pixels lower could put enough red into the
digit row and be recognized (still a true S, but outside the stated boundary). There is no temporal
carry-forward or sequence decoding.

## Evidence gaps and missing-fixture plan

- **Ranked Team lobby:** none in any source. Whether its panel shows a different rank is unknown.
- **Pre-S lobby badges** (A, A1–A4, UNRANKED): none at the panel. Rejection is shown only by constructed
  probes, never measured on genuine full-size pre-S badges.
- **Non-S content behind a lit header:** none in any source, so panel-stage discrimination on genuine pixels
  rests on one dev cross-fade; there is no validation instance.
- **Numberless S:** none. Its layout (for example a re-centred S) is unknown.
- **Layout breadth:** two native geometries and three tone curves only; no other resolution, locale or overlay.
- **Custom rooms:** genuine member lists with every member's rank exist, but are never a self-rank source.

Before extending support, verify from genuine originals at least two distinct sessions per proposed
presentation, keep one for validation, and add near-negative and partial evidence. Never synthesize
unavailable truth or adopt the unknown-provenance reference images without separate authorization.

## Gates and state

- **T0** (`tests/test_self_rank.py`): malformed and non-opaque inputs, unsupported geometry, determinism and
  immutability, context necessity, each panel condition, bounded registration, tone-curve invariance,
  dimmed rejection, output fields (no over-claim), dormant import boundary, template integrity, strict schema
  including the abstention stage, comparator, worker-spec truth isolation, video split across families, pinned
  template and refused pre-S/season categories. The T1 harness contract tests cover the two-image records.
- **T1:** formal replay, existing guarded job-owned workers; each worker knows the input crops and adapter
  but not the expected labels.
- **T2:** the T1 runner/worker lifecycle, because the replay adapter changed. Recognition-specific runtime T2
  is N/A.
- **T3:** N/A while dormant and offline; the independent reviewer confirmed this.

#15-6 Acceptance is **PENDING**, Released **NO**; stable remains **v1.2.0**. STOP after #15-6. Rating, pre-S,
numberless S, Team/Custom, opponent rank, runtime integration, Season (#28-A), UI and release need separate
owner authorization. This slice does not touch #15-5 / PR #65.
