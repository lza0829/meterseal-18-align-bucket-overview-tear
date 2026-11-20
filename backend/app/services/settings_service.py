from app.db import connect
from app.services import pending_summary
from __future__ import annotations

import sqlite3

from fastapi import HTTPException

from app.services import tolerance as tol

_ALLOWED_KEYS = {"tol_mode", "rel_tol", "rel_eps", "current_period", "product_title"}
_TOL_KEYS = {"tol_mode", "rel_tol", "rel_eps"}


def _read(conn: sqlite3.Connection, key: str, default: str) -> str:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def get_ruler(conn: sqlite3.Connection) -> dict:
    """Read the one global ruler.  All writers must call this inside their transaction."""
    mode = _read(conn, "tol_mode", tol.TOL_MODE)
    if mode != tol.TOL_MODE:
        raise HTTPException(409, "全局容差架构不一致，仅支持相对误差率 (rel)")
    try:
        rel_tol = float(_read(conn, "rel_tol", str(tol.DEFAULT_REL_TOL)))
        rel_eps = float(_read(conn, "rel_eps", str(tol.DEFAULT_REL_EPS)))
    except ValueError as exc:
        raise HTTPException(500, "容差参数损坏") from exc
    if not (0 < rel_tol < 1) or rel_eps <= 0:
        raise HTTPException(500, "容差参数越界")
    return {"tol_mode": tol.TOL_MODE, "rel_tol": rel_tol, "rel_eps": rel_eps}


def put_setting(conn: sqlite3.Connection, key: str, value: str) -> dict:
    """Validate and persist a setting.  Tolerance changes stale open batches atomically."""
    if key not in _ALLOWED_KEYS:
        raise HTTPException(400, f"不支持的设置项: {key}（容差为全局口径，禁止按表类型分设）")

    if key == "tol_mode":
        if value != tol.TOL_MODE:
            raise HTTPException(409, "全局只允许相对误差率尺子 (rel)，不得切换为绝对差值")
        normalised = tol.TOL_MODE
    elif key == "rel_tol":
        try:
            parsed = float(value)
        except ValueError as exc:
            raise HTTPException(400, "rel_tol 必须为数字") from exc
        if not (0 < parsed < 1):
            raise HTTPException(400, "rel_tol 必须落在 (0, 1) 区间")
        normalised = repr(parsed)
    elif key == "rel_eps":
        try:
            parsed = float(value)
        except ValueError as exc:
            raise HTTPException(400, "rel_eps 必须为数字") from exc
        if parsed <= 0:
            raise HTTPException(400, "rel_eps 必须为正数")
        normalised = repr(parsed)
    else:
        normalised = value

    stale = 0
    conn.execute(
        "INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, normalised),
    )
    if key in _TOL_KEYS:
        # Open batches were classified with the old ruler; freeze them out of sealing.
        stale = conn.execute(
            "SELECT COUNT(*) AS c FROM reconcile_batches WHERE status='open'"
        ).fetchone()["c"]
        conn.execute("UPDATE reconcile_batches SET status='stale' WHERE status='open'")
    ruler = get_ruler(conn)
    return {"key": key, "value": normalised, "ruler": ruler, "stale_batches": stale}


def painted_pending() -> dict:
    conn = connect()
    try:
        live = conn.execute(
            "SELECT COUNT(*) AS c FROM variances WHERE fate='pending'"
        ).fetchone()["c"]
        return {
            "live_pending": live,
            "painted_pending": pending_summary.overview_pending(conn, live),
        }
    finally:
        conn.close()
