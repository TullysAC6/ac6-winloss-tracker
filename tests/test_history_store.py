import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from history_store import HistoryStore


with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    store = HistoryStore(root)
    assert store.path == root / "history.db"
    assert store.path.exists()
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1

    first_session = store.start_session(started_at=1000)
    assert first_session > 0
    assert store.record_result(
        "event-win", "win", "auto",
        {"wins": 1, "losses": 0, "streak": 1}, created_at=1010,
    ) is True
    assert store.record_result(
        "event-win", "win", "auto",
        {"wins": 2, "losses": 0, "streak": 2}, created_at=1011,
    ) is False
    assert store.record_result(
        "event-loss", "loss", "auto",
        {"wins": 1, "losses": 1, "streak": 0}, created_at=1020,
    ) is True
    assert store.record_result(
        "event-best", "win", "auto",
        {"wins": 2, "losses": 1, "streak": 4}, created_at=1030,
    ) is True

    lifetime = store.lifetime_summary()
    assert lifetime == {
        "wins": 2, "losses": 1, "draws": 0, "matches": 3,
        "win_rate": 66.7, "best_streak": 4,
    }
    recent = store.recent_matches(10)
    assert [item["event_id"] for item in recent] == ["event-best", "event-loss", "event-win"]

    second_session = store.reset_session()
    assert second_session != first_session
    assert store.lifetime_summary() == lifetime
    assert len(store.recent_matches(10)) == 3

    reopened = HistoryStore(root)
    assert reopened.lifetime_summary() == lifetime
    third_session = reopened.start_session(started_at=2000)
    assert third_session != second_session
    with sqlite3.connect(store.path) as connection:
        open_sessions = connection.execute(
            "SELECT COUNT(*) FROM sessions WHERE ended_at IS NULL"
        ).fetchone()[0]
        assert open_sessions == 1

    reopened.record_result(
        "event-undo", "win", "manual",
        {"wins": 1, "losses": 0, "streak": 1}, created_at=2010,
    )
    assert reopened.undo_last()["result"] == "win"
    assert reopened.lifetime_summary() == lifetime
    reopened.close_session("shutdown")

print("history schema/session/results/idempotency/reopen/reset/undo: OK")
