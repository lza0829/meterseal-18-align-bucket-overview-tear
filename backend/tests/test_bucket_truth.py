"""计数一致性：差额分桶正式表是唯一真源，快照字段/总览/列表都是它的投影。

四个夹具对应四类事故：
  1. 快照大于分桶（分桶空而快照非零）→ 作废该对齐批现行；
  2. 总览跟错边（库内残留涂改层缓存）→ 总览只认分桶和，缓存被清除；
  3. 处置后只改列表 → 处置必须四分叉同动，库内不得残留第二套数；
  4. 并发对齐双快照 → 同账期任意时刻至多一个 open 批，无半截行。
"""
from __future__ import annotations

import json
import threading

from app.db import connect
from app.services import reconcile_service
from tests.conftest import sql


def align(client, period="2026-10"):
    r = client.post("/api/align", json={"period_id": period})
    assert r.status_code == 200, r.text
    return r.json()


def variances(client, batch_id):
    return client.get(f"/api/variances?batch_id={batch_id}").json()


def by_meter(rows):
    return {r["meter_id"]: r for r in rows}


def batch_row(db, batch_id):
    return sql(db, "SELECT * FROM reconcile_batches WHERE batch_id=?", (batch_id,))[0]


def paint_keys(db):
    return sql(
        db,
        "SELECT key FROM settings WHERE key='align_pending_summary' OR key LIKE 'batch_paint:%'",
    )


# ---------------------------------------------------------------- 夹具 1：快照大于分桶


def test_ghost_batch_empty_buckets_nonzero_snapshot_is_voided(client, db):
    """分桶空而快照非零：该批口径互斥，必须作废现行，不得重算成 0 蒙混。"""
    res = align(client)
    assert res["variance_count"] == 2
    # 模拟涂改层式半截：快照声称 12 条差额，分桶行被清空
    db.execute(
        "UPDATE reconcile_batches SET variance_count=12 WHERE batch_id=?",
        (res["batch_id"],),
    )
    db.execute("DELETE FROM variances WHERE batch_id=?", (res["batch_id"],))

    ov = client.get("/api/overview").json()  # 读路径触发自愈
    assert ov["unresolved_variances"] == 0

    row = batch_row(db, res["batch_id"])
    assert row["status"] == "void"

    # 作废批不可封印、其（已不存在的）差额无从处置
    r = client.post("/api/seal", json={"period_id": "2026-10", "batch_id": res["batch_id"]})
    assert r.status_code == 409

    # 重跑对齐：新批成为唯一现行，旧批保持 void，不得复活
    res2 = align(client)
    assert res2["batch_id"] != res["batch_id"]
    assert batch_row(db, res["batch_id"])["status"] == "void"
    assert batch_row(db, res2["batch_id"])["status"] == "open"
    opens = sql(
        db,
        "SELECT COUNT(*) AS c FROM reconcile_batches WHERE period_id='2026-10' AND status='open'",
    )[0]["c"]
    assert opens == 1


def test_drifted_snapshot_fields_are_recomputed_from_buckets(client, db):
    """分桶非空但快照字段被改大：按分桶重算并落库回写，总览与列表同步跟。"""
    res = align(client)  # matched 4, variance 2
    db.execute(
        "UPDATE reconcile_batches SET matched_count=9, variance_count=7 WHERE batch_id=?",
        (res["batch_id"],),
    )

    ov = client.get("/api/overview").json()
    assert ov["unresolved_variances"] == 2
    assert ov["auto_matched"] == 4

    row = batch_row(db, res["batch_id"])
    assert row["status"] == "open"
    assert row["matched_count"] == 4
    assert row["variance_count"] == 2
    snap = json.loads(row["snapshot"])
    assert snap["matched_count"] == 4
    assert snap["variance_count"] == 2

    # 批列表与总览读到的就是回写后的投影，不是第三套数
    b = client.get("/api/batches?period_id=2026-10").json()[0]
    assert b["matched_count"] == 4
    assert b["variance_count"] == 2


# ---------------------------------------------------------------- 夹具 2：总览跟错边


def test_overview_ignores_and_purges_legacy_paint_cache(client, db):
    """库内残留涂改层缓存（未决 7）时，总览仍只认分桶和；对齐事务把缓存清掉。"""
    res = align(client)
    # 对齐本身不得再写任何涂改键
    assert paint_keys(db) == []
    # 手工埋入旧版本遗留的第二套数
    db.execute(
        "INSERT INTO settings(key,value) VALUES('align_pending_summary', ?)",
        (json.dumps({"batch_id": res["batch_id"], "pending": 7, "matched": 0, "resolved": 0}),),
    )
    db.execute(
        "INSERT INTO settings(key,value) VALUES(?, ?)",
        (f"batch_paint:{res['batch_id']}", json.dumps({"pending": 7, "matched": 0, "resolved": 0})),
    )

    ov = client.get("/api/overview").json()
    assert ov["unresolved_variances"] == 2  # 分桶和，不是缓存里的 7
    assert "snapshot_pending" not in ov  # 不再暴露第二套口径
    b = client.get("/api/batches?period_id=2026-10").json()[0]
    assert b["variance_count"] == 2
    assert "snapshot_pending" not in b

    # 重跑对齐：旧批过期、遗留缓存随事务清除，总览只跟现行批分桶
    res2 = align(client)
    assert paint_keys(db) == []
    ov = client.get("/api/overview").json()
    live = variances(client, res2["batch_id"])
    assert ov["unresolved_variances"] == len(live) == 2


# ---------------------------------------------------------------- 夹具 3：处置后四分叉不得残留


def test_fate_moves_bucket_snapshot_overview_and_list_together(client, db):
    res = align(client)
    var = by_meter(variances(client, res["batch_id"]))
    vid = var["M-W-102"]["variance_id"]
    r = client.post(f"/api/variances/{vid}/fate", json={"fate": "accept_short"})
    assert r.status_code == 200, r.text

    # 链一：分桶正式表
    row = sql(db, "SELECT fate FROM variances WHERE variance_id=?", (vid,))[0]
    assert row["fate"] == "accept_short"
    # 链二：批快照字段仍是分桶投影（处置不改生成量）
    b = batch_row(db, res["batch_id"])
    assert (b["matched_count"], b["variance_count"]) == (4, 2)
    # 链三：总览未决/已决/自动合拢与分桶同源同步
    ov = client.get("/api/overview").json()
    assert ov["unresolved_variances"] == 1
    assert ov["resolved"] == 1
    assert ov["auto_matched"] == 4
    assert ov["fate_buckets"] == {"pending": 1, "accept_short": 1}
    # 链四：差额列表过滤读同一分桶
    v = by_meter(variances(client, res["batch_id"]))
    assert v["M-W-102"]["fate"] == "accept_short"
    assert v["M-E-201"]["fate"] == "pending"
    # 库内不得残留任何第二套计数缓存
    assert paint_keys(db) == []

    # 处置到清零：未决归零，封印预检读到的未决与现行口径一致
    vid2 = v["M-E-201"]["variance_id"]
    assert client.post(f"/api/variances/{vid2}/fate", json={"fate": "accept_over"}).status_code == 200
    ov = client.get("/api/overview").json()
    assert ov["unresolved_variances"] == 0
    assert ov["resolved"] == 2
    r = client.post("/api/seal", json={"period_id": "2026-10", "batch_id": res["batch_id"]})
    assert r.status_code == 200, r.text
    assert r.json()["pending_left"] == 0


# ---------------------------------------------------------------- 夹具 4：并发对齐不得双快照现行


def test_concurrent_aligns_leave_exactly_one_current_batch(client):
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            for _ in range(10):
                reconcile_service.run_align("2026-10")
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors

    conn = connect()
    try:
        opens = conn.execute(
            "SELECT COUNT(*) AS c FROM reconcile_batches WHERE period_id='2026-10' AND status='open'"
        ).fetchone()["c"]
        assert opens == 1  # 双快照现行即事故
        # 每个批（含已过期）的快照字段都等于其分桶投影：无半截行
        for b in conn.execute("SELECT * FROM reconcile_batches").fetchall():
            vc = conn.execute(
                "SELECT COUNT(*) AS c FROM variances WHERE batch_id=?", (b["batch_id"],)
            ).fetchone()["c"]
            mc = conn.execute(
                "SELECT COUNT(*) AS c FROM match_audit WHERE batch_id=?", (b["batch_id"],)
            ).fetchone()["c"]
            assert b["variance_count"] == vc, b["batch_id"]
            assert b["matched_count"] == mc, b["batch_id"]
        live = conn.execute(
            """SELECT COUNT(*) AS c FROM variances v
               JOIN reconcile_batches b ON b.batch_id = v.batch_id
               WHERE b.status='open' AND v.fate IN ('pending','quarantine')"""
        ).fetchone()["c"]
    finally:
        conn.close()

    ov = client.get("/api/overview").json()
    assert ov["unresolved_variances"] == live


def test_fate_racing_realign_never_leaves_residual_fork(client, db):
    """处置与重对齐抢跑：要么打在仍 open 的批上，要么 409；事后四分叉不得残留。"""
    res = align(client)
    ids = [v["variance_id"] for v in variances(client, res["batch_id"])]
    outcomes: list[str] = []
    errors: list[BaseException] = []

    def disposer() -> None:
        for vid in ids * 5:
            try:
                reconcile_service.set_fate(vid, "quarantine")
                outcomes.append("ok")
            except Exception as exc:
                if getattr(exc, "status_code", None) == 409:
                    outcomes.append("blocked")
                else:
                    errors.append(exc)

    def aligner() -> None:
        try:
            for _ in range(10):
                reconcile_service.run_align("2026-10")
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=disposer), threading.Thread(target=aligner)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors
    assert outcomes  # 每次尝试只有 ok / 409 两种合法结局
    # 终态：恰好一个 open 批，其快照字段等于分桶投影，总览与分桶和一致
    opens = sql(
        db,
        "SELECT * FROM reconcile_batches WHERE period_id='2026-10' AND status='open'",
    )
    assert len(opens) == 1
    live_batch = opens[0]
    vc = sql(db, "SELECT COUNT(*) AS c FROM variances WHERE batch_id=?", (live_batch["batch_id"],))[0]["c"]
    assert live_batch["variance_count"] == vc
    ov = client.get("/api/overview").json()
    pending = sql(
        db,
        """SELECT COUNT(*) AS c FROM variances v
           JOIN reconcile_batches b ON b.batch_id=v.batch_id
           WHERE b.status='open' AND v.fate IN ('pending','quarantine')""",
    )[0]["c"]
    assert ov["unresolved_variances"] == pending
    assert paint_keys(db) == []


# ---------------------------------------------------------------- 旧库就地升级


def test_startup_heals_legacy_paint_and_drift(client, db):
    """带涂改缓存与漂移计数的旧库：seed_if_empty 启动即清缓存、按分桶回写、作废 ghost。"""
    from app.seed import seed_if_empty

    res = align(client)
    # 漂移：现行批计数被涂改；另一个 open 批分桶被清空但快照非零（ghost）
    db.execute(
        "UPDATE reconcile_batches SET matched_count=11, variance_count=9 WHERE batch_id=?",
        (res["batch_id"],),
    )
    db.execute(
        """INSERT INTO reconcile_batches(
               batch_id,period_id,status,matched_count,variance_count,fingerprint,created_at,
               tol_mode,rel_tol,rel_eps,snapshot)
           VALUES ('B-ghost','2026-09','open',3,5,'fp','2026-09-30T00:00:00','rel',0.05,1e-09,'{}')"""
    )
    db.execute(
        "INSERT INTO settings(key,value) VALUES('align_pending_summary', '{\"pending\": 7}')"
    )
    db.execute(
        "INSERT INTO settings(key,value) VALUES(?, '{\"pending\": 7}')",
        (f"batch_paint:{res['batch_id']}",),
    )

    seed_if_empty()  # 等价于服务重启

    assert paint_keys(db) == []
    row = batch_row(db, res["batch_id"])
    assert (row["matched_count"], row["variance_count"]) == (4, 2)
    assert row["status"] == "open"
    assert batch_row(db, "B-ghost")["status"] == "void"
    ov = client.get("/api/overview").json()
    assert ov["unresolved_variances"] == 2
