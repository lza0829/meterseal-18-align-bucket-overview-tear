from __future__ import annotations
import sqlite3
from contextlib import contextmanager
from typing import Iterator

from app.config import DB_PATH


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    # WAL: one writer at a time, readers never blocked by writers.  Writers serialise
    # through BEGIN IMMEDIATE below, so a ruler change and an align cannot interleave.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Write transaction that serialises with other writers (BEGIN IMMEDIATE).

    Everything read inside the block is seen under one consistent snapshot and
    is committed atomically, so a ruler change can never be half-applied.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
