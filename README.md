# 样品拆分与装箱 API

基于 **FastAPI + SQLite** 的样品批次拆分服务。系统按批次独立生成装箱方案，保证：

- 容器不跨越样品批次边界；
- 每个装箱段保留批次内来源区间 `[source_start, source_end)`；
- 每箱装量满足 `min_load <= load <= max_load <= container_capacity`；
- 方案必须先确认，之后才能出库；
- 任一批次被显式修改后，包含该批次的未出库方案立即过期；
- 即使数量改回原值，批次 `revision` 已增加，旧方案仍禁止确认和出库。

## 1. Docker Compose 启动

```bash
docker compose up --build
```

启动后访问：

- Swagger UI：<http://localhost:8000/docs>
- ReDoc：<http://localhost:8000/redoc>
- 健康检查：<http://localhost:8000/health>

SQLite 数据库保存在 Compose 命名卷 `sample_splitting_data` 中的 `/app/data/packing.db`。

停止：

```bash
docker compose down
```

删除数据库卷并重置演示数据：

```bash
docker compose down -v
```

## 2. 本地开发与自测

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt

# 初始化并启动
uvicorn app.main:app --reload --port 8000

# 自测
pytest -q

# 启动真实 Uvicorn 后，可另开终端运行无第三方依赖的 HTTP 烟测
uvicorn app.main:app --port 8000
python scripts/smoke_check.py
```

也可以启动后运行一键场景接口：

```bash
curl -X POST http://localhost:8000/api/demo/run
```

该接口会创建带随机后缀的演示批次，并返回无可行装箱、跨批次拒绝、修改—恢复数量后旧方案失效、确认/出库拦截等检查结果。

## 3. 核心接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/batches` | 创建单个批次 |
| `POST` | `/api/batches/bulk` | 批量创建批次 |
| `GET` | `/api/batches` | 查询批次及 revision |
| `GET` | `/api/batches/{batch_id}` | 查询单个批次 |
| `PATCH` / `PUT` | `/api/batches/{batch_id}` | 修改批次；每次显式修改增加 revision 并过期相关方案 |
| `PATCH` | `/api/batches/by-code/{batch_code}/update` | 按批次号修改，方便 Swagger 演示 |
| `POST` | `/api/plans` | 生成装箱方案 |
| `GET` | `/api/plans` | 查询方案列表，可按 `batch_id`、`status` 过滤 |
| `GET` | `/api/plans/{plan_id}` | 查询方案详情、容器和来源区间 |
| `POST` | `/api/plans/{plan_id}/confirm` | 确认方案 |
| `POST` | `/api/plans/{plan_id}/outbound` | 方案确认后出库 |
| `GET` | `/api/plans/{plan_id}/boundary-check` | 核对不跨批、区间连续完整、装量合法 |
| `POST` | `/api/boundary/validate-container` | 校验手工容器是否跨批次 |
| `POST` | `/api/label-sheets` | 按纸号登记 3 列 x 8 行的 A4 标签纸及可用格 |
| `GET` | `/api/label-sheets`、`/{sheet_code}` | 查询标签纸的可用、空闲、占用和不可用格 |
| `POST` | `/api/label-layouts` | 提交已确认方案的箱码，生成带版面号的可打印版面 |
| `GET` | `/api/label-layouts`、`/{layout_no}` | 查询版面和逐格分配 |
| `GET` | `/api/label-layouts/{layout_no}/print` | 打开可直接打印的 A4 HTML 页面 |
| `POST` | `/api/label-layouts/{layout_no}/report` | 打印后按格分次回报结果 |
| `POST` | `/api/demo/run` | 一键运行三个核心演示场景 |
| `GET` | `/api/demo/guide` | 返回 `/docs` 手工演示步骤 |

## 4. 装箱规则

当前版本以**正整数样品单位**计数。对于数量 `q`、下限 `L`、上限 `U`：

1. 校验 `0 <= L <= U <= container_capacity`。
2. 最少需要的箱数为 `k = ceil(q / U)`。
3. 如果 `k * L > q`，则不存在满足上下限的箱数，返回 `422 NO_FEASIBLE_PACKING`。
4. 否则把 `q` 均衡拆到 `k` 箱：`base = q // k`，前 `q % k` 箱多 1 件。
5. 按顺序切分批次来源区间。例如数量 125 拆成 `42 / 42 / 41`：

```text
[0, 42)  [42, 84)  [84, 125)
```

半开区间相邻端点相同但不重叠，能够连续、无缺口地覆盖整个批次。响应还提供 `global_start` / `global_end`，即按批次提交顺序累计的全局来源坐标；例如第一批 125 件时，第二批的全局区间从 125 开始，但容器仍不会跨越 125 这个批次边界。

## 5. 在 `/docs` 中演示三个关键点

### 5.1 无可行装箱

打开 `POST /api/plans` → **Try it out** → 选择示例 **“❌ 无可行装箱：55 件落在箱数间隙”** → Execute。

请求：

```json
{
  "batches": [{"batch_code": "B-NO-GO", "quantity": 55}],
  "container_capacity": 60,
  "min_load": 30,
  "max_load": 50
}
```

结果：

```text
422 NO_FEASIBLE_PACKING
1 箱最多装 50；2 箱最少装 60，因此没有可行箱数。
```

失败时不会创建方案，`GET /api/plans` 仍为空或不包含失败请求。

### 5.2 不跨批次边界并保留来源区间

在同一个 `POST /api/plans` 中选择 **“✅ 可行：两个批次，容器不混批”**。

响应中每个 `containers[]`：

- 只有一个 `batch_id` / `batch_code`；
- `allocations[]` 属于同一批次；
- 包含 `source_start`、`source_end`、`quantity`；
- 所有容器数量之和等于对应批次数量。

随后调用：

```http
GET /api/plans/{plan_id}/boundary-check
```

应返回：

```json
{
  "valid": true,
  "no_cross_batch": true,
  "checks": {
    "single_batch_per_container": true,
    "source_intervals_contiguous_and_complete": true
  }
}
```

也可以在 `POST /api/boundary/validate-container` 中执行跨批次反例，接口返回：

```text
422 CROSS_BATCH_BOUNDARY
```

### 5.3 修改后旧方案失效，即使数量恢复

可直接调用 `POST /api/demo/run` 查看完整自动核对；也可手工执行：

1. `POST /api/batches` 创建批次，例如数量 `125`。
2. `POST /api/plans`，使用 `batch_ids` 生成方案。
3. 可选：调用 `POST /api/plans/{plan_id}/confirm`，观察已确认方案被修改后也会过期。
4. `PATCH /api/batches/{batch_id}`：

   ```json
   {"quantity": 126}
   ```

5. `GET /api/plans/{plan_id}`，看到：

   ```json
   {
     "status": "expired",
     "is_expired": true,
     "stale": true,
     "outbound_allowed": false
   }
   ```

6. 可按数量 `126` 生成替代方案。
7. 再把数量恢复为 `125`：

   ```json
   {"quantity": 125}
   ```

8. 查询替代方案，仍为 `expired`；调用确认或出库接口均返回 `409 PLAN_EXPIRED`。

原因是方案保存了生成时的批次 `revision` 快照。第一次创建时 revision 为 1；改成 126 后为 2；恢复成 125 后为 3。数量值虽然恢复，revision 没有恢复，因此旧快照仍判定为过期。

## 6. 出库状态机

```text
generated --confirm--> confirmed --outbound--> outbound
    |                     |
    +---- batch change ---+--> expired
```

- `generated` 直接出库：`409 PLAN_NOT_CONFIRMED`
- `expired` 确认或出库：`409 PLAN_EXPIRED`
- `confirmed` 出库：成功，状态变为 `outbound`
- 重复出库：`409 PLAN_ALREADY_OUTBOUND`

已出库记录作为历史事实保留；后续批次修改不会把历史出库记录改写成未出库。当前失效操作针对仍处于 `generated` / `confirmed` 的方案。

## 7. 箱签拼版

库管手里可能只剩一张 A4 标签纸上的若干空格。该功能把不同分装单的箱签拼到同一张纸上打印，并逐格跟踪打印结果。

### 7.1 纸张与格号

- 标签纸按纸号登记，固定等分为 **3 列 x 8 行 = 24 格**。
- 格号从 1 开始，按行优先编号：第 1 行是 1–3，第 2 行是 4–6，依此类推。
- `available_cells` 只登记当前仍可用的格；省略表示整张 24 格都可用。
- 同一纸号重复登记相同几何和可用格按幂等成功处理；内容不同返回 `409 LABEL_SHEET_EXISTS`。

```http
POST /api/label-sheets
{"sheet_code": "A4-3x8-LEFT", "available_cells": [2, 5, 7]}
```

`GET /api/label-sheets/{sheet_code}` 返回每格状态：`free`（空闲）、`occupied`（被某版面占用）、`unavailable`（实体不可用）。

### 7.2 拼版

```http
POST /api/label-layouts
{
  "sheet_code": "A4-3x8-LEFT",
  "items": [
    {"plan_id": 1, "box_codes": ["BOX-P1-001", "BOX-P1-002"]},
    {"plan_id": 2, "box_codes": ["BOX-P2-001"]}
  ]
}
```

- 只接受**已确认**方案的箱码；未确认返回 `409 PLAN_NOT_CONFIRMED`，过期返回 `409 PLAN_EXPIRED`。
- 箱码必须属于所声明的 `plan_id`，否则 `422 BOX_CODE_NOT_IN_PLAN`。
- 按 `items` 顺序占入纸上升序的空闲格，返回 `layout_no`（如 `LBL-000001`）、`page`（8 行 x 3 列网格，未分配格为 `null`，原位留白）和 `printable_page` 文本预览。
- 空闲格不足时返回 `422 LABEL_SHEET_CAPACITY_EXCEEDED`，**整版退回**：不创建版面，纸位和箱码都不占用。
- 同一个格或箱码不能同时分给两版；已占用的箱码返回 `409 LABEL_BOX_CODE_IN_USE`。
- 写入前用 `BEGIN IMMEDIATE` 加锁，避免并发拼版抢到同一格或同一箱码。

`GET /api/label-layouts/{layout_no}/print` 返回可直接打印的 A4 HTML 页面。

### 7.3 分次回报

```http
POST /api/label-layouts/LBL-000001/report
{"items": [{"cell_no": 2, "box_code": "BOX-P1-001", "result": "printed"}]}
```

`result` 取值和占用语义：

| 结果 | 格 | 箱码 |
| --- | --- | --- |
| `reserved`（未回报，默认） | 继续占用 | 继续占用 |
| `printed` | 永久占用 | 永久占用 |
| `blank` | **立即释放** | **立即释放** |
| `damaged` | 继续占用 | **释放，可重排** |

- 原样重报（同版同格同结果）返回首次结果，`idempotent=true`，不做改动。
- 同版同格改报不同结果时整次拒绝：`409 LABEL_REPORT_CONFLICT`，本次所有项都不写入。
- 回报中的 `box_code` 用于核对，必须与该格一致，否则 `422 LABEL_BOX_CODE_MISMATCH`。
- 释放的格可立即给别的分装单拼版；释放的箱码可在别处重排，响应中 `reprinted=true` 提示这是补印。

### 7.4 自测场景

`/docs` 中可手工走完：

1. 生成并确认两份方案，复制 `containers[].container_code` 作为箱码。
2. 登记只剩 2、5、7 格的半张纸。
3. 拼版两份方案，查看 `page` 中未分配格为 `null`。
4. 打开 `/print` 页面打印。
5. 分批回报：先报 2 格 `printed`，再报 5 格 `blank`。
6. 再拼一版，把释放出来的第 5 格给另一份方案，`GET /api/label-sheets/{sheet_code}` 验证续用。

## 8. 数据模型

- `batches`：批次号、当前数量、单调递增 `revision`
- `packing_plans`：方案状态、装箱参数、确认/出库/过期时间
- `plan_batches`：方案生成时的批次数量和 revision 快照
- `containers`：容器、唯一所属批次、装量和来源区间
- `allocations`：细粒度来源区间分配
- `outbound_records`：出库参考号、操作人、备注和时间
- `label_sheets` / `label_sheet_cells`：按纸号登记的纸张和 24 个格的可用地
- `label_layouts`：拼版版面号、纸张和创建时间
- `label_placements`：每格的版面、方案、箱码和 `reserved`/`printed`/`blank`/`damaged` 状态
  - 部分唯一索引 `uq_label_cell_active`：同一格在 `reserved/printed/damaged` 时只能属于一个版面；
  - 部分唯一索引 `uq_label_box_active`：同一箱码在 `reserved/printed` 时只能属于一个版面。`blank`/`damaged` 后释放。

## 9. 已完成的自测

在开发环境中已执行：

```text
27 passed in ...
```

覆盖：

- 装箱数量、上下限、容量和连续区间；
- 无可行装箱；
- 跨批次分配拒绝；
- FastAPI `/docs` 与 OpenAPI；
- 方案生成和边界核对；
- 未确认禁止出库、确认后出库；
- 相同数量 PATCH 也使方案过期；
- 数量修改后再恢复仍使替代方案过期；
- 一键 demo 全部检查通过；
- 标签纸 3x8 网格登记、幂等登记和非法格；
- 拼版占格顺序、未分配格留白、可打印 HTML；
- 未确认/过期方案禁止拼版、箱码不属于方案；
- 空格不足整版退回且不占纸位/箱码；
- 同一格或箱码不重复分配（含并发单格争用只有一个成功）；
- 分批回报、原样重报幂等、改报冲突整次拒绝；
- `blank` 格与箱码立即续用、`damaged` 只释放箱码、`printed` 永久占用。

另外已用真实 Uvicorn HTTP 服务运行 `scripts/smoke_check.py`：25 项端到端检查全部通过。当前执行环境没有安装 `docker` / `docker compose`，因此 Compose 文件只做了 YAML 解析检查，尚未在该环境实际执行 `docker compose up --build`。
