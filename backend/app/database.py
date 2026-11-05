"""对局与 Agent 的 SQLite 存储。"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        self._init()

    def _init(self) -> None:
        with self.lock:
            self.conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                PRAGMA foreign_keys=ON;
                CREATE TABLE IF NOT EXISTS games (
                    id TEXT PRIMARY KEY,
                    mode TEXT NOT NULL,
                    black_id TEXT NOT NULL,
                    white_id TEXT NOT NULL,
                    black_name TEXT NOT NULL,
                    white_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    winner TEXT,
                    end_reason TEXT,
                    end_detail TEXT,
                    win_stones REAL,
                    black_area INTEGER,
                    white_area INTEGER,
                    area_diff_after_komi REAL,
                    created_at TEXT NOT NULL,
                    finished_at TEXT
                );
                CREATE TABLE IF NOT EXISTS moves (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id TEXT NOT NULL,
                    move_number INTEGER NOT NULL,
                    color TEXT NOT NULL,
                    action TEXT NOT NULL,
                    x INTEGER,
                    y INTEGER,
                    vertex TEXT,
                    captured INTEGER NOT NULL DEFAULT 0,
                    think_ms INTEGER,
                    FOREIGN KEY (game_id) REFERENCES games(id)
                );
                CREATE INDEX IF NOT EXISTS idx_moves_game ON moves(game_id, move_number);
                CREATE TABLE IF NOT EXISTS analyses (
                    game_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (game_id) REFERENCES games(id)
                );
                CREATE TABLE IF NOT EXISTS agents (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    protocol TEXT NOT NULL,
                    command TEXT,
                    endpoint TEXT,
                    created_at TEXT NOT NULL
                );
                """
            )
            self.conn.commit()

    def create_game(self, row: dict) -> None:
        with self.lock:
            self.conn.execute(
                """
                INSERT INTO games (
                    id, mode, black_id, white_id, black_name, white_name,
                    status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'playing', ?)
                """,
                (
                    row["id"],
                    row["mode"],
                    row["black_id"],
                    row["white_id"],
                    row["black_name"],
                    row["white_name"],
                    row["created_at"],
                ),
            )
            self.conn.commit()

    def get_game(self, game_id: str) -> dict | None:
        with self.lock:
            row = self.conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
        return dict(row) if row else None

    def list_games(self, limit: int = 30) -> list[dict]:
        with self.lock:
            rows = self.conn.execute(
                "SELECT * FROM games ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_moves(self, game_id: str) -> list[dict]:
        with self.lock:
            rows = self.conn.execute(
                "SELECT * FROM moves WHERE game_id = ? ORDER BY move_number ASC",
                (game_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def add_move(self, row: dict) -> None:
        with self.lock:
            self.conn.execute(
                """
                INSERT INTO moves (
                    game_id, move_number, color, action, x, y, vertex, captured, think_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["game_id"],
                    row["move_number"],
                    row["color"],
                    row["action"],
                    row["x"],
                    row["y"],
                    row["vertex"],
                    row["captured"],
                    row["think_ms"],
                ),
            )
            self.conn.commit()

    def finish_game(self, game_id: str, fields: dict) -> None:
        with self.lock:
            self.conn.execute(
                """
                UPDATE games SET
                    status = 'finished',
                    winner = ?,
                    end_reason = ?,
                    end_detail = ?,
                    win_stones = ?,
                    black_area = ?,
                    white_area = ?,
                    area_diff_after_komi = ?,
                    finished_at = ?
                WHERE id = ?
                """,
                (
                    fields["winner"],
                    fields["end_reason"],
                    fields["end_detail"],
                    fields["win_stones"],
                    fields["black_area"],
                    fields["white_area"],
                    fields["area_diff_after_komi"],
                    fields["finished_at"],
                    game_id,
                ),
            )
            self.conn.commit()

    def save_analysis(self, game_id: str, payload: str) -> None:
        with self.lock:
            self.conn.execute(
                """
                INSERT INTO analyses (game_id, payload, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(game_id) DO UPDATE SET payload = excluded.payload, created_at = excluded.created_at
                """,
                (game_id, payload, utc_now()),
            )
            self.conn.commit()

    def get_analysis(self, game_id: str) -> str | None:
        with self.lock:
            row = self.conn.execute(
                "SELECT payload FROM analyses WHERE game_id = ?",
                (game_id,),
            ).fetchone()
        return None if row is None else row["payload"]

    def create_agent(self, row: dict) -> None:
        with self.lock:
            self.conn.execute(
                """
                INSERT INTO agents (id, name, protocol, command, endpoint, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    row["id"],
                    row["name"],
                    row["protocol"],
                    row["command"],
                    row["endpoint"],
                    row["created_at"],
                ),
            )
            self.conn.commit()

    def list_agents(self) -> list[dict]:
        with self.lock:
            rows = self.conn.execute("SELECT * FROM agents ORDER BY created_at ASC").fetchall()
        return [dict(row) for row in rows]

    def get_agent(self, agent_id: str) -> dict | None:
        with self.lock:
            row = self.conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
        return dict(row) if row else None

    def delete_agent(self, agent_id: str) -> bool:
        with self.lock:
            cursor = self.conn.execute("DELETE FROM agents WHERE id = ?", (agent_id,))
            self.conn.commit()
            return cursor.rowcount > 0
