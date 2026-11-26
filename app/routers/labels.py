from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Body, Depends, Query, Response, status
from fastapi.responses import HTMLResponse

from ..database import get_db
from ..examples import LABEL_IMPOSE_EXAMPLES, LABEL_REPORT_EXAMPLES, LABEL_SHEET_EXAMPLES
from ..labeling import (
    get_layout,
    get_sheet,
    impose_layout,
    list_layouts,
    list_sheets,
    register_sheet,
    render_layout_html,
    report_layout,
)
from ..schemas import (
    LabelImposeRequest,
    LabelLayoutOut,
    LabelReportOut,
    LabelReportRequest,
    LabelSheetOut,
    LabelSheetRegisterRequest,
)

router = APIRouter(prefix="/api", tags=["箱签拼版"])


@router.post(
    "/label-sheets",
    response_model=LabelSheetOut,
    status_code=status.HTTP_201_CREATED,
    summary="按纸号登记 A4 标签纸及可用格",
    description=(
        "按纸号登记一张等分为 **3 列 x 8 行（共 24 格）** 的 A4 标签纸，"
        "格号按行优先从 1 到 24 编号。`available_cells` 表示当前仍可用的格；"
        "省略表示整张 24 格都可用。相同纸号重复登记相同内容按幂等处理，"
        "内容不同返回 `409 LABEL_SHEET_EXISTS`。"
    ),
    openapi_extra={
        "requestBody": {"content": {"application/json": {"examples": LABEL_SHEET_EXAMPLES}}}
    },
)
def register_label_sheet(
    response: Response,
    payload: LabelSheetRegisterRequest = Body(...),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    sheet, created = register_sheet(db, payload)
    if not created:
        response.status_code = status.HTTP_200_OK
    return sheet


@router.get("/label-sheets", response_model=list[LabelSheetOut], summary="列出全部标签纸及格的占用情况")
def get_label_sheets(db: sqlite3.Connection = Depends(get_db)) -> list[dict]:
    return list_sheets(db)


@router.get(
    "/label-sheets/{sheet_code}",
    response_model=LabelSheetOut,
    summary="按纸号查询标签纸：可用、空闲、占用和不可用格",
)
def get_label_sheet(sheet_code: str, db: sqlite3.Connection = Depends(get_db)) -> dict:
    return get_sheet(db, sheet_code)


@router.post(
    "/label-layouts",
    response_model=LabelLayoutOut,
    status_code=status.HTTP_201_CREATED,
    summary="提交已确认方案中的箱码，拼成可打印版面",
    description=(
        "从各份**已确认**方案中挑选要打印的箱码，按提交顺序依次占入纸上的空闲格，"
        "返回带版面号的可打印页面；未分配的格原位留白。任一箱码不属于对应方案、"
        "方案未确认/已过期、箱码已被其他版面占用，或空闲格不足时，整版退回，"
        "纸位和箱码都不会被占用。"
    ),
    openapi_extra={
        "requestBody": {"content": {"application/json": {"examples": LABEL_IMPOSE_EXAMPLES}}}
    },
)
def create_label_layout(
    payload: LabelImposeRequest = Body(...),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    return impose_layout(db, payload)


@router.get("/label-layouts", response_model=list[LabelLayoutOut], summary="列出全部版面")
def get_label_layouts(
    sheet_code: str | None = Query(default=None, description="按纸号过滤"),
    db: sqlite3.Connection = Depends(get_db),
) -> list[dict]:
    return list_layouts(db, sheet_code=sheet_code)


@router.get("/label-layouts/{layout_no}", response_model=LabelLayoutOut, summary="按版面号查询版面")
def get_label_layout(layout_no: str, db: sqlite3.Connection = Depends(get_db)) -> dict:
    return get_layout(db, layout_no)


@router.post(
    "/label-layouts/{layout_no}/report",
    response_model=LabelReportOut,
    summary="打印后按版面号分次回报每格结果",
    description=(
        "逐格回报 `printed` / `blank` / `damaged`，可分多次提交。"
        "原样重报返回首次结果且不做改动；同一版面同一格改报不同结果时整次拒绝"
        "（`409 LABEL_REPORT_CONFLICT`），本次所有项都不会写入。"
        "回报为 `printed` 的格和箱码永久占用；`blank` 释放格和箱码；"
        "`damaged` 仅释放箱码，格仍占用。"
    ),
    openapi_extra={
        "requestBody": {"content": {"application/json": {"examples": LABEL_REPORT_EXAMPLES}}}
    },
)
def report_label_layout(
    layout_no: str,
    payload: LabelReportRequest = Body(...),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    return report_layout(db, layout_no, payload)


@router.get(
    "/label-layouts/{layout_no}/print",
    response_class=HTMLResponse,
    summary="以 HTML 打开可打印版面",
    description="返回可直接打印的 A4 版面；未分配或已确认空白的格保持空白。",
    responses={200: {"content": {"text/html": {}}}},
)
def print_label_layout(layout_no: str, db: sqlite3.Connection = Depends(get_db)) -> HTMLResponse:
    return HTMLResponse(content=render_layout_html(db, layout_no))
