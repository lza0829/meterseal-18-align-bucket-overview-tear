"""计数一致性守卫：差额分桶正式表是全库唯一真源。

全局架构（唯一）：``variances`` / ``match_audit`` 的正式行是真源，
``reconcile_batches.matched_count`` / ``variance_count`` / ``snapshot`` 与总览计数
都只是分桶的投影。任何漂移一律按分桶重算并 **落库回写**（不是只刷总览）；
分桶空而快照非零的批属于半截生成，不可信，作废其现行（``status='void'``）。

涂改层（已拆除的 pending_summary）曾把第二套数字写进 settings 缓存，
这里负责在启动与对齐事务里把遗留键清掉，保证库内不再残留第四套数。
"""
from __future__ import annotations

import json
import sqlite3

from app.db import transaction

# 涂改层遗留键（仅用于清理，任何代码不得再读它们取数）
LEGACY_CACHE_KEY = "align_pending_summary"
LEGACY_BATCH_PREFIX = "batch_paint:"


def bucket_counts(conn: sqlite3.Connection, batch_id: str) -> dict:
    """从正式表数出该批的分桶实况：自动合拢数 + 各命运分桶行数。"""
    matched = conn.execute(
        "SELECT COUNT(*) AS c FROM match_audit WHERE batch_id=?", (batch_id,)
    ).fetchone()["c"]
    by_fate = {
        r["fate"]: r["c"]
        for r in conn.execute(
            "SELECT fate, COUNT(*) AS c FROM variances WHERE batch_id=? GROUP BY fate",
            (batch_id,),
        ).fetchall()
    }
    return {"matched": matched, "variances": sum(by_fate.values()), "by_fate": by_fate}


def recompute_batch(conn: sqlite3.Connection, batch_id: str) -> dict | None:
    """按分桶重算批快照字段并回写；分桶空而快照非零则作废该批。

    必须在写事务内调用。返回动作：clean / recomputed / void。
    """
    row = conn.execute(
        "SELECT * FROM reconcile_batches WHERE batch_id=?", (batch_id,)
    ).fetchone()
    if row is None:
        return None
    live = bucket_counts(conn, batch_id)

    if live["variances"] == 0 and row["variance_count"] > 0:
        # 快照声称有差额而分桶一行没有：生成半截，口径互斥，作废现行。
        conn.execute(
            "UPDATE reconcile_batches SET status='void' WHERE batch_id=?", (batch_id,)
        )
        return {
            "batch_id": batch_id,
            "action": "void",
            "matched_count": live["matched"],
            "variance_count": 0,
        }

    snapshot = json.loads(row["snapshot"] or "{}")
    drift = (
        row["matched_count"] != live["matched"]
        or row["variance_count"] != live["variances"]
        or snapshot.get("matched_count") != live["matched"]
        or snapshot.get("variance_count") != live["variances"]
    )
    if not drift:
        return {
            "batch_id": batch_id,
            "action": "clean",
            "matched_count": live["matched"],
            "variance_count": live["variances"],
        }

    snapshot["matched_count"] = live["matched"]
    snapshot["variance_count"] = live["variances"]
    conn.execute(
        """UPDATE reconcile_batches
           SET matched_count=?, variance_count=?, snapshot=? WHERE batch_id=?""",
        (
            live["matched"],
            live["variances"],
            json.dumps(snapshot, ensure_ascii=False, sort_keys=True),
            batch_id,
        ),
    )
    return {
        "batch_id": batch_id,
        "action": "recomputed",
        "matched_count": live["matched"],
        "variance_count": live["variances"],
    }


def heal_open_batches(conn: sqlite3.Connection) -> list[dict]:
    """对全部 open 批按分桶重算回写 / 作废 ghost。必须在写事务内调用。"""
    actions = []
    rows = conn.execute(
        "SELECT batch_id FROM reconcile_batches WHERE status='open'"
    ).fetchall()
    for row in rows:
        result = recompute_batch(conn, row["batch_id"])
        if result and result["action"] != "clean":
            actions.append(result)
    return actions


def find_drift(conn: sqlite3.Connection) -> bool:
    """只读检测：是否存在 open 批的快照字段与其分桶行数不一致。"""
    row = conn.execute(
        """SELECT COUNT(*) AS c FROM reconcile_batches b
           WHERE b.status='open' AND (
                 b.matched_count <> (SELECT COUNT(*) FROM match_audit m
                                     WHERE m.batch_id = b.batch_id)
              OR b.variance_count <> (SELECT COUNT(*) FROM variances v
                                      WHERE v.batch_id = b.batch_id)
           )"""
    ).fetchone()
    return row["c"] > 0


def heal_if_drifted(conn: sqlite3.Connection) -> list[dict]:
    """读路径自愈：先只读检测，发现漂移才开写事务按分桶回写，再让调用方重读。

    无漂移时不抢写锁，避免总览/列表与并发对齐争写。
    """
    if not find_drift(conn):
        return []
    with transaction(conn):
        return heal_open_batches(conn)


def purge_paint_cache(conn: sqlite3.Connection) -> int:
    """清除涂改层遗留的第二套计数缓存（旧库就地升级 / 对齐事务内幂等调用）。"""
    cur = conn.execute(
        "DELETE FROM settings WHERE key=? OR key LIKE ?",
        (LEGACY_CACHE_KEY, LEGACY_BATCH_PREFIX + "%"),
    )
    return cur.rowcount
