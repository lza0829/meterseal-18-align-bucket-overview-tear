from __future__ import annotations

import sqlite3

from fastapi import HTTPException

from app.db import connect


def writable_in_tx(conn: sqlite3.Connection, period_id: str) -> None:
    """Seal gate.  MUST be called inside the same transaction as the write it guards,
    so a concurrent seal cannot slip between the check and the insert."""
    row = conn.execute(
        "SELECT status FROM periods WHERE period_id=?", (period_id,)
    ).fetchone()
    if not row:
        raise HTTPException(404, f"账期不存在: {period_id}")
    if row["status"] == "sealed":
        raise HTTPException(409, f"账期已封印，禁止写入: {period_id}")


def assert_period_writable(period_id: str) -> None:
    conn = connect()
    try:
        writable_in_tx(conn, period_id)
    finally:
        conn.close()


def get_setting(key: str, default: str = "") -> str:
    conn = connect()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default
    finally:
        conn.close()
