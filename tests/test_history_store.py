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
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2

    first_session = store.start_session(started_at=1000)
    assert first_session > 0
    assert store.record_result(
        "event-win", "win", "auto",
        {"wins": 1, "losses": 0, "streak": 1}, created_at=1010,
    ) is True
    assert store.create_match_context(
        "match-win", "event-win", result_detected_at=1010.5
    ) is True
    assert store.create_match_context(
        "match-win", "event-win", result_detected_at=1010.5
    ) is False
    context = store.match_context("match-win")
    assert context["result"] == "win"
    assert context["started_at"] is None
    assert context["ended_at"] == 1010.5
    assert context["opponent_recognition_status"] == "unknown"
    assert context["game_display_name"] is None
    assert context["steam_id64"] is None
    assert store.update_match_context(
        "match-win", self_side="left", opponent_side="right",
        opponent_recognition_status="proposed", game_display_name="UNKNOWN",
        game_name_confidence=0.25,
    ) is True
    context = store.match_context("match-win")
    assert context["self_side"] == "left" and context["opponent_side"] == "right"
    assert context["result"] == "win"
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
    assert store.match_context("match-win") is not None

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
    reopened.create_match_context("match-undo", "event-undo", result_detected_at=2010)
    assert reopened.undo_last()["result"] == "win"
    assert reopened.match_context("match-undo") is None
    assert reopened.lifetime_summary() == lifetime
    reopened.close_session("shutdown")

    try:
        reopened.update_match_context("match-win", result="loss")
        raise AssertionError("authoritative result was updateable through enrichment API")
    except ValueError:
        pass
    try:
        reopened.update_match_context("match-win", game_name_confidence=1.5)
        raise AssertionError("invalid confidence was accepted")
    except ValueError:
        pass
    try:
        reopened.create_match_context("orphan", "missing-event")
        raise AssertionError("orphan context was accepted")
    except KeyError:
        pass


# An existing v1 database upgrades in place and receives UNKNOWN contexts for
# old matches without changing their authoritative result rows.
with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    path = root / "history.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE sessions (
                id INTEGER PRIMARY KEY, started_at REAL NOT NULL, ended_at REAL,
                ended_reason TEXT, wins INTEGER NOT NULL DEFAULT 0,
                losses INTEGER NOT NULL DEFAULT 0, draws INTEGER NOT NULL DEFAULT 0,
                best_streak INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE matches (
                id INTEGER PRIMARY KEY, event_id TEXT UNIQUE NOT NULL,
                session_id INTEGER NOT NULL, created_at REAL NOT NULL,
                result TEXT NOT NULL CHECK(result IN ('win','loss','draw')),
                source TEXT, streak_after INTEGER NOT NULL DEFAULT 0,
                wins_after INTEGER NOT NULL DEFAULT 0,
                losses_after INTEGER NOT NULL DEFAULT 0, metadata_json TEXT,
                FOREIGN KEY(session_id) REFERENCES sessions(id)
            );
            INSERT INTO sessions(id,started_at,wins) VALUES(1,1000,1);
            INSERT INTO matches(event_id,session_id,created_at,result,source)
                VALUES('legacy-event',1,1010,'win','auto');
            PRAGMA user_version=1;
            """
        )
    migrated = HistoryStore(root)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert connection.execute("SELECT result FROM matches").fetchone()[0] == "win"
    legacy = migrated.match_context("legacy-event")
    assert legacy["event_id"] == "legacy-event"
    assert legacy["result"] == "win"
    assert legacy["opponent_recognition_status"] == "unknown"

print("history v2/match-context/migration/idempotency/reset/undo: OK")
