# Screenshot investigation (2026-09-09)

Baseline: origin `codex/wgc-rc-validation-20260908`, `b907685`.

## Findings and limits

- The reported AC6 incident has no event-level capture trace, so its root cause
  is **not yet established**. Do not mark it fixed or release-ready from the
  dummy-window results below.
- On the available Windows desktop, `NVIDIA GeForce Overlay DT` is a visible
  full-screen HWND above ordinary windows. The existing `region_unobscured`
  check rejects this overlap before MSS capture. Its window region was not an
  empty region, it was not DWM-cloaked, and its capture affinity was zero.
  Layered alpha was zero with flags zero: that is not proof of `LWA_ALPHA`
  transparency. No NVIDIA/title/style whitelist or safety bypass was added.
  Whether this HWND blocked the original AC6 incident remains unconfirmed.
- With a foreground, unobscured dummy game and the actual production Tk
  renderer/tick, the owned worker saves the real compositor pixels. This
  rules out unconditional failure of the rendering/spawn/MSS/save path on this
  machine, not AC6-specific fullscreen/HDR/occlusion conditions.
- A separate reproducible defect: `--panel-opacity 0` intentionally hides the
  HUD background, but capture required that HWND to be visible. A native test
  failed before the fix. Capture now requires the text and effect windows,
  plus the background only when its configured opacity is positive. The
  installed Overlay used the default opacity, so this does **not** explain
  the reported incident.
- The installed windowless Overlay does not send its usual startup messages
  to `startup.log`. Screenshot diagnostics now use an independent bounded
  file, including the parent observation/spawn request and worker result.

## Diagnostics and AC6 follow-up

`%LOCALAPPDATA%\AC6WinLossTracker\diagnostics\effect-screenshot.jsonl`
(256 KiB rotation, one backup) records event ID, process ID, stage/result and
skip reason. An occlusion rejection also records the blocking HWND, process
basename and rectangle; no window titles or images are logged. Logging errors
are isolated. Typical reasons include changed target/client, deadline, hidden
Overlay, lost foreground, occlusion, out-of-desktop bounds, missing banner
pixels and duplicate/pending reservation.

After deploying this RC locally, reproduce a visible 5-win milestone with
screenshots enabled. Correlate that event's `observed`, `spawn_requested`,
`worker_started`, and terminal record with PNG/pending files. If the reason is
`occluded`, compare the blocker with the active NVIDIA overlay and repeat after
the user disables that overlay. Do not disable it or change game settings
automatically. Fullscreen/HDR cases that cannot show the banner in MSS remain
fail-closed; no banner is synthesized.

## Regression coverage

`test_native_effect_screenshot.py` exercises real production Tk rendering and
tick, owned spawn using `pythonw.exe`, MSS, PNG encoding/atomic promotion,
5/10/50 milestones, hidden 0% background, exactly one file per event, opaque
window rejection, diagnostic reasons and child cleanup. Only the dummy
executable identity and destination are substituted. It requires an unlocked
desktop and `AC6_RUN_SCREENSHOT_INTEGRATION=1`; all data/output are isolated.
Hosted CI runs the headless/unit regressions; native checks remain opt-in.

Local full regressions passed on Windows Python 3.13.15 and 3.14.7, as did
PowerShell 7/5.1 installer/bootstrap/README/uninstaller checks and both Python
dependency checks. The final serial native run passed all four cases on each
Python version, including process-exit checks. An earlier 3.13 native run
overlapped the other regression suite and missed its first save; its temporary
trace was not retained, so the cause cannot be assigned. Failure output now
includes the diagnostic trace. This transient failure is not evidence that the
reported AC6 incident is resolved.

The previous native test painted the banner into the dummy game's own canvas
and called the saving function in-process with no Overlay HWNDs. It could not
exercise hidden background panels or windowless owned workers.

## Separate Dashboard change

The summary uses `DASHBOARD_RECENT_MATCH_LIMIT = 50`, with the existing bounded
SQL query, order and cache. The history view has a vertical scrollbar and
preserves its scroll position; unchanged polls do not rebuild rows. Tests use
61 stored matches, verify newest 50/lifetime 61, one query across 21 reads,
and actual Tk scrolling. Storage and reset semantics are unchanged.
