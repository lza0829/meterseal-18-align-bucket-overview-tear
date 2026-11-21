from __future__ import annotations

import hashlib
import json
import uuid
from collections import defaultdict
from datetime import datetime, timezone

from fastapi import HTTPException

from app.db import connect, transaction
from app.services import consistency
from app.services import tolerance as tol
from app.services.settings_service import get_ruler


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _short(prefix: str) -> str:
    return prefix + uuid.uuid4().hex[:10]


def _assert_writable(conn, period_id: str) -> None:
    row = conn.execute(
        "SELECT status FROM periods WHERE period_id=?", (period_id,)
    ).fetchone()
    if not row:
        raise HTTPException(404, f"账期不存在: {period_id}")
    if row["status"] == "sealed":
        raise HTTPException(409, f"账期已封印，禁止写入: {period_id}")


def _load_by_meter(conn, table: str, period_id: str) -> dict[str, list]:
    rows = conn.execute(
        f"SELECT * FROM {table} WHERE period_id=? ORDER BY meter_id, loaded_at, doc_no",
        (period_id,),
    ).fetchall()
    grouped: dict[str, list] = defaultdict(list)
    for row in rows:
        grouped[row["meter_id"]].append(row)
    return grouped


def run_align(period_id: str) -> dict:
    conn = connect()
    try:
        with transaction(conn):
            _assert_writable(conn, period_id)
            # One ruler read, one transaction: classification and snapshot cannot
            # straddle a concurrent tolerance edit.
            ruler = get_ruler(conn)

            est = _load_by_meter(conn, "estimates", period_id)
            act = _load_by_meter(conn, "actuals", period_id)
            keys = sorted(set(est) | set(act))
            if not keys:
                raise HTTPException(400, "该账期无估抄/实抄可对齐")

            conn.execute(
                "UPDATE reconcile_batches SET status='expired' WHERE period_id=? AND status IN ('open','stale')",
                (period_id,),
            )

            batch_id = _short("B-")
            matched = 0
            variance_rows: list[tuple] = []
            audit_rows: list[tuple] = []
            decisions: list[str] = []

            for mid in keys:
                est_rows = est.get(mid, [])
                act_rows = act.get(mid, [])
                ambiguous = len(est_rows) > 1 or len(act_rows) > 1
                eq = est_rows[-1]["qty"] if est_rows else None
                aq = act_rows[-1]["qty"] if act_rows else None
                eid = est_rows[-1]["estimate_id"] if est_rows else None
                aid = act_rows[-1]["actual_id"] if act_rows else None

                verdict = tol.classify_rel(
                    eq, aq, ruler["rel_tol"], ruler["rel_eps"], ambiguous=ambiguous
                )

                if verdict.outcome in (tol.OUTCOME_EXACT, tol.OUTCOME_AUTO):
                    matched += 1
                    delta = float(aq) - float(eq)
                    audit_rows.append(
                        (
                            _short("MA-"),
                            batch_id,
                            period_id,
                            mid,
                            eid,
                            aid,
                            float(eq),
                            float(aq),
                            delta,
                            verdict.ratio,
                            verdict.outcome,
                            verdict.reason,
                            ruler["tol_mode"],
                            ruler["rel_tol"],
                            ruler["rel_eps"],
                            _now(),
                        )
                    )
                    decisions.append(f"{mid}:{verdict.outcome}")
                    continue

                if ambiguous:
                    delta = 0.0
                else:
                    delta = (float(aq) if aq is not None else 0.0) - (
                        float(eq) if eq is not None else 0.0
                    )
                variance_rows.append(
                    (
                        _short("V-"),
                        batch_id,
                        mid,
                        period_id,
                        None if eq is None else float(eq),
                        None if aq is None else float(aq),
                        delta,
                        "pending",
                        verdict.ratio,
                        verdict.reason,
                        eid,
                        aid,
                        ruler["tol_mode"],
                        ruler["rel_tol"],
                    )
                )
                decisions.append(f"{mid}:{verdict.reason}")

            fp_src = "|".join(
                [
                    period_id,
                    ruler["tol_mode"],
                    repr(ruler["rel_tol"]),
                    repr(ruler["rel_eps"]),
                    str(matched),
                    str(len(variance_rows)),
                    *decisions,
                ]
            )
            fingerprint = hashlib.sha256(fp_src.encode()).hexdigest()[:16]
            snapshot = json.dumps(
                {
                    "period_id": period_id,
                    "ruler": ruler,
                    "matched_count": matched,
                    "variance_count": len(variance_rows),
                    "meter_keys": keys,
                    "fingerprint": fingerprint,
                    "computed_at": _now(),
                },
                ensure_ascii=False,
                sort_keys=True,
            )

            conn.execute(
                """INSERT INTO reconcile_batches(
                       batch_id,period_id,status,matched_count,variance_count,fingerprint,created_at,
                       tol_mode,rel_tol,rel_eps,snapshot)
                   VALUES (?,?, 'open',?,?,?,?,?,?,?,?)""",
                (
                    batch_id,
                    period_id,
                    matched,
                    len(variance_rows),
                    fingerprint,
                    _now(),
                    ruler["tol_mode"],
                    ruler["rel_tol"],
                    ruler["rel_eps"],
                    snapshot,
                ),
            )
            if variance_rows:
                conn.executemany(
                    """INSERT INTO variances(
                           variance_id,batch_id,meter_id,period_id,estimate_qty,actual_qty,delta,fate,
                           ratio,reason,estimate_id,actual_id,tol_mode,rel_tol)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    variance_rows,
                )
            if audit_rows:
                conn.executemany(
                    """INSERT INTO match_audit(
                           audit_id,batch_id,period_id,meter_id,estimate_id,actual_id,
                           estimate_qty,actual_qty,delta,ratio,outcome,reason,
                           tol_mode,rel_tol,rel_eps,created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    audit_rows,
                )

            # 批快照字段是分桶的投影：同事务按刚落库的分桶行重算回写，
            # 结构上杜绝「分桶一套、快照一套」；回执数字即落库数字。
            projected = consistency.recompute_batch(conn, batch_id)
            # 涂改层遗留缓存若存在，随本次对齐事务一并清除。
            consistency.purge_paint_cache(conn)

        return {
            "batch_id": batch_id,
            "period_id": period_id,
            "matched_count": projected["matched_count"],
            "variance_count": projected["variance_count"],
            "fingerprint": fingerprint,
            "status": "open",
            "ruler": ruler,
        }
    finally:
        conn.close()


def seal_period(period_id: str, batch_id: str | None = None) -> dict:
    conn = connect()
    try:
        with transaction(conn):
            period = conn.execute(
                "SELECT * FROM periods WHERE period_id=?", (period_id,)
            ).fetchone()
            if not period:
                raise HTTPException(404, "账期不存在")
            if period["status"] == "sealed":
                raise HTTPException(409, "账期已封印")

            if batch_id:
                batch = conn.execute(
                    "SELECT * FROM reconcile_batches WHERE batch_id=?", (batch_id,)
                ).fetchone()
                if not batch or batch["period_id"] != period_id:
                    raise HTTPException(400, "对账批与账期不匹配")
            else:
                batch = conn.execute(
                    """SELECT * FROM reconcile_batches WHERE period_id=? AND status='open'
                       ORDER BY created_at DESC LIMIT 1""",
                    (period_id,),
                ).fetchone()
                if not batch:
                    stale = conn.execute(
                        "SELECT COUNT(*) AS c FROM reconcile_batches WHERE period_id=? AND status='stale'",
                        (period_id,),
                    ).fetchone()["c"]
                    if stale:
                        raise HTTPException(409, "对账批基于旧容差，请按当前尺子重新对齐后再封印")
                    raise HTTPException(400, "无 open 对账批，请先对齐")
                batch_id = batch["batch_id"]

            if batch["status"] != "open":
                raise HTTPException(409, "对账批不可用（已被作废、过期或基于旧容差）")

            # 封印预检：快照字段必须等于分桶投影（不一致就在本事务内按分桶重算
            # 落库）；分桶空而快照非零的批口径互斥，作废现行并拒绝封印。
            healed = consistency.recompute_batch(conn, batch_id)
            if healed and healed["action"] == "void":
                raise HTTPException(409, "对账批分桶与快照互斥，已作废该批，请重新对齐")

            # The sealed batch must carry the ruler that is live right now, and every
            # row must have been classified with that same ruler.
            current = get_ruler(conn)
            snapshot = json.loads(batch["snapshot"] or "{}")
            snap_ruler = snapshot.get("ruler", {})
            if (
                batch["tol_mode"] != current["tol_mode"]
                or batch["rel_tol"] != current["rel_tol"]
                or batch["rel_eps"] != current["rel_eps"]
                or snap_ruler.get("rel_tol") != current["rel_tol"]
                or snap_ruler.get("rel_eps") != current["rel_eps"]
            ):
                raise HTTPException(409, "对账批容差快照与当前尺子不一致，请重新对齐")

            mixed = conn.execute(
                """SELECT COUNT(*) AS c FROM variances
                   WHERE batch_id=?
                     AND (tol_mode<>? OR rel_tol<>?)""",
                (batch_id, current["tol_mode"], current["rel_tol"]),
            ).fetchone()["c"]
            if mixed:
                raise HTTPException(409, "对账批内存在混用旧尺子的差额，请重新对齐")

            pending = conn.execute(
                "SELECT COUNT(*) AS c FROM variances WHERE batch_id=? AND fate='pending'",
                (batch_id,),
            ).fetchone()["c"]
            sealed_at = _now()
            conn.execute(
                "UPDATE periods SET status='sealed', sealed_at=?, batch_no=? WHERE period_id=?",
                (sealed_at, batch_id, period_id),
            )
            conn.execute(
                "UPDATE reconcile_batches SET status='sealed' WHERE batch_id=?",
                (batch_id,),
            )

        return {
            "period_id": period_id,
            "batch_id": batch_id,
            "sealed_at": sealed_at,
            "pending_left": pending,
            "status": "sealed",
        }
    finally:
        conn.close()


_FATES = ("pending", "accept_short", "accept_over", "quarantine")


def set_fate(variance_id: str, fate: str) -> dict:
    if fate not in _FATES:
        raise HTTPException(400, "非法命运")
    conn = connect()
    try:
        with transaction(conn):
            row = conn.execute(
                """SELECT v.*, p.status AS period_status, b.status AS batch_status
                   FROM variances v
                   JOIN periods p ON p.period_id = v.period_id
                   JOIN reconcile_batches b ON b.batch_id = v.batch_id
                   WHERE v.variance_id=?""",
                (variance_id,),
            ).fetchone()
            if not row:
                raise HTTPException(404, "差额不存在")
            if row["period_status"] == "sealed":
                raise HTTPException(409, "账期已封印，禁止改命运")
            if row["batch_status"] != "open":
                raise HTTPException(409, "该差额所属对账批已作废或基于旧容差，禁止处置")

            if fate in ("accept_short", "accept_over"):
                if row["reason"] == tol.REASON_MULTI_DOC:
                    raise HTTPException(409, "单号重复的差额不可直接认账，请先清理单据")
                if fate == "accept_short" and row["delta"] > 0:
                    raise HTTPException(409, "差值为正，不能认短")
                if fate == "accept_over" and row["delta"] < 0:
                    raise HTTPException(409, "差值为负，不能认溢")

            conn.execute(
                "UPDATE variances SET fate=? WHERE variance_id=?", (fate, variance_id)
            )
            # 总览未决/已决是分桶的实时投影：本事务改了分桶行，总览即同事务跟随，
            # 不存在也不允许存在「只改列表」或「只刷缓存」的第二条路径。
        return {"variance_id": variance_id, "fate": fate}
    finally:
        conn.close()
