from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse

from . import __version__
from .database import init_db
from .errors import AppError
from .routers import batches, boundary, demo, plans


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="样品拆分与装箱 API",
    version=__version__,
    description="""
使用 FastAPI + SQLite 实现样品批次拆分、来源区间保留、批次边界保护和方案版本失效。

## 核心规则

1. 按批次独立装箱，**同一容器不得跨批次**。
2. 每个装箱段保留批次内半开来源区间 `[source_start, source_end)`，相邻区间连续、无重叠、完整覆盖批次数量。
3. 每箱装量必须满足 `min_load <= load <= max_load <= container_capacity`。
4. 任一批次被显式修改后，包含该批次的未出库方案立即变为 `expired`；即使数量恢复原值，批次 `revision` 已增加，旧方案仍禁止确认和出库。
5. 方案只有处于 `confirmed` 状态才能调用出库接口。

## 在 /docs 中演示

- `POST /api/plans` 的请求示例包含 **No feasible packing** 和 **Valid multi-batch** 场景。
- `POST /api/boundary/validate-container` 可直接执行跨批次容器示例并观察 `422 CROSS_BATCH_BOUNDARY`。
- `POST /api/demo/run` 一键构造临时批次，演示无可行装箱、跨批次拒绝、修改—恢复数量后旧方案失效及出库拦截。
- `GET /api/demo/guide` 返回手工点击步骤。
""".strip(),
    lifespan=lifespan,
    openapi_tags=[
        {"name": "样品批次", "description": "批次提交、查询和修改；修改会使旧方案过期。"},
        {"name": "装箱方案", "description": "生成、查询、确认、出库和边界核对。"},
        {"name": "批次边界校验", "description": "手工验证容器分配是否跨越批次边界。"},
        {"name": "Docs 一键演示", "description": "可直接在 Swagger UI 中运行的演示与指引。"},
    ],
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(batches.router)
app.include_router(plans.router)
app.include_router(boundary.router)
app.include_router(demo.router)


@app.exception_handler(AppError)
async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "detail": exc.message,
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
            },
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "detail": jsonable_encoder(exc.errors()),
            "error": {
                "code": "REQUEST_VALIDATION_ERROR",
                "message": "Request validation failed.",
                "details": jsonable_encoder(exc.errors()),
            },
        },
    )


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/docs")


@app.get("/health", tags=["健康检查"], summary="健康检查和 SQLite 连通性")
def health() -> dict:
    from .database import connect

    conn = connect()
    try:
        conn.execute("SELECT 1").fetchone()
    finally:
        conn.close()
    return {"status": "ok", "database": "sqlite", "version": __version__, "docs": "/docs"}
