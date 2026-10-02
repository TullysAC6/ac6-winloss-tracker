# #15-5 owner real-game T3

Run only after the implementation PR's reviewed exact head has green CI. Keep that
head/worktree unchanged during T3. No merge or owner Acceptance is implied.
Use an isolated data root; these steps do not copy or alter your usual history.

## Setup (PowerShell)

Close the normal Tracker yourself first. Leave AC6 running. Open PowerShell and paste:

```powershell
$candidate = 'C:\Users\makis\OneDrive\ドキュメント\ChatGPT\AC6 Tool Dev\ac6-wt-issue15-5-runtime'
$python = 'C:\Users\makis\AppData\Local\Programs\Python\Python314\python.exe'
$env:LOCALAPPDATA = Join-Path $env:TEMP 'AC6-Issue15-5-owner-T3'
if (Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue) { throw 'Close your normal Tracker first; no process was stopped.' }
Set-Location -LiteralPath $candidate
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

Status must be disabled. Play one match and verify the normal WIN/LOSE count and
streak. Note Tracker CPU/memory in Task Manager and your game responsiveness.
No lobby_worker.py should appear in process command lines while OFF.

## Match 1: explicit acquisition and confirmation

Set AC6 to the accepted **English RANK MATCH: SINGLE, native 1920x1080** lobby.
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

Play exactly one match, then immediately check status. It should show confirmation,
fixed request_id/event_id, result and recorded_at. Within its 60-second window,
confirm only if that is the match you just played with no intervening mode/match:

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

Play the next match **without metadata-acquire**. Normal result/streak must update.
Status must not offer the old event as a confirmation for this match. Its metadata
must remain unknown/unwritten by #15-5. The previous last_write is a historical
status receipt, not evidence assigned to Match 2.

## Cancel/OFF and cleanup

At the supported lobby, request acquisition then cancel (or switch OFF):

```powershell
& $python control.py metadata-acquire --delay 0
& $python control.py metadata-cancel
& $python control.py metadata-off
& $python control.py metadata-status
```

No pending/confirmation/resident worker may remain. Close this isolated Tracker
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
