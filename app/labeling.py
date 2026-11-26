"""Box-label imposition on registered A4 label sheets.

Domain rules implemented here:

* A sheet is a fixed 3-column x 8-row grid numbered left-to-right, top-to-bottom.
  Registration records which of the 24 cells are physically available.
* A layout reserves a set of currently free cells and box codes. It is
  all-or-nothing: if the sheet has too few free cells, or any requested box
  code is invalid / already in use, nothing is written.
* Placements start as ``reserved`` and keep both their cell and box occupied
  until they are reported.
* Reporting ``printed`` keeps the cell and box occupied forever.
  ``blank`` releases both the cell and the box.
  ``damaged`` keeps the cell occupied but releases the box for a reprint.
* Re-reporting the same result for a cell returns the first result unchanged.
  Reporting a different result for the same layout/cell rejects the whole
  report and writes nothing.
"""

from __future__ import annotations

import html
import sqlite3
import uuid
from typing import Any

from .constants import (
    LABEL_ACTIVE_BOX_STATUSES,
    LABEL_ACTIVE_CELL_STATUSES,
    LABEL_SHEET_TOTAL_CELLS,
)
from .database import utc_now
from .errors import ConflictError, NotFoundError, PlanStateError, UnprocessableError
from .schemas import (
    LabelImposeRequest,
    LabelReportRequest,
    LabelSheetRegisterRequest,
)
from .service import expire_stale_plan_if_needed


def _in_clause(values: list[Any] | tuple[Any, ...]) -> str:
    return ",".join("?" for _ in values)


def _begin_immediate(conn: sqlite3.Connection) -> None:
    """Start a write transaction before reading the free cells.

    The check-then-insert sequence (pick free cells, verify box codes, then
    insert) must not interleave with another imposition, otherwise two layouts
    could claim the same cell or box code. ``BEGIN IMMEDIATE`` takes SQLite's
    write lock up front so concurrent impositions serialize; the request-scoped
    connection commits or rolls back the whole unit.
    """

    if conn.in_transaction:  # pragma: no cover - defensive
        return
    conn.execute("BEGIN IMMEDIATE")


def _cell_row(cell_no: int, columns: int) -> int:
    return (cell_no - 1) // columns + 1


def _cell_column(cell_no: int, columns: int) -> int:
    return (cell_no - 1) % columns + 1


# ---------------------------------------------------------------------------
# Sheets
# ---------------------------------------------------------------------------


def _get_sheet_row(conn: sqlite3.Connection, sheet_code: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM label_sheets WHERE sheet_code = ?", (sheet_code,)
    ).fetchone()
    if row is None:
        raise NotFoundError(
            f"Label sheet {sheet_code!r} was not found.",
            code="LABEL_SHEET_NOT_FOUND",
            details={"sheet_code": sheet_code},
        )
    return row


def _sheet_cell_rows(conn: sqlite3.Connection, sheet_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM label_sheet_cells WHERE sheet_id = ? ORDER BY cell_no",
        (sheet_id,),
    ).fetchall()


def _active_placements_by_cell(
    conn: sqlite3.Connection, sheet_id: int
) -> dict[int, sqlite3.Row]:
    placeholders = _in_clause(LABEL_ACTIVE_CELL_STATUSES)
    rows = conn.execute(
        f"""
        SELECT p.*, l.layout_no
        FROM label_placements p
        JOIN label_layouts l ON l.id = p.layout_id
        WHERE p.sheet_id = ? AND p.status IN ({placeholders})
        ORDER BY p.cell_no
        """,
        (sheet_id, *LABEL_ACTIVE_CELL_STATUSES),
    ).fetchall()
    return {row["cell_no"]: row for row in rows}


def _serialize_sheet(conn: sqlite3.Connection, sheet_row: sqlite3.Row) -> dict[str, Any]:
    columns = sheet_row["columns"]
    rows = sheet_row["rows"]
    cell_rows = _sheet_cell_rows(conn, sheet_row["id"])
    active = _active_placements_by_cell(conn, sheet_row["id"])

    cells: list[dict[str, Any]] = []
    free_cells: list[int] = []
    occupied_cells: list[int] = []
    unavailable_cells: list[int] = []
    available_cells: list[int] = []

    for cell_row in cell_rows:
        cell_no = cell_row["cell_no"]
        entry: dict[str, Any] = {
            "cell_no": cell_no,
            "row": _cell_row(cell_no, columns),
            "column": _cell_column(cell_no, columns),
            "layout_no": None,
            "box_code": None,
            "placement_status": None,
        }
        if not cell_row["is_available"]:
            entry["state"] = "unavailable"
            unavailable_cells.append(cell_no)
        elif cell_no in active:
            active_row = active[cell_no]
            entry["state"] = "occupied"
            entry["layout_no"] = active_row["layout_no"]
            entry["box_code"] = active_row["box_code"]
            entry["placement_status"] = active_row["status"]
            occupied_cells.append(cell_no)
        else:
            entry["state"] = "free"
            free_cells.append(cell_no)
        if cell_row["is_available"]:
            available_cells.append(cell_no)
        cells.append(entry)

    return {
        "id": sheet_row["id"],
        "sheet_code": sheet_row["sheet_code"],
        "rows": rows,
        "columns": columns,
        "note": sheet_row["note"],
        "created_at": sheet_row["created_at"],
        "total_cells": rows * columns,
        "available_cells": available_cells,
        "free_cells": free_cells,
        "occupied_cells": occupied_cells,
        "unavailable_cells": unavailable_cells,
        "cells": cells,
    }


def register_sheet(
    conn: sqlite3.Connection, payload: LabelSheetRegisterRequest
) -> tuple[dict[str, Any], bool]:
    """Register a sheet by code. Identical re-registration is idempotent."""

    _begin_immediate(conn)
    available = (
        sorted(payload.available_cells)
        if payload.available_cells is not None
        else list(range(1, LABEL_SHEET_TOTAL_CELLS + 1))
    )
    existing = conn.execute(
        "SELECT * FROM label_sheets WHERE sheet_code = ?", (payload.sheet_code,)
    ).fetchone()
    if existing is not None:
        existing_available = [
            row["cell_no"]
            for row in _sheet_cell_rows(conn, existing["id"])
            if row["is_available"]
        ]
        same = (
            existing["rows"] == payload.rows
            and existing["columns"] == payload.columns
            and existing_available == available
        )
        if not same:
            raise ConflictError(
                f"Label sheet {payload.sheet_code!r} is already registered with different geometry or available cells.",
                code="LABEL_SHEET_EXISTS",
                details={
                    "sheet_code": payload.sheet_code,
                    "existing_sheet_id": existing["id"],
                    "existing_available_cells": existing_available,
                    "requested_available_cells": available,
                },
            )
        return _serialize_sheet(conn, existing), False

    now = utc_now()
    try:
        cursor = conn.execute(
            """
            INSERT INTO label_sheets (sheet_code, rows, columns, note, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (payload.sheet_code, payload.rows, payload.columns, payload.note, now),
        )
        sheet_id = cursor.lastrowid
        available_set = set(available)
        for cell_no in range(1, LABEL_SHEET_TOTAL_CELLS + 1):
            conn.execute(
                """
                INSERT INTO label_sheet_cells (sheet_id, cell_no, row_no, col_no, is_available)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    sheet_id,
                    cell_no,
                    _cell_row(cell_no, payload.columns),
                    _cell_column(cell_no, payload.columns),
                    1 if cell_no in available_set else 0,
                ),
            )
    except sqlite3.IntegrityError as exc:  # concurrent registration of same code
        raise ConflictError(
            f"Label sheet {payload.sheet_code!r} was registered concurrently.",
            code="LABEL_SHEET_EXISTS",
            details={"sheet_code": payload.sheet_code, "reason": str(exc)},
        ) from exc

    row = conn.execute("SELECT * FROM label_sheets WHERE id = ?", (sheet_id,)).fetchone()
    return _serialize_sheet(conn, row), True


def list_sheets(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM label_sheets ORDER BY id").fetchall()
    return [_serialize_sheet(conn, row) for row in rows]


def get_sheet(conn: sqlite3.Connection, sheet_code: str) -> dict[str, Any]:
    return _serialize_sheet(conn, _get_sheet_row(conn, sheet_code))


# ---------------------------------------------------------------------------
# Layouts
# ---------------------------------------------------------------------------


def _get_layout_row(conn: sqlite3.Connection, layout_no: str) -> sqlite3.Row:
    row = conn.execute(
        """
        SELECT l.*, s.rows, s.columns
        FROM label_layouts l
        JOIN label_sheets s ON s.id = l.sheet_id
        WHERE l.layout_no = ?
        """,
        (layout_no,),
    ).fetchone()
    if row is None:
        raise NotFoundError(
            f"Label layout {layout_no!r} was not found.",
            code="LABEL_LAYOUT_NOT_FOUND",
            details={"layout_no": layout_no},
        )
    return row


def _plan_container_map(conn: sqlite3.Connection, plan_id: int) -> dict[str, sqlite3.Row]:
    rows = conn.execute(
        "SELECT id, container_code, batch_id, batch_code FROM containers WHERE plan_id = ?",
        (plan_id,),
    ).fetchall()
    return {row["container_code"]: row for row in rows}


def _ensure_plan_confirmed(conn: sqlite3.Connection, plan_id: int) -> None:
    # A batch edit after confirmation must not allow printing stale boxes.
    expire_stale_plan_if_needed(conn, plan_id)
    row = conn.execute(
        "SELECT id, status, expired_at, expiration_reason FROM packing_plans WHERE id = ?",
        (plan_id,),
    ).fetchone()
    if row is None:
        raise NotFoundError(f"Plan {plan_id} was not found.", code="PLAN_NOT_FOUND")
    if row["status"] == "expired":
        raise PlanStateError(
            "Plan is expired; its box labels cannot be imposed.",
            code="PLAN_EXPIRED",
            details={
                "plan_id": plan_id,
                "expired_at": row["expired_at"],
                "expiration_reason": row["expiration_reason"],
            },
        )
    if row["status"] not in ("confirmed", "outbound"):
        raise PlanStateError(
            "Plan must be confirmed before its box labels can be imposed.",
            code="PLAN_NOT_CONFIRMED",
            details={"plan_id": plan_id, "status": row["status"]},
        )


def _prior_report_history(
    conn: sqlite3.Connection, box_codes: list[str]
) -> dict[str, dict[str, Any]]:
    """Summarise past reports for box codes being imposed again.

    ``blank`` and ``damaged`` release the box code, so a later layout may
    reserve the same code. Flagging it lets the warehouse tell a first print
    apart from a retry and avoid double-counting stock or silently reprinting.
    """

    if not box_codes:
        return {}
    placeholders = _in_clause(box_codes)
    rows = conn.execute(
        f"""
        SELECT box_code,
               SUM(CASE WHEN status = 'printed' THEN 1 ELSE 0 END) AS printed_count,
               COUNT(*) AS report_count
        FROM label_placements
        WHERE box_code IN ({placeholders})
          AND status IN ('printed', 'blank', 'damaged')
        GROUP BY box_code
        """,
        box_codes,
    ).fetchall()
    return {
        row["box_code"]: {
            "printed_before": row["printed_count"] or 0,
            "previous_reports": row["report_count"],
        }
        for row in rows
    }


def _placement_to_dict(row: sqlite3.Row, columns: int) -> dict[str, Any]:
    return {
        "cell_no": row["cell_no"],
        "row": _cell_row(row["cell_no"], columns),
        "column": _cell_column(row["cell_no"], columns),
        "plan_id": row["plan_id"],
        "batch_id": row["batch_id"],
        "batch_code": row["batch_code"],
        "container_id": row["container_id"],
        "box_code": row["box_code"],
        "status": row["status"],
        "reported_at": row["reported_at"],
        "reprinted": bool(row["reprinted"]) if "reprinted" in row.keys() else False,
    }


def _render_printable_text(
    layout_no: str,
    sheet_code: str,
    rows: int,
    columns: int,
    page: list[list[dict[str, Any] | None]],
) -> str:
    lines = [
        f"版面号: {layout_no}",
        f"纸张: {sheet_code} ({columns} 列 x {rows} 行)",
    ]
    for row_index in range(rows):
        rendered: list[str] = []
        for column_index in range(columns):
            cell_no = row_index * columns + column_index + 1
            placement = page[row_index][column_index]
            if placement is None or placement["status"] == "blank":
                rendered.append(f"[{cell_no:02d} {'空白':<10}]")
            else:
                rendered.append(f"[{cell_no:02d} {placement['box_code'][:10]:<10}]")
        lines.append(" ".join(rendered))
    return "\n".join(lines)


def _serialize_layout(conn: sqlite3.Connection, layout_row: sqlite3.Row) -> dict[str, Any]:
    layout_id = layout_row["id"]
    rows = layout_row["rows"]
    columns = layout_row["columns"]
    placement_rows = conn.execute(
        "SELECT * FROM label_placements WHERE layout_id = ? ORDER BY cell_no",
        (layout_id,),
    ).fetchall()

    placements = [_placement_to_dict(row, columns) for row in placement_rows]
    page: list[list[dict[str, Any] | None]] = [
        [None for _ in range(columns)] for _ in range(rows)
    ]
    for placement in placements:
        page[placement["row"] - 1][placement["column"] - 1] = placement

    return {
        "id": layout_id,
        "layout_no": layout_row["layout_no"],
        "sheet_id": layout_row["sheet_id"],
        "sheet_code": layout_row["sheet_code"],
        "rows": rows,
        "columns": columns,
        "note": layout_row["note"],
        "created_at": layout_row["created_at"],
        "placement_count": len(placements),
        "placements": placements,
        "page": page,
        "printable_page": _render_printable_text(
            layout_row["layout_no"], layout_row["sheet_code"], rows, columns, page
        ),
    }


def get_layout(conn: sqlite3.Connection, layout_no: str) -> dict[str, Any]:
    return _serialize_layout(conn, _get_layout_row(conn, layout_no))


def list_layouts(conn: sqlite3.Connection, *, sheet_code: str | None = None) -> list[dict[str, Any]]:
    if sheet_code is not None:
        rows = conn.execute(
            "SELECT id FROM label_layouts WHERE sheet_code = ? ORDER BY id DESC",
            (sheet_code,),
        ).fetchall()
    else:
        rows = conn.execute("SELECT id FROM label_layouts ORDER BY id DESC").fetchall()
    layouts: list[dict[str, Any]] = []
    for row in rows:
        layout_row = _get_layout_row_by_id(conn, row["id"])
        layouts.append(_serialize_layout(conn, layout_row))
    return layouts


def _get_layout_row_by_id(conn: sqlite3.Connection, layout_id: int) -> sqlite3.Row:
    row = conn.execute(
        """
        SELECT l.*, s.rows, s.columns
        FROM label_layouts l
        JOIN label_sheets s ON s.id = l.sheet_id
        WHERE l.id = ?
        """,
        (layout_id,),
    ).fetchone()
    if row is None:  # pragma: no cover - defensive
        raise NotFoundError(
            f"Label layout id {layout_id} was not found.",
            code="LABEL_LAYOUT_NOT_FOUND",
            details={"layout_id": layout_id},
        )
    return row


def _already_active_box_codes(
    conn: sqlite3.Connection, box_codes: list[str]
) -> list[dict[str, Any]]:
    if not box_codes:
        return []
    status_placeholders = _in_clause(LABEL_ACTIVE_BOX_STATUSES)
    code_placeholders = _in_clause(box_codes)
    rows = conn.execute(
        f"""
        SELECT p.box_code, p.cell_no, p.status, l.layout_no
        FROM label_placements p
        JOIN label_layouts l ON l.id = p.layout_id
        WHERE p.status IN ({status_placeholders})
          AND p.box_code IN ({code_placeholders})
        ORDER BY p.id
        """,
        (*LABEL_ACTIVE_BOX_STATUSES, *box_codes),
    ).fetchall()
    return [
        {
            "box_code": row["box_code"],
            "layout_no": row["layout_no"],
            "cell_no": row["cell_no"],
            "status": row["status"],
        }
        for row in rows
    ]


def impose_layout(conn: sqlite3.Connection, payload: LabelImposeRequest) -> dict[str, Any]:
    _begin_immediate(conn)
    sheet_row = _get_sheet_row(conn, payload.sheet_code)
    sheet_view = _serialize_sheet(conn, sheet_row)
    free_cells: list[int] = list(sheet_view["free_cells"])

    # Flatten items in request order. plan_id is validated per requested box.
    requested: list[tuple[int, str]] = []
    seen_codes: set[str] = set()
    for item in payload.items:
        for box_code in item.box_codes:
            if box_code in seen_codes:
                raise UnprocessableError(
                    "A box code may be imposed only once per layout.",
                    code="LABEL_DUPLICATE_BOX_CODE",
                    details={"box_code": box_code},
                )
            seen_codes.add(box_code)
            requested.append((item.plan_id, box_code))

    # Validate every source plan and every box code before writing anything.
    plan_containers: dict[int, dict[str, sqlite3.Row]] = {}
    for plan_id in dict.fromkeys(plan_id for plan_id, _ in requested):
        _ensure_plan_confirmed(conn, plan_id)
        plan_containers[plan_id] = _plan_container_map(conn, plan_id)

    missing: list[dict[str, Any]] = []
    for plan_id, box_code in requested:
        if box_code not in plan_containers[plan_id]:
            missing.append({"plan_id": plan_id, "box_code": box_code})
    if missing:
        raise UnprocessableError(
            "One or more box codes do not belong to the given plan.",
            code="BOX_CODE_NOT_IN_PLAN",
            details={"items": missing},
        )

    active = _already_active_box_codes(
        conn, [box_code for _, box_code in requested]
    )
    if active:
        raise ConflictError(
            "One or more box codes are already occupied by another label layout.",
            code="LABEL_BOX_CODE_IN_USE",
            details={"box_codes": active},
        )

    if len(requested) > len(free_cells):
        raise UnprocessableError(
            "Not enough free cells on the label sheet; the whole layout was rejected.",
            code="LABEL_SHEET_CAPACITY_EXCEEDED",
            details={
                "sheet_code": payload.sheet_code,
                "requested_box_count": len(requested),
                "free_cell_count": len(free_cells),
                "free_cells": free_cells,
            },
        )

    now = utc_now()
    assigned_cells = free_cells[: len(requested)]
    report_history = _prior_report_history(conn, [box_code for _, box_code in requested])
    try:
        cursor = conn.execute(
            """
            INSERT INTO label_layouts (layout_no, sheet_id, sheet_code, note, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                f"tmp-{uuid.uuid4().hex}",
                sheet_row["id"],
                sheet_row["sheet_code"],
                payload.note,
                now,
            ),
        )
        layout_id = cursor.lastrowid
        layout_no = f"LBL-{layout_id:06d}"
        conn.execute(
            "UPDATE label_layouts SET layout_no = ? WHERE id = ?",
            (layout_no, layout_id),
        )

        for cell_no, (plan_id, box_code) in zip(assigned_cells, requested):
            container = plan_containers[plan_id][box_code]
            prior = report_history.get(box_code)
            # A code can be on the sheet again only after blank/damaged released
            # it; treat any earlier report as a reprint.
            reprinted = 1 if prior and prior["previous_reports"] > 0 else 0
            conn.execute(
                """
                INSERT INTO label_placements (
                    layout_id, sheet_id, cell_no, plan_id, batch_id, batch_code,
                    container_id, box_code, status, reprinted, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'reserved', ?, ?)
                """,
                (
                    layout_id,
                    sheet_row["id"],
                    cell_no,
                    plan_id,
                    container["batch_id"],
                    container["batch_code"],
                    container["id"],
                    box_code,
                    reprinted,
                    now,
                ),
            )
    except sqlite3.IntegrityError as exc:
        # Safety net for concurrent imposition; the request is rolled back by
        # the request-scoped connection dependency.
        raise ConflictError(
            "Label layout conflicted with a concurrent layout; nothing was written.",
            code="LABEL_LAYOUT_CONFLICT",
            details={"sheet_code": payload.sheet_code, "reason": str(exc)},
        ) from exc

    return get_layout(conn, layout_no)


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def report_layout(
    conn: sqlite3.Connection, layout_no: str, payload: LabelReportRequest
) -> dict[str, Any]:
    _begin_immediate(conn)
    layout_row = _get_layout_row(conn, layout_no)
    layout_id = layout_row["id"]
    placement_rows = conn.execute(
        "SELECT * FROM label_placements WHERE layout_id = ? ORDER BY cell_no",
        (layout_id,),
    ).fetchall()
    by_cell = {row["cell_no"]: row for row in placement_rows}

    # Validate the whole report before applying anything: one bad item rejects
    # the entire batch.
    pending: list[tuple[sqlite3.Row, str, bool]] = []  # placement, result, idempotent
    conflicts: list[dict[str, Any]] = []
    mismatches: list[dict[str, Any]] = []

    for item in payload.items:
        placement = by_cell.get(item.cell_no)
        if placement is None:
            raise UnprocessableError(
                "A reported cell does not belong to this label layout.",
                code="LABEL_CELL_NOT_IN_LAYOUT",
                details={"layout_no": layout_no, "cell_no": item.cell_no},
            )
        if placement["box_code"] != item.box_code:
            mismatches.append(
                {
                    "cell_no": item.cell_no,
                    "layout_box_code": placement["box_code"],
                    "reported_box_code": item.box_code,
                }
            )
            continue
        current = placement["status"]
        if current == "reserved":
            pending.append((placement, item.result, False))
        elif current == item.result:
            # Exact re-report: echo the first result and change nothing.
            pending.append((placement, current, True))
        else:
            conflicts.append(
                {
                    "cell_no": item.cell_no,
                    "box_code": item.box_code,
                    "first_result": current,
                    "reported_result": item.result,
                }
            )

    if mismatches:
        raise UnprocessableError(
            "A reported box code does not match the layout; the whole report was rejected.",
            code="LABEL_BOX_CODE_MISMATCH",
            details={"layout_no": layout_no, "items": mismatches},
        )
    if conflicts:
        raise ConflictError(
            "A cell was already reported with a different result; the whole report was rejected.",
            code="LABEL_REPORT_CONFLICT",
            details={"layout_no": layout_no, "conflicts": conflicts},
        )

    now = utc_now()
    results: list[dict[str, Any]] = []
    for placement, result, idempotent in pending:
        if not idempotent:
            conn.execute(
                "UPDATE label_placements SET status = ?, reported_at = ? WHERE id = ?",
                (result, now, placement["id"]),
            )
        results.append(
            {
                "cell_no": placement["cell_no"],
                "box_code": placement["box_code"],
                "result": result,
                "first_result": result,
                "status": result,
                "idempotent": idempotent,
                "message": (
                    "重复回报与原结果一致，返回首次结果，未做改动。"
                    if idempotent
                    else None
                ),
            }
        )

    layout = get_layout(conn, layout_no)
    return {
        "layout_no": layout_no,
        "sheet_code": layout_row["sheet_code"],
        "reported_at": now,
        "results": results,
        "layout": layout,
        "sheet": get_sheet(conn, layout_row["sheet_code"]),
    }


# ---------------------------------------------------------------------------
# Printable HTML view
# ---------------------------------------------------------------------------


def render_layout_html(conn: sqlite3.Connection, layout_no: str) -> str:
    layout = get_layout(conn, layout_no)
    page = layout["page"]
    cells_html: list[str] = []
    for row_index in range(layout["rows"]):
        for column_index in range(layout["columns"]):
            cell_no = row_index * layout["columns"] + column_index + 1
            placement = page[row_index][column_index]
            if placement is None or placement["status"] == "blank":
                cells_html.append(
                    f'<div class="cell blank"><span class="no">{cell_no}</span></div>'
                )
            else:
                status = html.escape(placement["status"])
                cells_html.append(
                    '<div class="cell {status}">'
                    '<span class="no">{cell_no}</span>'
                    '<span class="box">{box}</span>'
                    '<span class="meta">{batch} · {plan}</span>'
                    "</div>".format(
                        status=status,
                        cell_no=cell_no,
                        box=html.escape(placement["box_code"]),
                        batch=html.escape(placement["batch_code"]),
                        plan=placement["plan_id"],
                    )
                )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>版面 {html.escape(layout["layout_no"])}</title>
<style>
  @page {{ size: A4; margin: 8mm; }}
  body {{ font-family: system-ui, sans-serif; margin: 0; padding: 8mm; }}
  h1 {{ font-size: 16pt; margin: 0 0 2mm; }}
  .sub {{ font-size: 10pt; color: #444; margin-bottom: 4mm; }}
  .grid {{
    display: grid;
    grid-template-columns: repeat({layout["columns"]}, 1fr);
    grid-auto-rows: 32mm;
    gap: 2mm;
  }}
  .cell {{
    border: 1px dashed #999;
    border-radius: 2mm;
    padding: 2mm;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    overflow: hidden;
  }}
  .cell.blank {{ border-style: dotted; color: #aaa; }}
  .cell.printed {{ border-color: #2e7d32; }}
  .cell.damaged {{ border-color: #c62828; background: #fff5f5; }}
  .no {{ font-size: 8pt; color: #888; }}
  .box {{ font-size: 12pt; font-weight: 700; word-break: break-all; }}
  .meta {{ font-size: 8pt; color: #555; word-break: break-all; }}
</style>
</head>
<body>
  <h1>箱签版面 {html.escape(layout["layout_no"])}</h1>
  <div class="sub">纸张 {html.escape(layout["sheet_code"])} ·
    {layout["columns"]} 列 x {layout["rows"]} 行 ·
    {layout["placement_count"]} 个箱签 · 未分配格保持空白</div>
  <div class="grid">
    {"".join(cells_html)}
  </div>
</body>
</html>"""
