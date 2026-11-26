from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .constants import (
    LABEL_SHEET_COLUMNS,
    LABEL_SHEET_ROWS,
    LABEL_SHEET_TOTAL_CELLS,
)

PlanStatus = Literal["generated", "confirmed", "outbound", "expired"]
LabelPlacementStatus = Literal["reserved", "printed", "blank", "damaged"]
LabelReportResult = Literal["printed", "blank", "damaged"]
LabelCellState = Literal["free", "occupied", "unavailable"]


class BatchCreate(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "example": {"batch_code": "B-ALPHA", "quantity": 125}
    })

    batch_code: str = Field(min_length=1, max_length=80, description="Unique sample batch code.")
    quantity: int = Field(gt=0, description="Positive integer number of sample units in the batch.")


class BatchBulkCreate(BaseModel):
    batches: list[BatchCreate] = Field(min_length=1)


class BatchUpdate(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "example": {"quantity": 125}
    })

    batch_code: str | None = Field(default=None, min_length=1, max_length=80)
    quantity: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def require_any_field(self) -> "BatchUpdate":
        if self.batch_code is None and self.quantity is None:
            raise ValueError("Provide at least one of batch_code or quantity.")
        return self


class BatchOut(BaseModel):
    id: int
    batch_code: str
    quantity: int
    revision: int
    created_at: str
    updated_at: str


class BatchUpdateOut(BatchOut):
    expired_plan_count: int = 0


class PlanGenerateRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "summary": "Valid: two batches, no mixed container",
                    "description": "Each container belongs to exactly one batch and records contiguous [start,end) intervals.",
                    "value": {
                        "batches": [
                            {"batch_code": "B-ALPHA", "quantity": 125},
                            {"batch_code": "B-BETA", "quantity": 73},
                        ],
                        "container_capacity": 60,
                        "min_load": 25,
                        "max_load": 50,
                        "note": "Demo valid packing",
                    },
                },
                {
                    "summary": "Infeasible: gap between one and two containers",
                    "description": "Quantity 55 cannot be packed: one container holds at most 50, while two require at least 60.",
                    "value": {
                        "batches": [{"batch_code": "B-NO-GO", "quantity": 55}],
                        "container_capacity": 60,
                        "min_load": 30,
                        "max_load": 50,
                    },
                },
                {
                    "summary": "Generate from existing batch IDs",
                    "value": {
                        "batch_ids": [1, 2],
                        "container_capacity": 60,
                        "min_load": 25,
                        "max_load": 50,
                    },
                },
            ]
        }
    )

    batch_ids: list[int] | None = Field(
        default=None, description="Existing batch IDs. Use either batch_ids or inline batches."
    )
    batches: list[BatchCreate] | None = Field(
        default=None, description="Inline batches. New batch_code values are created; existing codes are resolved."
    )
    container_capacity: int = Field(gt=0, description="Hard physical capacity of one container.")
    min_load: int = Field(default=1, ge=0, description="Inclusive minimum allowed load per container.")
    max_load: int = Field(gt=0, description="Inclusive maximum operational load; must not exceed capacity.")
    note: str | None = Field(default=None, max_length=500)
    container_code_prefix: str = Field(default="BOX", min_length=1, max_length=20)

    @model_validator(mode="after")
    def require_one_batch_source(self) -> "PlanGenerateRequest":
        has_ids = bool(self.batch_ids)
        has_inline = bool(self.batches)
        if has_ids == has_inline:
            raise ValueError("Provide exactly one of non-empty batch_ids or batches.")
        if self.batch_ids and len(set(self.batch_ids)) != len(self.batch_ids):
            raise ValueError("batch_ids must not contain duplicates.")
        if self.min_load > self.max_load:
            raise ValueError("min_load must be less than or equal to max_load.")
        if self.max_load > self.container_capacity:
            raise ValueError("max_load must not exceed container_capacity.")
        return self


class AllocationOut(BaseModel):
    id: int | None = None
    ordinal: int
    batch_id: int
    batch_code: str
    source_start: int
    source_end: int
    global_start: int | None = None
    global_end: int | None = None
    quantity: int


class ContainerOut(BaseModel):
    id: int | None = None
    seq: int
    container_code: str
    batch_id: int
    batch_code: str
    batch_seq: int
    load_quantity: int
    source_start: int
    source_end: int
    global_start: int | None = None
    global_end: int | None = None
    allocations: list[AllocationOut] = Field(default_factory=list)


class PlanBatchOut(BaseModel):
    batch_id: int
    batch_code: str
    quantity: int
    revision: int
    source_start: int
    source_end: int
    intake_start: int
    intake_end: int
    current_quantity: int | None = None
    current_revision: int | None = None
    differs_from_current: bool = False


class OutboundRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "example": {"reference": "OUT-2026-0001", "operator": "warehouse", "note": "Picked and sealed."}
    })

    reference: str | None = Field(default=None, max_length=100)
    operator: str | None = Field(default=None, max_length=100)
    note: str | None = Field(default=None, max_length=500)


class OutboundOut(BaseModel):
    reference: str | None
    operator: str | None
    note: str | None
    created_at: str


class PlanOut(BaseModel):
    id: int
    status: PlanStatus
    container_capacity: int
    min_load: int
    max_load: int
    strategy: str
    note: str | None
    created_at: str
    confirmed_at: str | None
    outbound_at: str | None
    expired_at: str | None
    expiration_reason: str | None
    is_expired: bool
    expired: bool = False
    is_stale: bool = False
    stale: bool = False
    confirm_allowed: bool
    outbound_allowed: bool
    batches: list[PlanBatchOut]
    containers: list[ContainerOut]
    outbound: OutboundOut | None = None


class PlanSummary(BaseModel):
    id: int
    status: PlanStatus
    container_capacity: int
    min_load: int
    max_load: int
    created_at: str
    confirmed_at: str | None
    outbound_at: str | None
    expired_at: str | None
    is_expired: bool
    expired: bool = False
    is_stale: bool = False
    stale: bool = False
    confirm_allowed: bool
    outbound_allowed: bool
    batch_count: int
    container_count: int
    batch_codes: list[str]


class AllocationValidationItem(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "examples": [
            {
                "summary": "Cross-batch container (rejected)",
                "value": {
                    "batch_code": "B-ALPHA",
                    "source_start": 100,
                    "source_end": 125,
                    "quantity": 25,
                },
            }
        ]
    })

    batch_code: str = Field(min_length=1)
    source_start: int = Field(ge=0)
    source_end: int = Field(gt=0)
    quantity: int = Field(gt=0)


class ContainerBoundaryValidationRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "container_code": "BAD-MIX-01",
                "allocations": [
                    {"batch_code": "B-ALPHA", "source_start": 100, "source_end": 125, "quantity": 25},
                    {"batch_code": "B-BETA", "source_start": 0, "source_end": 20, "quantity": 20},
                ],
            }
        }
    )

    container_code: str | None = None
    allocations: list[AllocationValidationItem] = Field(min_length=1)


class BoundaryCheckOut(BaseModel):
    valid: bool
    no_cross_batch: bool
    batch_code: str | None = None
    allocation_count: int | None = None
    violations: list[Any] = Field(default_factory=list)
    checks: dict[str, bool] | None = None


# ---------------------------------------------------------------------------
# Label sheet imposition (箱签拼版)
# ---------------------------------------------------------------------------


class LabelSheetRegisterRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "summary": "登记一整张 A4 标签纸（24 格全部可用）",
                    "value": {"sheet_code": "A4-3x8-FULL", "available_cells": list(range(1, 25))},
                },
                {
                    "summary": "登记只剩 2、5、7 格的半张纸",
                    "value": {"sheet_code": "A4-3x8-LEFT-2-5-7", "available_cells": [2, 5, 7]},
                },
                {
                    "summary": "按纸号登记，缺省使用全部 24 格",
                    "value": {"sheet_code": "A4-3x8-DEFAULT"},
                },
            ]
        }
    )

    sheet_code: str = Field(min_length=1, max_length=80, description="纸号，全局唯一。")
    rows: int = Field(
        default=LABEL_SHEET_ROWS,
        gt=0,
        description="纸张行数；按纸号登记，A4 标签纸固定为 8 行。",
    )
    columns: int = Field(
        default=LABEL_SHEET_COLUMNS,
        gt=0,
        description="纸张列数；按纸号登记，A4 标签纸固定为 3 列。",
    )
    available_cells: list[int] | None = Field(
        default=None,
        description=(
            "当前可用的格号（从 1 开始，按行优先编号）。缺省表示整张 24 格都可用；"
            "已印刷或损坏的格不传即可。"
        ),
    )
    note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_geometry_and_cells(self) -> "LabelSheetRegisterRequest":
        if self.rows != LABEL_SHEET_ROWS or self.columns != LABEL_SHEET_COLUMNS:
            raise ValueError(
                f"Label sheet must be exactly {LABEL_SHEET_COLUMNS} columns x "
                f"{LABEL_SHEET_ROWS} rows; got {self.columns} x {self.rows}."
            )
        if self.available_cells is not None:
            if not self.available_cells:
                raise ValueError("available_cells must not be empty; omit it to use all cells.")
            duplicates = sorted(
                {cell for cell in self.available_cells if self.available_cells.count(cell) > 1}
            )
            if duplicates:
                raise ValueError(f"available_cells contains duplicate cell numbers: {duplicates}.")
            out_of_range = sorted(
                cell
                for cell in self.available_cells
                if cell < 1 or cell > LABEL_SHEET_TOTAL_CELLS
            )
            if out_of_range:
                raise ValueError(
                    f"available_cells must be within 1..{LABEL_SHEET_TOTAL_CELLS}; "
                    f"invalid: {out_of_range}."
                )
        return self


class LabelSheetCellOut(BaseModel):
    cell_no: int
    row: int
    column: int
    state: LabelCellState
    layout_no: str | None = None
    box_code: str | None = None
    placement_status: LabelPlacementStatus | None = None


class LabelSheetOut(BaseModel):
    id: int
    sheet_code: str
    rows: int
    columns: int
    note: str | None
    created_at: str
    total_cells: int
    available_cells: list[int]
    free_cells: list[int]
    occupied_cells: list[int]
    unavailable_cells: list[int]
    cells: list[LabelSheetCellOut]


class LabelImposeBoxItem(BaseModel):
    """One already-confirmed plan and the box codes to print from it."""

    plan_id: int = Field(gt=0)
    box_codes: list[str] = Field(min_length=1, description="该方案里要打印的箱码。")

    @model_validator(mode="after")
    def reject_duplicate_box_codes(self) -> "LabelImposeBoxItem":
        if len(set(self.box_codes)) != len(self.box_codes):
            raise ValueError("box_codes must not contain duplicates within one plan item.")
        return self


class LabelImposeRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "summary": "两个已确认方案拼到同一张纸",
                    "value": {
                        "sheet_code": "A4-3x8-FULL",
                        "items": [
                            {"plan_id": 1, "box_codes": ["BOX-P1-001", "BOX-P1-002"]},
                            {"plan_id": 2, "box_codes": ["BOX-P2-001"]},
                        ],
                    },
                }
            ]
        }
    )

    sheet_code: str = Field(min_length=1, max_length=80, description="已登记的纸号。")
    items: list[LabelImposeBoxItem] = Field(
        min_length=1, description="各份已确认方案及其要打印的箱码。"
    )
    note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def reject_duplicate_plan_items(self) -> "LabelImposeRequest":
        plan_ids = [item.plan_id for item in self.items]
        duplicates = sorted({pid for pid in plan_ids if plan_ids.count(pid) > 1})
        if duplicates:
            raise ValueError(f"items must not repeat plan_id: {duplicates}.")
        return self


class LabelPlacementOut(BaseModel):
    cell_no: int
    row: int
    column: int
    plan_id: int
    batch_id: int
    batch_code: str
    container_id: int
    box_code: str
    status: LabelPlacementStatus
    reported_at: str | None = None
    # True once this exact box code was reported printed before and is being
    # reprinted, so the operator can tell a reprint apart from a first print.
    reprinted: bool = False


class LabelLayoutOut(BaseModel):
    id: int
    layout_no: str
    sheet_id: int
    sheet_code: str
    rows: int
    columns: int
    note: str | None
    created_at: str
    placement_count: int
    placements: list[LabelPlacementOut]
    # Row-major grid of every cell on the sheet; cells not assigned to this
    # layout are null and stay blank in place.
    page: list[list[LabelPlacementOut | None]]
    printable_page: str


class LabelReportItem(BaseModel):
    cell_no: int = Field(gt=0, description="版面中的格号。")
    result: LabelReportResult = Field(description="printed=已印，blank=确认空白，damaged=损坏。")
    box_code: str = Field(
        min_length=1,
        description="该格对应的箱码；用于核对回报针对的确实是这一格。",
    )


class LabelReportRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "summary": "分批回报：先印好 1、2 格",
                    "value": {
                        "items": [
                            {"cell_no": 1, "box_code": "BOX-P1-001", "result": "printed"},
                            {"cell_no": 2, "box_code": "BOX-P1-002", "result": "printed"},
                        ]
                    },
                },
                {
                    "summary": "回报第 3 格为空白，释放箱码",
                    "value": {"items": [{"cell_no": 3, "box_code": "BOX-P2-001", "result": "blank"}]},
                },
            ]
        }
    )

    items: list[LabelReportItem] = Field(min_length=1)

    @model_validator(mode="after")
    def reject_duplicate_cells(self) -> "LabelReportRequest":
        cell_nos = [item.cell_no for item in self.items]
        duplicates = sorted({cell for cell in cell_nos if cell_nos.count(cell) > 1})
        if duplicates:
            raise ValueError(f"items must not repeat cell_no: {duplicates}.")
        return self


class LabelReportItemOut(BaseModel):
    cell_no: int
    box_code: str
    result: LabelPlacementStatus
    first_result: LabelPlacementStatus
    status: LabelPlacementStatus
    idempotent: bool = False
    message: str | None = None


class LabelReportOut(BaseModel):
    layout_no: str
    sheet_code: str
    reported_at: str
    results: list[LabelReportItemOut]
    layout: LabelLayoutOut
    sheet: LabelSheetOut
