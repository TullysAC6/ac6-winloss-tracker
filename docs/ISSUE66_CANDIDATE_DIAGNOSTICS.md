# #66: passive candidate-transition evidence

Owner authority: [Issue #66](https://github.com/TullysAC6/ac6-winloss-tracker/issues/66).
The 2026-10-03 #15-5 T3 observed one FINAL_WIN, then CLEAR reset a one-hit
candidate; no WIN reached persistence. The following LOSS persisted. Metadata
was OFF. **The exact classifier cause remains unknown.** This change provides
future evidence, not a detector repair, and does not require reproducing a miss.
Original failed data/labels remain unchanged outside Git. PR #65 remains
OPEN/DRAFT at reviewed `91dffacdddb48d2d96413617a904b72163873e5e`; no sync or T3
is part of #66. Stable remains v1.2.0; this work is unreleased.

## Trigger and lifetime

Each detector keeps at most **three** result-strip BGRA frames with complete
existing classifier debug, native/monotonic capture and observation timestamps,
motion/activity, sampled state before/after, and existing capture identity,
source, status, geometry and discontinuity flags. No new native/window query or
capture runs. Foreground is not invented: guarded foreground fallback status
records its existing guarantee, while WGC may be background capture.

With the existing two-hit confirmation, three slots contain the preceding
frame, candidate and decisive reset. Only an unconfirmed WIN/LOSS candidate
followed by candidate change, nonincreasing hits or rejection flushes a bundle.
Both directions are symmetric. Gap/identity/discontinuity/core-error boundaries
flush the preceding candidate with a no-pixel boundary record, never fabricate
a missing frame. Normal successful results clear without a new bundle.
Enable changes and shutdown clear the ring. Other frames evict the oldest slot;
there is no full-match archive or post-trigger capture window.
If a reset frame starts a different unconfirmed candidate, that same immutable
frame seeds the cleared ring so its own next reset remains observable.

## Bounds and local storage

Per-ROI cap2MiB; oversized/invalid diagnostic data are discarded without
changing classification. Three ROI slots (6MiB maximum), one waiting bundle and
one in-flight bundle bound retained pixels to18MiB plus a transient copy up to
2MiB. The native1920x1080 strip is1152x75 (345,600 bytes), so routine retained
pixels are about1MiB. Debug/state are fixed production dictionaries; serialized
context is capped128KiB per bundle. No secrets or arbitrary user fields enter.

New files use existing `diagnostics_dir()`:

```text
<data root>/diagnostics/candidate-bundles/<random id>.zip
  manifest.json     schema_version1, trigger, boundary, ordered frames
  0.bgra … 2.bgra   exact result ROI pixels only, width/height and SHA256
```

ZIP_STORED avoids image conversion/encoding/compression. Before publishing,
old completed bundles are pruned to allow at most **eight** total, plus one
in-progress `.partial` file. Each is at most6MiB pixels +128KiB context + ZIP
headers. Atomic rename publishes only complete bundles. Failed writes delete
their partial file; interrupted-write remnants are removed on the next write.
Completed ZIPs join the existing explicit local diagnostic export. No network
upload, continuous screenshot files, preferences/schema/version/dependency
changes or change to Effect Screenshot policy. Existing diagnostic result-ROI
retention already applies independently of optional milestone screenshots.

## Non-interference and observer effect

Classifier additions expose already-computed final geometry and win_ok/loss_ok
in debug only. All threshold expressions, decision order, ResultStateMachine
methods, two-hit rule, ResultGate, CLEAR/arming, poll cadence, WGC/MSS, SQL,
streak and DRAW behavior are unchanged. The new `diagnostic_snapshot` only reads
fixed fields under the existing lock, including monotonic timestamps omitted
from compact legacy telemetry. New observer failures are caught at their own
boundary; genuine core exceptions still follow the existing error path.

Before/after snapshots bracket observe but do not add a larger state lock;
concurrent manual mutations may occur between them. Offline state analysis
explicitly reports this limitation. It must not claim to reconstruct unsampled
ResultGate/history callbacks. Stored ROI is sufficient for exact classifier
replay; recorded state/motion/capture context localizes later layers without
inventing unavailable evidence.

Extra normal work: two small locked read snapshots, fixed context/ROI copies
and a three-slot deque operation. Copying/queue submission happens **after**
the original result callback. At the first suspicious flush, one daemon thread
is started; no new process or native capture. One waiting queue slot drops
excess diagnostics. The writer performs JSON, SHA256 and disk work independently
without a detector-held I/O lock. It blocks on an idle queue, with no wakeup
polling. Shutdown signals/drains inside the detector's unchanged4s server join
budget, allowing0.2s to join the diagnostic thread. A permanently stalled OS
filesystem may outlive that join as a daemon until process exit; never delay
results or increase a watchdog. Normal shutdown/I/O failures must leave no
thread or partial files. A blocked writer is not replaced/overlapped. Restarted
detectors can replace a closed, exited writer.

Synthetic local Python3.14.7 measurement (1,000 copy/context iterations,
1152x75 bytes; existing normal fixture debug): median0.0458ms, p950.0582ms,
max0.1203ms. Traced retained1,045,395 bytes, peak1,395,244 bytes. Existing757x50
classifier fixture median34.423ms (12 iterations). These are component costs,
not AC6 frametime or hardware-independent thresholds; nativegame performance
was not measured. The mechanism avoids new hot-path disk/encoding waits.

## Offline replay

Run from the build to compare (no running Tracker/AC6 required):

```powershell
python scripts/replay_candidate_bundle.py <bundle.zip> > replay.json
# Optional independent template selection:
python scripts/replay_candidate_bundle.py <bundle.zip> --templates <detector_templates.json>
```

The helper validates bounded manifest/pixel sizes and digests, reads BGRA
directly into that build's ResultClassifier, returns recorded/replayed classes
and full debug, and analyses each frame from its recorded sampled pre-observe
state with the captured monotonic clock. No sleeps/wall-clock substitution.
Context records classifier source SHA256 and a fingerprint of the actual
loaded templates/bins; replay reports its loaded fingerprint. Copy the bundle
locally and run the same pixels on relevant main/candidate builds to compare
exact failing conditions. No code is executed from a bundle and nothing is
extracted into arbitrary paths. Old evidence lacking decisive pixels cannot be
retroactively repaired or relabelled.

## Verification and completion

T0 owns ring bounds, long normal sequences, symmetric reset/candidate loss,
success/no-flush, exact decisive frame, immutable pixels/context, allocation
and callback failure, production loop non-interference and preservation of
core exceptions. A pinned AST check compares exact-base classifier, state
machine and detector loop after removing only named diagnostic insertions.
T1 retains all53 canonical cases/labels/digest and now exercises the observer
through an in-memory replay recorder with the same API.
Relevant T2 owns the actual writer, isolated root, eight-file retention,
export, bounded saturation, blocked I/O, failure/partial cleanup, shutdown and
canonical ROI offline replay. Full CI retains existing lifecycle/source-install
coverage. No new port/process/lock ownership; unrelated gameplay/native capture
T2 cases are unchanged. T3 is proposed N/A for passive diagnostics only, subject
to independent reviewer confirmation.

Required closure: local gates, fresh Astra/High review, fixes/reruns, exact-head
CI, #66-only merge, exact-main green, issue/coordination bookkeeping, cleanup.
After closure **STOP**. A separate task will sync/revalidate PR #65 and repeat
the full #15-5 owner T3; no underlying WIN repair or #15-6 is authorized here.
