from __future__ import annotations

from fastapi import APIRouter, Body

from ..examples import CROSS_BATCH_EXAMPLE
from ..schemas import BoundaryCheckOut, ContainerBoundaryValidationRequest
from ..service import validate_manual_container

router = APIRouter(prefix="/api/boundary", tags=["批次边界校验"])


@router.post(
    "/validate-container",
    response_model=BoundaryCheckOut,
    summary="校验单个手工容器是否跨批次",
    description=(
        "这是一个用于在 `/docs` 直接观察跨批次拒绝结果的演示接口。"
        "同一容器的所有 allocations 必须具有相同 `batch_code`，否则返回 "
        "`422 CROSS_BATCH_BOUNDARY`。正常生成接口不会产生这种容器。"
    ),
    openapi_extra={
        "requestBody": {
            "content": {"application/json": {"examples": CROSS_BATCH_EXAMPLE}}
        }
    },
)
def validate_container(
    payload: ContainerBoundaryValidationRequest = Body(...),
) -> dict:
    return validate_manual_container(payload)
