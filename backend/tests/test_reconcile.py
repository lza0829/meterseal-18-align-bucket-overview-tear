"""对齐、快照、封印闸、命运、重对齐的集成口径。"""
from __future__ import annotations

from tests.conftest import put_ruler, sql


def align(client, period="2026-10"):
    r = client.post("/api/align", json={"period_id": period})
    assert r.status_code == 200, r.text
    return r.json()


def matches(client, batch_id):
    return client.get(f"/api/matches?batch_id={batch_id}").json()


def variances(client, batch_id):
    return client.get(f"/api/variances?batch_id={batch_id}").json()


def by_meter(rows):
    return {r["meter_id"]: r for r in rows}


def test_align_snapshot_and_audit(client, db):
    res = align(client)
    assert res["ruler"] == {"tol_mode": "rel", "rel_tol": 0.05, "rel_eps": 1e-09}
    # seed: exact matches on 4 meters, over-tolerance on 2
    assert res["matched_count"] == 4
    assert res["variance_count"] == 2

    batch = sql(db, "SELECT * FROM reconcile_batches WHERE batch_id=?", (res["batch_id"],))[0]
    assert batch["tol_mode"] == "rel"
    assert batch["rel_tol"] == 0.05
    assert batch["rel_eps"] == 1e-09

    # every matched meter has an immutable audit row carrying the ruler
    audit = matches(client, res["batch_id"])
    assert len(audit) == 4
    assert {a["meter_id"] for a in audit} == {"M-W-101", "M-E-101", "M-W-201", "M-E-202"}
    for a in audit:
        assert a["tol_mode"] == "rel"
        assert a["rel_tol"] == 0.05
        assert a["ratio"] <= 0.05

    # over-tolerance never auto-closes, never auto-accepts
    var = by_meter(variances(client, res["batch_id"]))
    assert set(var) == {"M-W-102", "M-E-201"}
    for v in var.values():
        assert v["fate"] == "pending"
        assert v["reason"] == "over_tolerance"
        assert v["ratio"] > 0.05


def test_auto_matched_is_decided_by_ruler_not_exact_equality(client):
    """仅差一点点但在容差内，应进 matched 并留审计，而非差额。"""
    put_ruler(client, rel_tol=0.10)
    res = align(client)
    var = by_meter(variances(client, res["batch_id"]))
    audit = by_meter(matches(client, res["batch_id"]))
    # 15 -> 14 is 6.7%, within 10%: matched now
    assert "M-W-102" not in var
    assert audit["M-W-102"]["outcome"] == "auto_matched"
    assert audit["M-W-102"]["reason"] == "within_tolerance"
    # 310 -> 330 is 6.45%, within 10%: matched now
    assert "M-E-201" not in var
    assert audit["M-E-201"]["outcome"] == "auto_matched"


def test_relative_ruler_does_not_let_small_base_large_error_pass(client):
    """小基数大偏差必须 pending，不允许相对尺子放过。"""
    put_ruler(client, rel_tol=0.05)
    # estimate 12 -> actual 20 (~66.7%): must be a variance
    client.post(
        "/api/estimates",
        json={"meter_id": "M-W-101", "period_id": "2026-09", "qty": 12.0, "doc_no": "EST-101-S"},
    )
    client.post(
        "/api/actuals",
        json={
            "meter_id": "M-W-101",
            "period_id": "2026-09",
            "qty": 20.0,
            "occurred_at": "2026-09-28T09:00:00",
            "reader_code": "R09",
            "doc_no": "ACT-101-S",
        },
    )
    res = align(client, "2026-09")
    var = by_meter(variances(client, res["batch_id"]))
    assert "M-W-101" in var
    assert var["M-W-101"]["fate"] == "pending"
    assert var["M-W-101"]["reason"] == "over_tolerance"


def test_relative_ruler_accepts_large_base_tiny_error(client):
    """大基数微差：5% 尺子下自动合拢，而不是因绝对差值小大起大落。"""
    put_ruler(client, rel_tol=0.05)
    client.post(
        "/api/estimates",
        json={"meter_id": "M-E-202", "period_id": "2026-09", "qty": 280.0, "doc_no": "EST-E202-S"},
    )
    client.post(
        "/api/actuals",
        json={
            "meter_id": "M-E-202",
            "period_id": "2026-09",
            "qty": 285.0,
            "occurred_at": "2026-09-28T09:00:00",
            "reader_code": "R09",
            "doc_no": "ACT-E202-S",
        },
    )
    res = align(client, "2026-09")
    var = by_meter(variances(client, res["batch_id"]))
    audit = by_meter(matches(client, res["batch_id"]))
    assert "M-E-202" not in var
    assert audit["M-E-202"]["outcome"] == "auto_matched"


def test_zero_base_is_never_auto_matched(client):
    """0 估抄 + 0.4 实抄：相对误差率没有有限分母，必须 pending。"""
    client.post(
        "/api/estimates",
        json={"meter_id": "M-E-101", "period_id": "2026-09", "qty": 0.0, "doc_no": "EST-E101-Z"},
    )
    client.post(
        "/api/actuals",
        json={
            "meter_id": "M-E-101",
            "period_id": "2026-09",
            "qty": 0.4,
            "occurred_at": "2026-09-29T09:00:00",
            "reader_code": "R09",
            "doc_no": "ACT-E101-Z",
        },
    )
    res = align(client, "2026-09")
    var = by_meter(variances(client, res["batch_id"]))
    assert "M-E-101" in var
    assert var["M-E-101"]["fate"] == "pending"
    assert var["M-E-101"]["reason"] == "over_tolerance"


def test_sealed_batch_rejects_fate_change(client):
    res = align(client)
    r = client.post("/api/seal", json={"period_id": "2026-10", "batch_id": res["batch_id"]})
    assert r.status_code == 200, r.text

    vid = variances(client, res["batch_id"])[0]["variance_id"]
    r = client.post(f"/api/variances/{vid}/fate", json={"fate": "accept_over"})
    assert r.status_code == 409


def test_sealed_period_rejects_reading_writes(client):
    align(client)
    r = client.post("/api/seal", json={"period_id": "2026-10"})
    assert r.status_code == 200, r.text
    for path, body in (
        (
            "/api/estimates",
            {"meter_id": "M-W-101", "period_id": "2026-10", "qty": 1.0, "doc_no": "X"},
        ),
        (
            "/api/actuals",
            {
                "meter_id": "M-W-101",
                "period_id": "2026-10",
                "qty": 1.0,
                "occurred_at": "2026-10-30T00:00:00",
                "doc_no": "Y",
            },
        ),
    ):
        assert client.post(path, json=body).status_code == 409


def test_tolerance_change_invalidates_open_batch(client, db):
    """改尺子后，旧批不得再封印；必须按新尺子重对齐。"""
    old = align(client)
    r = client.put("/api/settings/rel_tol", json={"value": "0.20"})
    assert r.status_code == 200, r.text
    assert r.json()["stale_batches"] >= 1

    row = sql(db, "SELECT status FROM reconcile_batches WHERE batch_id=?", (old["batch_id"],))[0]
    assert row["status"] == "stale"

    # cannot seal the stale batch
    r = client.post("/api/seal", json={"period_id": "2026-10", "batch_id": old["batch_id"]})
    assert r.status_code == 409

    # re-align under the new ruler: everything within 20%
    new = align(client)
    assert new["variance_count"] == 0
    assert new["matched_count"] == 6
    audit = matches(client, new["batch_id"])
    assert all(a["rel_tol"] == 0.20 for a in audit)

    r = client.post("/api/seal", json={"period_id": "2026-10", "batch_id": new["batch_id"]})
    assert r.status_code == 200, r.text


def test_setting_tol_mode_cannot_switch_architecture(client):
    r = client.put("/api/settings/tol_mode", json={"value": "abs"})
    assert r.status_code == 409


def test_rel_tol_validation(client):
    assert client.put("/api/settings/rel_tol", json={"value": "0"}).status_code == 400
    assert client.put("/api/settings/rel_tol", json={"value": "1.5"}).status_code == 400
    assert client.put("/api/settings/rel_tol", json={"value": "abc"}).status_code == 400
    assert client.put("/api/settings/rel_eps", json={"value": "0"}).status_code == 400
    # cannot create per-meter-type tolerance
    assert client.put("/api/settings/tol_water", json={"value": "0.1"}).status_code == 400


def test_realign_expires_previous_open_batch(client, db):
    first = align(client)
    second = align(client)
    assert first["batch_id"] != second["batch_id"]
    st = sql(db, "SELECT status FROM reconcile_batches WHERE batch_id=?", (first["batch_id"],))[0]
    assert st["status"] == "expired"
    # stale batches' variances cannot be dispositioned
    vid = variances(client, first["batch_id"])[0]["variance_id"]
    assert client.post(f"/api/variances/{vid}/fate", json={"fate": "accept_over"}).status_code == 409


def test_fate_direction_guard(client):
    res = align(client)
    var = by_meter(variances(client, res["batch_id"]))
    short = var["M-W-102"]["variance_id"]  # delta -1
    over = var["M-E-201"]["variance_id"]  # delta +20
    assert client.post(f"/api/variances/{short}/fate", json={"fate": "accept_over"}).status_code == 409
    assert client.post(f"/api/variances/{over}/fate", json={"fate": "accept_short"}).status_code == 409
    assert client.post(f"/api/variances/{short}/fate", json={"fate": "accept_short"}).status_code == 200
    assert client.post(f"/api/variances/{over}/fate", json={"fate": "accept_over"}).status_code == 200


def test_overview_buckets_match_headline(client):
    res = align(client)
    var = by_meter(variances(client, res["batch_id"]))
    assert client.post(f"/api/variances/{var['M-W-102']['variance_id']}/fate", json={"fate": "accept_short"}).status_code == 200

    ov = client.get("/api/overview").json()
    assert ov["unresolved_variances"] == (
        ov["fate_buckets"].get("pending", 0) + ov["fate_buckets"].get("quarantine", 0)
    )
    assert ov["unresolved_variances"] == 1
    assert ov["ruler"]["tol_mode"] == "rel"


def test_overview_ignores_stale_batches(client):
    old = align(client)
    client.put("/api/settings/rel_tol", json={"value": "0.1"})
    new = align(client)
    ov = client.get("/api/overview").json()
    # only the new batch's variances count, not the superseded/stale ones
    live = variances(client, new["batch_id"])
    assert ov["unresolved_variances"] == len(live)
    assert len(variances(client, old["batch_id"])) >= 2


def test_overview_separates_actionable_from_sealed_leftovers(client):
    """封印后仍有 pending 时，它们不再计入可处置未决，而是单列审计。"""
    align(client)
    r = client.post("/api/seal", json={"period_id": "2026-10"})
    assert r.status_code == 200, r.text
    assert r.json()["pending_left"] == 2

    ov = client.get("/api/overview").json()
    assert ov["unresolved_variances"] == 0
    assert ov["sealed_unresolved"] == 2
    assert ov["fate_buckets"] == {}
    assert ov["sealed_fate_buckets"] == {"pending": 2}
