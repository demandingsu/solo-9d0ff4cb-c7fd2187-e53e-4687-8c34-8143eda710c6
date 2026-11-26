from __future__ import annotations

import os
from pathlib import Path


def database_path() -> Path:
    """Return the SQLite database path, reading DB_PATH on every call."""
    raw = os.getenv("DB_PATH", "data/sample_packing.db")
    if raw.startswith("sqlite:///"):
        raw = raw.removeprefix("sqlite:///")
    elif raw.startswith("sqlite://"):
        raw = raw.removeprefix("sqlite://")
    return Path(raw).expanduser().resolve()
