from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import datetime, timezone

from .config import database_path

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_code TEXT NOT NULL UNIQUE,
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision > 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS packing_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    status TEXT NOT NULL CHECK (
        status IN ('generated', 'confirmed', 'outbound', 'expired')
    ),
    container_capacity INTEGER NOT NULL CHECK (container_capacity > 0),
    min_load INTEGER NOT NULL CHECK (min_load >= 0),
    max_load INTEGER NOT NULL CHECK (max_load > 0),
    strategy TEXT NOT NULL DEFAULT 'balanced_min_containers',
    note TEXT,
    created_at TEXT NOT NULL,
    confirmed_at TEXT,
    outbound_at TEXT,
    expired_at TEXT,
    expiration_reason TEXT
);

CREATE TABLE IF NOT EXISTS plan_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id INTEGER NOT NULL REFERENCES packing_plans(id) ON DELETE CASCADE,
    batch_id INTEGER NOT NULL REFERENCES batches(id) ON DELETE RESTRICT,
    batch_code TEXT NOT NULL,
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    revision INTEGER NOT NULL CHECK (revision > 0),
    source_start INTEGER NOT NULL DEFAULT 0,
    source_end INTEGER NOT NULL,
    intake_start INTEGER NOT NULL,
    intake_end INTEGER NOT NULL,
    UNIQUE (plan_id, batch_id)
);

CREATE TABLE IF NOT EXISTS containers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id INTEGER NOT NULL REFERENCES packing_plans(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    container_code TEXT NOT NULL UNIQUE,
    batch_id INTEGER NOT NULL REFERENCES batches(id) ON DELETE RESTRICT,
    batch_code TEXT NOT NULL,
    batch_seq INTEGER NOT NULL,
    load_quantity INTEGER NOT NULL CHECK (load_quantity > 0),
    source_start INTEGER NOT NULL,
    source_end INTEGER NOT NULL,
    UNIQUE (plan_id, seq),
    UNIQUE (plan_id, batch_id, batch_seq)
);

CREATE TABLE IF NOT EXISTS allocations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id INTEGER NOT NULL REFERENCES packing_plans(id) ON DELETE CASCADE,
    container_id INTEGER NOT NULL REFERENCES containers(id) ON DELETE CASCADE,
    batch_id INTEGER NOT NULL REFERENCES batches(id) ON DELETE RESTRICT,
    batch_code TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    source_start INTEGER NOT NULL,
    source_end INTEGER NOT NULL,
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    UNIQUE (container_id, ordinal)
);

CREATE TABLE IF NOT EXISTS outbound_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id INTEGER NOT NULL UNIQUE REFERENCES packing_plans(id) ON DELETE RESTRICT,
    reference TEXT,
    operator TEXT,
    note TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_plan_batches_batch ON plan_batches(batch_id);
CREATE INDEX IF NOT EXISTS idx_containers_plan ON containers(plan_id);
CREATE INDEX IF NOT EXISTS idx_allocations_plan ON allocations(plan_id);
CREATE INDEX IF NOT EXISTS idx_allocations_container ON allocations(container_id);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def connect() -> sqlite3.Connection:
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # FastAPI may advance a synchronous yielding dependency and execute its
    # endpoint on different AnyIO worker threads. Each request still owns one
    # connection and uses it serially, so relaxing SQLite's creator-thread check
    # is safe here and prevents intermittent "objects created in a thread" 500s.
    conn = sqlite3.connect(path, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    conn = connect()
    try:
        conn.executescript(SCHEMA_SQL)
        conn.commit()
    finally:
        conn.close()


def get_db() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
