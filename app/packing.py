from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from .errors import CrossBatchBoundaryError, InfeasiblePackingError, UnprocessableError


@dataclass(frozen=True)
class LoadSegment:
    """One contiguous source interval cut assigned to a container."""

    batch_seq: int
    load_quantity: int
    source_start: int
    source_end: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class BatchPacking:
    batch_code: str
    quantity: int
    container_count: int
    loads: tuple[LoadSegment, ...]

    def to_dict(self) -> dict:
        return {
            "batch_code": self.batch_code,
            "quantity": self.quantity,
            "container_count": self.container_count,
            "loads": [load.to_dict() for load in self.loads],
        }


def validate_load_parameters(
    quantity: int | None = None,
    *,
    container_capacity: int,
    min_load: int,
    max_load: int,
) -> None:
    errors: list[dict] = []
    if container_capacity <= 0:
        errors.append({"field": "container_capacity", "reason": "must be greater than 0"})
    if min_load < 0:
        errors.append({"field": "min_load", "reason": "must be greater than or equal to 0"})
    if max_load <= 0:
        errors.append({"field": "max_load", "reason": "must be greater than 0"})
    if min_load > max_load:
        errors.append(
            {
                "field": "min_load",
                "reason": "min_load must be less than or equal to max_load",
                "min_load": min_load,
                "max_load": max_load,
            }
        )
    if max_load > container_capacity:
        errors.append(
            {
                "field": "max_load",
                "reason": "max_load cannot exceed container_capacity",
                "max_load": max_load,
                "container_capacity": container_capacity,
            }
        )
    if quantity is not None and quantity <= 0:
        errors.append({"field": "quantity", "reason": "must be greater than 0"})
    if errors:
        raise UnprocessableError("Invalid packing parameters.", code="INVALID_PACKING_PARAMETERS", details=errors)


def assess_batch(
    batch_code: str,
    quantity: int,
    *,
    min_load: int,
    max_load: int,
) -> tuple[BatchPacking | None, dict | None]:
    """Return a balanced packing, or a machine-readable infeasibility reason."""
    minimum_count = math.ceil(quantity / max_load) if max_load > 0 else None
    maximum_count = math.floor(quantity / min_load) if min_load > 0 else None

    failure = {
        "batch_code": batch_code,
        "quantity": quantity,
        "min_load": min_load,
        "max_load": max_load,
        "minimum_container_count": minimum_count,
        "maximum_container_count_by_lower_limit": maximum_count,
    }

    if quantity <= 0:
        failure["reason"] = "BATCH_QUANTITY_MUST_BE_POSITIVE"
        return None, failure
    if quantity < min_load:
        failure["reason"] = "QUANTITY_BELOW_MIN_LOAD"
        failure["hint"] = "The entire batch is smaller than the minimum allowed container load."
        return None, failure
    if minimum_count is None or minimum_count <= 0:
        failure["reason"] = "INVALID_MAX_LOAD"
        return None, failure
    if maximum_count is not None and minimum_count > maximum_count:
        failure["reason"] = "NO_CONTAINER_COUNT_SATISFIES_BOUNDS"
        failure["hint"] = (
            f"At least {minimum_count} container(s) are required by max_load, "
            f"but min_load permits at most {maximum_count} container(s)."
        )
        return None, failure

    # Balanced integer split. Because k*min <= q <= k*max, floor/ceil(q/k)
    # are guaranteed to be within [min_load, max_load].
    base, remainder = divmod(quantity, minimum_count)
    quantities = [base + 1 if i < remainder else base for i in range(minimum_count)]

    loads: list[LoadSegment] = []
    cursor = 0
    for index, load_quantity in enumerate(quantities, start=1):
        start = cursor
        end = cursor + load_quantity
        loads.append(
            LoadSegment(
                batch_seq=index,
                load_quantity=load_quantity,
                source_start=start,
                source_end=end,
            )
        )
        cursor = end

    if cursor != quantity:  # defensive invariant
        failure["reason"] = "INTERNAL_INTERVAL_COVERAGE_ERROR"
        failure["covered_quantity"] = cursor
        return None, failure

    return (
        BatchPacking(
            batch_code=batch_code,
            quantity=quantity,
            container_count=minimum_count,
            loads=tuple(loads),
        ),
        None,
    )


def build_packing(
    batches: list[tuple[str, int]],
    *,
    container_capacity: int,
    min_load: int,
    max_load: int,
) -> list[BatchPacking]:
    validate_load_parameters(
        container_capacity=container_capacity,
        min_load=min_load,
        max_load=max_load,
    )

    plans: list[BatchPacking] = []
    failures: list[dict] = []
    for batch_code, quantity in batches:
        plan, failure = assess_batch(
            batch_code,
            quantity,
            min_load=min_load,
            max_load=max_load,
        )
        if failure is not None:
            failures.append(failure)
        elif plan is not None:
            # Capacity is already checked against max_load; assert every load too.
            for load in plan.loads:
                if load.load_quantity > container_capacity:
                    failures.append(
                        {
                            "batch_code": batch_code,
                            "reason": "LOAD_EXCEEDS_CONTAINER_CAPACITY",
                            "load_quantity": load.load_quantity,
                            "container_capacity": container_capacity,
                        }
                    )
                    break
            else:
                plans.append(plan)

    if failures:
        raise InfeasiblePackingError(details={"failures": failures})
    return plans


def validate_no_cross_batch_allocation(
    allocations: list[dict],
    *,
    batch_quantities: dict[str, int] | None = None,
) -> dict:
    """Validate that one proposed container never mixes batch source intervals."""
    errors: list[dict] = []
    if not allocations:
        raise InfeasiblePackingError(
            "A container must contain at least one allocation.",
            code="EMPTY_CONTAINER",
            details={"allocations": allocations},
        )

    batch_codes = {item.get("batch_code") for item in allocations}
    if len(batch_codes) > 1:
        errors.append(
            {
                "code": "CROSS_BATCH_BOUNDARY",
                "batch_codes": sorted(str(code) for code in batch_codes if code is not None),
                "message": "All allocations in one container must belong to one batch.",
            }
        )

    intervals_by_batch: dict[str, list[tuple[int, int]]] = {}
    for index, item in enumerate(allocations):
        code = str(item.get("batch_code", ""))
        start = item.get("source_start")
        end = item.get("source_end")
        quantity = item.get("quantity")

        if not isinstance(start, int) or not isinstance(end, int):
            errors.append({"index": index, "code": "INVALID_INTERVAL", "message": "source_start/end must be integers."})
            continue
        if start < 0 or end <= start:
            errors.append(
                {
                    "index": index,
                    "code": "INVALID_INTERVAL",
                    "source_start": start,
                    "source_end": end,
                    "message": "Intervals must satisfy 0 <= start < end.",
                }
            )
        if isinstance(quantity, int) and quantity != end - start:
            errors.append(
                {
                    "index": index,
                    "code": "QUANTITY_INTERVAL_MISMATCH",
                    "quantity": quantity,
                    "interval_length": end - start,
                }
            )
        if batch_quantities and code in batch_quantities and end > batch_quantities[code]:
            errors.append(
                {
                    "index": index,
                    "code": "INTERVAL_OUTSIDE_BATCH",
                    "batch_code": code,
                    "source_end": end,
                    "batch_quantity": batch_quantities[code],
                }
            )
        intervals_by_batch.setdefault(code, []).append((start, end))

    for code, intervals in intervals_by_batch.items():
        ordered = sorted(intervals)
        for (prev_start, prev_end), (next_start, next_end) in zip(ordered, ordered[1:]):
            if next_start < prev_end:
                errors.append(
                    {
                        "code": "OVERLAPPING_SOURCE_INTERVALS",
                        "batch_code": code,
                        "intervals": ordered,
                    }
                )
                break

    boundary_violations = [error for error in errors if error.get("code") == "CROSS_BATCH_BOUNDARY"]
    if boundary_violations:
        raise CrossBatchBoundaryError(details={"violations": errors})
    if errors:
        raise UnprocessableError("Container allocation is invalid.", code="INVALID_ALLOCATION", details={"violations": errors})

    return {
        "valid": True,
        "no_cross_batch": len(batch_codes) == 1,
        "batch_code": next(iter(batch_codes)),
        "allocation_count": len(allocations),
        "violations": [],
    }
