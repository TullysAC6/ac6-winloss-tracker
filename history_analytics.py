"""Read-only analytics, CSV export and integrity checks over history.db.

This module never writes to history.db and never starts, stops or contacts a
Tracker process. Every connection is opened with ``mode=ro`` plus
``PRAGMA query_only=ON``, so it is safe to run while server.py owns the
database. Destructive history maintenance is deliberately absent here: it
belongs to the owning server process (see ``/api/history/purge``).

Win-rate definition is taken from the Tracker as it already exists
(``server.status_payload`` and ``HistoryStore.lifetime_summary``):
DRAW is reported but excluded from the win-rate denominator.
"""
from __future__ import annotations

import csv
import os
import sqlite3
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

DB_NAME = "history.db"
RECENT_WINDOWS = (10, 30, 100)
PERIODS = ("today", "week", "month", "all")
PERIOD_LABELS = {
    "today": "今日",
    "week": "今週（月曜開始）",
    "month": "今月",
    "all": "全期間",
}
CSV_COLUMNS = (
    "行番号", "日時", "created_at", "結果", "記録元",
    "連勝数", "WIN累計", "LOSE累計", "セッションID", "イベントID", "metadata_json",
)
CSV_ORDER_DESCRIPTION = "古い試合が先頭（created_at 昇順、同時刻は記録順）"
RESULT_LABELS = {"win": "WIN", "loss": "LOSE", "draw": "DRAW"}
ACTIVE_SESSION_PURGE_MESSAGE = (
    "現在のセッションに削除対象の試合が含まれています。"
    "Trackerを再起動して新しいセッションを開始してから実行してください。"
)


class HistoryUnavailable(RuntimeError):
    """history.db could not be opened for reading."""


def database_path(root: str | Path) -> Path:
    return Path(root) / DB_NAME


def format_local(epoch: float) -> str:
    return datetime.fromtimestamp(float(epoch)).strftime("%Y-%m-%d %H:%M:%S")


@contextmanager
def open_readonly(root: str | Path):
    """Open history.db strictly for reading, or raise HistoryUnavailable."""
    path = database_path(root)
    if not path.exists():
        raise HistoryUnavailable("履歴データベース (history.db) がまだ作成されていません。")
    try:
        connection = sqlite3.connect(
            path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5.0
        )
    except sqlite3.Error as error:
        raise HistoryUnavailable(
            f"履歴データベースを読み取り専用で開けませんでした: {error}"
        ) from error
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        yield connection
    except sqlite3.Error as error:
        raise HistoryUnavailable(f"履歴データベースを読み取れませんでした: {error}") from error
    finally:
        connection.close()


def _has_matches_table(connection: sqlite3.Connection) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='matches'"
    ).fetchone()
    return row is not None


def period_start(period: str, now: float | None = None) -> float | None:
    """Local-time start of a period. ``all`` has no lower bound (None).

    Boundaries follow the Windows local clock. The week starts on Monday.
    """
    if period not in PERIODS:
        raise ValueError(f"unknown period: {period}")
    if period == "all":
        return None
    moment = datetime.fromtimestamp(time.time() if now is None else float(now))
    midnight = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "today":
        start = midnight
    elif period == "week":
        start = midnight - timedelta(days=midnight.weekday())  # Monday == 0
    else:
        start = midnight.replace(day=1)
    return start.timestamp()


def period_start_text(period: str, now: float | None = None) -> str:
    start = period_start(period, now)
    return "制限なし（最初の記録から）" if start is None else format_local(start)


def latest_allowed_cutoff(now: float | None = None) -> float:
    """Newest purge cutoff a user may choose: today's local midnight.

    A later cutoff would delete matches the current session still counts in
    stats.json, leaving the session and lifetime displays inconsistent.
    """
    return period_start("today", now)


def _aggregate(rows) -> dict[str, Any]:
    wins = sum(1 for row in rows if row["result"] == "win")
    losses = sum(1 for row in rows if row["result"] == "loss")
    draws = sum(1 for row in rows if row["result"] == "draw")
    counted = wins + losses
    return {
        "wins": wins,
        "losses": losses,
        "draws": draws,
        "matches": wins + losses + draws,
        "counted": counted,
        "win_rate": round(wins / counted * 100.0, 1) if counted else 0.0,
        "best_streak": max((int(row["streak_after"]) for row in rows), default=0),
    }


def _period_rows(connection: sqlite3.Connection, start: float | None):
    if start is None:
        return connection.execute("SELECT result,streak_after FROM matches").fetchall()
    return connection.execute(
        "SELECT result,streak_after FROM matches WHERE created_at >= ?", (start,)
    ).fetchall()


def summarize(
    root: str | Path,
    now: float | None = None,
    windows: tuple[int, ...] = RECENT_WINDOWS,
) -> dict[str, Any]:
    """Period and recent-N aggregates. Raises HistoryUnavailable on read failure."""
    with open_readonly(root) as connection:
        if not _has_matches_table(connection):
            raise HistoryUnavailable("履歴テーブルがまだ作成されていません。")
        periods = []
        for period in PERIODS:
            start = period_start(period, now)
            summary = _aggregate(_period_rows(connection, start))
            summary.update(
                period=period,
                label=PERIOD_LABELS[period],
                start_at=start,
                start_text=period_start_text(period, now),
            )
            periods.append(summary)
        recent = []
        for size in windows:
            rows = connection.execute(
                "SELECT result,streak_after FROM matches ORDER BY id DESC LIMIT ?",
                (int(size),),
            ).fetchall()
            summary = _aggregate(rows)
            summary.update(size=int(size), available=len(rows))
            recent.append(summary)
        span = connection.execute(
            "SELECT MIN(created_at) first_at, MAX(created_at) last_at FROM matches"
        ).fetchone()
    total = next(entry["matches"] for entry in periods if entry["period"] == "all")
    return {
        "database": str(database_path(root)),
        "generated_at": time.time() if now is None else float(now),
        "total_matches": total,
        "first_match_at": span["first_at"],
        "last_match_at": span["last_at"],
        "periods": periods,
        "recent": recent,
    }


def count_before(root: str | Path, cutoff: float) -> dict[str, Any]:
    """Read-only preview of how many matches a ``before`` purge would remove.

    ``active_session_removable`` is the part of that which the still-open
    session also counts in stats.json. Removing those would leave the session
    and lifetime displays disagreeing, so both the settings window and
    server.py refuse a cutoff where it is not zero.
    """
    with open_readonly(root) as connection:
        if not _has_matches_table(connection):
            raise HistoryUnavailable("履歴テーブルがまだ作成されていません。")
        row = connection.execute(
            "SELECT COUNT(*) removable, (SELECT COUNT(*) FROM matches) total "
            "FROM matches WHERE created_at < ?",
            (float(cutoff),),
        ).fetchone()
        active = connection.execute(
            "SELECT id FROM sessions WHERE ended_at IS NULL ORDER BY id DESC LIMIT 1"
        ).fetchone()
        active_removable = 0
        if active is not None:
            active_removable = int(connection.execute(
                "SELECT COUNT(*) FROM matches WHERE session_id=? AND created_at < ?",
                (int(active["id"]), float(cutoff)),
            ).fetchone()[0])
    removable, total = int(row["removable"]), int(row["total"])
    return {
        "cutoff": float(cutoff),
        "cutoff_text": format_local(cutoff),
        "removable": removable,
        "total": total,
        "kept": total - removable,
        "active_session_id": None if active is None else int(active["id"]),
        "active_session_removable": active_removable,
    }


def integrity_check(root: str | Path, thorough: bool = False) -> dict[str, Any]:
    """Run PRAGMA quick_check (or integrity_check). Never repairs or writes."""
    pragma = "integrity_check" if thorough else "quick_check"
    path = database_path(root)
    with open_readonly(root) as connection:
        rows = connection.execute(f"PRAGMA {pragma}").fetchall()
        schema_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    messages = [str(row[0]) for row in rows]
    return {
        "pragma": pragma,
        "ok": messages == ["ok"],
        "messages": messages,
        "database": str(path),
        "size_bytes": path.stat().st_size,
        "schema_version": schema_version,
    }


def export_csv(root: str | Path, destination: str | Path) -> dict[str, Any]:
    """Write every recorded match to a UTF-8 BOM CSV. The database is read-only.

    The file is built in a temporary file next to the destination and renamed
    only after a complete write, so a failure never leaves a partial export and
    never touches history.db.
    """
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    # The database is opened before any temporary file exists, so a read
    # failure cannot leave a stray export file behind.
    with open_readonly(root) as connection:
        if not _has_matches_table(connection):
            raise HistoryUnavailable("履歴テーブルがまだ作成されていません。")
        rows = connection.execute(
            "SELECT id,event_id,session_id,created_at,result,source,streak_after,"
            "wins_after,losses_after,metadata_json "
            "FROM matches ORDER BY created_at ASC, id ASC"
        )
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".ac6-export-", suffix=".tmp", dir=destination.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(CSV_COLUMNS)
                for row in rows:
                    written += 1
                    writer.writerow([
                        written,
                        format_local(row["created_at"]),
                        row["created_at"],
                        RESULT_LABELS.get(row["result"], row["result"]),
                        row["source"],
                        row["streak_after"],
                        row["wins_after"],
                        row["losses_after"],
                        row["session_id"],
                        row["event_id"],
                        row["metadata_json"],
                    ])
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
    return {
        "path": str(destination),
        "rows": written,
        "encoding": "UTF-8 (BOM付き)",
        "order": CSV_ORDER_DESCRIPTION,
    }
