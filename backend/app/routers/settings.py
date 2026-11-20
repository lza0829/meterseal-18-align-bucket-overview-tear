from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.db import connect, transaction
from app.services.settings_service import get_ruler, put_setting

router = APIRouter(tags=["settings"])


class SettingIn(BaseModel):
    value: str


@router.get("/settings")
def list_settings():
    conn = connect()
    try:
        settings = {r["key"]: r["value"] for r in conn.execute("SELECT * FROM settings").fetchall()}
        return {"settings": settings, "ruler": get_ruler(conn)}
    finally:
        conn.close()


@router.put("/settings/{key}")
def update_setting(key: str, body: SettingIn):
    conn = connect()
    try:
        with transaction(conn):
            return put_setting(conn, key, body.value)
    finally:
        conn.close()
