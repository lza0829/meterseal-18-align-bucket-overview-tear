from __future__ import annotations

from fastapi import APIRouter

from app.db import connect
from app.services import consistency
from app.services.gate import get_setting
from app.services.settings_service import get_ruler

router = APIRouter(tags=["dashboard"])

# A variance is "unresolved" while it still needs a human decision and still can get
# one — i.e. its batch is open.  Sealed batches can no longer be dispositioned, so
# their leftovers are reported separately rather than inflating the actionable count.
UNRESOLVED_FATES = ("pending", "quarantine")
RESOLVED_FATES = ("accept_short", "accept_over")


@router.get("/health")
def health():
    return {"ok": True, "product": "抄表簿"}


@router.get("/overview")
def overview():
    """总览头条（未决/已决/自动合拢）全部分桶正式表实时聚合：与 ``fate_buckets``
    同源同事务快照，库内不存在第二套计数；发现批快照字段漂移先按分桶自愈再读。"""
    conn = connect()
    try:
        consistency.heal_if_drifted(conn)

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
        # actionable: open-batch rows still awaiting a decision
        unresolved = sum(open_buckets.get(f, 0) for f in UNRESOLVED_FATES)
        resolved = sum(open_buckets.get(f, 0) for f in RESOLVED_FATES)
        auto_matched = conn.execute(
            """SELECT COUNT(*) AS c FROM match_audit m
               JOIN reconcile_batches b ON b.batch_id = m.batch_id
               WHERE b.status='open'"""
        ).fetchone()["c"]
        # sealed with a pending/quarantine fate: cannot change, surfaced for audit
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
            "unresolved_variances": unresolved,
            "resolved": resolved,
            "auto_matched": auto_matched,
            "sealed_unresolved": sealed_unresolved,
            "fate_buckets": open_buckets,
            "sealed_fate_buckets": buckets.get("sealed", {}),
            "recent_batches": batches,
        }
    finally:
        conn.close()
