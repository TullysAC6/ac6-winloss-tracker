# Issue #73 RA-0 — result-accuracy baseline (evidence inventory and benchmark freeze)

## Executive summary — completed offline comparison, 2026-10-08 JST

The adopted Pass-A inventory contains **142 independently discovered result events**, including **107 events
from four completely covered standard-typeface sources**. At four correlated sampling phases, current main misses
**17/428 standard event-phases**: 12 DRAW, three WINs lost jointly to bright-band rejection/confirmation resets,
and two results blocked by the previous match's re-arm lock. It accepts no false result in the audited footage.
The separate Korean-typeface source misses 80/80 event-phases. The partial broadcast window passes 60/60.

A foreground WIN/LOSS prototype reduces standard WIN/LOSS misses **5/416 → 2/416**, recovers the three IXV8
WIN misses and the retained #72 faded LOSS, and preserves validation WIN/LOSS **136/136**. However, it mistakes
the canonical synthetic white-prefix PHASE control for WIN; two such polls cause an **accepted false WIN** in
the real T1 detector loop. **Reject this frozen prototype as an RA-1 candidate.** The glyph signal is useful,
but the complete phrase/context must still exclude PHASE. DRAW and re-arm remain separate repairs.

This continues Claude's existing branch and report. **No production code or expected fixture truth changed.**
RA-0 is analysis, not product acceptance. A fresh independent review is recorded in §12 when completed.

Status: **RA-0 evidence package. No product change.** RA-1 has not started and needs separate owner
authorization. RA-0 completion is not result-accuracy acceptance.

| item | value |
|---|---|
| measured code | exact `main` `039d3bc1eb3116e27326099ee585cd2186531eb7` (exact-main CI run 37568479507 SUCCESS, attempt 1) |
| branch | existing `claude/issue-73-ra0-result-accuracy`; adopted head `98f8a0f`; added report/metadata plus isolated offline scripts |
| production code replayed | `result_detector.py`, `result_gate.py`, `game_capture.py`, `detector_templates.json` byte-identical to `039d3bc1` (LF-normalized SHA-256 `cfa0d73f…`, `1532e07c…`, `72ca522b…`, `02a00968…`); classifier fingerprint `486066cb0f3376ba…` |
| semantics changed | none: no threshold, state machine, `CONFIRM_HITS`, polling, ResultGate, CLEAR/re-arm, persistence or #72 change |

## 1. Method

### 1.1 Anti-circularity (two passes)

**Pass A — independent truth.** Result events were discovered and bounded from visible game pixels only:

- 1 fps 64×36 thumbnails for every second of each covered video;
- a contact-sheet review of every 10-second thumbnail;
- a lobby-correlation aid against manually chosen lobby seconds, used only to delimit candidate matches;
- 2 fps central-band strips over each match end.

Production detector output (`FINAL_*`, candidates, template scores, reject reasons) was never used to include, exclude
or bound an event. Every event was then checked a second time on a native high-resolution copy: a central-band crop at
the banner midpoint, or a 10/60 fps strip where the midpoint fell on a flicker dip.

Each source's truth file was committed (frozen) **before** that source's first detector replay. All six regenerate
byte-identically from their Pass A scripts, and each was committed once and never edited:

| source | truth file | freeze commit (JST) | first replay of the source |
|---|---|---|---|
| vP-M8cPgMtE | `ground-truth/vP-M8cPgMtE.json` | `da87859` 2026-10-07 17:58 | after (Pass B tool validation on 200–280 s, then full replay 18:15) |
| LT25joZOjhU | `ground-truth/LT25joZOjhU.json` | `1f2d0f1` 18:20 | 20:20 |
| yB_Hcy45v5c | `ground-truth/yB_Hcy45v5c.json` | `c583c56` 18:24 | 20:07 |
| fAphPer-yas | `ground-truth/fAphPer-yas.json` | `c583c56` 18:24 | 20:27 |
| IXV8Y_t6U34 | `ground-truth/IXV8Y_t6U34.json` | `7eb47a0` 18:26 | 20:31 |
| AYngtoqhwKs | `ground-truth/AYngtoqhwKs.json` | `c6dc132` 20:10 | 20:35 |

**Annotator independence (limit).** Claude produced both the Pass A annotation and the second check: independent of
the detector, not independent of the annotator. Owner/human spot verification is still open (`reviewer status` in
each event record).

**Pass B — replay.** The unchanged production path was driven by the repository's T1 engine (`tests/t1/replay.py:
run_sequence`): `ResultClassifier` with the shipped templates, the `ResultDetector.run` loop with motion arming,
`ResultStateMachine`, and `ResultGate` with the server's 5 s cooldown and `external_mutation()` lock. Only capture is
replaced. Each poll receives the production result ROI (x .20W, y .43H, w .60W, h .07H of the game image), cut from the
**exact source frame nearest the poll time**; extraction is bit-identical to a full-frame decode. Polls are spaced
830 ms apart (the cadence observed in retained runtime bundles), with 80 ms processing time.

Every source is replayed at **four sampling phases** (0, 0.2075, 0.415, 0.6225 s), because the outcome depends on
where the polls land relative to the banner animation. The 4 phases of one event are correlated trials of the same
event, not independent samples.

Not reproduced by video replay:
- native WGC capture, its timing jitter and its foreground/occlusion checks;
- persistence (T1 does not replay storage; T0/T2 own it);
- the original game-render pixels: the video was re-encoded by the streamer and by YouTube (H.264 4:2:0).

### 1.3 Extraction defect, invalidation and handoff verification

The first extractor requested a 75-row production ROI directly from an ffmpeg YUV420 crop. ffmpeg rounded
the height to 74 rows. Reading those bytes as 75-row frames misaligned pixels, source-frame indices and polls;
the initial VP-01 miss conclusion was false. **Every pre-fix output and conclusion is invalid.**

The adopted extractor decodes an even-aligned margin and slices the exact ROI in NumPy, refusing a partial
trailing frame. The six final source directories were regenerated with this corrected extractor. Their 24
phase metadata files all contain the margin crop; array dimensions, complete poll/time/source-index mapping,
and replay header geometry agree. At handoff, **48 exact full-frame decode spot checks** (two per final phase)
matched the extracted ROI bit-for-bit. This is a spot check across all phases, not an exhaustive comparison
of every decoded pixel. VP-01 is Correct on all four final phases.

No stale pre-fix output was found in the final source directories. `vP-test` is a corrected, partial tool-validation
subset and is explicitly excluded from every aggregate. No glob over arbitrary caches supplies the denominator:
only the six named final sources and their frozen truth files are used. All 24 stored evaluations were independently
recomputed from their replay JSONL and matched; all six Pass-A generators reproduced the unchanged files byte-for-byte.
Exact hashes, per-phase geometry and checks: [handoff-verification.json](issue73-ra0/handoff-verification.json).

### 1.2 Scoring (per event, per phase)

Matching window: `[banner_first − 3 s, banner_last + 10 s]`. Events in these sources are minutes apart, so windows
never overlap.

- **Correct** — WIN/LOSS: exactly one accepted gate result in the window, of the true class. DRAW: a confirmed
  `draw` detection and no accepted WIN/LOSS.
- **Miss** — no accepted result in the window (for DRAW: no `draw` detection and no WIN/LOSS).
- **Misclassification** — one accepted result of the wrong class.
- **Duplicate** — two or more accepted results in the window.
- **False positive** — an accepted WIN/LOSS or confirmed DRAW outside every event window. Because Pass A coverage
  is structurally complete for the covered footage, every such acceptance would be visually checked. None occurred.

## 2. Evidence inventory

### 2.1 Canonical repository fixtures (T1)

| family | cases |
|---|---|
| final WIN | 5 still ROIs + 3 owner bright-band (#68) cases |
| final LOSS | 2 still ROIs |
| DRAW | 3 still ROIs, **all from one non-native video** (555×40 ROI, word at y-centre 0.22 of the ROI) |
| CLEAR / PHASE / negatives | 2 / 3 / 10 |
| sequences | 22 (two-hit, CLEAR-break, capture-gap, re-arm, undo, startup, stale/identity, bright band, DRAW, false DRAW) |

Current-main formal T1: **83/83 PASS, 0 failed, 0 skipped** (50 result-family cases). Corpus `35f3ba4b4e9543f0`;
source SHA-256 as in the status table. The canonical corpus is thin for LOSS (2 ROIs) and DRAW (3 ROIs from one
non-native source, see §4.3).

Handoff rerun: **83/83 PASS, 0 failed, 0 skipped, 48.53 s**, Python 3.13.15 / MSS 10.2.0, branch
head `7f6e7fa4fc5173b2830da7cc5ee8f88bc323229b`; all measured production sources/fixtures are unchanged
from the exact RA-0 base. This is the canonical production gate, not a claim that the foreground prototype passes T1.

### 2.2 Retained natural evidence (#66 / #68 / #72), owner-attested truth

| id | session | owner truth | evidence retained | runtime outcome | current main (`039d3bc1` code) |
|---|---|---|---|---|---|
| N1 | 2026-10-03 12:00 (#15-5 T3, pre-#68 build) | WIN | diagnostic log only (no pixels; bundles did not exist yet) | **Miss**: FINAL_WIN (1-D/2-D 0.9926/0.9901) → next frame CLEAR (WIN 0.9917/0.9885, dark 0.7369) → reset | **unknown** (no pixels) |
| N2 | 2026-10-03 12:05 | LOSS | log | Correct (persisted) | not replayable; result path unchanged except #68's additive recovery |
| N3 | 2026-10-03 pm (#68) | WIN | bundle `98e926fa` (3 ROIs) | **Miss**: CLEAR → FINAL_WIN → CLEAR (dark 0.690 < 0.72, WIN 0.994/0.990) | **Correct**: frame 2 now FINAL_WIN (recovery path); T1 `bright-band-*` sequences prove exactly one WIN |
| N4 | 2026-10-06 22:21 (Borderless) | LOSS | log | Correct (persisted) | not replayable |
| N5 | 2026-10-06, match 2 after 22:21 (#72, Borderless) | LOSS | bundles `bb8ce355` + `b3f1faf5` (5 ROIs) | **Miss** | **Miss** (reproduced, see §4.1) |

The 2026-10-06 22:34 banner in bundle `f6dc97e3` has no owner-attested truth: it is detector-discovered, so it is
characterized but excluded from every count. Other detector acceptances in retained sessions that lack a per-match
owner-attested record (e.g. the 29 acceptances between 13:34 and 16:03 on 2026-10-03) have no independent truth and
are excluded.

### 2.3 Owner-provided YouTube sources

All six are public live archives obtained locally with yt-dlp. Full videos stay local, read-only, outside Git and
OneDrive (≈ 16 GB of video and ≈ 31 GB of extracted poll arrays). Nothing from them is committed except the truth
records.

| source | channel / title note | platform | UI / banner typeface | overlay family and ROI status | discovery / replay copy | coverage | events W/L/D |
|---|---|---|---|---|---|---|---|
| vP-M8cPgMtE | かさすげ | Steam (owner-stated) | Japanese UI, standard | **none** (owner claim visually verified on all 719 thumbnails) | 360p / 1080p60 | **complete**, 0–7187 s | 36/4/1 |
| LT25joZOjhU | タリーズ, "(PS)" | PlayStation (title) | Japanese UI, standard | none (431 thumbnails) | 360p / 1080p60 | **complete**, 0–4308 s | 8/7/0 |
| fAphPer-yas | サバーニャch | unknown | Japanese UI, standard | chat column from ~3245 s, **outside ROI** (nearest element ~0.815W; ROI right edge 0.800W) | 360p / 720p60 (source max) | **complete**, 0–4744 s | 4/15/1 |
| IXV8Y_t6U34 | ドラちゃん営業部長, "/steam版" | Steam (title) | Japanese UI, standard | chat column (~0.875W) + corner logo, **outside ROI** (whole stream) | 360p / 720p60 (source max) | **complete**, 0–6441 s | 23/7/1 |
| yB_Hcy45v5c | Bob | unknown | **Korean UI; banner words drawn in a plain sans typeface** | none (628 thumbnails) | 360p / 1080p60 | **complete**, 0–6274 s | 12/8/0 |
| AYngtoqhwKs | ユイレン@Vのすがた, "Steam版" | Steam (title) | Japanese UI, standard | **broadcast layout**: game scaled to 1632×918 at (16,36) of 1080p; avatar/comments/ticker outside the game rectangle | 360p / 1080p60 (0–4640 s section) | **PARTIAL**: 0–4630 s of 17135 s (27 %) | 9/6/0 |

The overlay families are distinct (two different chat renderers and one broadcast layout); none is shared between
streams. No overlay intersects the result ROI in any covered event, so the outcome "unsupported / occluded" never
arose. AYng is **stress evidence**: the Tracker captures the game window, not a rescaled broadcast composite.

**Coverage audit.**
- vP, LT25, fAph, IXV8 and yB: every 10 s thumbnail was reviewed and every match end was stripped at 2 fps. In vP, 45
  of 45 inter-lobby intervals were reviewed, and every gap without a result is recorded as a non-event interval.
- AYng: only the stated window; no claim is made about 4630–17135 s.
- Second verification: 142/142 events confirmed at native 720p/1080p. Four midpoint crops fell on flicker dips and were
  confirmed on 10/60 fps strips (VP-26, YB-15, FA-04, IX-12); several other midpoints were dim but legible.

**Evidence dimensions present.**
- Banner and lighting: first appearance, fully lit banner, text pulse/flicker troughs, fade-out, bright and dark
  arenas, the translucent band over bright scenery (IXV8), and multi-phase matches with PHASE banners.
- Transitions and notices: CLEAR transitions, tunnel/loading, ENEMY DESTROYED / DESTROYED / TIME UP notices.
- Matches: a disconnect DRAW (IX-26) and a short-match DRAW (VP-24).
- Encoding and platform: H.264 at 1080p and 720p; Steam, PlayStation and unknown platforms.
- Display mode: Borderless/Fullscreen is not visible in video (unknown).
- Capture discontinuities: not present in video.

**Adverse controls** inside the replayed footage are all non-event footage of the covered videos. This includes combat
HUD, every PHASE banner, garage/assembly/AC-test arenas, menus, lobbies, loading tunnels, notices, chat overlays,
logos and the broadcast frame: 151,258 polls outside event windows across the 4 phases (vP 31,585; LT25 19,632;
fAph 21,378; IXV8 28,732; yB 28,742; AYng 21,189).

## 3. Current-main baseline

### 3.1 Per source (four phases each, shown as phase 0/1/2/3)

| source | W/L/D | polls per phase | Correct | Miss | Misclass | Dup | FP | events correct at 4/4 · phase-dependent · never |
|---|---|---|---|---|---|---|---|---|
| vP | 36/4/1 | 8660 | 40/40/39/39 | 1/1/2/2 | 0 | 0 | 0 | 38 · 2 · 1 |
| LT25 | 8/7/0 | 5191 | 15/15/15/15 | 0 | 0 | 0 | 0 | 15 · 0 · 0 |
| fAph | 4/15/1 | 5716 | 19/19/19/19 | 1/1/1/1 | 0 | 0 | 0 | 19 · 0 · 1 |
| IXV8 | 23/7/1 | 7761 | 29/30/29/29 | 2/1/2/2 | 0 | 0 | 0 | 28 · 2 · 1 |
| yB | 12/8/0 | 7560 | 0/0/0/0 | 20/20/20/20 | 0 | 0 | 0 | 0 · 0 · 20 |
| AYng (partial, stress) | 9/6/0 | 5579 | 15/15/15/15 | 0 | 0 | 0 | 0 | 15 · 0 · 0 |

### 3.2 Sets kept separate

```
A. Canonical T1 (repository fixtures)          83/83 PASS (50 result-family cases)

B. Retained natural evidence (owner truth)     5 events: 2 WIN, 3 LOSS
   current main: Correct 1 proven (N3 #68), Miss 1 proven (N5 #72),
                 runtime-Correct 2 not replayable (N2, N4), unknown 1 (N1)

C. Clean YouTube, standard banner typeface, complete coverage (vP, LT25, fAph, IXV8)
   events 107: WIN 71, LOSS 33, DRAW 3
   event-phases 428: Correct 411, Miss 17, Misclassification 0, Duplicate 0
   false positives: 0 in 6.30 h of footage x 4 phases (101,327 non-event polls)
   WIN  event-phase recall 280/284 = 98.6 %
   LOSS event-phase recall 131/132 = 99.2 %
   DRAW event-phase recall   0/12  (3 events; a percentage is not meaningful)
   events correct at all four phases: 100/107; phase-dependent 4; never 3 (the 3 DRAWs)
   precision of accepted results: 411/411

D. Korean-UI typeface (yB), complete coverage
   20 events (12 WIN, 8 LOSS): Miss 80/80 event-phases; false positives 0 in 1.74 h x 4

E. Overlay stress, PARTIAL window (AYng 0-4630 s)
   15 events (9 WIN, 6 LOSS): Correct 60/60 event-phases; false positives 0 in 1.29 h x 4
```

Set C mixes Steam, PlayStation and unknown platforms, and no-overlay with outside-ROI families. Its four sources are
listed separately in §3.1. Recall is reported only for set C, where coverage is complete and the denominators are frozen
Pass A events. The numbers describe this footage replayed at an 830 ms cadence. They are not field error rates.
Precision is "0 false positives in the audited footage", not a rate claim.

**Precision margin.** In C–E there is no FINAL frame outside an event window. There is one wrong-class armed hit:
- vP phase 1, t = 1284.217 s, 2.8 s before the VP-07 WIN: a single `FINAL_DRAW` from **combat explosion smoke**.
- Its DRAW grid score was 0.75, exactly at the gate; draw cluster span .25, y-span .50, band dark .76.
- One more such poll would have confirmed a **false DRAW**, which also installs the post-result lock and could swallow
  the genuine WIN 3 s later.

## 4. Failure timelines and layers

### 4.1 #72 (N5) — still a Miss on current main

Retained bundles `bb8ce355` (SHA-256 `93c39baa…`) and `b3f1faf5` (`21290af8…`), replayed on this tree; every recorded
decision reproduces. Captured_at is the native monotonic clock; the capture source is WGC window capture, with no
discontinuity or identity change.

| captured_at | class | key subconditions | state after |
|---|---|---|---|
| 181193.029 | CLEAR | band dark .068 | armed, clear_hits 2 |
| 181193.866 | FINAL_LOSS | dark .927, LOSS 1-D .9945, grid .9905, geom ✓ | cand loss/1 |
| 181194.696 | **NON_CLEAR** | band ✓ (dark .937), **text pulse**: LOSS mask nearly empty, cluster .00016, 1-D .373, grid 0 | **candidate reset** |
| 181195.526 | FINAL_LOSS | dark .873, 1-D .9945, grid .9905 | cand loss/1 |
| 181196.568 | CLEAR | banner gone (dark .000) | reset → **no result** |

- Earliest failing layer: the **classifier** rejects a genuine but dimmed text frame. Proven: the game animates only the
  text layer, and the underline and band stay constant.
- The miss itself comes from the **temporal confirmation**, which requires two *consecutive* FINAL_LOSS and so never
  confirms two hits separated by one pulse frame. Proven.
- Capture and display mode: the ROI reproduces offline, so Borderless is at most an upstream sampling contributor.
  Proven not to be the classifier cause.
- ResultGate and persistence were never reached.

### 4.2 #68 (N3) — repaired on current main (permanent regression)

Bundle `98e926fa` (SHA-256 `350ef3c6…`):

| frame | recorded at runtime | replayed on current main |
|---|---|---|
| 0 | CLEAR | CLEAR |
| 1 | FINAL_WIN (dark .898, WIN .995/.992) | FINAL_WIN |
| 2 | **CLEAR** (dark .690 < .72, WIN .994/.990) | **FINAL_WIN** (recovery path) |

Two consecutive hits now count exactly one WIN. T1 `result.owner-bright-band.*` and the five `bright-band-*` sequences
pass. Original layer: classifier (proven).

### 4.3 DRAW — 3/3 genuine DRAWs missed at every phase (classifier, proven)

VP-24, FA-18 and IX-26 (three different sources) behave identically on every banner poll:
- band ✓ (dark .88–.95);
- DRAW mask cluster span .19–.22, centre .50, coverage ≈ .04, density ≈ .20;
- y-span .50–.56, density ≈ .075 — all geometry gates pass;
- but **`draw_grid_score` is .43–.48 against a ≥ .75 gate** → NON_CLEAR.

The shipped DRAW grid template places the word in the **top half** of the ROI: template rows 0–3 of 8, y-centre
0.22. That matches the only DRAW fixtures (three 555×40 ROIs from one non-native video), which score 1.00. On native
16:9 sources the DRAW word sits vertically centred (y ≈ .47), exactly like YOU WIN / YOU LOSE. **DRAW recognition is
effectively unsupported on native-geometry footage.** No DRAW was ever confirmed, so the `draw` lock never engaged.

### 4.4 Re-arm lock starvation — VP-26 (phase 2) and VP-39 (phase 3) (CLEAR/re-arm, proven)

On the missed phase the banner polls were classified perfectly (VP-26 FINAL_LOSS 1.00/.99 ×4; VP-39 FINAL_WIN
1.00/1.00 ×3), but the state was `post_result_lock` from the **previous match's** counted result.

The lock releases only after the 5 s cooldown followed by **≥ 5 s of uninterrupted CLEAR**, which at 0.83 s cadence
means 8 consecutive CLEAR polls. Activity cannot release it (strict public mode).

| match before the miss | gameplay polls | CLEAR / NON_CLEAR / PHASE | longest uninterrupted CLEAR run |
|---|---|---|---|
| VP-26 phase 2 | 246 | 102 / 138 / 6 | **5.0 s (7 polls)** |
| VP-39 phase 3 | 169 | 74 / 92 / 3 | **5.0 s (7 polls)** |

Other phases of the same matches reached 5.8–9.1 s and were released. Dark-ish arena gameplay is mostly NON_CLEAR
(dark ROI ≥ 0.78). The re-arm margin is therefore **sampling-phase-dependent**, and a whole match can be missed even
though the classifier is right.

### 4.5 Banner interruption, the shared #68/#72 class (temporal confirmation, proven)

A one-hit candidate reset by a genuine-banner frame that does not classify FINAL_X (`ra0_resets.py`):

| source | event-phases with ≥ 1 reset | interrupting frame | rescued by later consecutive hits | lost |
|---|---|---|---|---|
| vP | 10 / 164 | pulse-trough NON_CLEAR ×10 | 10 | 0 |
| LT25 | 5 / 60 | pulse-trough NON_CLEAR ×5 | 5 | 0 |
| fAph | 5 / 80 | pulse-trough NON_CLEAR ×5 | 5 | 0 |
| IXV8 | 11 / 124 | pulse-trough NON_CLEAR ×9, bright-band CLEAR ×3 (+1 CLEAR after the banner ended) | 8 | **3** |
| AYng | 4 / 60 | pulse-trough NON_CLEAR ×4 | 4 | 0 |

**Pulse trough.** Every trough frame has the same signature: band present (dark ≥ .96), text mask empty or nearly empty, about
0.8 s after the first hit. It is the game's text blink, the deeper form of the #72 dimmed frame.

**Bright-band CLEAR (IXV8).** These frames show a fully legible YOU WIN (WIN 1-D/grid .995–.998, geometry ✓), but
dark_ratio is .67–.71 < .72. The #68 recovery path then fails because the lower margin is only .74–.86 dark (it needs
≥ .90) and prefix_dark is .21–.35 or 1.0. This is the #68 class outside its repaired sub-case.

The three lost event-phases:

| event | polls | outcome |
|---|---|---|
| IX-01 phase 0 | FINAL_WIN → trough NON_CLEAR → FINAL_WIN → bright-band CLEAR (dark .713) → banner over | Miss |
| IX-12 phase 2 | FINAL_WIN → bright-band CLEAR (dark .706) → trough NON_CLEAR → CLEAR | Miss |
| IX-12 phase 3 | trough → FINAL_WIN → bright-band CLEAR (dark .669) → FINAL_WIN → banner over | Miss |

Earliest failing layer: the classifier for the bright-band frames (legible banner rejected), the temporal confirmation
for the troughs (no text visible, but the result is still on screen). Both are proven from the replayed pixels.

### 4.6 Korean-UI typeface — yB, 20/20 missed (classifier, proven)

The detector was armed at all 80 banner onsets (20 events × 4 phases) and logged no FINAL frame anywhere in the stream.
Over the 281 banner-window polls with a coloured mask:

| | banner-frame cluster span (gate) | grid (gate) | 1-D (gate ≥ .78) |
|---|---|---|---|
| WIN | ≈ .19 (.22–.39) | max .79 (≥ .82) | up to .90 |
| LOSS | ≈ .22 (.24–.42) | max .67 (≥ .82) | up to .86 |

The Korean client renders the English words in a narrower plain typeface. The templates and geometry encode the
Japanese/English-client typeface in this evidence. Whether other client locales are in product scope is an **owner decision**.

### 4.7 Failure-layer breakdown

| layer | YouTube set C event-phases | other |
|---|---|---|
| independent truth / source / compression | 0 proven (re-encoding is a possible contributor, unmeasured) | — |
| capture | not exercised by video replay; retained #72 bundles show no capture fault | — |
| classifier | 12 (DRAW template) + 3 lost to bright-band rejection (jointly with temporal) | yB 80 (typeface); N1 #66 (proven at log level, subcondition unknown); N5 #72 (dimmed text) |
| temporal candidate/confirmation | 3 (IXV8, jointly with the classifier) + 32 rescued near-misses | N5 #72 |
| CLEAR / re-arm | 2 (lock starvation) | — |
| ResultGate / callback | 0 observed (no duplicate, no wrong class) | — |
| persistence | **not measured** (T1 does not replay storage) | N2/N4 persisted correctly at runtime |

## 5. Temporal-architecture assessment (analysis only)

#68 and #72 are **the same failure class**: a genuine result-animation frame between two FINAL_X hits fails the
classifier, and the consecutive-exact confirmation resets. They differ only in which predicate rejects the
interrupting frame (global band darkness vs. text-mask colour/opacity).

The YouTube replay shows the class is common, not anecdotal:
- 35 of 488 replayed standard + broadcast event-phases contain a reset in the audit window
  `[first visible − 1 s, last visible + 1 s]`; the 80 Korean phases are separate and have no candidates;
- 32 were rescued only because the 2.5–3.5 s banner left time for two more consecutive hits;
- 3 were lost.

**Arithmetic:** 33 NON_CLEAR reset occurrences occur in 33 event-phases; four CLEAR reset occurrences occur
in three event-phases. IX-01 phase 0 contains both classes, and IX-12 phase 3 contains two CLEAR resets.
Thus the union is **33 + 3 − 1 = 35 event-phases**, while the occurrence count is **33 + 4 = 37**.
Only 33 NON_CLEAR + two CLEAR occurrences fall strictly within the frozen bounds. The CLEAR at IX-01
224.100 s is beyond the conservative Pass-A endpoint but still visibly shows YOU WIN on direct pixel inspection;
IX-12 2152.8125 s is after the banner has disappeared. Truth bounds remain frozen; these boundary facts do not
silently redefine them. The audit-window reset count is not a claim of 37 text-visible failures.

The repaired #68 sub-case did not remove the class (IXV8). A #72 colour-mask relaxation alone would not address
bright-band frames or full troughs. The evidence therefore indicates a **brittle consecutive-exact-final
confirmation**, not unrelated visual predicates. Separately, the post-result re-arm requirement (uninterrupted
CLEAR) is a second, independent sampling-dependent brittleness.

Options for RA-2 (not implemented):

| option | idea | evidence for | risk |
|---|---|---|---|
| A. keep consecutive-exact, extend classifier per interrupting frame | #72 mask fix + broader #68 recovery | each fix is local and testable | predicate accumulation, which #73 forbids after the class reappears; troughs have no text at all |
| B. bounded same-class accumulation | after FINAL_X, accept a second FINAL_X within a short bounded window (≈ ≤ 3 polls / ≤ 2.5 s); reset on conflicting final, PHASE or long CLEAR | covers troughs and bright-band rejections uniformly | two isolated spurious hits of the same class within the window |
| C. hold through proven transitional states only | candidate survives (without counting) only a band-present/text-empty trough or a band-darkness-only rejection with ≥ .95 same-class glyph evidence | narrowest; both signatures are measured here | must prove combat/menu frames never mimic these states |
| D. faster polling / `CONFIRM_HITS = 1` | — | — | excluded by #73 |

DRAW, re-arm and locale are separate classifier/state items, not temporal-confirmation issues.

## 6. False-positive protection (potential recall fix → surface it could widen)

| potential fix | false-positive surface it could widen | evidence already on hand |
|---|---|---|
| tolerate one non-final frame between hits (B/C) | two spurious hits bridged by a gap: combat smoke DRAW (grid .75 at 1284 s), PHASE `YOU WIN/LOSE` sub-banners, red/cyan HUD bursts | 151,258 non-event polls with 0 FINAL frames outside events, except the DRAW hit near VP-07 |
| relax band darkness (#68 class) | bright PHASE banners, garage cyan bodies, menu text on light panels | 10 T1 negatives; PHASE polls vP 771, LT25 236, fAph 309, IXV8 455, AYng 283, yB 269 |
| widen the LOSS colour mask (#72 candidate) | red damage/glitch overlays (FA-04 red stripes), red HUD, orange explosions | fAph red-glitch LOSS background; #72 adverse sweeps |
| recalibrate the DRAW template to centred native geometry | white combat smoke/explosions, white notices (ENEMY DESTROYED, TIME UP); a false DRAW also locks the next result | the near-FP at 1284 s is already at the gate; T1 false-DRAW combat negatives |
| ease re-arm (cumulative CLEAR or activity release) | double count of one result screen, carry-over after undo, re-count when returning to a still-visible result | T1 undo/no-recount/rearm sequences; the original reason for strict mode |
| support another locale typeface | other glyph sets and other on-screen text | yB only (one source) |

## 7. Gaps

- **N1 (#66, 2026-10-03 WIN)**: no pixels, so the current-main outcome is unknown.
- **N2/N4**: runtime-correct, but cannot be replayed.
- **Persistence**: not measured by any replay here.
- **Native capture**: WGC timing jitter, Borderless/Fullscreen and capture gaps are not represented by video.
- **Re-encoding**: YouTube re-encoding (4:2:0) may alter colour masks and darkness. No same-moment native-vs-YouTube
  pair exists.
- **DRAW**: only 3 genuine events (all missed).
- **Canonical LOSS**: only 2 ROIs.
- **Platform**: PlayStation and unknown-platform footage is included in set C; per-source rows let a Steam-only view
  be read off.
- **AYng**: annotated for 27 % of the stream only.
- **Annotation review**: owner/human spot check of the Pass A annotations is outstanding.
- **Tooling**: Pass A/B tools are kept outside the repository (§9). Whether to version them for RA-1 is an owner
  decision.

The foreground PoC **dev/validation split** was committed before its parameters, by source with no frames
of one animation on both sides ([foreground-split.json](issue73-ra0/foreground-split.json), commit `7f6e7fa`):
- dev: vP + IXV8;
- validation: LT25 + fAph;
- stress: AYng;
- locale: yB;
- permanent regressions: N3 (#68) and N5 (#72).

## 8. Proposed RA-1 boundary (not started)

1. **RA-1A candidate boundary: WIN/LOSS foreground identity and result-animation continuity assessment.**
   Repair visible faded/bright text while preserving complete PHASE/negative discrimination. A blank pulse
   trough must remain blank at the classifier; decide separately from evidence whether temporal continuity
   needs a bounded redesign. The frozen PoC is rejected (§10), not adopted. Done would require:
   - N5 (#72) counts exactly one LOSS;
   - the 3 lost IXV8 event-phases (IX-01 ph0, IX-12 ph2/ph3) count exactly one WIN;
   - the 32 rescued near-misses still count exactly one result;
   - zero new acceptances in the 151,258 non-event polls;
   - zero duplicates;
   - unchanged T1.

   The parked local #72 candidate `a439259` is preserved and was not run, reset, rebased or otherwise used.
2. **Separate coherent boundaries, names provisional:**
   - RA-1B: CLEAR / post-result-lock re-arm, with duplicate/undo/still-visible-result controls;
   - RA-1C: DRAW geometry/recognition redesign, including the smoke near-FP, not a lower score gate alone;
   - locale/typeface scope remains an owner decision, separate from the standard-typeface repairs.
3. RA-1 changes production semantics, so it requires T0/T1/T2, a fresh independent review, exact-head CI and RA-4
   field acceptance per #73.

## 9. Reproduction and local evidence (not in Git)

The tools live outside the repository in a local evidence folder. They are read-only diagnostics that import production
modules unchanged. Every external process was bounded by a timeout.

| tool | SHA-256 |
|---|---|
| `pass_a_structure.py` (1 fps thumbnails, lobby aid) | `5b563744…f35f8a` |
| `contact_sheets.py` | `2070be75…a4be7f` |
| `match_end_strips.py` | `073cae7f…201304` |
| `verify_sheet.py` | `ecef1fbd…1ce0` |
| `pass_a_<source>.py` ×6 (truth generators) | vP `a7d7f315…`, LT25 `75df96e8…`, yB `6084a4d4…`, fAph `90d415e7…`, IXV8 `e7453a0f…`, AYng `0fd1e242…` |
| `ra0_video_replay.py` (exact-frame poll extraction + `run_sequence` replay) | `f6e9973c…c11b7` |
| `ra0_evaluate.py` (scoring vs frozen truth) | `6217af0b…932c` |
| `ra0_timelines.py`, `ra0_lock_audit.py`, `ra0_margin.py`, `ra0_resets.py`, `ra0_banner_ranges.py`, `ra0_aggregate.py` | `6c559f66…`, `ac39863a…`, `00fcda78…`, `f79c7880…`, `b7b44b91…`, `25f64127…` |
| `inventory_sessions.py` (retained-session inventory) | `947d447e…` |
| `run_replays.sh`, `eval_source.sh` (bounded wrappers: 4 parallel phase replays with `timeout 3600`; evaluation pipeline) | local only |

Retained locally:
- the 12 video files (360p discovery plus high-resolution replay copies);
- per-source poll arrays, replay JSONL, evaluations, timelines, lock audits and margin/reset scans;
- retained-bundle replays;
- the T1 report.

## 10. Frozen foreground WIN/LOSS PoC

### 10.1 Model, split and limits

`scripts/ra0_foreground.py` is an offline NumPy diagnostic. It uses two chroma masks per class (unchanged
shipped lit mask and a dim mask), normalized 32×8 glyph shape, separate YOU/suffix shape checks, expected
ROI position/geometry, local glyph contrast and a margin over the PHASE template. It does not classify
by coloured-pixel count alone and has no global result-band/lower-margin darkness gate. DRAW is excluded.
Templates are the shipped production templates; no broadcast pixels create templates.

Source assignments were frozen at `7f6e7fa`, before parameters. V1 (`2002b73`) was invalid due to Codex's
int16 weighted-luminance overflow and was stopped. V2 (`c63d7ce`) fixed it; development inspection showed
the dim mask included background chroma in bright IXV8. V3 (`5c93919`) added the shipped lit mask as a
second view. **Only vP/IXV8 were used for this development. V3 was frozen before LT25/fAph/yB/AYng evaluation
and was never tuned afterward**, including after discovering the PHASE failure. Earlier versions remain
separate local outputs and are excluded from final metrics. This rework was from Codex's approach, not Claude's.

Validation is held out from **PoC parameter selection**, not blind to the existing production-baseline report.
LT25 is a clean standard-typeface validation-only source. yB and AYng remain separate holdout/stress groups.

Frame comparison evaluates every adopted poll. Event comparison calls unchanged production
`ResultStateMachine.observe` and `ResultGate`, preserving capture timestamps, 80 ms processing delay,
two-hit confirmation, cooldown, CLEAR/re-arm and the cached capture/motion/gameplay-activity inputs.
The control reproduced **every baseline state snapshot, detection and gate call on all 24 source-phases**.
This isolates the classifier intervention. It is **not an integrated ResultDetector.run forecast**: activity
inputs are held fixed, and native capture, jitter, identity/gap handling and persistence are not re-evaluated.
The separate PHASE counterexample does run the full unchanged T1 detector loop with a substituted classifier.

### 10.2 Event results: standard sources only

| Metric | Current | Text-focused PoC v3 |
|---|---:|---:|
| Standard WIN correct | 280/284 | 283/284 |
| Standard LOSS correct | 131/132 | 131/132 |
| WIN misses | 4 | 1 |
| LOSS misses | 1 | 1 |
| Wrong class / duplicate | 0 / 0 | 0 / 0 |
| Accepted false positives in natural standard footage | 0 | 0 |
| Dangerous armed one-hit near-FP in standard footage | 1 DRAW | 1 DRAW (unchanged) |
| #68 retained WIN | pass, one WIN | pass, one WIN |
| #72 retained LOSS | fail, zero LOSS | pass, one LOSS |
| IXV8 previously missed WIN event-phases recovered | 0/3 | 3/3 |
| Validation WIN/LOSS correct (LT25 + fAph) | 136/136 | 136/136 |
| Canonical white-prefix PHASE: false final classification | 0 | 1 |
| Separate repeated-prefix synthetic stress: accepted false WIN | 0 | **1** |

The retained #72 trial is separate from the 416 natural standard WIN/LOSS event-phases. The two remaining
natural WIN/LOSS misses are VP-26 phase 2 (LOSS) and VP-39 phase 3 (WIN), both prior-match re-arm locks.
Including unchanged DRAW gives 411/428 → 414/428; do not present that as a WIN/LOSS accuracy denominator.
28 canonical result still images retain all nine WIN/LOSS positives, but the synthetic PHASE control fails
(the three DRAW images are outside PoC scope). Canonical production T1 remains 83/83 PASS.

### 10.3 Frame evidence and temporal distinction

| Frame signal measure | Current | PoC v3 |
|---|---:|---:|
| Matching final signal in frozen standard WIN/LOSS intervals | 1089/1240 polls | 1118/1240 polls |
| Matching final signal in validation WIN/LOSS intervals | 354/415 polls | 362/415 polls |
| WIN/LOSS final signal outside standard scoring windows | 0/101327 polls | 0/101327 polls |

These are **signal-coverage counts within event intervals**, not frame-label accuracy: frozen event bounds
include genuinely blank pulse troughs. They do not assume text is visible at every poll. A post-evaluation
pixel inspection of disagreements confirmed 13 dim vP WIN frames and two dim LT25 LOSS frames are visibly
legible (e.g. VP-09 1572.020, LT-02 ph2 367.275, LT-03 ph3 717.7425), and PoC recognizes them. The retained
#72 181194.696 frame visibly contains faded YOU LOSE and changes NON_CLEAR → FINAL_LOSS.
This selected sample demonstrates mechanisms; it is not a separately sampled validation accuracy estimate.

For IX-01 ph0, the exact sequence is WIN 221.610 → **blank NON_CLEAR 222.440** → WIN 223.270 →
**visible bright WIN 224.100, current CLEAR / PoC WIN**. PoC leaves the blank frame unrecognized and
confirms on the last two visible frames. IX-12 ph2/ph3 bright frames at 2150.945/2151.1525 also recover.
The post-banner IX-12 2152.8125 frame stays non-final. vP 260.620 is a visibly blank trough and remains non-final.
Improved visible-text classification is therefore distinct from granting classifier identity to a blank trough.

### 10.4 Holdout and broadcast remain separate

| Korean/typeface holdout (yB) | Current | PoC v3 |
|---|---:|---:|
| WIN correct | 0/48 | 0/48 |
| LOSS correct | 0/32 | 0/32 |
| Accepted FP / near-FP | 0 / 0 | 0 / 0 |
| Matching final signal in banner intervals | 0/240 | 0/240 |

| AYng broadcast stress, **0–4630 s only** | Current | PoC v3 |
|---|---:|---:|
| WIN correct | 36/36 | 36/36 |
| LOSS correct | 24/24 | 24/24 |
| Accepted FP / near-FP | 0 / 0 | 0 / 0 |
| Matching final signal in banner intervals | 162/188 | 164/188 |
| Final WIN/LOSS signal outside scoring windows | 0/21189 | 0/21189 |

No result ROI is overlay-occluded in this evidence. AYng tests rescaling and a broadcast layout, not arbitrary
chat/logo occlusion through the glyphs. Covered footage contains combat, explosions/smoke, notices including
ENEMY DESTROYED, PHASE, garage/assembly, menu/loading and unrelated coloured graphics. There are no
separate exhaustive per-category denominators, and an unseen overlay intersecting the ROI remains untested.

### 10.5 Precision failure and decision

The existing synthetic `phase_white_prefix_synthetic.ppm` contains white prefix blocks before the coloured
YOU WIN suffix. The coloured glyph shape itself matches a final. V3 ignores the neutral prefix and therefore
classifies FINAL_WIN. An isolated diagnostic sequence of three canonical CLEARs followed by **two copies of
that existing PHASE negative** passes zero results under current main, but accepts **one false WIN** under v3
through `tests/t1/replay.py:run_sequence`. This is explicitly synthetic adverse evidence, not a natural-video event.
It is sufficient to fail the precision-first decision rule despite zero observed natural-video false positives.

**Reject v3 as an RA-1 candidate.** Do not tune it on the now-seen validation/stress sources to hide this failure.
The positive conclusion is narrower: foreground identity can recognize visible faded/bright standard glyphs
without the global darkness gate, while blank troughs remain blank. A future separately authorized design
must identify the complete result phrase/context, retain PHASE discrimination, and undergo a new independent
validation protocol. Existing evidence supports evaluating classifier identity and continuity together; it does
not authorize arbitrary temporal gaps, global gate removal, locale expansion or RA-1 implementation.

## 11. Reproduction, hygiene and ownership at handoff

Machine-readable comparison: [foreground-comparison.json](issue73-ra0/foreground-comparison.json).
Final local output: `issue73-ra0-evidence/foreground-v3/` in the workspace parent; source arrays/videos:
`<user>/ac6-ra0-video-sources/`. Neither directory is added to Git. In the adopted worktree:

```powershell
# Existing verification Python 3.13 environment; NumPy is offline-tooling-only.
python -B scripts/ra0_compare.py <polls-root> <existing-evidence-root> <output-root> vP IXV8 LT25 fAph yB AYng
```

The existing evidence root supplies the adopted `ra0_evaluate.py` scorer, hash `6217af0b…932c` (§9).
Source comparisons have an explicit 30-minute deadline per source and use no capture/network/persistence.
Only our invalid v1 comparison processes were stopped, by their exact command ownership. Steam and Claude
were left running. No downloader, video conversion worker, Tracker, OBS or AC6 was started. Full-frame checks
owned each ffmpeg invocation, bounded it to 60 s and waited for completion. Temporary truth/audit and T1 roots
were removed; final process residue is checked before publication. Diagnostics add no product process/thread,
dependency, fixture edit, DB/schema/migration, release or tag action.

GitHub was checked in the requested order. Main remains `039d3bc1…`, exact-main CI 37568479507 SUCCESS
attempt 1, stable v1.2.0; #73/#72 OPEN, #68/#66 CLOSED. PR #65 is the only pre-existing open PR,
OPEN/DRAFT at `889d9e18…`; no #65 branch or worktree was modified. The existing #73 worktree was clean at
adoption and was reused without reset/rebase/new generation. Maximum two generations is maintained when
the RA-0 draft PR is opened. Issue #6's old body contains historical checkpoints; current main/CI, newer
owner comments and current priority sections supersede them. No conflict requires a product-scope change.

## 12. Independent review

Pending fresh-context review of the completed report/PoC and underlying local evidence. No RA-0 completion,
owner Acceptance, merge or RA-1 start is inferred from the automated results above.
