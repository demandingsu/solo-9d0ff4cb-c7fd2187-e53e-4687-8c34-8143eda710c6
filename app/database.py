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

CREATE TABLE IF NOT EXISTS label_sheets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sheet_code TEXT NOT NULL UNIQUE,
    rows INTEGER NOT NULL CHECK (rows > 0),
    columns INTEGER NOT NULL CHECK (columns > 0),
    note TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS label_sheet_cells (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sheet_id INTEGER NOT NULL REFERENCES label_sheets(id) ON DELETE CASCADE,
    cell_no INTEGER NOT NULL CHECK (cell_no > 0),
    row_no INTEGER NOT NULL CHECK (row_no > 0),
    col_no INTEGER NOT NULL CHECK (col_no > 0),
    is_available INTEGER NOT NULL CHECK (is_available IN (0, 1)),
    UNIQUE (sheet_id, cell_no)
);

CREATE TABLE IF NOT EXISTS label_layouts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    layout_no TEXT NOT NULL UNIQUE,
    sheet_id INTEGER NOT NULL REFERENCES label_sheets(id) ON DELETE RESTRICT,
    sheet_code TEXT NOT NULL,
    note TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS label_placements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    layout_id INTEGER NOT NULL REFERENCES label_layouts(id) ON DELETE CASCADE,
    sheet_id INTEGER NOT NULL REFERENCES label_sheets(id) ON DELETE RESTRICT,
    cell_no INTEGER NOT NULL CHECK (cell_no > 0),
    plan_id INTEGER NOT NULL REFERENCES packing_plans(id) ON DELETE RESTRICT,
    batch_id INTEGER NOT NULL REFERENCES batches(id) ON DELETE RESTRICT,
    batch_code TEXT NOT NULL,
    container_id INTEGER NOT NULL REFERENCES containers(id) ON DELETE RESTRICT,
    box_code TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('reserved', 'printed', 'blank', 'damaged')
    ),
    reprinted INTEGER NOT NULL DEFAULT 0 CHECK (reprinted IN (0, 1)),
    created_at TEXT NOT NULL,
    reported_at TEXT,
    UNIQUE (layout_id, cell_no)
);

-- A physical cell is occupied by at most one layout while its result is
-- reserved, printed or damaged. A cell confirmed blank is released for reuse.
CREATE UNIQUE INDEX IF NOT EXISTS uq_label_cell_active
    ON label_placements(sheet_id, cell_no)
    WHERE status IN ('reserved', 'printed', 'damaged');

-- A box code can belong to at most one active layout. Printed and reserved
-- codes stay occupied; blank and damaged results release the code for reprint.
CREATE UNIQUE INDEX IF NOT EXISTS uq_label_box_active
    ON label_placements(box_code)
    WHERE status IN ('reserved', 'printed');

CREATE INDEX IF NOT EXISTS idx_plan_batches_batch ON plan_batches(batch_id);
CREATE INDEX IF NOT EXISTS idx_containers_plan ON containers(plan_id);
CREATE INDEX IF NOT EXISTS idx_allocations_plan ON allocations(plan_id);
CREATE INDEX IF NOT EXISTS idx_allocations_container ON allocations(container_id);
CREATE INDEX IF NOT EXISTS idx_label_layouts_sheet ON label_layouts(sheet_id);
CREATE INDEX IF NOT EXISTS idx_label_placements_layout ON label_placements(layout_id);
CREATE INDEX IF NOT EXISTS idx_label_placements_sheet ON label_placements(sheet_id);
CREATE INDEX IF NOT EXISTS idx_label_placements_plan ON label_placements(plan_id);
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
