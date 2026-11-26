from __future__ import annotations

import sqlite3
from typing import Literal

from fastapi import APIRouter, Body, Depends, Query, status

from ..database import get_db
from ..examples import PLAN_REQUEST_EXAMPLES
from ..schemas import OutboundRequest, PlanGenerateRequest, PlanOut, PlanSummary
from ..service import (
    check_plan_boundaries,
    confirm_plan,
    generate_plan,
    get_plan_detail,
    list_plans,
    outbound_plan,
)

router = APIRouter(prefix="/api/plans", tags=["装箱方案"])


@router.post(
    "",
    response_model=PlanOut,
    status_code=status.HTTP_201_CREATED,
    summary="生成不跨批次且保留来源区间的装箱方案",
    description=(
        "算法按批次独立装箱，最小化容器数并均衡每箱装量。每个容器只属于一个批次；"
        "每条 allocation 都给出批次内半开来源区间 `[source_start, source_end)`。"
        "若任一批次在上下限内无可行箱数，返回 `422 NO_FEASIBLE_PACKING`，且不会创建方案。"
    ),
    openapi_extra={
        "requestBody": {
            "content": {"application/json": {"examples": PLAN_REQUEST_EXAMPLES}}
        }
    },
)
def create_plan(
    payload: PlanGenerateRequest = Body(...),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    return generate_plan(db, payload)


@router.get("", response_model=list[PlanSummary], summary="查询方案列表")
def get_plans(
    batch_id: int | None = Query(default=None),
    status_filter: Literal["generated", "confirmed", "outbound", "expired"] | None = Query(
        default=None, alias="status"
    ),
    db: sqlite3.Connection = Depends(get_db),
) -> list[dict]:
    return list_plans(db, batch_id=batch_id, status=status_filter)


@router.get("/{plan_id}", response_model=PlanOut, summary="查询方案详情")
def get_plan(plan_id: int, db: sqlite3.Connection = Depends(get_db)) -> dict:
    return get_plan_detail(db, plan_id)


@router.post(
    "/{plan_id}/confirm",
    response_model=PlanOut,
    summary="确认方案",
    description="只有 `generated` 方案可以确认；`expired` 方案返回 `409 PLAN_EXPIRED`。重复确认当前已确认方案按幂等成功处理。",
)
def confirm_existing_plan(plan_id: int, db: sqlite3.Connection = Depends(get_db)) -> dict:
    return confirm_plan(db, plan_id)


@router.post(
    "/{plan_id}/outbound",
    response_model=PlanOut,
    summary="方案确认后出库",
    description=(
        "未确认方案返回 `409 PLAN_NOT_CONFIRMED`；过期方案返回 `409 PLAN_EXPIRED`。"
        "出库成功后状态变为 `outbound`。"
    ),
)
def outbound_confirmed_plan(
    plan_id: int,
    payload: OutboundRequest | None = Body(default=None),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    return outbound_plan(db, plan_id, payload)


@router.get(
    "/{plan_id}/boundary-check",
    summary="核对方案未跨批次且来源区间完整",
    response_model=dict,
)
def boundary_check(plan_id: int, db: sqlite3.Connection = Depends(get_db)) -> dict:
    return check_plan_boundaries(db, plan_id)
