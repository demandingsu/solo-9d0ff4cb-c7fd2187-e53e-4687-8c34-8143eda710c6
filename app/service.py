from __future__ import annotations

import sqlite3
from typing import Any

from .database import utc_now
from .errors import ConflictError, NotFoundError, PlanStateError
from .packing import build_packing, validate_no_cross_batch_allocation
from .schemas import OutboundRequest, PlanGenerateRequest


def batch_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "batch_code": row["batch_code"],
        "quantity": row["quantity"],
        "revision": row["revision"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def create_batch(conn: sqlite3.Connection, batch_code: str, quantity: int) -> dict[str, Any]:
    existing = conn.execute("SELECT * FROM batches WHERE batch_code = ?", (batch_code,)).fetchone()
    if existing is not None:
        raise ConflictError(
            f"Batch code {batch_code!r} already exists.",
            code="BATCH_CODE_EXISTS",
            details={"batch_code": batch_code, "existing_id": existing["id"]},
        )
    now = utc_now()
    cursor = conn.execute(
        """
        INSERT INTO batches (batch_code, quantity, revision, created_at, updated_at)
        VALUES (?, ?, 1, ?, ?)
        """,
        (batch_code, quantity, now, now),
    )
    return get_batch(conn, cursor.lastrowid)


def bulk_create_batches(conn: sqlite3.Connection, batches: list[Any]) -> list[dict[str, Any]]:
    codes = [item.batch_code for item in batches]
    if len(set(codes)) != len(codes):
        raise ConflictError("Duplicate batch_code in request.", code="DUPLICATE_BATCH_CODE", details={"batch_codes": codes})

    placeholders = ",".join("?" for _ in codes)
    existing = conn.execute(
        f"SELECT batch_code FROM batches WHERE batch_code IN ({placeholders})", codes
    ).fetchall()
    if existing:
        raise ConflictError(
            "One or more batch codes already exist.",
            code="BATCH_CODE_EXISTS",
            details={"batch_codes": [row["batch_code"] for row in existing]},
        )
    return [create_batch(conn, item.batch_code, item.quantity) for item in batches]


def get_batch(conn: sqlite3.Connection, batch_id: int) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM batches WHERE id = ?", (batch_id,)).fetchone()
    if row is None:
        raise NotFoundError(f"Batch {batch_id} was not found.", code="BATCH_NOT_FOUND")
    return batch_to_dict(row)


def get_batch_by_code(conn: sqlite3.Connection, batch_code: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM batches WHERE batch_code = ?", (batch_code,)).fetchone()
    if row is None:
        raise NotFoundError(f"Batch {batch_code!r} was not found.", code="BATCH_NOT_FOUND")
    return batch_to_dict(row)


def list_batches(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM batches ORDER BY id").fetchall()
    return [batch_to_dict(row) for row in rows]


def expire_plans_for_batch(
    conn: sqlite3.Connection,
    batch_id: int,
    reason: str,
    *,
    at: str | None = None,
) -> int:
    timestamp = at or utc_now()
    cursor = conn.execute(
        """
        UPDATE packing_plans
        SET status = 'expired',
            expired_at = COALESCE(expired_at, ?),
            expiration_reason = ?
        WHERE status IN ('generated', 'confirmed')
          AND id IN (SELECT plan_id FROM plan_batches WHERE batch_id = ?)
        """,
        (timestamp, reason, batch_id),
    )
    return cursor.rowcount


def update_batch(
    conn: sqlite3.Connection,
    batch_id: int,
    *,
    batch_code: str | None = None,
    quantity: int | None = None,
) -> dict[str, Any]:
    old = conn.execute("SELECT * FROM batches WHERE id = ?", (batch_id,)).fetchone()
    if old is None:
        raise NotFoundError(f"Batch {batch_id} was not found.", code="BATCH_NOT_FOUND")

    new_code = batch_code if batch_code is not None else old["batch_code"]
    new_quantity = quantity if quantity is not None else old["quantity"]

    if new_code != old["batch_code"]:
        duplicate = conn.execute(
            "SELECT id FROM batches WHERE batch_code = ? AND id <> ?",
            (new_code, batch_id),
        ).fetchone()
        if duplicate is not None:
            raise ConflictError(
                f"Batch code {new_code!r} is already in use.",
                code="BATCH_CODE_EXISTS",
                details={"batch_code": new_code, "existing_id": duplicate["id"]},
            )

    # Every explicit modification gets a new revision, including an update that
    # restores a previous quantity. Plan staleness is based on revision, not
    # merely whether the numeric quantity differs from the current row.
    new_revision = old["revision"] + 1
    now = utc_now()
    changed_fields: list[str] = []
    if new_code != old["batch_code"]:
        changed_fields.append("batch_code")
    if new_quantity != old["quantity"]:
        changed_fields.append("quantity")
    if not changed_fields:
        changed_fields.append("explicit_update")

    conn.execute(
        """
        UPDATE batches
        SET batch_code = ?, quantity = ?, revision = ?, updated_at = ?
        WHERE id = ?
        """,
        (new_code, new_quantity, new_revision, now, batch_id),
    )
    reason = (
        f"Batch {old['batch_code']} was modified ({', '.join(changed_fields)}); "
        f"new revision {new_revision}. Historical packing plans are expired."
    )
    expired_count = expire_plans_for_batch(conn, batch_id, reason, at=now)
    result = get_batch(conn, batch_id)
    result["expired_plan_count"] = expired_count
    return result


def update_batch_by_code(
    conn: sqlite3.Connection,
    batch_code: str,
    *,
    new_batch_code: str | None = None,
    quantity: int | None = None,
) -> dict[str, Any]:
    batch = get_batch_by_code(conn, batch_code)
    return update_batch(
        conn,
        batch["id"],
        batch_code=new_batch_code,
        quantity=quantity,
    )


def expire_all_stale_plans(conn: sqlite3.Connection) -> int:
    now = utc_now()
    cursor = conn.execute(
        """
        UPDATE packing_plans
        SET status = 'expired',
            expired_at = ?,
            expiration_reason = COALESCE(
                expiration_reason,
                'One or more source batch revisions changed after plan generation.'
            )
        WHERE status IN ('generated', 'confirmed')
          AND id IN (
              SELECT pb.plan_id
              FROM plan_batches pb
              JOIN batches b ON b.id = pb.batch_id
              WHERE pb.revision <> b.revision
                 OR pb.quantity <> b.quantity
                 OR pb.batch_code <> b.batch_code
          )
        """,
        (now,),
    )
    return cursor.rowcount


def expire_stale_plan_if_needed(conn: sqlite3.Connection, plan_id: int) -> bool:
    row = conn.execute("SELECT status FROM packing_plans WHERE id = ?", (plan_id,)).fetchone()
    if row is None:
        raise NotFoundError(f"Plan {plan_id} was not found.", code="PLAN_NOT_FOUND")
    if row["status"] not in ("generated", "confirmed"):
        return False

    stale = conn.execute(
        """
        SELECT pb.batch_code, pb.revision AS plan_revision, b.revision AS current_revision
        FROM plan_batches pb
        JOIN batches b ON b.id = pb.batch_id
        WHERE pb.plan_id = ?
          AND (
              pb.revision <> b.revision
              OR pb.quantity <> b.quantity
              OR pb.batch_code <> b.batch_code
          )
        LIMIT 1
        """,
        (plan_id,),
    ).fetchone()
    if stale is None:
        return False

    now = utc_now()
    conn.execute(
        """
        UPDATE packing_plans
        SET status = 'expired',
            expired_at = ?,
            expiration_reason = ?
        WHERE id = ? AND status IN ('generated', 'confirmed')
        """,
        (
            now,
            (
                f"Batch {stale['batch_code']} revision changed from "
                f"{stale['plan_revision']} to {stale['current_revision']}."
            ),
            plan_id,
        ),
    )
    return True


def _resolve_generation_batches(
    conn: sqlite3.Connection,
    payload: PlanGenerateRequest,
) -> list[dict[str, Any]]:
    if payload.batch_ids:
        ids = payload.batch_ids
        placeholders = ",".join("?" for _ in ids)
        rows = conn.execute(
            f"SELECT * FROM batches WHERE id IN ({placeholders})", ids
        ).fetchall()
        by_id = {row["id"]: row for row in rows}
        missing = [batch_id for batch_id in ids if batch_id not in by_id]
        if missing:
            raise NotFoundError(
                "One or more batches were not found.",
                code="BATCH_NOT_FOUND",
                details={"missing_batch_ids": missing},
            )
        return [batch_to_dict(by_id[batch_id]) for batch_id in ids]

    assert payload.batches is not None
    codes = [item.batch_code for item in payload.batches]
    if len(set(codes)) != len(codes):
        raise ConflictError(
            "Inline batches must not contain duplicate batch_code values.",
            code="DUPLICATE_BATCH_CODE",
            details={"batch_codes": codes},
        )

    resolved: list[dict[str, Any]] = []
    for item in payload.batches:
        row = conn.execute("SELECT * FROM batches WHERE batch_code = ?", (item.batch_code,)).fetchone()
        if row is None:
            resolved.append(create_batch(conn, item.batch_code, item.quantity))
        elif row["quantity"] != item.quantity:
            # An inline resubmission with a changed quantity is a real revision.
            resolved.append(update_batch(conn, row["id"], quantity=item.quantity))
        else:
            resolved.append(batch_to_dict(row))
    return resolved


def generate_plan(conn: sqlite3.Connection, payload: PlanGenerateRequest) -> dict[str, Any]:
    batches = _resolve_generation_batches(conn, payload)

    # Pure planning happens before inserts so infeasible input creates no plan.
    packings = build_packing(
        [(batch["batch_code"], batch["quantity"]) for batch in batches],
        container_capacity=payload.container_capacity,
        min_load=payload.min_load,
        max_load=payload.max_load,
    )
    by_code = {batch["batch_code"]: batch for batch in batches}
    now = utc_now()

    cursor = conn.execute(
        """
        INSERT INTO packing_plans (
            status, container_capacity, min_load, max_load, strategy, note, created_at
        ) VALUES ('generated', ?, ?, ?, 'balanced_min_containers', ?, ?)
        """,
        (
            payload.container_capacity,
            payload.min_load,
            payload.max_load,
            payload.note,
            now,
        ),
    )
    plan_id = cursor.lastrowid

    intake_cursor = 0
    for packing in packings:
        batch = by_code[packing.batch_code]
        conn.execute(
            """
            INSERT INTO plan_batches (
                plan_id, batch_id, batch_code, quantity, revision,
                source_start, source_end, intake_start, intake_end
            ) VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?)
            """,
            (
                plan_id,
                batch["id"],
                batch["batch_code"],
                batch["quantity"],
                batch["revision"],
                batch["quantity"],
                intake_cursor,
                intake_cursor + batch["quantity"],
            ),
        )
        intake_cursor += batch["quantity"]

    global_seq = 0
    for packing in packings:
        batch = by_code[packing.batch_code]
        for load in packing.loads:
            global_seq += 1
            container_code = f"{payload.container_code_prefix}-P{plan_id}-{global_seq:03d}"
            container_cursor = conn.execute(
                """
                INSERT INTO containers (
                    plan_id, seq, container_code, batch_id, batch_code, batch_seq,
                    load_quantity, source_start, source_end
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plan_id,
                    global_seq,
                    container_code,
                    batch["id"],
                    batch["batch_code"],
                    load.batch_seq,
                    load.load_quantity,
                    load.source_start,
                    load.source_end,
                ),
            )
            container_id = container_cursor.lastrowid
            conn.execute(
                """
                INSERT INTO allocations (
                    plan_id, container_id, batch_id, batch_code, ordinal,
                    source_start, source_end, quantity
                ) VALUES (?, ?, ?, ?, 1, ?, ?, ?)
                """,
                (
                    plan_id,
                    container_id,
                    batch["id"],
                    batch["batch_code"],
                    load.source_start,
                    load.source_end,
                    load.load_quantity,
                ),
            )

    return get_plan_detail(conn, plan_id)


def _serialize_plan(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    plan_id = row["id"]
    pb_rows = conn.execute(
        "SELECT * FROM plan_batches WHERE plan_id = ? ORDER BY id",
        (plan_id,),
    ).fetchall()
    current_rows = conn.execute(
        f"SELECT * FROM batches WHERE id IN ({','.join('?' for _ in pb_rows)})",
        [pb["batch_id"] for pb in pb_rows] or [-1],
    ).fetchall() if pb_rows else []
    current_by_id = {item["id"]: item for item in current_rows}

    batches: list[dict[str, Any]] = []
    for pb in pb_rows:
        current = current_by_id.get(pb["batch_id"])
        current_quantity = current["quantity"] if current else None
        current_revision = current["revision"] if current else None
        differs = bool(
            current is None
            or current_revision != pb["revision"]
            or current_quantity != pb["quantity"]
            or (current is not None and current["batch_code"] != pb["batch_code"])
        )
        batches.append(
            {
                "batch_id": pb["batch_id"],
                "batch_code": pb["batch_code"],
                "quantity": pb["quantity"],
                "revision": pb["revision"],
                "source_start": pb["source_start"],
                "source_end": pb["source_end"],
                "intake_start": pb["intake_start"],
                "intake_end": pb["intake_end"],
                "current_quantity": current_quantity,
                "current_revision": current_revision,
                "differs_from_current": differs,
            }
        )

    container_rows = conn.execute(
        "SELECT * FROM containers WHERE plan_id = ? ORDER BY seq",
        (plan_id,),
    ).fetchall()
    intake_start_by_batch = {pb["batch_id"]: pb["intake_start"] for pb in pb_rows}
    allocation_rows = conn.execute(
        "SELECT * FROM allocations WHERE plan_id = ? ORDER BY container_id, ordinal, id",
        (plan_id,),
    ).fetchall()
    allocations_by_container: dict[int, list[dict[str, Any]]] = {}
    for allocation in allocation_rows:
        intake_start = intake_start_by_batch.get(allocation["batch_id"], 0)
        allocations_by_container.setdefault(allocation["container_id"], []).append(
            {
                "id": allocation["id"],
                "ordinal": allocation["ordinal"],
                "batch_id": allocation["batch_id"],
                "batch_code": allocation["batch_code"],
                "source_start": allocation["source_start"],
                "source_end": allocation["source_end"],
                "global_start": intake_start + allocation["source_start"],
                "global_end": intake_start + allocation["source_end"],
                "quantity": allocation["quantity"],
            }
        )

    containers = []
    for container in container_rows:
        intake_start = intake_start_by_batch.get(container["batch_id"], 0)
        containers.append(
            {
                "id": container["id"],
                "seq": container["seq"],
                "container_code": container["container_code"],
                "batch_id": container["batch_id"],
                "batch_code": container["batch_code"],
                "batch_seq": container["batch_seq"],
                "load_quantity": container["load_quantity"],
                "source_start": container["source_start"],
                "source_end": container["source_end"],
                "global_start": intake_start + container["source_start"],
                "global_end": intake_start + container["source_end"],
                "allocations": allocations_by_container.get(container["id"], []),
            }
        )

    outbound_row = conn.execute(
        "SELECT * FROM outbound_records WHERE plan_id = ?",
        (plan_id,),
    ).fetchone()
    outbound = None
    if outbound_row is not None:
        outbound = {
            "reference": outbound_row["reference"],
            "operator": outbound_row["operator"],
            "note": outbound_row["note"],
            "created_at": outbound_row["created_at"],
        }

    status = row["status"]
    return {
        "id": plan_id,
        "status": status,
        "container_capacity": row["container_capacity"],
        "min_load": row["min_load"],
        "max_load": row["max_load"],
        "strategy": row["strategy"],
        "note": row["note"],
        "created_at": row["created_at"],
        "confirmed_at": row["confirmed_at"],
        "outbound_at": row["outbound_at"],
        "expired_at": row["expired_at"],
        "expiration_reason": row["expiration_reason"],
        "is_expired": status == "expired",
        "expired": status == "expired",
        "is_stale": status == "expired",
        "stale": status == "expired",
        "confirm_allowed": status == "generated",
        "outbound_allowed": status == "confirmed",
        "batches": batches,
        "containers": containers,
        "outbound": outbound,
    }


def get_plan_detail(conn: sqlite3.Connection, plan_id: int, *, sync_staleness: bool = True) -> dict[str, Any]:
    if sync_staleness:
        expire_stale_plan_if_needed(conn, plan_id)
    row = conn.execute("SELECT * FROM packing_plans WHERE id = ?", (plan_id,)).fetchone()
    if row is None:
        raise NotFoundError(f"Plan {plan_id} was not found.", code="PLAN_NOT_FOUND")
    return _serialize_plan(conn, row)


def list_plans(conn: sqlite3.Connection, *, batch_id: int | None = None, status: str | None = None) -> list[dict[str, Any]]:
    expire_all_stale_plans(conn)
    query = "SELECT * FROM packing_plans p"
    params: list[Any] = []
    where: list[str] = []
    if batch_id is not None:
        where.append("p.id IN (SELECT plan_id FROM plan_batches WHERE batch_id = ?)")
        params.append(batch_id)
    if status is not None:
        where.append("p.status = ?")
        params.append(status)
    if where:
        query += " WHERE " + " AND ".join(where)
    query += " ORDER BY p.id DESC"
    rows = conn.execute(query, params).fetchall()

    summaries: list[dict[str, Any]] = []
    for row in rows:
        plan_id = row["id"]
        batch_rows = conn.execute(
            "SELECT batch_code FROM plan_batches WHERE plan_id = ? ORDER BY id",
            (plan_id,),
        ).fetchall()
        container_count = conn.execute(
            "SELECT COUNT(*) AS count FROM containers WHERE plan_id = ?",
            (plan_id,),
        ).fetchone()["count"]
        batch_codes = [item["batch_code"] for item in batch_rows]
        summaries.append(
            {
                "id": plan_id,
                "status": row["status"],
                "container_capacity": row["container_capacity"],
                "min_load": row["min_load"],
                "max_load": row["max_load"],
                "created_at": row["created_at"],
                "confirmed_at": row["confirmed_at"],
                "outbound_at": row["outbound_at"],
                "expired_at": row["expired_at"],
                "is_expired": row["status"] == "expired",
                "expired": row["status"] == "expired",
                "is_stale": row["status"] == "expired",
                "stale": row["status"] == "expired",
                "confirm_allowed": row["status"] == "generated",
                "outbound_allowed": row["status"] == "confirmed",
                "batch_count": len(batch_codes),
                "container_count": container_count,
                "batch_codes": batch_codes,
            }
        )
    return summaries


def confirm_plan(conn: sqlite3.Connection, plan_id: int) -> dict[str, Any]:
    expire_stale_plan_if_needed(conn, plan_id)
    row = conn.execute("SELECT * FROM packing_plans WHERE id = ?", (plan_id,)).fetchone()
    if row is None:
        raise NotFoundError(f"Plan {plan_id} was not found.", code="PLAN_NOT_FOUND")

    if row["status"] == "expired":
        raise PlanStateError(
            "Plan is expired because a source batch changed; generate and confirm a new plan.",
            code="PLAN_EXPIRED",
            details={
                "plan_id": plan_id,
                "status": row["status"],
                "expired_at": row["expired_at"],
                "expiration_reason": row["expiration_reason"],
            },
        )
    if row["status"] == "outbound":
        raise PlanStateError(
            "Plan has already been outbound and cannot be confirmed again.",
            code="PLAN_ALREADY_OUTBOUND",
            details={"plan_id": plan_id, "outbound_at": row["outbound_at"]},
        )
    if row["status"] == "confirmed":
        return get_plan_detail(conn, plan_id, sync_staleness=False)

    now = utc_now()
    conn.execute(
        "UPDATE packing_plans SET status = 'confirmed', confirmed_at = ? WHERE id = ?",
        (now, plan_id),
    )
    return get_plan_detail(conn, plan_id)


def outbound_plan(
    conn: sqlite3.Connection,
    plan_id: int,
    payload: OutboundRequest | None = None,
) -> dict[str, Any]:
    expire_stale_plan_if_needed(conn, plan_id)
    row = conn.execute("SELECT * FROM packing_plans WHERE id = ?", (plan_id,)).fetchone()
    if row is None:
        raise NotFoundError(f"Plan {plan_id} was not found.", code="PLAN_NOT_FOUND")

    if row["status"] == "expired":
        raise PlanStateError(
            "Expired plan cannot be outbound. Generate a new plan, confirm it, and retry outbound.",
            code="PLAN_EXPIRED",
            details={
                "plan_id": plan_id,
                "expired_at": row["expired_at"],
                "expiration_reason": row["expiration_reason"],
            },
        )
    if row["status"] == "generated":
        raise PlanStateError(
            "Plan must be confirmed before outbound.",
            code="PLAN_NOT_CONFIRMED",
            details={"plan_id": plan_id, "status": row["status"]},
        )
    if row["status"] == "outbound":
        outbound_row = conn.execute(
            "SELECT reference FROM outbound_records WHERE plan_id = ?",
            (plan_id,),
        ).fetchone()
        raise PlanStateError(
            "Plan has already been outbound.",
            code="PLAN_ALREADY_OUTBOUND",
            details={"plan_id": plan_id, "reference": outbound_row["reference"] if outbound_row else None},
        )

    payload = payload or OutboundRequest()
    now = utc_now()
    conn.execute(
        """
        INSERT INTO outbound_records (plan_id, reference, operator, note, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (plan_id, payload.reference, payload.operator, payload.note, now),
    )
    conn.execute(
        "UPDATE packing_plans SET status = 'outbound', outbound_at = ? WHERE id = ?",
        (now, plan_id),
    )
    return get_plan_detail(conn, plan_id)


def check_plan_boundaries(conn: sqlite3.Connection, plan_id: int) -> dict[str, Any]:
    plan = get_plan_detail(conn, plan_id)
    violations: list[dict[str, Any]] = []

    for container in plan["containers"]:
        codes = {item["batch_code"] for item in container["allocations"]}
        batch_ids = {item["batch_id"] for item in container["allocations"]}
        if codes != {container["batch_code"]} or batch_ids != {container["batch_id"]} or len(codes) != 1:
            violations.append(
                {
                    "type": "CROSS_BATCH_BOUNDARY",
                    "container_code": container["container_code"],
                    "batch_codes": sorted(codes),
                }
            )
        allocation_quantity = sum(item["quantity"] for item in container["allocations"])
        if allocation_quantity != container["load_quantity"]:
            violations.append(
                {
                    "type": "LOAD_MISMATCH",
                    "container_code": container["container_code"],
                    "allocation_quantity": allocation_quantity,
                    "load_quantity": container["load_quantity"],
                }
            )
        if not (plan["min_load"] <= container["load_quantity"] <= plan["max_load"]):
            violations.append({"type": "LOAD_OUTSIDE_LIMITS", "container_code": container["container_code"]})
        if container["load_quantity"] > plan["container_capacity"]:
            violations.append({"type": "LOAD_EXCEEDS_CAPACITY", "container_code": container["container_code"]})

    for batch in plan["batches"]:
        intervals: list[dict[str, int]] = []
        for container in plan["containers"]:
            for allocation in container["allocations"]:
                if allocation["batch_id"] == batch["batch_id"]:
                    intervals.append(allocation)
        ordered = sorted(intervals, key=lambda item: item["source_start"])
        cursor = batch["source_start"]
        for allocation in ordered:
            if allocation["source_start"] != cursor:
                violations.append(
                    {
                        "type": "SOURCE_INTERVAL_GAP_OR_OVERLAP",
                        "batch_code": batch["batch_code"],
                        "expected_start": cursor,
                        "actual_start": allocation["source_start"],
                    }
                )
                break
            if allocation["source_end"] - allocation["source_start"] != allocation["quantity"]:
                violations.append(
                    {
                        "type": "QUANTITY_INTERVAL_MISMATCH",
                        "batch_code": batch["batch_code"],
                        "allocation_id": allocation["id"],
                    }
                )
                break
            cursor = allocation["source_end"]
        else:
            if cursor != batch["source_end"]:
                violations.append(
                    {
                        "type": "INCOMPLETE_SOURCE_COVERAGE",
                        "batch_code": batch["batch_code"],
                        "covered_until": cursor,
                        "source_end": batch["source_end"],
                    }
                )

    checks = {
        "single_batch_per_container": not any(v["type"] == "CROSS_BATCH_BOUNDARY" for v in violations),
        "loads_match_allocations": not any(v["type"] == "LOAD_MISMATCH" for v in violations),
        "loads_within_limits_and_capacity": not any(
            v["type"] in {"LOAD_OUTSIDE_LIMITS", "LOAD_EXCEEDS_CAPACITY"} for v in violations
        ),
        "source_intervals_contiguous_and_complete": not any(
            v["type"] in {"SOURCE_INTERVAL_GAP_OR_OVERLAP", "INCOMPLETE_SOURCE_COVERAGE", "QUANTITY_INTERVAL_MISMATCH"}
            for v in violations
        ),
    }
    return {
        "plan_id": plan_id,
        "valid": not violations,
        "no_cross_batch": checks["single_batch_per_container"],
        "checks": checks,
        "violations": violations,
    }


def validate_manual_container(payload: Any) -> dict[str, Any]:
    allocations = [item.model_dump() for item in payload.allocations]
    result = validate_no_cross_batch_allocation(allocations)
    result["container_code"] = payload.container_code
    return result
