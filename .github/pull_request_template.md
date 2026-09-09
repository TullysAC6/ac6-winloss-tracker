<!--
Standard PR handoff contract — see docs/DECISIONS.md and docs/MASTER_REQUIREMENTS.md section 40.

Every field is required. "Not changed" is mandatory: it is what stops the reviewer rediscovering
the blast radius from scratch. Do not claim "No impact" for a path you did not check — write
"not checked" instead.

A documentation-only PR may answer N/A to the implementation fields, but must still fill in
"Implemented", "Changed files" and "Not changed".
-->

## Implemented

<!-- What this PR does, in the terms a reviewer needs. -->

## Changed files

<!-- Path + one line of why. -->

## Not changed

<!-- REQUIRED. The nearby paths this PR deliberately leaves alone: detector thresholds,
     ResultGate, CLEAR re-arm, WGC/MSS safety conditions, screenshot safety conditions,
     authoritative result write, install.ps1, version strings — whichever apply. -->

## New dependencies

<!-- None, or: purpose, necessity, CPU/RAM/disk impact, transitive deps, vulnerability review,
     and why an existing dependency cannot do the job. -->

## Tests

<!-- T0 — unit / DB / schema / migration / config / static. What was added, what was run. -->

## Fixture replay

<!-- T1 — which fixtures replay through this path, and the result. State if none exist yet. -->

## Real-device test required

<!-- T3 — the smallest real-AC6 set this change actually needs. Not a full manual regression suite. -->

## Known limitations

## Security impact

## Performance impact

<!-- Baseline delta where relevant: Tracker OFF / current accepted Tracker / Tracker + this feature.
     Do not invent absolute numbers. -->

## Process impact

<!-- REQUIRED to be explicit: does this introduce any new process, thread or worker?
     Any long-running process? PID ownership, timeout and cleanup. -->

## DB migration

<!-- Schema version before/after, forward migration behaviour, rollback/backup expectation,
     old-fixture migration coverage. Or: none. -->

## Feature flags

<!-- Flag names and defaults. New optional features default OFF before acceptance.
     Confirm the OFF path was tested at T2. -->

## Depends on

<!-- "Depends on PR #..." if this branches from unmerged work, or: none (branched from main). -->

---

## Acceptance state

<!-- Tick only what is actually true. "Code exists" is not "accepted". -->

- [ ] T0 — unit / DB / static passes
- [ ] T1 — fixture replay passes (or: no fixture path exists for this change)
- [ ] T2 — isolated E2E / lifecycle / process cleanup / feature-flag OFF passes
- [ ] T3 — confirmed by the user in a real AC6 session

Merging before T3 is not permitted for anything that can change a recorded result.
