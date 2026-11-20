from __future__ import annotations
from fastapi import APIRouter
from app.services import pending_summary

from app.db import connect
from app.services.gate import get_setting
from app.services.settings_service import get_ruler

router = APIRouter(tags=["dashboard"])

# A variance is "unresolved" while it still needs a human decision and still can get
# one — i.e. its batch is open.  Sealed batches can no longer be dispositioned, so
# their leftovers are reported separately rather than inflating the actionable count.
UNRESOLVED_FATES = ("pending", "quarantine")


@router.get("/health")
def health():
    return {"ok": True, "product": "抄表簿"}


@router.get("/overview")
def overview():
    """Headline unresolved count is derived from exactly the same rows as the
    ``fate_buckets`` it is made of, so the two can never disagree."""
    conn = connect()
    try:
        open_periods = conn.execute(
            "SELECT COUNT(*) AS c FROM periods WHERE status='open'"
        ).fetchone()["c"]
        sealed_periods = conn.execute(
            "SELECT COUNT(*) AS c FROM periods WHERE status='sealed'"
        ).fetchone()["c"]
        meters = conn.execute("SELECT COUNT(*) AS c FROM meters").fetchone()["c"]

        buckets = {}
        for status, rows in (
            ("open", conn.execute(
                """SELECT v.fate AS fate, COUNT(*) AS c
                   FROM variances v JOIN reconcile_batches b ON b.batch_id = v.batch_id
                   WHERE b.status='open' GROUP BY v.fate"""
            ).fetchall()),
            ("sealed", conn.execute(
                """SELECT v.fate AS fate, COUNT(*) AS c
                   FROM variances v JOIN reconcile_batches b ON b.batch_id = v.batch_id
                   WHERE b.status='sealed' GROUP BY v.fate"""
            ).fetchall()),
        ):
            for r in rows:
                buckets.setdefault(status, {})[r["fate"]] = r["c"]

        open_buckets = buckets.get("open", {})
        unresolved = sum(open_buckets.get(f, 0) for f in UNRESOLVED_FATES)
        sealed_unresolved = sum(
            buckets.get("sealed", {}).get(f, 0) for f in UNRESOLVED_FATES
        )

        batches = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM reconcile_batches ORDER BY created_at DESC LIMIT 8"
            ).fetchall()
        ]
        return {
            "current_period": get_setting("current_period", ""),
            "ruler": get_ruler(conn),
            "open_periods": open_periods,
            "sealed_periods": sealed_periods,
            "meter_count": meters,
            # actionable: open-batch rows still awaiting a decision
            "unresolved_variances": pending_summary.overview_pending(conn, unresolved),
            # sealed with a pending/quarantine fate: cannot change, surfaced for audit
            "sealed_unresolved": sealed_unresolved,
            "fate_buckets": open_buckets,
            "snapshot_pending": pending_summary.overview_pending(conn, unresolved),
            "sealed_fate_buckets": buckets.get("sealed", {}),
            "recent_batches": [pending_summary.batch_view(b) for b in batches],
        }
    finally:
        conn.close()
