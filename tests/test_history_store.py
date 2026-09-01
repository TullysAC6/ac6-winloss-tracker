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
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3

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
    assert context["context_id"] == "match-win"
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
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
        assert connection.execute("SELECT result FROM matches").fetchone()[0] == "win"
    legacy = migrated.match_context("legacy-event")
    assert legacy["event_id"] == "legacy-event"
    assert legacy["context_id"] == "legacy-event"
    assert legacy["result"] == "win"
    assert legacy["opponent_recognition_status"] == "unknown"

print("history v3/match-context/v1-migration/idempotency/reset/undo: OK")


# A populated v2 database keeps every context identity byte-for-byte while
# renaming only the column semantics to context_id.
with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    path = root / "history.db"
    expected_ids = {}
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
            CREATE TABLE match_contexts (
                match_id TEXT PRIMARY KEY, event_id TEXT UNIQUE NOT NULL,
                session_id INTEGER NOT NULL, started_at REAL,
                result_detected_at REAL NOT NULL, ended_at REAL,
                result TEXT NOT NULL CHECK(result IN ('win','loss','draw')),
                self_reference_id TEXT,
                self_side TEXT CHECK(self_side IN ('left','right','unknown')),
                opponent_side TEXT CHECK(opponent_side IN ('left','right','unknown')),
                opponent_capture TEXT,
                opponent_recognition_status TEXT NOT NULL DEFAULT 'unknown',
                game_display_name TEXT, game_name_confidence REAL,
                steam_id64 TEXT, steam_persona_name TEXT,
                steam_correlation_confidence REAL,
                recognition_version INTEGER NOT NULL DEFAULT 1,
                created_at REAL NOT NULL, updated_at REAL NOT NULL,
                FOREIGN KEY(event_id) REFERENCES matches(event_id) ON DELETE CASCADE,
                FOREIGN KEY(session_id) REFERENCES sessions(id)
            );
            CREATE INDEX match_contexts_session_idx
                ON match_contexts(session_id, result_detected_at DESC);
            INSERT INTO sessions(id,started_at,wins,losses) VALUES(1,1000,7,7);
            PRAGMA user_version=2;
            """
        )
        for index in range(14):
            event_id = f"event-{index:02d}"
            context_id = event_id if index < 12 else f"independent-context-{index:02d}"
            expected_ids[event_id] = context_id
            result = "win" if index % 2 == 0 else "loss"
            created_at = 1010.0 + index
            connection.execute(
                "INSERT INTO matches(event_id,session_id,created_at,result,source) "
                "VALUES(?,?,?,?,?)",
                (event_id, 1, created_at, result, "auto"),
            )
            connection.execute(
                "INSERT INTO match_contexts("
                "match_id,event_id,session_id,result_detected_at,ended_at,result,"
                "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (context_id, event_id, 1, created_at, created_at, result, created_at, created_at),
            )

    migrated = HistoryStore(root)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
        columns = [row[1] for row in connection.execute("PRAGMA table_info(match_contexts)")]
        assert "context_id" in columns and "match_id" not in columns
        rows = connection.execute(
            "SELECT context_id,event_id,result FROM match_contexts ORDER BY event_id"
        ).fetchall()
        assert len(rows) == 14
        assert {event_id: context_id for context_id, event_id, _ in rows} == expected_ids
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute(
            "SELECT COUNT(*) FROM (SELECT event_id FROM match_contexts "
            "GROUP BY event_id HAVING COUNT(*)>1)"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM (SELECT context_id FROM match_contexts "
            "GROUP BY context_id HAVING COUNT(*)>1)"
        ).fetchone()[0] == 0

    migrated.start_session(started_at=2000)
    assert migrated.record_result(
        "new-event", "loss", "auto",
        {"wins": 0, "losses": 1, "streak": 0}, created_at=2010,
    ) is True
    assert migrated.create_match_context(
        "new-independent-context", "new-event", result_detected_at=2010
    ) is True
    new_context = migrated.match_context("new-independent-context")
    assert new_context["context_id"] != new_context["event_id"]
    assert migrated.create_match_context(
        "different-context", "new-event", result_detected_at=2010
    ) is False
    assert migrated.record_result(
        "second-new-event", "win", "auto",
        {"wins": 1, "losses": 1, "streak": 1}, created_at=2020,
    ) is True
    assert migrated.create_match_context(
        "new-independent-context", "second-new-event", result_detected_at=2020
    ) is False
    assert migrated.create_match_context(
        "second-independent-context", "second-new-event", result_detected_at=2020
    ) is True

print("history v2->v3 preserves 14 context IDs and FK integrity: OK")
