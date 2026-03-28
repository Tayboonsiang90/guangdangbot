"""SQLite persistence for worker snapshot state and per-worker Discord channel IDs."""

from __future__ import annotations

import sqlite3
from pathlib import Path
class StateStore:
    """Thread-safe enough for asyncio single-thread event loop (one connection per open)."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS worker_state (
                    worker_id TEXT PRIMARY KEY NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS worker_channels (
                    worker_id TEXT PRIMARY KEY NOT NULL,
                    channel_id INTEGER NOT NULL,
                    created_at TEXT
                )
                """
            )
            conn.commit()

    def get_worker_payload(self, worker_id: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM worker_state WHERE worker_id = ?",
                (worker_id,),
            ).fetchone()
            return str(row["payload_json"]) if row else None

    def set_worker_payload(self, worker_id: str, payload_json: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO worker_state (worker_id, payload_json)
                VALUES (?, ?)
                ON CONFLICT(worker_id) DO UPDATE SET payload_json = excluded.payload_json
                """,
                (worker_id, payload_json),
            )
            conn.commit()

    def get_worker_channel_id(self, worker_id: str) -> int | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT channel_id FROM worker_channels WHERE worker_id = ?",
                (worker_id,),
            ).fetchone()
            return int(row["channel_id"]) if row else None

    def set_worker_channel_id(self, worker_id: str, channel_id: int) -> None:
        from datetime import datetime, timezone

        created = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO worker_channels (worker_id, channel_id, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(worker_id) DO UPDATE SET channel_id = excluded.channel_id
                """,
                (worker_id, channel_id, created),
            )
            conn.commit()

    def delete_worker_channel_row(self, worker_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM worker_channels WHERE worker_id = ?",
                (worker_id,),
            )
            conn.commit()
