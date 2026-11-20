from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

_TMPDIR = tempfile.mkdtemp(prefix="meterseal-test-")
os.environ["METERS_DB"] = str(Path(_TMPDIR) / "test.db")

from fastapi.testclient import TestClient  # noqa: E402

from app.db import connect  # noqa: E402
from app.main import app  # noqa: E402
from app.seed import SCHEMA, seed_if_empty  # noqa: E402


@pytest.fixture()
def client():
    """Fresh, fully seeded database for every test."""
    conn = connect()
    try:
        for table in (
            "match_audit",
            "variances",
            "reconcile_batches",
            "estimates",
            "actuals",
            "periods",
            "meters",
            "settings",
        ):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
        seed_if_empty()
    finally:
        conn.close()
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def db():
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


def sql(db, query, params=()):
    return db.execute(query, params).fetchall()


def put_ruler(client, rel_tol=None, rel_eps=None):
    if rel_tol is not None:
        r = client.put("/api/settings/rel_tol", json={"value": repr(rel_tol)})
        assert r.status_code == 200, r.text
    if rel_eps is not None:
        r = client.put("/api/settings/rel_eps", json={"value": repr(rel_eps)})
        assert r.status_code == 200, r.text
