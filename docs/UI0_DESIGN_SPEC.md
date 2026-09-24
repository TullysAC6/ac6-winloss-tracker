# UI-0 design specification

Status: **DESIGN SPEC APPROVED**

Date: 2026-09-22 JST

Approval recorded: 2026-09-23 JST

Owner: Issue [#25](https://github.com/TullysAC6/ac6-winloss-tracker/issues/25)

Baseline: `main` at `fbc2131d177aa3fb5b271c1bd6894440e07c97e0`

Nature of this change: documentation only; no production code, test, runtime, dependency, or framework change

This document turns MASTER_REQUIREMENTS §§57–63 into an implementation-ready UI contract. The
owner decisions are recorded in §20. Approval of this specification does **not** authorize UI-1A
or any later implementation.

## 1. Outcome and boundaries

The target identity is:

```text
Fluent shell × AC6 telemetry × Pachinko celebration
```

The operating rule is: **blend into the game during ordinary play; break the visual rhythm only
at a win milestone.** This is an incremental polish programme. The accepted result path and
lifecycle stay intact.

### In scope for this specification

- A source-grounded inventory of Player Overlay, Broadcast Overlay, Dashboard/Overview, History,
  Settings, Launcher, and milestone effects.
- Shared visual tokens and surface-specific layout contracts.
- Before → Proposed definitions, dependencies, risk, rollback, and verification.
- Accessibility, DPI, performance, lifecycle, and framework feasibility.
- Phase boundaries for UI-1A, UI-1B, UI-2, UI-3A, UI-3B, and UI-4.

### Out of scope

- Production UI or test implementation.
- Detector, ResultGate, WGC, capture cadence, persistence, or process-lifecycle refactoring.
- New analytics, metadata, Season, rank/rating, or opponent-build data.
- Framework migration.
- Tray behaviour. UI-4 remains wholly owned by [#26](https://github.com/TullysAC6/ac6-winloss-tracker/issues/26).
- Starting UI-1A before this documentation PR is merged and the owner separately authorizes
  implementation.

### Authoritative sequence

```text
UI-0 design approval
→ UI-1A Player Overlay
→ UI-1B Broadcast Overlay
→ #15 match metadata
→ #28-A Season Catalog / Assignment
→ UI-2 Dashboard / History / Settings shell
→ #16-A / #28-B data
→ UI-3A Growth / Rank / Rating presentation
→ #17 and #10/#11/#12 data
→ UI-3B Opponent build statistics presentation
→ …
→ UI-4 Tray / Launcher modernization
```

The current roadmap is newer than the older sequence embedded in Issue #25. In particular,
**#15 → #28-A → UI-2 is mandatory**. UI-2 must not be built against today's row shape and then
rebuilt after the metadata and Season contracts settle.

## 2. Evidence used

The design is based on the current source, not on a hypothetical rewrite:

- [`game_overlay.py`](../game_overlay.py)
- [`overlay.html`](../overlay.html)
- [`dashboard.py`](../dashboard.py)
- [`settings_window.py`](../settings_window.py)
- [`launcher.pyw`](../launcher.pyw)
- [`app.py`](../app.py)
- [`server.py`](../server.py)
- [`config_utils.py`](../config_utils.py)
- [`effect_screenshot.py`](../effect_screenshot.py)
- [`requirements.lock`](../requirements.lock)
- [current Launcher screenshot](images/launcher.png)
- [current Player Overlay screenshot](images/in-game-overlay.png)
- [current Overview screenshot](images/dashboard-overview.png)
- [current History screenshot](images/dashboard-history.png)

No Tracker process was launched to produce this specification. Existing repository screenshots and
static source inspection were sufficient and avoided touching the user's live installation or data.

### 2.1 MASTER_REQUIREMENTS coverage map

| Requirement | Covered here |
|---|---|
| §57 UI/UX design identity | §§1, 4 |
| §58 Player Overlay | §§3, 6 |
| §59 Broadcast Overlay, shared language, milestones | §§3, 7, 11 |
| §60 Dashboard, History, Statistics, Settings, ownership | §§8–10, 13 |
| §61 Typography, tokens, material, avoided treatments | §§5, 14 |
| §62 Accessibility, responsiveness, performance | §§15–16 |
| §63 Phases, framework and lifecycle boundaries | §§1, 12, 17–19 |

## 3. Current technology and architecture survey

### 3.1 Surface inventory

| Surface | Current technology | Process / owner | Data and update path | Window / DPI / lifecycle facts |
|---|---|---|---|---|
| Player Overlay | Python `tkinter` Canvas plus Win32 APIs through `ctypes` | Dedicated overlay child launched by `app.py`; mutex `Local\AC6StatsOverlayV22` | SSE `/events` on a daemon thread; `stats.json` fallback every 3 s; Tk position/render tick every 250 ms; config read through cached `load_config()` | Three topmost, click-through, non-activating HWNDs: translucent panel, opaque color-key text, transient effect. Explicit Per-Monitor-V2 DPI awareness with legacy fallback. Visible only while AC6 owns the foreground client area |
| Broadcast Overlay | One self-contained `overlay.html`; plain HTML/CSS/JavaScript | Rendered by OBS browser source; served by the server process at `http://127.0.0.1:8765/` | SSE `/events`; `/config` poll every 3 s; browser reconnect is native `EventSource` behaviour | Transparent document. CSS pixels and OBS browser-source canvas determine effective scale. No native HWND ownership in the Tracker |
| Dashboard / Overview | `ttkbootstrap==2.2.2` over Tk, `darkly` theme | Dedicated dashboard child launched/focused by Launcher; mutex `Local\AC6WinLossTrackerDashboard` | `/api/dashboard/summary` REST poll every 1 s on a daemon worker; Tk consumes queued results | Fixed initial 950×650, minimum 800×540. Runtime heartbeat file and HWND support focus/reuse. No explicit DPI-awareness call in this process. Closing Dashboard closes only Dashboard |
| History | `ttk.Treeview` inside Dashboard Notebook | Same dashboard process | Same 1 s summary payload; 50 newest rows; identical data does not rebuild the tree and preserves scroll | Three fixed-width columns: Time, Result, Streak. No date grouping or filters |
| Settings | Standard Tk/ttk `Toplevel` and Notebook | Owned by the Launcher root; not a standalone process | Direct atomic config save for display settings; localhost control API for destructive operations; read-only analytics in background workers; release metadata check in a worker | Fixed, non-resizable window. Close withdraws and reuses it. No explicit DPI-awareness call. It exists only while the already-running Launcher status window is open |
| Launcher | Standard Tk widgets | Short-lived shortcut target; starts the server/app and can spawn Dashboard | Health and runtime-file checks happen on workers; Tk polls a result queue at 100 ms | Fixed 420×280. Normally exits 2 s after successful start. When Tracker is already running it exposes Dashboard, Settings, full Tracker exit, and window close. It is not a tray app |
| Milestone effects | Native Tk Canvas in Player Overlay **and** CSS/DOM in Broadcast Overlay | Native effect HWND plus browser DOM | Both consume remembered SSE effect events with TTL/deduplication | Native duration is 3.5 s, or 6 s for 50; browser is 3 s. Implementations and visuals are currently duplicated. Effect screenshot waits for visible native composition and is failure-isolated |

### 3.2 Current process and data flow

```text
Desktop shortcut
  → launcher.pyw (short-lived Tk owner)
      → app.py / server.py (authoritative runtime and localhost HTTP)
          → game_overlay.py (owned overlay child)
          → /events (SSE) ──────────┬→ native Player Overlay
          │                         └→ OBS Broadcast Overlay
          → /api/dashboard/summary ──→ dashboard.py (1 s polling)
      → dashboard.py (separate process, on demand)
      → settings_window.py (Launcher-owned Toplevel, on demand)
```

This topology is a safety contract, not an implementation inconvenience:

- UI-1A and UI-1B preserve the current process count and UI ownership.
- UI-3A and UI-3B preserve the current process and lifecycle topology.
- UI-2 may make exactly one ownership seam, proposal `S-01` (**Medium**): move presentation of the
  existing Settings UI from the Launcher-owned Toplevel into the existing Dashboard process/shell.
- `S-01` may not create a process or change server ownership, shutdown semantics, port ownership,
  runtime-file ownership, overlay ownership, or the Launcher/server lifecycle merely to support
  Settings.
- Rolling back `S-01` restores the Launcher-owned Toplevel without changing stored settings.

Every phase preserves the existing mutexes, shutdown API, runtime files, port ownership, and
authoritative server/runtime ownership unless a later, separately approved lifecycle issue says
otherwise.

### 3.3 Current constraints exposed by the framework

| Constraint | Consequence |
|---|---|
| Tk alpha applies to a whole top-level | The Player Overlay correctly uses separate panel and text windows so panel transparency does not dim text |
| Tk/ttk is not a Fluent control library | Fluent character must come from spacing, hierarchy, typography, restrained colour, and bounded Win32 window attributes—not a fake control rewrite |
| `ttkbootstrap` themes widgets but not the native non-client area comprehensively | Dark titlebar and any Mica experiment need explicit Windows capability detection and solid fallback |
| Tk layout and font units differ from browser CSS pixels | Player and Broadcast tokens share meaning, not literal pixel values |
| Dashboard uses REST polling while overlays use SSE | UI polish must not opportunistically replace this transport architecture |
| Settings is owned by Launcher | Moving Settings into the Dashboard shell is a medium-risk ownership/navigation change and belongs to UI-2, not UI-1 |
| Native and browser milestone renderers are independent | Visual parity needs explicit shared constants/spec tests; a runtime framework merge is not justified |

## 4. Design principles

1. **Value before label.** The number answers the glance; the label supplies context.
2. **Quiet is the default.** Cyan marks the system, red marks loss/error, gold marks achievement.
3. **No colour-only meaning.** Every state retains a word, symbol, or shape.
4. **One hierarchy, two overlay products.** Player and Broadcast share tokens, but never a single
   density, opacity, animation, duration, or layout setting.
5. **No data theatre.** Do not create empty Statistics pages, charts for scalar values, or sample-free
   percentages.
6. **No performance debt for polish.** Static when idle; event-driven where already event-driven;
   no permanent 60 fps loop.
7. **No lifecycle drift.** The bounded `S-01` Settings presentation seam is not permission to change
   process ownership, server ownership, or what a close action means.
8. **Pachinko is punctuation.** It belongs to milestones, not the surrounding shell.

## 5. Shared design system

### 5.1 Colour tokens

These are implementation targets. They may be adjusted only if measured contrast or real-game
legibility fails; semantic roles remain stable.

| Token | Value | Use |
|---|---:|---|
| `color.base` | `#0B1116` | Dashboard base and solid fallback |
| `color.surface` | `#111B22` | Primary surface |
| `color.surface.raised` | `#17242D` | Selected/raised region |
| `color.divider` | `#2B3C46` | Low-emphasis separation |
| `color.text.primary` | `#F2F7F8` | Primary text |
| `color.text.secondary` | `#AFC0C7` | Secondary text |
| `color.system.cyan` | `#4BD9E8` | Tracker/system/primary action |
| `color.system.teal` | `#20C9A6` | Positive/connected/win accent |
| `color.loss` | `#FF6B6B` | Loss/error/danger |
| `color.achievement` | `#FFD166` | Milestone/achievement only |
| `color.focus` | `#8BE9F3` | Keyboard focus ring |

All text/accent values above exceed 4.5:1 against `color.base` and `color.surface` in the sRGB
contrast calculation. Implementation still runs automated contrast checks because antialiasing,
font weight, transparency, and game footage affect perceived legibility.

Player Overlay panel target: `color.base` at an effective 12–18% opacity, selected by the real-game
matrix. Broadcast Overlay panel target: 45–60% opacity for stream compression. Neither uses blur.

### 5.2 Typography

```text
Dashboard / Launcher / Settings:
  Segoe UI Variable → Segoe UI → Yu Gothic UI → Meiryo

Player / Broadcast overlays:
  Segoe UI Variable → Yu Gothic UI → Meiryo → sans-serif
```

| Role | Dashboard target | Overlay target | Rule |
|---|---:|---:|---|
| Display KPI | 40 logical px, semibold | 24–32 logical px, bold | Tabular numerals where supported |
| Section title | 20, semibold | 16–20, semibold | Short, no decorative all-caps in Japanese |
| Body | 14 | 14–18 | Minimum 12 logical px at supported scale |
| Label | 12, semibold | 11–14, semibold | Secondary contrast, never the only state cue |
| Caption | 11 | 11–13 | Not for critical status |

Tk implementations use logical point sizes that preserve these proportions under Windows scaling;
they must not hard-code physical pixels to imitate CSS.

### 5.3 Spacing, shape, and elevation

- Spacing scale: `4 / 8 / 12 / 16 / 24 / 32` logical px.
- Dashboard outer margin: 24; section gap: 24; card internal padding: 16 or 24.
- Corner radius target: 4 for telemetry surfaces, 6 for dashboard surfaces, 8 maximum for a modal.
- Borders are one-pixel dividers, not boxes around every datum.
- Shadow is reserved for transient separation. No glow outside milestone effects.
- Icons are optional and must come from a consistent monochrome set. No emoji.

### 5.4 Motion

- Normal surfaces: no looping animation.
- Result acknowledgement: one 100–200 ms cyan/teal edge pulse; opacity only; no layout shift.
- Milestone: finite, event-triggered, automatically cleaned up.
- Reduced motion: replace scale/flash sequences with one static achievement banner and a short
  opacity transition. Windows reduced-motion preference is the first signal; an explicit setting is
  a later fallback only if Tk/browser parity cannot consume the system preference reliably.

### 5.5 State language

Canonical visible words:

```text
RUNNING · PAUSED · AC6 DETECTED · WAITING FOR AC6 · CAPTURE ERROR · OFFLINE
```

Each combines text with shape/icon; colour is supplementary. `● RUNNING` alone is insufficient
when the system can distinguish game waiting, detector degraded, and server offline.

## 6. Player Overlay — UI-1A

### Before

- One line combines `WIN`, `LOSE`, Japanese win rate, streak, and pachinko status at equal weight.
- A second line always shows best streak.
- Position is fixed 18 logical pixels from the AC6 client top-left.
- Font is Yu Gothic UI bold, default 22; panel opacity defaults to 10%.
- Native Canvas redraw is key-guarded, but window position is checked every 250 ms.
- Pachinko status remains present after thresholds, so ordinary play can stay visually “hot”.

### Proposed layout

```text
┌─ AC6 TELEMETRY ─────────────────────────┐
│  12        7        63.2%        3      │
│  WIN      LOSS      RATE       STREAK   │
└─────────────────────────────────────────┘
```

- Values are the first scan line; labels are smaller and quieter.
- `BEST STREAK` leaves the always-on Player Overlay. It remains always visible in Dashboard
  Overview. Broadcast exposes it through its own user-toggleable `show_best_streak` setting.
- Player and Broadcast settings remain independent. This presentation change does not alter the
  underlying BEST statistic, its calculation, or its persistence.
- A thin cyan leading rule and two corner ticks supply telemetry character. No faux AC6 copy.
- Status degradation appears as a compact labelled chip on the right, not as a colour-only change.
- A genuine result may pulse the leading rule for 100–200 ms. Milestones use the separate effect
  window. Threshold status words such as `激アツ` do not loop or blink in the persistent panel.

### Placement, DPI, and safe zone

- Anchor remains the AC6 **client** top-left, never the full monitor.
- Base inset: max of 24 logical px and 1.25% of client width/height on each relevant axis. This is
  the Player safe-zone boundary; the complete panel retains the same minimum clearance from any
  opposite edge after layout fallback.
- Supported matrix: 1920×1080, 2560×1440, 3840×2160, and 3440×1440; 16:9 and 21:9;
  Windows scale 100/125/150%.
- If the panel cannot fit, reduce horizontal gaps to the 8 px token before reducing type. Critical
  values never drop below the overlay body minimum.
- Retain Per-Monitor-V2 awareness and all three existing HWND roles.

### Data, update, and lifecycle contract

- Reuse the current SSE stats/lifetime stream, `stats.json` fallback, 250 ms positioning tick,
  foreground-client test, mutex, heartbeat, and server-death exit.
- Do not add another thread, process, WebView, GPU surface, continuous animation timer, or game hook.
- `overlay_stats_scope` continues to change totals while streak remains session-scoped.
- No UI-1A change may write history or config during ordinary rendering.

### Rollback and verification

- UI-1A is one isolated PR. Reverting it restores only Canvas layout/tokens; no schema or migration.
- Preserve static assertions for click-through/topmost/no-activate styles, separate alpha/text HWNDs,
  mutex, heartbeat, fallback, SSE freshness, effect isolation, and shutdown.
- Add deterministic Canvas measurement tests and screenshot comparisons for every DPI/aspect cell.
- T3: confirm glance legibility during real AC6, zero focus theft, no HUD collision, unchanged result
  counting, and baseline-relative performance.

## 7. Broadcast Overlay — UI-1B

### Before

- The browser overlay mirrors the original Player content in a compact top-left box.
- It uses 24 px bold text, 50% black panel, a small best-streak subline, three right-side health
  warnings, and persistent blink classes for “hot” streak states.
- Config is polled every 3 s; stats/effects arrive by SSE.
- It has no explicit OBS canvas/safe-area contract or independent presentation settings.

### Proposed layout

Default compact scene component:

```text
┌─ CURRENT SESSION ────────────────────────┐
│  12 W   7 L          WIN RATE 63.2%      │
│  STREAK 3             BEST 6             │
└──────────────────────────────────────────┘
```

Milestone slot, normally absent:

```text
                  ┌──────────────────┐
                  │  10 WIN STREAK   │
                  │    超激アツ!!     │
                  └──────────────────┘
```

- Primary text size: preserve the current 24 px bold baseline as the UI-1B target (owner decision
  5, §20). Do not enlarge it merely because the surface is viewer-facing; increase it only if real
  OBS / viewer-distance evidence shows insufficient readability. The earlier 28–36 CSS px proposal
  is not approved. Any scaling is applied to the component, not via browser zoom.
- Preserve a transparent page. The component receives its own 45–60% solid dark backing for video
  compression. No backdrop blur or full-canvas material.
- Default safe area is 5% of canvas on every edge. Provide named `top-left`, `top-right`,
  `bottom-left`, and `bottom-right` anchors in the future Broadcast settings; default stays top-left.
- Health warnings stack within the same safe region and include an icon/word, not red alone.
- Ordinary streak state is static. No infinite blink. Only the finite milestone slot is loud.

### Separate settings contract

Player and Broadcast may share semantic tokens, but Broadcast owns distinct settings and must
expose BEST as an independently user-toggleable option:

```text
broadcast_overlay.enabled
broadcast_overlay.anchor
broadcast_overlay.scale
broadcast_overlay.opacity
broadcast_overlay.show_best_streak
```

These keys are a UI-1B implementation design input, not authorization to change `config.json` now.
Any schema addition must be versioned, bounded, and backward-compatible. Player settings must not
mutate these values, and Broadcast settings must not mutate Player presentation.

### Rollback and verification

- UI-1B is separate from UI-1A. Revert `overlay.html` and any new bounded config keys/defaults;
  older config remains valid.
- Keep SSE reconnect/replay/freshness/dedup tests and config polling tests.
- Add browser screenshots at 1920×1080 and 2560×1440, transparent-background alpha checks,
  safe-area assertions, long Japanese/English value stress, stream-compression legibility review,
  and reduced-motion coverage.
- OBS T3 verifies scene composition, chat/HUD non-collision, viewer-distance readability, and no
  measurable game-performance regression.

## 8. Dashboard / Overview — UI-2

### Before

- Dark ttkbootstrap window with header, `概要`/`履歴` Notebook tabs, two bordered label frames,
  five equal rows per card, and a footer reset action.
- WIN RATE has no stronger hierarchy than other values.
- `● RUNNING` is the only top-level runtime state.
- Fixed side-by-side cards require an 800 px minimum width.

### Proposed information architecture

```text
AC6 WIN/LOSS TRACKER                         [WAITING FOR AC6]

OVERVIEW   HISTORY   STATISTICS   SETTINGS
──────────────────────────────────────────────────────────

CURRENT SESSION                         LIFETIME
  WIN RATE                               WIN RATE
    63.2%                                  61.8%
  12 W · 7 L        STREAK 3             250 MATCHES
  BEST 6                                  BEST 12

LAST RESULT  WIN · 00:11                 HISTORY HEALTH  OK
```

- Top navigation replaces nested Notebook styling. `STATISTICS` is not exposed until its first
  real-data slice exists; it must not be an empty page.
- WIN RATE is the primary KPI; W/L is secondary; STREAK/BEST is tertiary.
- Current Session may receive the stronger surface; Lifetime remains adjacent and comparable.
- At narrow width, cards stack Session then Lifetime without horizontal scrolling.
- Runtime and history health are distinct labelled states.
- The session-reset action stays explicit, secondary, and confirmed.

### Responsive and focus contract

- Wide: two equal columns at ≥800 logical px content width.
- Narrow: one column below that threshold, minimum useful window target 560 logical px.
- Natural tab order follows navigation → page content → page actions.
- Arrow keys move among top-navigation tabs; Enter/Space activates; focus ring uses
  `color.focus`; the selected tab has both underline and weight.
- Text scaling to 200% may stack or scroll vertically; it must not clip actions.

### Mica and titlebar

- UI-2 baseline is a solid dark shell plus a safely supported dark native titlebar and solid
  fallback.
- Mica is not required and cannot block UI-2. Only after the solid shell is accepted may a bounded,
  Windows 11-only Dashboard top-level proof be attempted.
- Adopt Mica only when the proof shows a clear visible benefit with no repaint artifacts, a working
  solid fallback, no new dependency, no extra render loop, and no meaningful performance
  regression. Failure or rejection returns to `color.base` without blocking UI-2.
- Mica is not used on overlays. Acrylic remains not proposed.

### Rollback and verification

- UI-2 is one shell PR after #15 and #28-A. It changes presentation/ownership only as explicitly
  specified; server endpoints and Dashboard process identity remain.
- Revert restores the Notebook shell. No analytics/history migration is allowed.
- Preserve single-instance, HWND focus/reuse, heartbeat, server-death close, 1 s background polling,
  non-repainting identical summaries, session reset, and history-health tests.
- Add keyboard, focus, narrow/wide, 100/125/150% DPI, 200% text, high-contrast, dark-titlebar
  fallback, and state-matrix screenshots.

## 9. History — UI-2

### Before

- Flat 50-row Treeview with `Time`, `Result`, and `Streak` columns.
- Fixed-width columns, no date groups or filters.
- Results are uppercase text without a deliberate badge system.

### Proposed layout

```text
ALL  |  WINS  |  LOSSES        TODAY  |  7D  |  30D  |  ALL TIME

SEP 04
  00:11   [WIN]    STREAK 3
  00:06   [WIN]    STREAK 2
  00:02   [WIN]    STREAK 1

SEP 03
  23:56   [LOSS]   —
```

- Group by local calendar date; preserve newest-first order.
- Result badge includes the word and restrained shape; teal/red is supplementary.
- Hover/selection uses a surface change and focus outline.
- Filters compose result × period, are keyboard reachable, and expose active state in text.
- Preserve row/scroll position when an identical summary arrives.
- Search is deferred until #15 supplies meaningful searchable metadata.
- Mode/format/rank/Season columns are reserved, not fabricated. They appear only after #15 and
  #28-A define their honest unknown/unresolved states.

### Rollback and verification

- Grouping/filtering is a view transform over the existing payload; it must not write the DB.
- Revert returns to the flat Treeview with no data migration.
- Test date boundaries in local time, all filter combinations, zero-result states, 50-row cap,
  identical-poll scroll preservation, keyboard selection, and `unknown`/`unresolved` rendering.

## 10. Settings — UI-2

### Before

- Fixed, non-resizable Launcher-owned Toplevel with four Notebook tabs: display/effects,
  analytics, maintenance, support.
- Analytics is monospaced text; destructive history actions sit in a maintenance tab.
- Tasks correctly run off the Tk thread and publish results through queues.

### Proposed shell

- Settings becomes the fourth Dashboard navigation destination in UI-2, after the Dashboard shell
  has a stable owner. It does not become a second settings implementation.
- This is the sole approved `S-01` ownership seam (**Medium**): presentation moves from the
  Launcher-owned Toplevel into the existing Dashboard process/shell.
- Existing functions and server ownership remain. The move is presentation/ownership plumbing,
  not a rewrite of #8/#9, and it creates no process or lifecycle topology change.
- Sections: `DISPLAY & EFFECTS`, `DATA & HISTORY`, `SUPPORT`, then `SEASON INFORMATION` after
  #28-A. Destructive actions receive a clearly separated danger region.
- Inline save/progress/error states remain non-modal. Only irreversible confirmation is modal.
- Closing Settings returns to the last Dashboard page and does not affect Tracker runtime.

Season block, only after #28-A:

```text
SEASON INFORMATION
Current cached season   Season 16
Last checked            2026-09-22 10:30
[ シーズン情報を更新 ]

最新です / 更新しました / 確認できませんでした — cached dataを使用 / Season未解決
```

- Refresh never steals AC6 focus, blocks startup, blocks result recording, or discards the last
  valid cache.
- The Season selector and charts do **not** appear here; they belong to UI-3A after #16-A/#28-B.

### Rollback and verification

- Preserve current config schema behaviour, atomic save, control-token API, background task model,
  destructive previews, version check, diagnostic report, and non-blocking close.
- The UI-2 PR must define a reversible ownership seam so reverting the shell restores the
  Launcher-owned Toplevel without changing stored settings.
- Test keyboard traversal, labels associated with controls, async close, busy-state deduplication,
  write failure, concurrent edit, destructive confirmation, narrow layout, and 200% text.

## 11. Milestone effects

### Before

- Native and browser effects are separately implemented and visually divergent.
- Native before-state: 3.5 s for ordinary milestones and 6.0 s for 50; centered near 62% of client
  height. The 6.0 s value documents current production only and is not an approved target.
- Browser: 3 s CSS scale/blink sequence centered on the canvas.
- Native 50 includes a white/dark flash; browser 50 uses a radial gold surface.
- Source code includes a 25-streak milestone, while the adopted requirement list is
  `5/10/15/20/30/35/40/45/50`.

### Proposed presentation tiers

| Tier | Milestones | Treatment |
|---|---|---|
| Heat | 5, 10 | Gold edge, one entrance pulse, `激アツ` / `超激アツ` |
| Zone | 15, 20 | Stronger gold banner, short two-step reveal |
| Rush | 30, 35, 40, 45 | Violet-gold escalation, increasing type scale without longer obstruction |
| Legend | 50 | Unique gold/white treatment; reduced-motion alternative has no full-field flash |

- One design-token table defines copy, duration, colours, and motion stages for both renderers;
  each renderer stays native to its current technology.
- Target obstruction is ≤3.5 s for every tier except 50. The approved 50 target is ≤4.5 s on the
  normal path and ≤3.0 s with reduced motion. Evidence that would require more than 4.5 s needs a
  new owner decision; 6.0 s is not an equally approved option.
- Persistent stats never disappear while a milestone is active.
- Cleanup, TTL, replay, deduplication, screenshot timing, and failure isolation are unchanged.
- Production already contains a 25-win milestone while canonical §§57–63 omit it. This is an
  existing requirement/code discrepancy: UI-0, UI-1A, and UI-1B preserve the current production
  trigger, name, and semantics exactly. They must not add, remove, rename, remap, or otherwise
  silently resolve it.

### Rollback and verification

- Keep milestone work separate from ordinary overlay layout when practical. Token/copy changes are
  revertible without changing event identity.
- Parity tests assert both renderers preserve the production milestone set, including the existing
  25 discrepancy, and map shared milestones to the same approved copy/tier/duration.
- Preserve reconnect replay, freshness, deduplication, expired-event rejection, visible-paint
  screenshot, 50-flash screenshot, Alt+Tab rejection, and worker cleanup tests.
- T3 uses only naturally reached milestones; no history/stat manipulation to manufacture one.

## 12. Launcher and UI-4 boundary

### Before

- Launcher is a short-lived 420×280 Tk status/action window.
- Successful initial start closes it after 2 s.
- Reopening while Tracker runs exposes Dashboard, Settings, complete Tracker exit, and local close.
- `Trackerを終了` owns full teardown verification. `閉じる` closes only Launcher.

### Proposed now

No Launcher process or lifecycle change occurs in UI-1A, UI-1B, UI-2, UI-3A, or UI-3B. UI-2 may
route Settings presentation through the reviewed `S-01` seam, but changing close semantics,
keeping a resident icon, relocating Settings ownership beyond that seam, or altering process
startup/shutdown is UI-4.

### UI-4 concept — not authorized

```text
Dashboard [×] → Dashboard closes; Tracker remains in tray
Tray → Exit    → complete server/overlay/dashboard/tray teardown
```

This is proposal `L-01`, **High** risk, and belongs to its own branch/PR under Issue #26 after all
earlier work. It requires a full T2 lifecycle proof: single instance, server port, overlay and tray
mutexes, runtime files, DB flush, child/grandchild ownership, duplicate refusal, normal shutdown,
abnormal shutdown, and zero residue. If that proof fails, UI-4 is not implemented.

## 13. Future Statistics — presentation contract only

UI-0 records the layout rules so UI-3 does not invent them later. It does not create the page now.

### UI-3A, after #16-A / #28-B

- Season selector, win-rate trend, rolling 30-match win rate, period comparison, rank/rating
  progression, current rating, Season high/low, delta, and rank-transition markers.
- Every statistic shows sample size and sparse-data state.
- Charts are for change over time. A single win-rate value remains a KPI, not a pie chart.
- **Pre-S (UNRANKED through A4) and S are not drawn as one continuous comparable scale.** Use
  separate panels/scales with an explicit transition marker unless the data owner establishes a
  justified conversion.

### UI-3B, after #17 and #10/#11/#12

- Weapon, leg-type, component, complete-build, combination, and improved-matchup presentation.
- Single and Team contexts stay visibly separate. Partial/failed/not-acquired/not-supported remain
  distinct. No presentation infers missing opponent data.

## 14. Mica, Acrylic, and native material decision

| Material | Feasibility in current stack | Decision |
|---|---|---|
| Solid dark shell + dark titlebar | Good through bounded DWM attributes on supported Windows; solid/native fallback is straightforward | UI-2 baseline, proposal `E-02` Low |
| Mica on Dashboard top-level | Technically possible on supported Windows 11, but opaque ttk frames can hide most benefit and OS/version fallback must be tested | Optional, non-blocking `E-01` Medium proof only after solid-shell acceptance; adopt only with clear visible benefit, clean fallback, no artifacts, dependency, extra loop, or meaningful performance regression |
| Acrylic transient surfaces | Poor fit for Tk/ttk menus and popups without custom HWND/control work; adds complexity for little user value | Remains not proposed |
| Material on Player/Broadcast overlays | Conflicts with performance, transparency, capture, and stream requirements | Prohibited |

No new package is justified for material. A framework migration is not a material implementation
strategy.

## 15. Accessibility and input acceptance

Every implementation PR must show:

- WCAG-style contrast calculation for text and critical non-text indicators; 4.5:1 normal text,
  3:1 large text and focus/essential graphics as minimums.
- State words/icons in addition to colour.
- Visible keyboard focus, logical tab order, Enter/Space activation, Escape only where cancellation
  is safe, and no keyboard trap.
- 100/125/150% Windows DPI matrix and 200% text-size review.
- High-contrast fallback that removes transparency/material and uses system colours where required.
- No clipping at the minimum supported window width; vertical scrolling is preferable to shrinking
  critical text.
- Player Overlay remains non-activating and click-through. Broadcast remains pointer-inert.
- Motion-reduced rendering and no infinite blink.
- Japanese and English sample strings, large counts, `100.0%`, and long error states.

## 16. Performance and lifecycle acceptance

Absolute targets are not invented. Each implementation compares:

```text
Tracker OFF
current accepted Tracker
accepted Tracker + candidate UI change
```

Measure where practical: Tracker CPU, RAM, process/thread count, disk write rate, overlay update
frequency, result-to-visible-update latency, cleanup time, and AC6 frametime p95/p99. Use the #37
baseline capability when available.

Structural budgets:

- UI-1A/UI-1B: no new process; no new permanent thread; no continuous 60 fps animation; no new disk
  write during idle rendering; keep 250 ms native positioning, SSE delivery, 3 s fallback/config
  cadence unless evidence justifies a separate change.
- UI-2: keep one Dashboard process, one in-flight summary request, and the 1 s poll cadence; do not
  move detector/capture work to Tk.
- UI-3: render on data change or explicit navigation, not at display refresh rate.
- Every phase: unchanged server-port ownership, overlay/dashboard mutex semantics, runtime-file
  ownership, full shutdown API, and zero process/port/mutex/runtime-file residue.

Any meaningful regression is optimized, redesigned, or rejected. “Looks smoother” is not evidence
that game cost is acceptable.

## 17. Proposal register

Risk meanings:

- **Low:** visual/style change within an existing surface and data/lifecycle contract.
- **Medium:** layout, navigation, config schema, cross-render parity, ownership seam, or native-window
  integration that requires focused regression proof.
- **High:** process/lifecycle architecture.

| ID | Proposal | Phase / dependency | Risk | Rollback unit | Required proof |
|---|---|---|---|---|---|
| DS-01 | Shared semantic colour tokens | UI-1A first | Low | Token constants/CSS | Contrast + screenshots |
| DS-02 | Shared typography roles and fallback order | UI-1A first | Low | Font declarations | DPI/text matrix |
| DS-03 | 4/8/12/16/24/32 spacing scale | UI-1A first | Low | Layout constants | Geometry snapshots |
| DS-04 | Restrained radius/divider/elevation rules | Per surface | Low | Style declarations | Visual review |
| DS-05 | System-aware reduced-motion behaviour | UI-1A/UI-1B | Medium | Motion adapter/settings default | Native/browser parity |
| DS-06 | Text/icon/shape in addition to colour | Per surface | Low | Labels/styles | High-contrast/state matrix |
| P-01 | Value-first four-metric layout | UI-1A | Low | Native Canvas layout | AC6/DPI screenshots |
| P-02 | Remove BEST from always-on Player surface | UI-1A; owner-approved | Low | One rendered field | Glance test; value remains in Overview and optional Broadcast |
| P-03 | Thin telemetry rule/corner ticks | UI-1A | Low | Canvas primitives | Pixel/safe-zone review |
| P-04 | Responsive DPI/aspect safe-zone placement | UI-1A | Medium | Placement calculation | 1080p/1440p/4K, 16:9/21:9 |
| P-05 | 100–200 ms result acknowledgement | UI-1A | Low | One finite animation | Event/cleanup/performance test |
| P-06 | Remove persistent pachinko blink/status from normal panel | UI-1A | Low | Status style mapping | Milestones unaffected |
| B-01 | Session-first viewer layout | UI-1B | Low | HTML/CSS component | Browser screenshots |
| B-02 | Type scale holding the current 24 px bold primary baseline; enlarge only on OBS evidence | UI-1B; owner-approved | Low | CSS type tokens | OBS 1080p/1440p review |
| B-03 | Independent Broadcast settings namespace | UI-1B | Medium | Versioned config keys/defaults | Migration + live reload |
| B-04 | OBS safe-area anchors and scale | UI-1B | Medium | Anchor/scale config | Canvas/safe-area matrix |
| B-05 | Labelled stacked health states | UI-1B | Low | DOM/CSS | State/contrast tests |
| D-01 | Top navigation shell | UI-2 after #15/#28-A | Medium | Dashboard shell | Keyboard/navigation/lifecycle |
| D-02 | WIN RATE primary KPI hierarchy | UI-2 | Low | Overview layout | Data parity/screenshots |
| D-03 | Explicit runtime/history states | UI-2 | Low | Status mapping | Health-state matrix |
| D-04 | Wide two-column / narrow stacked layout | UI-2 | Medium | Layout breakpoint | Narrow/DPI/text matrix |
| H-01 | Date-grouped History | UI-2 after #15 | Medium | View transform | Local-date boundary tests |
| H-02 | Result and period filters | UI-2 after #15 | Medium | View state | Combination/keyboard tests |
| H-03 | Worded result badges and hover/focus | UI-2 | Low | Row styles | Contrast/high-contrast |
| H-04 | Honest metadata/Season columns | UI-2 after #15/#28-A | Medium | Conditional columns | unknown/unresolved fixtures |
| S-01 | One Settings destination in Dashboard | UI-2 after #15/#28-A | Medium | Ownership seam | Async/config/lifecycle tests |
| S-02 | Manual Season information block | UI-2 after #28-A | Medium | One section | Cached/offline/state tests |
| S-03 | Visually isolated destructive actions | UI-2 | Low | Section style | Confirmation regression |
| S-04 | Consistent inline progress/error/focus | UI-2 | Low | Feedback styles | Async close/keyboard tests |
| M-01 | Native/browser milestone token parity | UI-1A/UI-1B | Medium | Shared spec/constants | Mapping parity tests |
| M-02 | Bounded common timing/easing | UI-1A/UI-1B | Medium | Duration/motion constants | Obstruction/performance test |
| M-03 | Unique but bounded 50 treatment | UI-1A/UI-1B; owner-approved timing | Medium | 50 renderer branch | ≤4.5 s normal; ≤3.0 s reduced motion + screenshot |
| M-04 | Preserve stats and deterministic cleanup | UI-1A/UI-1B | Low | Effect presentation only | Existing replay/cleanup suite |
| E-01 | Conditional Dashboard Mica proof | UI-2 optional | Medium | Capability-gated adapter | Win11/fallback/repaint test |
| E-02 | Dark native titlebar with fallback | UI-2 | Low | DWM attribute adapter | Supported/unsupported OS test |
| L-01 | Resident tray / Launcher lifecycle | UI-4 only, Issue #26 | **High** | Separate PR; full revert | Complete T2 lifecycle gate |

Proposal count: **36 total — Low 20, Medium 15, High 1**.

## 18. Regression plan by phase

| Phase | Automated before review | Human/visual before acceptance | Must remain unchanged |
|---|---|---|---|
| UI-1A | T0 full; T1 42/42; overlay static/lifecycle, SSE, Canvas geometry, DPI screenshot matrix; T2 lifecycle | Real AC6 glance, safe zone, focus, update acknowledgement, natural milestone if available, performance comparison | Detection, ResultGate, stats/history writes, three HWND roles, process count, shutdown |
| UI-1B | T0/T1; browser JS replay tests; transparent screenshots; config migration; T2 | OBS 1080p/1440p composition, viewer distance, chat/HUD collision, performance | Server endpoints, SSE identity/TTL, Player settings |
| UI-2 | T0/T1; Dashboard/Settings/History tests; keyboard/DPI/text/high-contrast screenshots; T2 | Normal use, reset confirmation, offline/degraded states, no focus theft, performance | Dashboard process/mutex/runtime heartbeat, server ownership, DB ownership |
| UI-3A | T0/T1 fixtures for provided data; chart snapshots including pre-S/S boundary; T2 | Interpretability and sparse-data review | #16/#28 data semantics; unresolved Season honesty |
| UI-3B | T0/T1 opponent fixtures; partial/failed states; T2 | Interpretability and mode separation | #17/#10/#11/#12 data and match independence |
| UI-4 | Full T0/T1/T2 lifecycle matrix; independent review; affected-gate rerun | Real-machine T3 normal/abnormal exit and residue audit | Everything in Issue #26 safety boundary |

For this UI-0 documentation PR itself, T0/T1/T2/T3 are **N/A — documentation only**. CI, if
triggered, is recorded as repository evidence but is not relabelled as a UI gate.

## 19. Phase rollback strategy

1. One phase per PR; UI-1A and UI-1B never share a rollback unit.
2. Keep current data endpoints and persisted meanings. Visual rollback must not require DB repair.
3. New config fields, if accepted, are additive, bounded, versioned, and have current-behaviour
   defaults. Old config remains loadable.
4. Do not delete current renderer paths until the replacement has passed the phase's T3.
5. A failed material experiment falls back to solid surfaces without blocking the rest of UI-2.
6. A failed UI-4 lifecycle proof rolls back the complete UI-4 PR; tray behaviour is not partially
   retained.

## 20. Owner decisions recorded

1. `BEST STREAK` is removed from the always-on Player Overlay, remains always visible in Dashboard
   Overview, and is independently user-toggleable in Broadcast. Player and Broadcast settings stay
   independent; the underlying statistic is unchanged.
2. The production 25-win milestone is preserved exactly. Its omission from canonical §§57–63 is
   recorded as an existing requirement/code discrepancy and is not resolved in UI-0, UI-1A, or
   UI-1B.
3. The 50-win effect target is ≤4.5 s normally and ≤3.0 s with reduced motion. Current 6.0 s is
   before-state evidence only; exceeding 4.5 s requires a new owner decision.
4. UI-2 starts with a solid dark shell, safely supported dark native titlebar, and solid fallback.
   Mica is optional, Windows 11-only, bounded, and non-blocking after solid-shell acceptance;
   Acrylic remains not proposed.
5. Broadcast Overlay primary text keeps its current size, the 24 px bold implementation baseline,
   as the UI-1B target (2026-09-23,
   [Issue #25](https://github.com/TullysAC6/ac6-winloss-tracker/issues/25#issuecomment-5787040618)).
   It is not enlarged merely because the surface is viewer-facing, and increases only if real OBS /
   viewer-distance evidence shows a readability problem. The earlier 28–36 CSS px proposal is not
   an approved target. This decision covers typography size only; the other Broadcast decisions
   are unchanged.

## 21. Approval checklist

- [x] Identity and quiet/loud boundary approved.
- [x] Player and Broadcast remain separate products.
- [x] All 36 proposals have an accepted risk classification.
- [x] UI-1A and UI-1B layouts approved independently.
- [x] #15 → #28-A → UI-2 dependency accepted.
- [x] UI-3A and UI-3B data ownership boundaries accepted.
- [x] Pre-S through A4 vs S discontinuity preserved.
- [x] Mica/Acrylic decision accepted.
- [x] Accessibility, DPI, performance, lifecycle, rollback, and regression plans accepted.
- [x] Milestone 25 and 50-duration decisions recorded.
- [x] UI-4 remains isolated under Issue #26.

The design specification is approved. Issue #25 remains open, UI-1A is **NOT STARTED**, and this
approval is neither implementation acceptance nor release status. The exact next action is owner
authorization to merge this documentation-only PR; UI-1A still requires separate authorization.
