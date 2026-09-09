# Decisions

Decisions that must not be reversed silently. If one of these needs to change, change it here first and say why.

---

## Auto update

**Decision: the Tracker does not update itself.**

It checks whether a newer public release exists and reports the result. That is all.

Instead:

- "Check for a new version" compares the installed version with the latest published GitHub release. Metadata only — no asset download.
- "Install the new version" opens the official Releases page. Updating runs the same `install.ps1` the user installed with.

Explicitly not done: background auto-update, self-replacement, automatic download or install, automatic exit to make way for an installer.

Why: the install path is hash-verified and pinned to an immutable commit, and it stops the running Tracker in a controlled way. A self-updater would have to duplicate that logic inside a process that is itself being replaced.

---

## Process safety

**Decision: never double-launch, and never leave an orphan.**

- **Server** — the configured port bind (`SO_EXCLUSIVEADDRUSE`) is the ownership boundary. Nothing that mutates user state happens before that bind succeeds.
- **Overlay** — a named mutex (`Local\AC6StatsOverlayV22`) makes a second overlay impossible.
- **Launcher / dashboard** — a runtime file plus process liveness plus a health probe. A stale runtime file is never treated as a running instance.
- **Workers** (WGC capture, effect screenshot) — spawned into a Win32 job object with kill-on-close, and the child does no native work until the parent has installed that containment.
- **Startup failure** — anything already spawned is collected before the failure is reported.
- **Normal and abnormal termination** — no orphan process survives. The overlay exits by itself when the server dies, and removes its own heartbeat file.

Why: this application starts several processes that hold a camera-like capture and a topmost window. An orphan is invisible to the user and keeps holding resources.

---

## Parallel AI development

**Decision: two assistants never edit the same working tree.**

- One branch and one worktree per assistant.
- Nobody resets, rebases or force-pushes another assistant's branch.
- Nobody edits `main` directly.
- If work overlaps, the later branch merges the other one in and resolves it on its own branch — never the reverse.

Why: this repository has already had two assistants independently reach the same conclusion and edit the same file. Separation makes that a merge conflict, which is visible, instead of a lost edit, which is not.

---

## Analytics data volume

**Decision: statistics must not make logs grow without bound.**

- The persisted unit is the **match row** in `history.db`. Statistics are derived from those rows, not from replayed logs.
- Detector telemetry stays bounded: an in-memory frame ring plus a size-capped, rotated `detector.jsonl`; screenshot outcomes in a rotated `effect-screenshot.jsonl`.
- Analytics reads `history.db` read-only (`mode=ro` + `PRAGMA query_only=ON`) so it can never grow or corrupt it.
- Anything that wants finer-grained data (per-match opponent details) adds columns or a related table on the same per-match unit — not a new append-only log.

Why: the Tracker runs for a whole play session next to a game. Diagnostic value has to stay bounded in disk and in write rate.

---

## Result-path integrity

**Decision: enrichment never weakens the accepted result.**

- The authoritative write is the stats mutation plus the `matches` row. Optional enrichment (`match_contexts`, diagnostics, screenshots, effects) happens after it and separately.
- A failure in any optional path is recorded and ignored; it never rolls back or blocks an accepted result.
- Detection thresholds, `ResultGate` and CLEAR re-arm are not changed to make another feature work.

Why: the whole point of the tool is a correct win/loss count. Everything else is secondary and must fail in a way that leaves the count intact.

---

## Win-rate definition

**Decision: `win rate = WIN / (WIN + LOSE) * 100`. DRAW is reported but excluded from the denominator.**

This is what `server.status_payload` and `HistoryStore.lifetime_summary` already do. Every new statistic reuses it. Changing it would silently rewrite every historical number the user has seen.

---

## Destructive history operations

**Decision: only the owning server process writes `history.db`, and destructive operations are transactional and confirmed.**

- The settings window asks the running Tracker over its localhost control API; it never writes the database itself.
- Delete-before-a-date is capped at today, and a cutoff that would remove matches the still-open session counts is refused — otherwise the session and lifetime displays would disagree.
- Every destructive action shows the exact number of affected rows and the exact boundary before it runs.

---

## Supported game modes

**Decision: RANK MATCH: SINGLE only.**

CUSTOM MATCH and RANK MATCH: TEAM are out of scope. Detection, counting, overlay updates and history recording are not verified for them, and the README says so. This is a scope decision, not a limitation to be quietly worked around.
