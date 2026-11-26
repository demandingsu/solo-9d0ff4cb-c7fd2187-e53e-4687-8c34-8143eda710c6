from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

PlanStatus = Literal["generated", "confirmed", "outbound", "expired"]


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
