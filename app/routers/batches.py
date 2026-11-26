from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Body, Depends, status

from ..database import get_db
from ..examples import BATCH_PATCH_EXAMPLES
from ..schemas import BatchBulkCreate, BatchCreate, BatchOut, BatchUpdate, BatchUpdateOut
from ..service import (
    bulk_create_batches,
    create_batch,
    get_batch,
    list_batches,
    update_batch,
    update_batch_by_code,
)

router = APIRouter(prefix="/api/batches", tags=["样品批次"])


@router.post(
    "",
    response_model=BatchOut,
    status_code=status.HTTP_201_CREATED,
    summary="创建一个样品批次",
)
def create_one_batch(payload: BatchCreate, db: sqlite3.Connection = Depends(get_db)) -> dict:
    return create_batch(db, payload.batch_code, payload.quantity)


@router.post(
    "/bulk",
    response_model=list[BatchOut],
    status_code=status.HTTP_201_CREATED,
    summary="批量提交样品批次",
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "examples": {
                        "two_batches": {
                            "summary": "两个批次",
                            "value": {
                                "batches": [
                                    {"batch_code": "B-ALPHA", "quantity": 125},
                                    {"batch_code": "B-BETA", "quantity": 73},
                                ]
                            },
                        }
                    }
                }
            }
        }
    },
)
def create_many_batches(payload: BatchBulkCreate, db: sqlite3.Connection = Depends(get_db)) -> list[dict]:
    return bulk_create_batches(db, payload.batches)


@router.get("", response_model=list[BatchOut], summary="列出全部样品批次")
def get_batches(db: sqlite3.Connection = Depends(get_db)) -> list[dict]:
    return list_batches(db)


@router.get("/{batch_id}", response_model=BatchOut, summary="按 ID 查询批次")
def get_one_batch(batch_id: int, db: sqlite3.Connection = Depends(get_db)) -> dict:
    return get_batch(db, batch_id)


@router.patch(
    "/{batch_id}",
    response_model=BatchUpdateOut,
    summary="修改批次并使相关旧方案过期",
    description=(
        "每次显式 PATCH 都会增加 `revision`，并把所有包含该批次且仍为 "
        "`generated`/`confirmed` 的方案标记为 `expired`。即使把数量改回历史值，"
        "新 revision 仍与旧方案快照不同，因此旧方案不能确认或出库。"
    ),
    openapi_extra={
        "requestBody": {
            "content": {"application/json": {"examples": BATCH_PATCH_EXAMPLES}}
        }
    },
)
def patch_batch(
    batch_id: int,
    payload: BatchUpdate = Body(...),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    return update_batch(db, batch_id, batch_code=payload.batch_code, quantity=payload.quantity)


@router.put(
    "/{batch_id}",
    response_model=BatchUpdateOut,
    summary="PUT 修改批次（与 PATCH 相同的失效语义）",
)
def put_batch(
    batch_id: int,
    payload: BatchUpdate,
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    return update_batch(db, batch_id, batch_code=payload.batch_code, quantity=payload.quantity)


@router.patch(
    "/by-code/{batch_code}/update",
    response_model=BatchUpdateOut,
    summary="按批次号修改，便于 /docs 演示",
)
def patch_batch_by_code(
    batch_code: str,
    payload: BatchUpdate,
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    return update_batch_by_code(
        db,
        batch_code,
        new_batch_code=payload.batch_code,
        quantity=payload.quantity,
    )
