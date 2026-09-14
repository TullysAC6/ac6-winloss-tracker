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
- **Update check improvements** — the サポート tab compares your version with the latest published Release using release metadata only, and 「新しいバージョンをインストール」 opens the Release page. Nothing is downloaded or installed automatically; updating still uses the install command.

## Reliability

- **A result counts only once it is saved.** A result is accepted only after it has been written to match history and counted in the session stats. If the history write fails, nothing is counted. If counting fails after the history row was written, the Tracker removes that row again.
- **Corrections that cannot finish right away are kept.** If that row cannot be removed immediately, the correction is saved to `pending-history.json`, kept across a normal shutdown and retried, including at the next start. Each new result and each undo retries it first. Until it completes, history shows as degraded, lifetime totals can still include that row, and new results and undo are refused rather than recorded.
- **Undo reports what actually happened.** If history cannot remove the result, the stats are put back and the undo is reported as failed. If the stats cannot be put back either, the undo is reported as incomplete: the stats stay undone and removing the row from history is kept as a pending correction, as above. A failed or incomplete undo is never reported as a successful one.
- **History reset and delete report failures.** A failure reports the state the Tracker was actually left in and is never shown as success.

## Updating from v1.1.1

Run the one-liner in the README at the `v1.2.0` tag. Installing and updating use the same command. That command installs exactly v1.2.0 and never switches to a different Release; a later version comes with its own command.

- Your match history, lifetime stats and settings are kept. The history database format is unchanged.
- `config.json` is upgraded automatically on first start, and the previous file is kept as `config.json.v17.bak`. The two new settings start at values that match v1.1.1: effect ON, overlay stats for the current session.
- v1.1.1 cannot read the upgraded `config.json`. If you ever need v1.1.1's settings file again, `config.json.v17.bak` is the copy to restore.

Python install strategy is unchanged: Python 3.14 preferred with a 3.13 fallback, packages installed with `pip --user` against `requirements.lock`. Dedicated venv isolation remains a future version.

## Known limitations

- If the Tracker process is killed at the moment between writing a result to history and counting it, that match can stay in history without being counted in the session stats. The pending-history recovery above covers failed writes, not a killed process.
- If `pending-history.json` itself becomes corrupt, history stays unavailable until the file is repaired or removed, and there is no in-app hint for it yet.
