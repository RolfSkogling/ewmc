"""
db.py — SQLite wrapper for the EWMC memory store.

Read-only mode is used automatically for read commands so the store can be
queried from sandboxed agents that don't have write access to the memory
directory. Write commands use normal mode with WAL.
"""
from __future__ import annotations

import sqlite3
import struct
from pathlib import Path
from typing import Optional, Sequence

import numpy as np


# Commands that only read — open read-only/immutable to avoid WAL lock attempts
READONLY_COMMANDS = {"status", "show", "list", "search", "recall", "tier1"}


def connect(db_path: Path, readonly: bool = False) -> sqlite3.Connection:
    """Open a SQLite connection.

    readonly=True: opens via URI with mode=ro&immutable=1 — safe from sandboxes
                   that can't write WAL/SHM files.
    readonly=False: opens normally with WAL journal mode.
    """
    db_path = Path(db_path)

    if readonly:
        uri = f"file:{db_path.as_posix()}?mode=ro&immutable=1"
        conn = sqlite3.connect(uri, uri=True, isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_schema(conn: sqlite3.Connection, schema_path: Path) -> None:
    """Apply schema.sql if tables don't yet exist."""
    sql = Path(schema_path).read_text(encoding="utf-8")
    conn.executescript(sql)


def encode_embedding(vec: Sequence[float]) -> bytes:
    arr = np.asarray(vec, dtype=np.float32)
    return arr.tobytes()


def decode_embedding(blob: Optional[bytes]) -> Optional[np.ndarray]:
    if blob is None:
        return None
    return np.frombuffer(blob, dtype=np.float32)


def get_meta(conn: sqlite3.Connection, key: str) -> Optional[str]:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """INSERT INTO meta (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)
           ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP""",
        (key, value),
    )
