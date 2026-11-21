from __future__ import annotations

import json
import sqlite3

from app.db import connect
from app.services import consistency

SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS meters (
  meter_id TEXT PRIMARY KEY,
  building TEXT NOT NULL,
  unit_label TEXT NOT NULL,
  meter_type TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active'
);
CREATE TABLE IF NOT EXISTS periods (
  period_id TEXT PRIMARY KEY,
  status TEXT NOT NULL DEFAULT 'open',
  sealed_at TEXT,
  batch_no TEXT
);
CREATE TABLE IF NOT EXISTS estimates (
  estimate_id TEXT PRIMARY KEY,
  meter_id TEXT NOT NULL,
  period_id TEXT NOT NULL,
  qty REAL NOT NULL,
  doc_no TEXT NOT NULL,
  loaded_at TEXT NOT NULL,
  UNIQUE(meter_id, period_id, doc_no)
);
CREATE TABLE IF NOT EXISTS actuals (
  actual_id TEXT PRIMARY KEY,
  meter_id TEXT NOT NULL,
  period_id TEXT NOT NULL,
  qty REAL NOT NULL,
  occurred_at TEXT NOT NULL,
  reader_code TEXT NOT NULL,
  doc_no TEXT NOT NULL,
  loaded_at TEXT NOT NULL,
  UNIQUE(meter_id, period_id, doc_no)
);
CREATE TABLE IF NOT EXISTS reconcile_batches (
  batch_id TEXT PRIMARY KEY,
  period_id TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open',
  matched_count INTEGER NOT NULL DEFAULT 0,
  variance_count INTEGER NOT NULL DEFAULT 0,
  fingerprint TEXT,
  created_at TEXT NOT NULL,
  tol_mode TEXT NOT NULL DEFAULT 'rel',
  rel_tol REAL NOT NULL DEFAULT 0.05,
  rel_eps REAL NOT NULL DEFAULT 0.000000001,
  snapshot TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS variances (
  variance_id TEXT PRIMARY KEY,
  batch_id TEXT NOT NULL,
  meter_id TEXT NOT NULL,
  period_id TEXT NOT NULL,
  estimate_qty REAL,
  actual_qty REAL,
  delta REAL NOT NULL,
  fate TEXT NOT NULL DEFAULT 'pending',
  ratio REAL,
  reason TEXT NOT NULL DEFAULT 'over_tolerance',
  estimate_id TEXT,
  actual_id TEXT,
  tol_mode TEXT NOT NULL DEFAULT 'rel',
  rel_tol REAL NOT NULL DEFAULT 0.05
);
CREATE TABLE IF NOT EXISTS match_audit (
  audit_id TEXT PRIMARY KEY,
  batch_id TEXT NOT NULL,
  period_id TEXT NOT NULL,
  meter_id TEXT NOT NULL,
  estimate_id TEXT,
  actual_id TEXT,
  estimate_qty REAL NOT NULL,
  actual_qty REAL NOT NULL,
  delta REAL NOT NULL,
  ratio REAL NOT NULL,
  outcome TEXT NOT NULL,
  reason TEXT NOT NULL,
  tol_mode TEXT NOT NULL,
  rel_tol REAL NOT NULL,
  rel_eps REAL NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_variances_batch ON variances(batch_id, fate);
CREATE INDEX IF NOT EXISTS idx_match_audit_batch ON match_audit(batch_id);
"""

# Columns added in v2; used to migrate a pre-existing v1 database in place.
_ADDED_COLUMNS = {
    "reconcile_batches": [
        ("tol_mode", "TEXT NOT NULL DEFAULT 'rel'"),
        ("rel_tol", "REAL NOT NULL DEFAULT 0.05"),
        ("rel_eps", "REAL NOT NULL DEFAULT 0.000000001"),
        ("snapshot", "TEXT NOT NULL DEFAULT '{}'"),
    ],
    "variances": [
        ("ratio", "REAL"),
        ("reason", "TEXT NOT NULL DEFAULT 'over_tolerance'"),
        ("estimate_id", "TEXT"),
        ("actual_id", "TEXT"),
        ("tol_mode", "TEXT NOT NULL DEFAULT 'rel'"),
        ("rel_tol", "REAL NOT NULL DEFAULT 0.05"),
    ],
}


def _migrate(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version >= SCHEMA_VERSION:
        return
    for table, columns in _ADDED_COLUMNS.items():
        existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if not existing:
            continue  # fresh database: SCHEMA above already has them
        for name, ddl in columns:
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_variances_batch ON variances(batch_id, fate)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_match_audit_batch ON match_audit(batch_id)")
    conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")


def seed_if_empty() -> None:
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        _migrate(conn)
        # 旧库可能带着涂改层的第二套计数（settings 缓存、被回写的批快照字段）
        # 升级：启动即清缓存、按分桶正式表重算 open 批快照字段，ghost 批作废。
        consistency.purge_paint_cache(conn)
        consistency.heal_open_batches(conn)
        n = conn.execute("SELECT COUNT(*) AS c FROM meters").fetchone()["c"]
        if n:
            conn.commit()
            return
        meters = [
            ("M-W-101", "A栋", "101", "water"),
            ("M-W-102", "A栋", "102", "water"),
            ("M-E-101", "A栋", "101", "electric"),
            ("M-W-201", "B栋", "201", "water"),
            ("M-E-201", "B栋", "201", "electric"),
            ("M-E-202", "B栋", "202", "electric"),
        ]
        conn.executemany(
            "INSERT INTO meters(meter_id,building,unit_label,meter_type,status) VALUES (?,?,?,?, 'active')",
            meters,
        )
        conn.execute("INSERT INTO periods(period_id,status) VALUES ('2026-10','open')")
        conn.execute("INSERT INTO periods(period_id,status) VALUES ('2026-09','open')")
        # estimates for 2026-10
        est = [
            ("E1", "M-W-101", "2026-10", 12.0, "EST-101", "2026-10-01T08:00:00"),
            ("E2", "M-W-102", "2026-10", 15.0, "EST-102", "2026-10-01T08:00:00"),
            ("E3", "M-E-101", "2026-10", 220.0, "EST-E101", "2026-10-01T08:00:00"),
            ("E4", "M-W-201", "2026-10", 18.0, "EST-201", "2026-10-01T08:00:00"),
            ("E5", "M-E-201", "2026-10", 310.0, "EST-E201", "2026-10-01T08:00:00"),
            ("E6", "M-E-202", "2026-10", 280.0, "EST-E202", "2026-10-01T08:00:00"),
        ]
        conn.executemany(
            "INSERT INTO estimates(estimate_id,meter_id,period_id,qty,doc_no,loaded_at) VALUES (?,?,?,?,?,?)",
            est,
        )
        # actuals: most match; two variance
        act = [
            ("A1", "M-W-101", "2026-10", 12.0, "2026-10-28T10:00:00", "R01", "ACT-101", "2026-10-28T10:05:00"),
            ("A2", "M-W-102", "2026-10", 14.0, "2026-10-28T10:10:00", "R01", "ACT-102", "2026-10-28T10:15:00"),  # 6.7% short
            ("A3", "M-E-101", "2026-10", 220.0, "2026-10-28T11:00:00", "R02", "ACT-E101", "2026-10-28T11:05:00"),
            ("A4", "M-W-201", "2026-10", 18.0, "2026-10-28T11:20:00", "R02", "ACT-201", "2026-10-28T11:25:00"),
            ("A5", "M-E-201", "2026-10", 330.0, "2026-10-28T12:00:00", "R03", "ACT-E201", "2026-10-28T12:05:00"),  # 6.5% over
            ("A6", "M-E-202", "2026-10", 280.0, "2026-10-28T12:20:00", "R03", "ACT-E202", "2026-10-28T12:25:00"),
        ]
        conn.executemany(
            "INSERT INTO actuals(actual_id,meter_id,period_id,qty,occurred_at,reader_code,doc_no,loaded_at) VALUES (?,?,?,?,?,?,?,?)",
            act,
        )
        conn.executemany(
            "INSERT INTO settings(key,value) VALUES (?,?)",
            [
                ("current_period", "2026-10"),
                ("product_title", "抄表簿"),
                ("tol_mode", "rel"),
                ("rel_tol", "0.05"),
                ("rel_eps", "1e-09"),
            ],
        )
        conn.commit()
    finally:
        conn.close()
