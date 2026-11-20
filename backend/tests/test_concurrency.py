"""并发：改尺子与对账不得半截混用新旧尺子。"""
from __future__ import annotations

import threading

from app.db import connect, transaction
from app.services import reconcile_service
from app.services.settings_service import put_setting

TOLERANCES = [0.02, 0.05, 0.08, 0.12, 0.2]
ROUNDS = 30


def _set_tol(value: float) -> None:
    conn = connect()
    try:
        with transaction(conn):
            put_setting(conn, "rel_tol", repr(value))
    finally:
        conn.close()


def test_concurrent_align_and_tolerance_edit_never_mixes_rulers(client):
    errors: list[BaseException] = []

    def worker(idx: int) -> None:
        try:
            for _ in range(ROUNDS):
                reconcile_service.run_align("2026-10")
                _set_tol(TOLERANCES[idx % len(TOLERANCES)])
        except BaseException as exc:  # noqa: BLE001 - surface to main thread
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors

    conn = connect()
    try:
        batches = conn.execute("SELECT * FROM reconcile_batches").fetchall()
        assert batches
        for b in batches:
            rows = conn.execute(
                "SELECT tol_mode, rel_tol FROM variances WHERE batch_id=?", (b["batch_id"],)
            ).fetchall()
            # every variance row was classified with exactly the batch's ruler
            for r in rows:
                assert r["tol_mode"] == b["tol_mode"], b["batch_id"]
                assert r["rel_tol"] == b["rel_tol"], b["batch_id"]
            audit = conn.execute(
                "SELECT tol_mode, rel_tol, rel_eps FROM match_audit WHERE batch_id=?",
                (b["batch_id"],),
            ).fetchall()
            for a in audit:
                assert a["tol_mode"] == b["tol_mode"]
                assert a["rel_tol"] == b["rel_tol"]
                assert a["rel_eps"] == b["rel_eps"]

        # no row may have been split between a batch and a different batch
        orphans = conn.execute(
            """SELECT COUNT(*) AS c FROM variances v
               LEFT JOIN reconcile_batches b ON b.batch_id = v.batch_id
               WHERE b.batch_id IS NULL"""
        ).fetchone()["c"]
        assert orphans == 0
    finally:
        conn.close()
