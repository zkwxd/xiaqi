"""路径与运行参数。"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DECISION_TIMEOUT_MS = 10_000


def db_path() -> Path:
    override = os.environ.get("XIAQI_DB")
    if override:
        return Path(override)
    return ROOT / "data" / "xiaqi.db"
