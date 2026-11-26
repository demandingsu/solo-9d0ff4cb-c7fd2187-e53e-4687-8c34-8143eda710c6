from __future__ import annotations

from typing import Any


class AppError(Exception):
    status_code = 400
    code = "BAD_REQUEST"
    message = "Request could not be processed."

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        details: Any | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message or self.message)
        if message is not None:
            self.message = message
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        self.details = details


class NotFoundError(AppError):
    status_code = 404
    code = "NOT_FOUND"
    message = "Resource not found."


class ConflictError(AppError):
    status_code = 409
    code = "CONFLICT"
    message = "Resource state conflicts with the request."


class UnprocessableError(AppError):
    status_code = 422
    code = "UNPROCESSABLE_ENTITY"
    message = "Request is semantically invalid."


class InfeasiblePackingError(UnprocessableError):
    code = "NO_FEASIBLE_PACKING"
    message = "One or more batches cannot be packed within the configured load limits."


class CrossBatchBoundaryError(UnprocessableError):
    code = "CROSS_BATCH_BOUNDARY"
    message = "A container allocation crosses a sample batch boundary."


class PlanStateError(ConflictError):
    code = "INVALID_PLAN_STATE"
    message = "The plan's current status does not allow this operation."
