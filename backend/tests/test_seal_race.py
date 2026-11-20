"""封印闸：并发封印与写入不得出现「检查通过后又落库」。"""
from __future__ import annotations

import threading
import uuid

from app.db import connect
from app.services import reconcile_service

ROUNDS = 25


def _new_period(period_id: str) -> None:
    conn = connect()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO periods(period_id,status) VALUES (?, 'open')", (period_id,)
        )
        for qty, doc in ((10.0, "EST-A"), (14.0, "EST-B")):
            conn.execute(
                """INSERT OR IGNORE INTO estimates(estimate_id,meter_id,period_id,qty,doc_no,loaded_at)
                   VALUES (?,?,?,?,?, '2026-11-01T00:00:00')""",
                (f"E-{period_id}-{doc}", "M-W-101", period_id, qty, doc),
            )
        conn.execute(
            """INSERT OR IGNORE INTO actuals(actual_id,meter_id,period_id,qty,occurred_at,reader_code,doc_no,loaded_at)
               VALUES (?,?,?,?, '2026-11-28T00:00:00', 'R01', ?, '2026-11-28T00:00:00')""",
            (f"A-{period_id}", "M-W-101", period_id, 14.0, "ACT-A"),
        )
        conn.commit()
    finally:
        conn.close()


def test_seal_blocks_concurrent_align(client):
    period = "2026-11"
    _new_period(period)

    start = threading.Barrier(2)
    results: dict[str, str] = {}
    errors: list[BaseException] = []

    def aligner() -> None:
        try:
            start.wait()
            for _ in range(ROUNDS):
                try:
                    reconcile_service.run_align(period)
                    results["align"] = "ok"
                except Exception as exc:  # 409 once sealed
                    if getattr(exc, "status_code", None) == 409:
                        results["align"] = "blocked"
                    else:
                        raise
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    def sealer() -> None:
        try:
            start.wait()
            for _ in range(ROUNDS):
                try:
                    reconcile_service.seal_period(period)
                    results["seal"] = "ok"
                    return
                except Exception as exc:
                    if getattr(exc, "status_code", None) in (400, 409):
                        continue
                    raise
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=aligner), threading.Thread(target=sealer)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors
    assert results.get("seal") == "ok"

    conn = connect()
    try:
        status = conn.execute(
            "SELECT status FROM periods WHERE period_id=?", (period,)
        ).fetchone()["status"]
        assert status == "sealed"

        # No batch may have been created after the period was sealed.
        sealed_at = conn.execute(
            "SELECT sealed_at FROM periods WHERE period_id=?", (period,)
        ).fetchone()["sealed_at"]
        late = conn.execute(
            """SELECT COUNT(*) AS c FROM reconcile_batches
               WHERE period_id=? AND status='open'""",
            (period,),
        ).fetchone()["c"]
        assert late == 0

        # Every variance row still points at the sealed batch/ruler; nothing half-written.
        bad = conn.execute(
            """SELECT COUNT(*) AS c FROM variances v
               JOIN reconcile_batches b ON b.batch_id = v.batch_id
               WHERE v.period_id=? AND (v.tol_mode<>b.tol_mode OR v.rel_tol<>b.rel_tol)""",
            (period,),
        ).fetchone()["c"]
        assert bad == 0
        assert sealed_at
    finally:
        conn.close()
