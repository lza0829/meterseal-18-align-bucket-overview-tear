from __future__ import annotations
import os
from pathlib import Path

PROJECT_NAME = "抄表簿"
ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
DB_PATH = Path(os.environ.get("METERS_DB") or (DATA_DIR / "meterseal.db"))
