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
  card, name, PLACE or RATING value.

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

Formal T1 adds **21 rank records** (42 lossless PNGs, 1,098,686 bytes) to the unchanged 61 cases:
**10 positives** under `ranks/s_rank/` and **11 genuine negatives** under `ranks/negatives/`.

| Split | Sources | Positives | Negatives |
|---|---|---|---|
| dev | `aRnhg9vs-zs`, `WabYwwGakEQ` | 4: idle and matching, places 14/15/08, one after the player changed card and AC | 7: dimmed match start, system menu, garage license, opponent intro card, custom room, custom result, truncated panel |
| validation | `xJ86EUS510w`, `y86wDex-EQI` | 6: idle and matching, places 08/05/01, two sessions | 4: dimmed match start, cross-faded lobby, Ranked mode select, opponent intro |

- Each record names the original video, timestamp, both crop rectangles, the original-frame SHA-256, crop
  hashes, visible truth, the self-identity reasoning, verifier/method/date, split and corrections.
- Truth `S` is transcribed from visible UI; truth `unknown` means no supported evidence in these crops.
  Expected truth and actual outputs stay separate; the worker receives pixels and the adapter only.
- Source videos never cross splits, within or across families (`match_metadata` uses the same split).
  `WabYwwGakEQ` contributes negatives only. The same player card that is screen owner in `xJ86` (validation)
  also appears on `WabY` custom screens (dev); no S pixels come from it.
- Exactly one pinned dev positive (`ranks.dev-s-matching`) is the template source.
- Privacy: minimal game-UI crops only; no player or opponent name, handle, emblem, AC name, NP, match ID,
  date, chat, desktop or full frame. One custom-result negative shows the edge of a generic in-game nameplate
  decoration. Original full frames remain local for review.

Truth was transcribed by Claude Code (Opus 5.5) visual inspection of original full frames and crops. This is
human-verifiable pixel truth, **not owner Acceptance**. Corrections follow the #15-4 rule: original evidence,
previous truth, reason, verifier/date and evidence hash; never change truth to suit a prediction.

## Initial validation failure and correction

The first formal T1 run (78/80) was retained as local evidence. Dev, `xJ86` and every negative passed; both
`y86w` positives abstained at the glyph stage. Diagnosis found a **dev-side defect**: the original glyph ink
(`R >= 150`) captured only the lower bowl of the S, because the glyph is shaded top to bottom. `y86w` uses a
different tone curve (crushed blacks, darker upper strokes), and its lit strokes overlap the dev source's
*dimmed* lobby levels, so no absolute-intensity glyph threshold can serve all sources.

The glyph stage now uses zero-mean normalized correlation of the red channel inside the registered S box
against the pinned dev crop. It is invariant to gain and offset. Its constant (0.95) is unchanged. Every other
stage, threshold and expected label is unchanged. The lit presentation is enforced by the header gate
(absolute white ink) and the RANK label stage, which reject dimmed lobbies.

The four earlier validation positives were inspected during diagnosis: they are **regression checks, not
untouched holdout**. Two **fresh** validation positives (`val-s-fresh-idle`, `val-s-third-source-fresh`)
were visually verified and declared before they were first scored, after the correction; both pass. The
`xJ86` layout offset (+2 px x, +1–2 px y) was also observed during truth verification, before the
registration window was fixed. Ten positives are far too few for any accuracy or population claim.

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

Both templates are embedded constants mechanically extracted from the pinned dev crop; T0 reproduces them
from that asset. Dev margins: genuine S correlation ≥ 0.996. Constructed probes: mirrored S 0.819, S shifted
4 rows 0.711, S erased 0.358. Numberless S (outside the ranking top 100, per owner note) is unevidenced and
abstains at stage 6. No temporal carry-forward or sequence decoding exists.

## Evidence gaps and missing-fixture plan

- **Ranked Team lobby:** none in any source. Whether its panel shows a different rank is unknown.
- **Pre-S lobby badges** (A, A1–A4, UNRANKED): none at the panel. Rejection is shown only by constructed
  probes, never measured on genuine full-size pre-S badges.
- **Numberless S:** none. Its layout (for example a re-centred S) is unknown.
- **Layout breadth:** two native geometries only; no other resolution, locale or overlay.
- **Custom rooms:** genuine member lists with every member's rank exist, but are never a self-rank source.

Before extending support, verify from genuine originals at least two distinct sessions per proposed
presentation, keep one for validation, and add near-negative and partial evidence. Never synthesize
unavailable truth or adopt the unknown-provenance reference images without separate authorization.

## Gates and state

- **T0** (`tests/test_self_rank.py`): malformed and non-opaque inputs, unsupported geometry, determinism and
  immutability, context necessity, each panel condition, bounded registration, tone-curve invariance,
  dimmed rejection, output fields (no over-claim), dormant import boundary, template integrity, strict
  schema, comparator, worker-spec truth isolation, video split across families, pinned template and refused
  pre-S/season categories. The T1 harness contract tests cover the two-image records.
- **T1:** formal replay, existing guarded job-owned workers; each worker knows the input crops and adapter
  but not the expected labels.
- **T2:** the T1 runner/worker lifecycle, because the replay adapter changed. Recognition-specific runtime T2
  is N/A.
- **T3:** N/A while dormant and offline; the independent reviewer must confirm it.

#15-6 Acceptance is **PENDING**, Released **NO**; stable remains **v1.2.0**. STOP after #15-6. Rating, pre-S,
numberless S, Team/Custom, opponent rank, runtime integration, Season (#28-A), UI and release need separate
owner authorization. This slice does not touch #15-5 / PR #65.
