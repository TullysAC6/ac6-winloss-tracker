# AC6 Win/Loss Tracker v1.2.0

This release adds a Settings window with history analytics, CSV export and history maintenance, and hardens how results are saved. It supersedes v1.1.1.

Supported mode remains **RANK MATCH: SINGLE only**. CUSTOM MATCH and RANK MATCH: TEAM are not supported by this release.

## New

- **Settings in four tabs** — 表示・演出 / 成績 / メンテナンス / サポート. With the Tracker running, open the shortcut again and press 「設定」. Saved changes apply without a restart and never affect win/loss counting.
- **Milestone effect ON/OFF.** With the effect off, results and streaks are still counted.
- **Overlay stats: current session or lifetime.** The streak and the milestone effect stay session-based either way.
- **History analytics** — today, this week (Monday start), this month and all time, plus the last 10 / 30 / 100 matches. Periods use your PC's local time; win rate is `WIN ÷ (WIN + LOSE)` and does not count DRAW.
- **CSV export** of the full history, UTF-8 with BOM, oldest first.
- **History database check** — `quick_check` and a full `integrity_check`. Read-only: nothing is repaired or rewritten, and detection keeps running.
- **History maintenance** — reset all history, or delete matches before a chosen date (matches on that date are kept). Both show what will be removed and ask for confirmation; neither can be undone.
- **Diagnostic Report from Settings** — writes the Tracker's in-memory detection state to disk before building the ZIP. Create it right after a problem, before restarting the Tracker.
- **Update check improvements** — the サポート tab compares your version with the latest published Release using release metadata only, and 「新しいバージョンをインストール」 opens the Release page. Nothing is downloaded or installed automatically; updating still uses the same install command.

## Reliability

- **Ordered result saving.** A result is written to history first and counted in stats second. If either write fails, the result is not counted, rather than being counted in one place only.
- **Pending-history recovery.** If a result that had to be taken back cannot be removed from history, the correction is saved to `pending-history.json` and applied at the next start.
- **Safe failure for history reset / delete and undo.** A failure reports the state the Tracker was actually left in and is never shown as success. Undo removes a result from both history and stats, or from neither.
- **Shutdown keeps pending corrections.** A normal shutdown keeps an unsettled history correction for the next start instead of dropping it.

## Updating from v1.1.1

Run the one-liner in the README at the `v1.2.0` tag. Installing and updating use the same command.

- Your match history, lifetime stats and settings are kept. The history database format is unchanged.
- `config.json` is upgraded automatically on first start, and the previous file is kept as `config.json.v17.bak`. The two new settings start at values that match v1.1.1: effect ON, overlay stats for the current session.
- v1.1.1 cannot read the upgraded `config.json`. If you ever need v1.1.1's settings file again, `config.json.v17.bak` is the copy to restore.

Python install strategy is unchanged: Python 3.14 preferred with a 3.13 fallback, packages installed with `pip --user` against `requirements.lock`. Dedicated venv isolation remains a future version.

## Known limitations

- If `pending-history.json` itself becomes corrupt, history stays unavailable until the file is repaired or removed, and there is no in-app hint for it yet.
