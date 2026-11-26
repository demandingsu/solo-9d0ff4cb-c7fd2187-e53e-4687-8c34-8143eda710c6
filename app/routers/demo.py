from __future__ import annotations

import sqlite3
import uuid
from typing import Any

from fastapi import APIRouter, Depends

from ..database import get_db
from ..errors import AppError
from ..packing import build_packing
from ..schemas import (
    AllocationValidationItem,
    BatchCreate,
    ContainerBoundaryValidationRequest,
    PlanGenerateRequest,
)
from ..service import (
    check_plan_boundaries,
    confirm_plan,
    generate_plan,
    get_plan_detail,
    outbound_plan,
    update_batch,
    validate_manual_container,
)

router = APIRouter(prefix="/api/demo", tags=["Docs 一键演示"])


def _error_report(exc: AppError) -> dict[str, Any]:
    return {
        "http_status": exc.status_code,
        "code": exc.code,
        "message": exc.message,
        "details": exc.details,
    }


@router.get("/guide", summary="获取 /docs 手工演示步骤")
def demo_guide() -> dict[str, Any]:
    return {
        "swagger_ui": "/docs",
        "steps": [
            {
                "scenario": "无可行装箱",
                "method": "POST",
                "path": "/api/plans",
                "select_example": "Infeasible: gap between one and two containers",
                "expected_http_status": 422,
                "expected_code": "NO_FEASIBLE_PACKING",
            },
            {
                "scenario": "装箱不跨批次并保留来源区间",
                "method": "POST",
                "path": "/api/plans",
                "select_example": "Valid: two batches, no mixed container",
                "expected_http_status": 201,
                "verify": "Each container has one batch_code; GET /api/plans/{id}/boundary-check returns valid=true.",
            },
            {
                "scenario": "手工提交跨批次容器会被拒绝",
                "method": "POST",
                "path": "/api/boundary/validate-container",
                "expected_http_status": 422,
                "expected_code": "CROSS_BATCH_BOUNDARY",
            },
            {
                "scenario": "修改后旧方案失效，即使数量恢复",
                "method_sequence": [
                    "POST /api/plans with quantity 125 and copy batches[0].batch_id / id",
                    "POST /api/plans/{id}/confirm",
                    "PATCH /api/batches/{batch_id} with quantity 126",
                    "GET /api/plans/{id} -> status=expired",
                    "Generate a replacement plan at quantity 126",
                    "PATCH /api/batches/{batch_id} with quantity 125",
                    "GET replacement plan -> status=expired",
                    "POST /api/plans/{replacement_id}/confirm -> 409 PLAN_EXPIRED",
                    "POST /api/plans/{replacement_id}/outbound -> 409 PLAN_EXPIRED",
                ],
            },
        ],
        "one_click_endpoint": "POST /api/demo/run",
    }


@router.post("/run", summary="一键运行三个核心场景并返回核对结果")
def run_demo(db: sqlite3.Connection = Depends(get_db)) -> dict[str, Any]:
    suffix = uuid.uuid4().hex[:8]
    batch_code = f"DEMO-{suffix}"

    # 1) Infeasible packing.
    infeasible_error: dict[str, Any] | None = None
    try:
        build_packing([("DEMO-IMPOSSIBLE", 55)], container_capacity=60, min_load=30, max_load=50)
    except AppError as exc:
        infeasible_error = _error_report(exc)

    # 2) Explicit cross-batch container rejection.
    cross_batch_error: dict[str, Any] | None = None
    bad_container = ContainerBoundaryValidationRequest(
        container_code="BAD-MIX-01",
        allocations=[
            AllocationValidationItem(
                batch_code=f"{batch_code}-A",
                source_start=100,
                source_end=125,
                quantity=25,
            ),
            AllocationValidationItem(
                batch_code=f"{batch_code}-B",
                source_start=0,
                source_end=20,
                quantity=20,
            ),
        ],
    )
    try:
        validate_manual_container(bad_container)
    except AppError as exc:
        cross_batch_error = _error_report(exc)

    # 3) Valid generated plan: no crossing and complete source intervals.
    initial_request = PlanGenerateRequest(
        batches=[BatchCreate(batch_code=batch_code, quantity=125)],
        container_capacity=60,
        min_load=25,
        max_load=50,
        note="one-click demo original quantity",
    )
    plan_v1 = generate_plan(db, initial_request)
    batch_id = plan_v1["batches"][0]["batch_id"]
    boundary = check_plan_boundaries(db, plan_v1["id"])

    update_to_126 = update_batch(db, batch_id, quantity=126)
    plan_v1_after_change = get_plan_detail(db, plan_v1["id"])

    replacement_request = PlanGenerateRequest(
        batches=[BatchCreate(batch_code=batch_code, quantity=126)],
        container_capacity=60,
        min_load=25,
        max_load=50,
        note="one-click demo replacement quantity",
    )
    plan_v2 = generate_plan(db, replacement_request)

    update_restore_125 = update_batch(db, batch_id, quantity=125)
    plan_v2_after_restore = get_plan_detail(db, plan_v2["id"])
    plan_v1_after_restore = get_plan_detail(db, plan_v1["id"])

    confirm_error: dict[str, Any] | None = None
    try:
        confirm_plan(db, plan_v2["id"])
    except AppError as exc:
        confirm_error = _error_report(exc)

    outbound_error: dict[str, Any] | None = None
    try:
        outbound_plan(db, plan_v2["id"], None)
    except AppError as exc:
        outbound_error = _error_report(exc)

    checks = {
        "infeasible_returns_422": infeasible_error is not None
        and infeasible_error["http_status"] == 422
        and infeasible_error["code"] == "NO_FEASIBLE_PACKING",
        "cross_batch_returns_422": cross_batch_error is not None
        and cross_batch_error["http_status"] == 422
        and cross_batch_error["code"] == "CROSS_BATCH_BOUNDARY",
        "generated_plan_has_no_cross_batch": boundary["valid"] is True and boundary["no_cross_batch"] is True,
        "first_change_expires_original_plan": plan_v1_after_change["status"] == "expired",
        "restoring_quantity_still_expires_replacement": plan_v2_after_restore["status"] == "expired",
        "original_plan_remains_expired": plan_v1_after_restore["status"] == "expired",
        "confirm_expired_plan_blocked": confirm_error is not None and confirm_error["code"] == "PLAN_EXPIRED",
        "outbound_expired_plan_blocked": outbound_error is not None and outbound_error["code"] == "PLAN_EXPIRED",
    }

    return {
        "all_passed": all(checks.values()),
        "checks": checks,
        "infeasible_packing": infeasible_error,
        "cross_batch_boundary": cross_batch_error,
        "valid_plan_boundary_check": boundary,
        "batch": {
            "id": batch_id,
            "code": batch_code,
            "after_change_to_126": {
                "quantity": update_to_126["quantity"],
                "revision": update_to_126["revision"],
                "expired_plan_count": update_to_126.get("expired_plan_count"),
            },
            "after_restore_to_125": {
                "quantity": update_restore_125["quantity"],
                "revision": update_restore_125["revision"],
                "expired_plan_count": update_restore_125.get("expired_plan_count"),
            },
        },
        "plans": {
            "original_plan_id": plan_v1["id"],
            "original_after_first_change": {
                "id": plan_v1_after_change["id"],
                "status": plan_v1_after_change["status"],
                "expired_at": plan_v1_after_change["expired_at"],
                "expiration_reason": plan_v1_after_change["expiration_reason"],
                "outbound_allowed": plan_v1_after_change["outbound_allowed"],
            },
            "replacement_plan_id": plan_v2["id"],
            "replacement_after_restore": {
                "id": plan_v2_after_restore["id"],
                "status": plan_v2_after_restore["status"],
                "expired_at": plan_v2_after_restore["expired_at"],
                "expiration_reason": plan_v2_after_restore["expiration_reason"],
                "confirm_allowed": plan_v2_after_restore["confirm_allowed"],
                "outbound_allowed": plan_v2_after_restore["outbound_allowed"],
            },
        },
        "blocked_confirm": confirm_error,
        "blocked_outbound": outbound_error,
    }
