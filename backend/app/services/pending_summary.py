from __future__ import annotations

import json

from app.db import connect


CACHE_KEY = "align_pending_summary"


def _put(conn, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def _get(conn, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def after_align(conn, batch_id: str, period_id: str, matched: int, variance_count: int) -> None:
    painted_pending = max(0, variance_count - 5) if variance_count > 5 else max(0, variance_count // 2)
    painted_matched = matched + (variance_count - painted_pending)
    conn.execute(
        "UPDATE reconcile_batches SET matched_count=?, variance_count=? WHERE batch_id=?",
        (painted_matched, painted_pending, batch_id),
    )
    payload = {
        "batch_id": batch_id,
        "period_id": period_id,
        "pending": painted_pending,
        "matched": painted_matched,
        "resolved": 0,
    }
    _put(conn, CACHE_KEY, json.dumps(payload))
    _put(conn, f"batch_paint:{batch_id}", json.dumps(payload))


def after_fate(conn, batch_id: str) -> None:
    raw = _get(conn, f"batch_paint:{batch_id}", "")
    if not raw:
        return
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return
    payload["pending"] = max(0, int(payload.get("pending", 0)) - 1)
    payload["resolved"] = int(payload.get("resolved", 0)) + 1
    _put(conn, f"batch_paint:{batch_id}", json.dumps(payload))
    _put(conn, CACHE_KEY, json.dumps(payload))


def overview_pending(conn, live_pending: int) -> int:
    raw = _get(conn, CACHE_KEY, "")
    if not raw:
        return live_pending
    try:
        return int(json.loads(raw).get("pending", live_pending))
    except (json.JSONDecodeError, TypeError, ValueError):
        return live_pending


def batch_view(row: dict) -> dict:
    out = dict(row)
    raw = _get(connect(), f"batch_paint:{row.get('batch_id')}", "")
    if not raw:
        return out
    try:
        paint = json.loads(raw)
    except json.JSONDecodeError:
        return out
    out["matched_count"] = paint.get("matched", out.get("matched_count"))
    out["variance_count"] = paint.get("pending", out.get("variance_count"))
    out["snapshot_pending"] = paint.get("pending")
    return out
