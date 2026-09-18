# T1 fixture corpus

Stored, reviewed AC6 pixels and the scenarios built from them. `python tests/run_t1.py` replays every record through the production recognition path and is the only T1 gate (issue #14, MASTER_REQUIREMENTS §38–§39).

Fixtures are test assets, not runtime logs. Add one only when it covers a new edge case or regression, and never edit an expected value to make a failing test pass.

## Layout

```text
tests/fixtures/
  README.md                 this contract
  families.json             every family, and whether it is implemented or reserved
  required-coverage.json    coverage tags T1 must prove with a passing case
  legacy-coverage.json      where each pre-#14 pixel assertion went
  *.ppm                     the 25 legacy pixel files, kept at their original paths and bytes
  results/                  implemented
    win/  lose/  draw/  clear/  phase/  negatives/  sequences/
  match_metadata/           reserved (#15)
    ranked_single/  ranked_team/  custom_single/  custom_team/
  ranks/                    reserved
    pre_s/  s_rank/  season_boundaries/
  season/                   reserved (#28, Revision 4 draft)
    pre_reset/  transition/  post_reset/  multi_season/
  rating/                   reserved (#28)
    pre_s/  s_rank/
  opponent_builds/          reserved (#17)
  self_builds/              reserved (#27)
```

A reserved family has no recognizer and no replay adapter. A record placed in one fails the run: it is never counted as skipped, and no placeholder PASS exists. New families and categories are added to `families.json` without restructuring what is here. pre-S (UNRANKED through A4) and S rating presentations stay separate categories because the game presents them as different systems.

New images live next to their record under `results/<category>/`. The legacy PPMs stay at the fixture root, byte-for-byte: they are not moved, re-encoded, cropped, resized or colour-adjusted. `.gitattributes` marks fixture images binary, because two of them contain no NUL byte and Git's CRLF conversion would otherwise shift their pixels on a Windows checkout. It also keeps fixture JSON at LF on every checkout, because the corpus SHA-256 in a T1 report is taken over the metadata bytes as well.

## Records (schema version 1)

Every record has `schema_version`, `id`, `family`, `category`, `input`, `truth`, `checks`, `coverage`, `provenance` and `review`. Unknown fields, versions, adapters, enum values and debug paths are errors. The strict loader also rejects duplicate JSON keys, `NaN`/`Infinity`, a byte-order mark and oversized metadata.

The three things below are never mixed:

| | Meaning | Where it lives |
|---|---|---|
| `truth` | What a human confirmed the pixels show | the record |
| `checks` | Which production outputs are asserted, and their expected values | the record |
| actual | What production produced | runner reports only; the worker is never told the expectation |

### Result vocabulary

- `truth.result` is one of `win`, `loss`, `draw`, `none`, `unknown`. The directory is `lose/` for historical reasons; the runtime value is `loss`.
- `none` means the screen shows no match result (gameplay, a PHASE banner, a menu). `unknown` means the result cannot be determined from the evidence.
- `truth.match_type` and `truth.match_format` are always `unknown` for a result ROI. The ROI carries no evidence of mode or format, so they are never inferred. Rank and season are not recorded at all.

### Image records (`input.kind: roi`)

`input` names a fixture-root-relative `path`, its `format` (`ppm` or `png`), `width`, `height`, exact `bytes` and `sha256`.

- Paths use forward slashes. Absolute, drive, UNC, `..`, device-name, symlink and reparse-point paths are refused, and the resolved path must stay under the fixture root.
- Decoding is strict. PPM must be binary P6 with maxval 255, no header comments and no trailing bytes. PNG must be 8-bit RGB, non-interlaced, with only IHDR/IDAT/IEND chunks and valid CRCs.
- The only conversion is an exact RGB → BGRA copy with alpha 255: no resize, gamma, contrast, sharpening, colour correction or interpolation.
- Limits: 16 MiB and 3840×2160 pixels per image, 64 MiB for all images, 1000 records.

`checks.adapter` is `result_classifier.v1`. `checks.frame_class` is the expected `ResultClassifier.classify_bgra` class. Optional `checks.debug` entries assert named classifier debug values with `equals`, `at_least`, `at_most`, `above` or `below`.

Every image file must be described by exactly one record; an orphaned image fails the run.

### Sequence records (`input.kind: sequence`)

A sequence references image records by id and never copies pixels. Each step has an `id` (`s01`…) and a strictly increasing `at_ms`, and one of:

- `frame_source` capture: `frame` (an image id) or `gap` (`capture_unavailable`), optionally with `capture: {discontinuity, identity_changed}`.
- `wgc_boundary` capture: `wgc: {target, sample}`. The sample `{frame, captured_ms, timespan}` is handed to the real `GameCapture.grab` as the capture worker's reply, so production's own freshness, repeated-timestamp, identity and geometry checks decide whether the frame is used. One ROI geometry is replayed per sequence and it must be one `result_region` can produce, so its height is at least 40.
- optionally `after`, one of:
  - `after_undo`: what `server.undo_result` tells the gate and detector;
  - `external_mutation`: what `server.reset_stats` tells them;
  - `manual_win`: a manual WIN entered while the detector runs. `server.record_result` is source-agnostic and the ResultGate is "shared by automatic and manual result sources", so the replay performs that one gate arbitration — `try_accept` with the server's 5 s cooldown, and `external_mutation()` only if it is accepted. History and stats are not replayed. This is the only way a gate rejection can be observed at all: the detector's own post-result lock is stronger than the gate cooldown, so an automatic result can never arrive inside it.

Metadata can only name these fixed values. It can never carry code, an expression, a shell command or a process to launch. At most 256 steps.

`checks.adapter` is `result_detector_run.v1`. `checks.steps` must assert every step:

- `frame_class` and `detection` are required;
- optionally `gate`, `gameplay_activity`, a subset of the state snapshot, capture flags and health;
- `after_gate` is required exactly on a step whose `after` is a manual result, and is refused anywhere else. It asserts that result, its `source` and whether the real ResultGate accepted it.

`checks.totals` asserts accepted WIN/LOSS, detections, gate rejections, classified frames and `detector_errors: 0`. The detections in `truth.expected_results`, the per-step detections and `totals.detections` must agree.

The runner also enforces contract checks no record can switch off:

- every WIN/LOSS detection reaches the gate exactly once;
- DRAW and non-results never reach it;
- the loop never leaves its normal poll path;
- `run()` reaches its cleanup.

### Provenance and review

"It is already in Git" is not a review.

- **Image records** carry `provenance.origin`, whether the image is `synthetic`, its `template_partition` and the migrated `legacy` manifest entry. Their review has three parts:
  - `review.truth`: a human confirmed what is shown;
  - `review.privacy`: the full checklist of player/opponent name, Steam identifier, notification, overlay, desktop, local path, token or secret, and embedded metadata;
  - `review.redistribution`: where the asset came from. This is not a licence determination.
- **Sequences** inherit privacy and redistribution from the frames they reference.

`template_partition`:

- `training` and `holdout` follow `tests/template_training.json`.
- `unmapped` means that file names source photos (`IMG_6340`…`IMG_6347`) but the repository never recorded which fixture each photo became, so no holdout status is claimed.
- `not_applicable` means the image is not a template source.

Do not commit a whole diagnostic ZIP, full-screen captures when a result-region ROI will do, or production user data. Do not adopt external images automatically. When capturing a new ROI, keep the whole production result region (20% / 43% / 60% × 7% of the client): the classifier depends on density, span and background, not only the glyphs. An artificially scaled image is not evidence for another resolution.

## Replay

```text
validated record
  → worker process (isolated LOCALAPPDATA / TEMP / TMP / cwd, guards installed and probed)
  → FrameSource or real GameCapture.grab over a fake capture worker
  → real ResultDetector.run → real ResultClassifier + motion helpers → real ResultStateMachine
  → accepted-result callback: real ResultGate.try_accept(5 s), then detector.external_mutation()
  → observations → comparison in the runner → reports
```

Only native capture is replaced: the WinApi instance, the mss desktop context and, in `wgc_boundary` mode, the capture process, its pipe and its job object. The `time` name inside the replayed modules is bound to a virtual clock, not the global `time` module:

- **sequence time** is `at_ms`;
- **capture time** is what the state machine observes;
- **processing time** (capture plus `processing_ms`) is what `external_mutation()` and the gate see.

Real time bounds the worker, the case and the suite.

DRAW is detected and reported (`health.last_result = draw`). It never reaches the gate, never changes WIN or LOSE and never becomes a persisted result. Persistence itself is T0/T2 territory and is not replayed.

## What T1 PASS means

T1 PASS is reported only when all of the following hold:

- the canonical corpus loads;
- every case replays through the real classifier, state machine and gate and matches its record;
- every tag in `required-coverage.json` is proven by a passing case;
- every worker exits cleanly with no surviving child process and no residue.

Any of these fails the run:

- an empty corpus;
- missing required coverage;
- a duplicate id or JSON key, a bad schema, hash or decode;
- a path escape or an oversized input;
- an unknown adapter, check or version;
- a case that did not run;
- a worker timeout or abnormal exit;
- a cleanup failure, a leaked child process or temp residue.

There is no skip and no expected failure. A run against any other corpus (`--fixtures`) is reported as non-canonical and is never a T1 PASS.
