from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


class HistoryStore:
    """Transactional lifetime history owned exclusively by server.py.

    Identity semantics:
    - matches.id is SQLite's internal integer key.
    - matches.event_id is the stable detector/result-event identity and the
      canonical FK for future match-level enrichment tables.
    - match_contexts.context_id identifies only the context row itself.
    - A future game_match_id is AC6 result-screen metadata and must remain
      distinct from both event_id and context_id.
    """

    SCHEMA_VERSION = 3
    MATCH_CONTEXT_VERSION = 1
    MATCH_CONTEXT_FIELDS = {
        "started_at", "ended_at", "self_reference_id", "self_side",
        "opponent_side", "opponent_capture", "opponent_recognition_status",
        "game_display_name", "game_name_confidence", "steam_id64",
        "steam_persona_name", "steam_correlation_confidence",
        "recognition_version",
    }

    def __init__(self, root: str | Path):
        self.path = Path(root) / "history.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._current_session_id: int | None = None
        self._migrate()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _migrate(self) -> None:
        with self._lock, self._connect() as connection:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version > self.SCHEMA_VERSION:
                raise RuntimeError(f"unsupported history schema version: {version}")
            if version == 0:
                connection.executescript(
                    """
                    CREATE TABLE sessions (
                        id INTEGER PRIMARY KEY,
                        started_at REAL NOT NULL,
                        ended_at REAL,
                        ended_reason TEXT,
                        wins INTEGER NOT NULL DEFAULT 0,
                        losses INTEGER NOT NULL DEFAULT 0,
                        draws INTEGER NOT NULL DEFAULT 0,
                        best_streak INTEGER NOT NULL DEFAULT 0
                    );
                    CREATE TABLE matches (
                        id INTEGER PRIMARY KEY,
                        event_id TEXT UNIQUE NOT NULL,
                        session_id INTEGER NOT NULL,
                        created_at REAL NOT NULL,
                        result TEXT NOT NULL CHECK(result IN ('win','loss','draw')),
                        source TEXT,
                        streak_after INTEGER NOT NULL DEFAULT 0,
                        wins_after INTEGER NOT NULL DEFAULT 0,
                        losses_after INTEGER NOT NULL DEFAULT 0,
                        metadata_json TEXT,
                        FOREIGN KEY(session_id) REFERENCES sessions(id)
                    );
                    CREATE INDEX matches_created_at_idx ON matches(created_at DESC);
                    CREATE INDEX matches_session_id_idx ON matches(session_id, id);
                    PRAGMA user_version=1;
                    """
                )
                version = 1
            if version == 1:
                connection.executescript(
                    """
                    CREATE TABLE match_contexts (
                        match_id TEXT PRIMARY KEY,
                        event_id TEXT UNIQUE NOT NULL,
                        session_id INTEGER NOT NULL,
                        started_at REAL,
                        result_detected_at REAL NOT NULL,
                        ended_at REAL,
                        result TEXT NOT NULL CHECK(result IN ('win','loss','draw')),
                        self_reference_id TEXT,
                        self_side TEXT CHECK(self_side IN ('left','right','unknown')),
                        opponent_side TEXT CHECK(opponent_side IN ('left','right','unknown')),
                        opponent_capture TEXT,
                        opponent_recognition_status TEXT NOT NULL DEFAULT 'unknown',
                        game_display_name TEXT,
                        game_name_confidence REAL CHECK(
                            game_name_confidence IS NULL OR
                            game_name_confidence BETWEEN 0.0 AND 1.0
                        ),
                        steam_id64 TEXT,
                        steam_persona_name TEXT,
                        steam_correlation_confidence REAL CHECK(
                            steam_correlation_confidence IS NULL OR
                            steam_correlation_confidence BETWEEN 0.0 AND 1.0
                        ),
                        recognition_version INTEGER NOT NULL DEFAULT 1,
                        created_at REAL NOT NULL,
                        updated_at REAL NOT NULL,
                        FOREIGN KEY(event_id) REFERENCES matches(event_id) ON DELETE CASCADE,
                        FOREIGN KEY(session_id) REFERENCES sessions(id)
                    );
                    CREATE INDEX match_contexts_session_idx
                        ON match_contexts(session_id, result_detected_at DESC);
                    INSERT INTO match_contexts(
                        match_id,event_id,session_id,started_at,
                        result_detected_at,ended_at,result,
                        opponent_recognition_status,recognition_version,
                        created_at,updated_at
                    )
                    SELECT
                        event_id,event_id,session_id,NULL,
                        created_at,created_at,result,
                        'unknown',1,created_at,created_at
                    FROM matches;
                    PRAGMA user_version=2;
                    """
                )
                version = 2
            if version == 2:
                try:
                    connection.executescript(
                        """
                        BEGIN IMMEDIATE;
                        ALTER TABLE match_contexts RENAME TO match_contexts_v2;
                        CREATE TABLE match_contexts (
                            context_id TEXT PRIMARY KEY,
                            event_id TEXT UNIQUE NOT NULL,
                            session_id INTEGER NOT NULL,
                            started_at REAL,
                            result_detected_at REAL NOT NULL,
                            ended_at REAL,
                            result TEXT NOT NULL CHECK(result IN ('win','loss','draw')),
                            self_reference_id TEXT,
                            self_side TEXT CHECK(self_side IN ('left','right','unknown')),
                            opponent_side TEXT CHECK(opponent_side IN ('left','right','unknown')),
                            opponent_capture TEXT,
                            opponent_recognition_status TEXT NOT NULL DEFAULT 'unknown',
                            game_display_name TEXT,
                            game_name_confidence REAL CHECK(
                                game_name_confidence IS NULL OR
                                game_name_confidence BETWEEN 0.0 AND 1.0
                            ),
                            steam_id64 TEXT,
                            steam_persona_name TEXT,
                            steam_correlation_confidence REAL CHECK(
                                steam_correlation_confidence IS NULL OR
                                steam_correlation_confidence BETWEEN 0.0 AND 1.0
                            ),
                            recognition_version INTEGER NOT NULL DEFAULT 1,
                            created_at REAL NOT NULL,
                            updated_at REAL NOT NULL,
                            FOREIGN KEY(event_id) REFERENCES matches(event_id) ON DELETE CASCADE,
                            FOREIGN KEY(session_id) REFERENCES sessions(id)
                        );
                        INSERT INTO match_contexts(
                            context_id,event_id,session_id,started_at,
                            result_detected_at,ended_at,result,self_reference_id,
                            self_side,opponent_side,opponent_capture,
                            opponent_recognition_status,game_display_name,
                            game_name_confidence,steam_id64,steam_persona_name,
                            steam_correlation_confidence,recognition_version,
                            created_at,updated_at
                        )
                        SELECT
                            match_id,event_id,session_id,started_at,
                            result_detected_at,ended_at,result,self_reference_id,
                            self_side,opponent_side,opponent_capture,
                            opponent_recognition_status,game_display_name,
                            game_name_confidence,steam_id64,steam_persona_name,
                            steam_correlation_confidence,recognition_version,
                            created_at,updated_at
                        FROM match_contexts_v2;
                        DROP TABLE match_contexts_v2;
                        CREATE INDEX match_contexts_session_idx
                            ON match_contexts(session_id, result_detected_at DESC);
                        PRAGMA user_version=3;
                        COMMIT;
                        """
                    )
                except Exception:
                    connection.rollback()
                    raise

    @property
    def current_session_id(self) -> int | None:
        with self._lock:
            return self._current_session_id

    def start_session(self, started_at: float | None = None) -> int:
        started_at = time.time() if started_at is None else float(started_at)
        with self._lock, self._connect() as connection:
            connection.execute(
                "UPDATE sessions SET ended_at=?, ended_reason=? WHERE ended_at IS NULL",
                (started_at, "recovered"),
            )
            cursor = connection.execute(
                "INSERT INTO sessions(started_at) VALUES (?)", (started_at,)
            )
            self._current_session_id = int(cursor.lastrowid)
            return self._current_session_id

    def close_session(self, reason: str = "shutdown", ended_at: float | None = None) -> None:
        ended_at = time.time() if ended_at is None else float(ended_at)
        with self._lock:
            session_id = self._current_session_id
            if session_id is None:
                return
            with self._connect() as connection:
                connection.execute(
                    "UPDATE sessions SET ended_at=?, ended_reason=? "
                    "WHERE id=? AND ended_at IS NULL",
                    (ended_at, str(reason), session_id),
                )
            self._current_session_id = None

    def reset_session(self) -> int:
        self.close_session("manual_reset")
        return self.start_session()

    def record_result(
        self,
        event_id: str,
        result: str,
        source: str,
        stats: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        created_at: float | None = None,
    ) -> bool:
        if result not in ("win", "loss", "draw"):
            raise ValueError("invalid history result")
        created_at = time.time() if created_at is None else float(created_at)
        with self._lock:
            session_id = self._current_session_id
            if session_id is None:
                raise RuntimeError("history session is not active")
            metadata_json = (
                json.dumps(metadata, ensure_ascii=False, separators=(",", ":"))
                if metadata else None
            )
            with self._connect() as connection:
                cursor = connection.execute(
                    "INSERT OR IGNORE INTO matches("
                    "event_id,session_id,created_at,result,source,streak_after,"
                    "wins_after,losses_after,metadata_json) VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        str(event_id), session_id, created_at, result, str(source),
                        int(stats.get("streak", 0)), int(stats.get("wins", 0)),
                        int(stats.get("losses", 0)), metadata_json,
                    ),
                )
                if cursor.rowcount == 0:
                    return False
                column = {"win": "wins", "loss": "losses", "draw": "draws"}[result]
                connection.execute(
                    f"UPDATE sessions SET {column}={column}+1, "
                    "best_streak=MAX(best_streak, ?) WHERE id=?",
                    (int(stats.get("streak", 0)), session_id),
                )
                return True

    @staticmethod
    def _optional_timestamp(value: Any, field: str) -> float | None:
        if value is None:
            return None
        if type(value) is bool:
            raise ValueError(f"{field} must be a timestamp or null")
        timestamp = float(value)
        if timestamp < 0:
            raise ValueError(f"{field} must be non-negative")
        return timestamp

    @staticmethod
    def _optional_confidence(value: Any, field: str) -> float | None:
        if value is None:
            return None
        if type(value) is bool:
            raise ValueError(f"{field} must be 0.0..1.0 or null")
        confidence = float(value)
        if not 0.0 <= confidence <= 1.0:
            raise ValueError(f"{field} must be 0.0..1.0 or null")
        return confidence

    @classmethod
    def _clean_context_updates(cls, fields: dict[str, Any]) -> dict[str, Any]:
        unknown = set(fields) - cls.MATCH_CONTEXT_FIELDS
        if unknown:
            raise ValueError("unknown match context field(s): " + ", ".join(sorted(unknown)))
        cleaned = dict(fields)
        for field in ("started_at", "ended_at"):
            if field in cleaned:
                cleaned[field] = cls._optional_timestamp(cleaned[field], field)
        for field in ("game_name_confidence", "steam_correlation_confidence"):
            if field in cleaned:
                cleaned[field] = cls._optional_confidence(cleaned[field], field)
        for field in ("self_side", "opponent_side"):
            if field in cleaned and cleaned[field] not in (None, "left", "right", "unknown"):
                raise ValueError(f"{field} must be left, right, unknown or null")
        if "recognition_version" in cleaned:
            value = cleaned["recognition_version"]
            if type(value) is not int or value < 1:
                raise ValueError("recognition_version must be a positive integer")
        for field in (
            "self_reference_id", "opponent_capture", "opponent_recognition_status",
            "game_display_name", "steam_id64", "steam_persona_name",
        ):
            if field in cleaned and cleaned[field] is not None:
                cleaned[field] = str(cleaned[field])[:1024]
        return cleaned

    def create_match_context(
        self,
        context_id: str,
        event_id: str,
        result_detected_at: float | None = None,
        started_at: float | None = None,
        ended_at: float | None = None,
    ) -> bool:
        """Create optional enrichment only after the authoritative result exists."""
        context_id = str(context_id).strip()
        event_id = str(event_id).strip()
        if not context_id or not event_id:
            raise ValueError("context_id and event_id are required")
        detected_at = (
            time.time() if result_detected_at is None
            else self._optional_timestamp(result_detected_at, "result_detected_at")
        )
        started_at = self._optional_timestamp(started_at, "started_at")
        ended_at = self._optional_timestamp(ended_at, "ended_at")
        if ended_at is None:
            ended_at = detected_at
        now = time.time()
        with self._lock, self._connect() as connection:
            match = connection.execute(
                "SELECT session_id,result FROM matches WHERE event_id=?", (event_id,)
            ).fetchone()
            if match is None:
                raise KeyError("authoritative history result does not exist")
            cursor = connection.execute(
                "INSERT OR IGNORE INTO match_contexts("
                "context_id,event_id,session_id,started_at,result_detected_at,ended_at,"
                "result,opponent_recognition_status,recognition_version,created_at,updated_at"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    context_id, event_id, int(match["session_id"]), started_at,
                    detected_at, ended_at, str(match["result"]), "unknown",
                    self.MATCH_CONTEXT_VERSION, now, now,
                ),
            )
            return cursor.rowcount == 1

    def update_match_context(self, context_id: str, **fields: Any) -> bool:
        """Safely add optional recognition fields without touching match result."""
        cleaned = self._clean_context_updates(fields)
        if not cleaned:
            return False
        assignments = ",".join(f"{field}=?" for field in cleaned)
        values = list(cleaned.values())
        values.extend((time.time(), str(context_id)))
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                f"UPDATE match_contexts SET {assignments},updated_at=? WHERE context_id=?",
                values,
            )
            return cursor.rowcount == 1

    def match_context(self, context_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM match_contexts WHERE context_id=?", (str(context_id),)
            ).fetchone()
        return dict(row) if row is not None else None

    def undo_last(self) -> dict[str, Any] | None:
        with self._lock:
            session_id = self._current_session_id
            if session_id is None:
                return None
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT event_id FROM matches WHERE session_id=? ORDER BY id DESC LIMIT 1",
                    (session_id,),
                ).fetchone()
            return self.undo_event(str(row["event_id"])) if row is not None else None

    def undo_event(self, event_id: str) -> dict[str, Any] | None:
        """Undo only the matching accepted event; never remove a neighbour."""
        with self._lock:
            session_id = self._current_session_id
            if session_id is None:
                return None
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT id,event_id,result FROM matches "
                    "WHERE session_id=? AND event_id=?",
                    (session_id, str(event_id)),
                ).fetchone()
                if row is None:
                    return None
                connection.execute("DELETE FROM matches WHERE id=?", (int(row["id"]),))
                aggregate = connection.execute(
                    "SELECT COALESCE(SUM(result='win'),0) wins, "
                    "COALESCE(SUM(result='loss'),0) losses, "
                    "COALESCE(SUM(result='draw'),0) draws, "
                    "COALESCE(MAX(streak_after),0) best_streak "
                    "FROM matches WHERE session_id=?",
                    (session_id,),
                ).fetchone()
                connection.execute(
                    "UPDATE sessions SET wins=?,losses=?,draws=?,best_streak=? WHERE id=?",
                    (aggregate["wins"], aggregate["losses"], aggregate["draws"],
                     aggregate["best_streak"], session_id),
                )
                return {
                    "id": int(row["id"]), "event_id": str(row["event_id"]),
                    "result": str(row["result"]),
                }

    def lifetime_summary(self) -> dict[str, Any]:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(SUM(wins),0) wins, COALESCE(SUM(losses),0) losses, "
                "COALESCE(SUM(draws),0) draws, COALESCE(MAX(best_streak),0) best_streak "
                "FROM sessions"
            ).fetchone()
        wins, losses, draws = int(row["wins"]), int(row["losses"]), int(row["draws"])
        matches = wins + losses
        return {
            "wins": wins, "losses": losses, "draws": draws, "matches": matches,
            "win_rate": round(wins / matches * 100.0, 1) if matches else 0.0,
            "best_streak": int(row["best_streak"]),
        }

    def recent_matches(self, limit: int = 10) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 100))
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT event_id,session_id,created_at,result,source,streak_after "
                "FROM matches ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

    def session_metadata(self) -> dict[str, Any] | None:
        with self._lock:
            session_id = self._current_session_id
            if session_id is None:
                return None
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT id,started_at,ended_at FROM sessions WHERE id=?", (session_id,)
                ).fetchone()
            return dict(row) if row is not None else None
