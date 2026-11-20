"""从旧库（无快照/审计列）就地升级，旧数据不丢。"""
from __future__ import annotations

import sqlite3

from app import seed
from app.db import connect

V1_SCHEMA = """
CREATE TABLE meters (meter_id TEXT PRIMARY KEY, building TEXT NOT NULL, unit_label TEXT NOT NULL,
  meter_type TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active');
CREATE TABLE periods (period_id TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'open',
  sealed_at TEXT, batch_no TEXT);
CREATE TABLE estimates (estimate_id TEXT PRIMARY KEY, meter_id TEXT NOT NULL, period_id TEXT NOT NULL,
  qty REAL NOT NULL, doc_no TEXT NOT NULL, loaded_at TEXT NOT NULL, UNIQUE(meter_id, period_id, doc_no));
CREATE TABLE actuals (actual_id TEXT PRIMARY KEY, meter_id TEXT NOT NULL, period_id TEXT NOT NULL,
  qty REAL NOT NULL, occurred_at TEXT NOT NULL, reader_code TEXT NOT NULL, doc_no TEXT NOT NULL,
  loaded_at TEXT NOT NULL, UNIQUE(meter_id, period_id, doc_no));
CREATE TABLE reconcile_batches (batch_id TEXT PRIMARY KEY, period_id TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open', matched_count INTEGER NOT NULL DEFAULT 0,
  variance_count INTEGER NOT NULL DEFAULT 0, fingerprint TEXT, created_at TEXT NOT NULL);
CREATE TABLE variances (variance_id TEXT PRIMARY KEY, batch_id TEXT NOT NULL, meter_id TEXT NOT NULL,
  period_id TEXT NOT NULL, estimate_qty REAL, actual_qty REAL, delta REAL NOT NULL,
  fate TEXT NOT NULL DEFAULT 'pending');
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def test_v1_database_is_migrated_in_place():
    from app.config import DB_PATH

    DB_PATH.unlink(missing_ok=True)
    legacy = sqlite3.connect(DB_PATH)
    legacy.executescript(V1_SCHEMA)
    legacy.execute("INSERT INTO meters VALUES ('M-1','A栋','101','water','active')")
    legacy.execute("INSERT INTO periods(period_id,status) VALUES ('2026-08','open')")
    legacy.execute(
        """INSERT INTO reconcile_batches
           VALUES ('B-old','2026-08','sealed',0,1,'fp','2026-09-01T00:00:00')"""
    )
    legacy.execute(
        """INSERT INTO variances
           VALUES ('V-old','B-old','M-1','2026-08',10.0,12.0,2.0,'pending')"""
    )
    legacy.commit()
    legacy.close()

    seed.seed_if_empty()

    conn = connect()
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == seed.SCHEMA_VERSION
        batch = conn.execute("SELECT * FROM reconcile_batches WHERE batch_id='B-old'").fetchone()
        assert batch["tol_mode"] == "rel"
        assert batch["rel_tol"] == 0.05
        assert batch["snapshot"] == "{}"
        var = conn.execute("SELECT * FROM variances WHERE variance_id='V-old'").fetchone()
        assert var["tol_mode"] == "rel"
        assert var["reason"] == "over_tolerance"
        assert var["fate"] == "pending"
        # legacy data survived
        assert conn.execute("SELECT COUNT(*) AS c FROM variances").fetchone()["c"] == 1
        # new table exists
        assert conn.execute("SELECT COUNT(*) AS c FROM match_audit").fetchone()["c"] == 0
    finally:
        conn.close()
