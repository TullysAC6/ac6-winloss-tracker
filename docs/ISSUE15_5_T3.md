# #15-5 owner real-game T3

Run only after the implementation PR's reviewed exact head has green CI. Keep that
head/worktree unchanged during T3. No merge or owner Acceptance is implied.
Use an isolated data root; these steps do not copy or alter your usual history.

## Historical failures and stop rule

Earlier acquisition T3 on1bf2ed53 failed capture_gap;91dffac preserves the strict
native captured_at > newest monotonic gap correction. The2026-10-03 T3 on91dffac
also FAILED: OFF-baseline first WIN had one FINAL_WIN then CLEAR, no saved WIN;
second LOSS saved. Exact classifier cause remains UNKNOWN. #66 diagnostics are
passive evidence, not a WIN fix. Both historical failures remain preserved.

If any natural WIN/LOSS miss recurs: STOP T3/gameplay, preserve the isolated root
and diagnostics/candidate-bundles/*.zip, then replay offline on the current main
and candidate. Do not tune thresholds, repair history, or play retries for green.
An expired/UNKNOWN acquisition or confirmation is reported honestly; no timeout
increase or repeated matches to get a pass.

## Setup (PowerShell)

Close the normal Tracker yourself first. Leave AC6 running. Open PowerShell and paste:

```powershell
$candidate = 'C:\Users\makis\OneDrive\ドキュメント\ChatGPT\AC6 Tool Dev\ac6-wt-issue15-5-main-sync'
$python = 'C:\Users\makis\AppData\Local\Programs\Python\Python314\python.exe'
$t3Base = Join-Path $env:TEMP ('AC6-Issue15-5-sync-T3-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
if (Test-Path -LiteralPath $t3Base) { throw 'Existing T3 root: preserve it and use a new timestamp.' }
$env:LOCALAPPDATA = $t3Base
if (Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue) { throw 'Close your normal Tracker first; no process was stopped.' }
Set-Location -LiteralPath $candidate
$candidateHead = (git rev-parse HEAD).Trim()
# Compare with the exact reviewed/CI-passing SHA supplied in the handoff.
# Do not proceed if different or if git status --porcelain prints anything.
git status --porcelain
$candidateHead
$t3Data = Join-Path $env:LOCALAPPDATA 'AC6WinLossTracker'
New-Item -ItemType Directory -Path $t3Data -Force | Out-Null
if (Test-Path (Join-Path $t3Data '.runtime.json')) { throw 'Existing T3 runtime: inspect/close its Tracker first.' }
& $python control.py metadata-status
```

The last command initially says runtime unavailable; that is expected before startup.
In this same PowerShell, start the isolated Tracker in a hidden window:

```powershell
Start-Process -FilePath $python -ArgumentList 'launcher.pyw' -WorkingDirectory $candidate -WindowStyle Hidden
```

Wait for the Player Overlay. Keep this PowerShell for commands (its LOCALAPPDATA
points to T3 data). Existing source dependencies are used; no installer or live-data
migration is run. If the launcher reports a failure, stop and report it; do not
increase readiness/watchdog limits. Start with a clean T3 root for a new attempt,
after inspecting owned runtime/processes; never delete an active root.

## OFF baseline

```powershell
& $python control.py metadata-off
& $python control.py metadata-status
```

Status must be disabled. Play exactly ONE baseline match, then STOP and verify the normal WIN/LOSE count and
streak before any next match. A miss fails T3: preserve the #66 bundle and stop. Note Tracker CPU/memory in Task Manager and your game responsiveness.
No lobby_worker.py should appear in process command lines while OFF.

## Match 1: explicit acquisition and confirmation

Set AC6 to the accepted **English RANK MATCH: SINGLE, native 1920x1080** lobby.
Use a visible nonminimized native client (for example borderless/windowed 1920x1080)
so switching to PowerShell does not minimize AC6. A gap after successful capture
still discards pending/confirmation; it must not be ignored to obtain a pass.
The previous candidate's T3 was FAIL/capture_gap; start again on the PR's new
reviewed/CI-passing exact head, not the old `1bf2ed53` or its original data root.
Do not use the future-rank reference images as a recognition test. Then:

```powershell
& $python control.py metadata-on
& $python control.py metadata-acquire
```

Immediately Alt+Tab back to AC6 within the printed 3-second focus allowance.
After acquisition, return to PowerShell and run:

```powershell
& $python control.py metadata-status
```

Expect pending (ranked/single), no resident child. UNKNOWN is a failure to capture
supported evidence, not proof the match is unranked. If UNKNOWN, report the reason;
do not change labels/geometry/timeouts to force a pass.

Play exactly ONE metadata-bearing match. Then **STOP gameplay before another
accepted result** and immediately run metadata-status. Show its fixed request_id,
event_id, WIN/LOSS and recorded_at to the owner, and verify that event exists in
normal history. The owner personally attests: "this displayed event is the same
match whose lobby I acquired; no intervening match/mode change occurred."

Only AFTER that explicit attestation may the following command run, using the
fixed IDs just shown. If the owner runs it themselves, --same-match is their
personal attestation. An assistant/observer must wait for the owner's explicit
answer; it must never infer identity from timing or confirm on the owner's behalf.
No second match before confirmation/write verification. The unchanged window is
60 seconds; if expired, stop/report, do not extend it or fabricate a confirmation:

```powershell
& $python control.py metadata-confirm --request-id 'PASTE_REQUEST_ID' --event-id 'PASTE_EVENT_ID' --same-match
& $python control.py metadata-status
```

Initial writing is asynchronous; run status again after completion and expect
last_write.state=saved. WIN/LOSE and streak must match normal counting. Copy only
the status output; do not copy runtime token contents.

Read the exact saved snapshot (replace the event ID; read-only facade):

```powershell
$event = 'PASTE_EVENT_ID'
& $python -c 'import sys; from app_paths import data_dir; from optional_enrichment import OptionalEnrichmentService; s=OptionalEnrichmentService(data_dir(),active=True); r=s.lookup_snapshot(sys.argv[1]); print(r.status,r.health); print(r.snapshots); s.deactivate()' $event
```

Expect ranked/single, self_rank=None, opponent_rank=None, recognized,
rank-single-header.v1. A conflict must preserve earlier facts rather than relabel them.
Note acquisition completion time, Tracker CPU/memory delta and game responsiveness;
report observations, with no arbitrary pass threshold.

## Match 2: no acquisition

Only after the exact snapshot above is verified, play ONE next match **without metadata-acquire**. Normal result/streak must update.
Status must not offer the old event as a confirmation for this match. Its metadata
must remain unknown/unwritten by #15-5. Verify the second fixed event with the
same read-only lookup command: it must have no #15-5 snapshot; NULL ranks alone
are not proof of no inheritance. The previous last_write is a historical
status receipt, not evidence assigned to Match 2.

## Cancel/OFF and cleanup

After result checks, at the supported nonminimized lobby request acquisition
(use the usual3-second return-to-AC6 allowance), then cancel. Check no pending or
confirmation. Separately request again then switch OFF; check disabled and no
child. These are control checks, not extra gameplay matches:

```powershell
& $python control.py metadata-acquire
# Return to AC6 during the printed allowance; return to this shell for cancellation.
& $python control.py metadata-cancel
& $python control.py metadata-status
& $python control.py metadata-acquire
# Return to AC6 during the printed allowance, then disable from this shell.
& $python control.py metadata-off
& $python control.py metadata-status
```

No pending/confirmation/resident worker may remain after bounded cleanup; status
may be polled at most once per second while the existing5-second attempt exits.
Do not increase waits/watchdogs. Natural DRAW consumes pending with no WIN/LOSS
row; automated evidence covers DRAW, so do not schedule gameplay just to force it. Close this isolated Tracker
through its normal UI. Check without stopping any other process:

```powershell
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like ('*' + $candidate + '*') } | Select-Object ProcessId,Name,CommandLine
Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
Get-ChildItem -LiteralPath $t3Data -Force | Where-Object { $_.Name -match 'runtime|lock|tmp' }
```

Report PASS/FAIL for OFF, Match1 saved fields/result/streak, Match2 no inheritance,
cancel/OFF, shutdown residue and performance observations. Keep the isolated T3
data until evidence is recorded. Explicitly say **T3 PASS** only if all checks pass.
Any DRAW you happen to observe must consume pending; a separate DRAW gameplay
session is not required when automated evidence passes. STOP; no next slice.

## Optional passive observer — authorization before starting

No observer is prepared or started by this sync task. The old continuous-two-match
observer must not be reused. If the owner later requests background observation,
it must stop for each checkpoint above: OFF baseline verification, fixed-event
human attestation, confirmed snapshot verification, no-acquisition verification.
It cannot prevent a player from entering another match, so the owner must honor
the stop point. No readiness-to-play message is issued during this task.

Allowed design: read-only status/history sampling at most once/second; process,
CPU/RAM sampling at most once/5seconds; bounded total lifetime; known PID ownership;
no focus/window changes/game inputs, automatic acquisition/confirmation, history
mutations, or retry-until-green. Declare these rates/load before actual use.
Status/read-only SQLite and diagnostics monitoring still consume local CPU/I/O;
no AC6 performance PASS is inferred from synthetic test measurements.
Existing user Tracker must be closed by the owner before T3 setup; this task does
not stop it. Retain the full failed/successful T3 root and diagnostics for evidence.
