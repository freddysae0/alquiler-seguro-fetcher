from __future__ import annotations

import os
import sqlite3
from pathlib import Path

DATA_DIR = os.environ.get("DATA_DIR", "data")


def _db_path() -> Path:
    path = Path(DATA_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return path / "alquiler.db"


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                chat_id INTEGER NOT NULL,
                username TEXT,
                first_seen TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS user_config (
                user_id INTEGER PRIMARY KEY,
                provincia TEXT,
                precio_min REAL,
                precio_max REAL,
                habitaciones INTEGER,
                banyos INTEGER,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            );

            CREATE TABLE IF NOT EXISTS notified (
                user_id INTEGER NOT NULL,
                inmueble_id INTEGER NOT NULL,
                PRIMARY KEY (user_id, inmueble_id),
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            );
            """
        )


def upsert_user(user_id: int, chat_id: int, username: str | None, first_seen: str) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO users (user_id, chat_id, username, first_seen)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                chat_id = excluded.chat_id,
                username = excluded.username
            """,
            (user_id, chat_id, username, first_seen),
        )


def save_config(user_id: int, config: dict) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO user_config (user_id, provincia, precio_min, precio_max, habitaciones, banyos)
            VALUES (:user_id, :provincia, :precio_min, :precio_max, :habitaciones, :banyos)
            ON CONFLICT(user_id) DO UPDATE SET
                provincia = excluded.provincia,
                precio_min = excluded.precio_min,
                precio_max = excluded.precio_max,
                habitaciones = excluded.habitaciones,
                banyos = excluded.banyos
            """,
            {"user_id": user_id, **config},
        )


def get_config(user_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT provincia, precio_min, precio_max, habitaciones, banyos FROM user_config WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    return dict(row) if row else None


def all_configs() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT c.user_id, c.provincia, c.precio_min, c.precio_max, c.habitaciones, c.banyos,
                   u.chat_id
            FROM user_config c
            JOIN users u ON u.user_id = c.user_id
            """
        ).fetchall()
    return [dict(r) for r in rows]


def add_notified(user_id: int, inmueble_ids: list[int]) -> None:
    if not inmueble_ids:
        return
    with get_conn() as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO notified (user_id, inmueble_id) VALUES (?, ?)",
            [(user_id, iid) for iid in inmueble_ids],
        )


def has_notified(user_id: int, inmueble_id: int) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM notified WHERE user_id = ? AND inmueble_id = ?",
            (user_id, inmueble_id),
        ).fetchone()
    return row is not None


def reset_notified(user_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM notified WHERE user_id = ?", (user_id,))
