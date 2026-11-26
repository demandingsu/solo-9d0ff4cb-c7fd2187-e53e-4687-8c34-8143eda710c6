from __future__ import annotations

PLAN_REQUEST_EXAMPLES = {
    "valid_two_batches": {
        "summary": "✅ 可行：两个批次，容器不混批",
        "description": "5 个容器；A=42/42/41，B=37/36，所有来源区间连续完整。",
        "value": {
            "batches": [
                {"batch_code": "B-ALPHA", "quantity": 125},
                {"batch_code": "B-BETA", "quantity": 73},
            ],
            "container_capacity": 60,
            "min_load": 25,
            "max_load": 50,
            "note": "Docs valid packing example",
        },
    },
    "infeasible_gap": {
        "summary": "❌ 无可行装箱：55 件落在箱数间隙",
        "description": "1 箱最多 50；2 箱最少 60，因此返回 422 NO_FEASIBLE_PACKING。",
        "value": {
            "batches": [{"batch_code": "B-NO-GO", "quantity": 55}],
            "container_capacity": 60,
            "min_load": 30,
            "max_load": 50,
        },
    },
    "existing_batch_ids": {
        "summary": "使用已创建批次 ID",
        "value": {
            "batch_ids": [1, 2],
            "container_capacity": 60,
            "min_load": 25,
            "max_load": 50,
        },
    },
}

CROSS_BATCH_EXAMPLE = {
    "cross_batch_rejected": {
        "summary": "❌ 跨批次容器（应返回 422）",
        "value": {
            "container_code": "BAD-MIX-01",
            "allocations": [
                {"batch_code": "B-ALPHA", "source_start": 100, "source_end": 125, "quantity": 25},
                {"batch_code": "B-BETA", "source_start": 0, "source_end": 20, "quantity": 20},
            ],
        },
    },
    "same_batch_allowed": {
        "summary": "✅ 同批次两个连续区间",
        "value": {
            "container_code": "GOOD-SPLIT-01",
            "allocations": [
                {"batch_code": "B-ALPHA", "source_start": 0, "source_end": 25, "quantity": 25},
                {"batch_code": "B-ALPHA", "source_start": 25, "source_end": 50, "quantity": 25},
            ],
        },
    },
}

BATCH_PATCH_EXAMPLES = {
    "change_quantity": {
        "summary": "修改为新数量 126",
        "value": {"quantity": 126},
    },
    "restore_quantity": {
        "summary": "恢复原数量 125，旧方案仍过期",
        "value": {"quantity": 125},
    },
    "same_value_explicit_patch": {
        "summary": "显式提交相同数量也增加 revision",
        "value": {"quantity": 125},
    },
}


LABEL_SHEET_EXAMPLES = {
    "full_sheet": {
        "summary": "整张 A4 标签纸（3 列 x 8 行，24 格全部可用）",
        "value": {"sheet_code": "A4-3x8-FULL", "available_cells": list(range(1, 25))},
    },
    "remaining_cells": {
        "summary": "只剩 2、5、7 格的半张纸",
        "value": {"sheet_code": "A4-3x8-LEFT-2-5-7", "available_cells": [2, 5, 7]},
    },
    "default_geometry": {
        "summary": "按纸号登记，缺省使用全部 24 格",
        "value": {"sheet_code": "A4-3x8-DEFAULT"},
    },
}

LABEL_IMPOSE_EXAMPLES = {
    "two_plans_one_sheet": {
        "summary": "两个已确认方案拼到同一张纸",
        "description": "按 items 顺序占入空闲格；图案见响应 page，未分配格为 null。",
        "value": {
            "sheet_code": "A4-3x8-FULL",
            "items": [
                {"plan_id": 1, "box_codes": ["BOX-P1-001", "BOX-P1-002"]},
                {"plan_id": 2, "box_codes": ["BOX-P2-001"]},
            ],
        },
    },
    "reuse_blank_cells": {
        "summary": "确认空白后，把空格与新箱码拼到同一张纸",
        "value": {
            "sheet_code": "A4-3x8-LEFT-2-5-7",
            "items": [{"plan_id": 3, "box_codes": ["BOX-P3-001"]}],
        },
    },
}

LABEL_REPORT_EXAMPLES = {
    "printed_batch": {
        "summary": "分批回报：1、2 格已印",
        "value": {
            "items": [
                {"cell_no": 1, "box_code": "BOX-P1-001", "result": "printed"},
                {"cell_no": 2, "box_code": "BOX-P1-002", "result": "printed"},
            ]
        },
    },
    "blank_release": {
        "summary": "回报第 3 格为空白，释放该格与箱码",
        "value": {"items": [{"cell_no": 3, "box_code": "BOX-P2-001", "result": "blank"}]},
    },
    "damaged_release_code": {
        "summary": "回报某格损坏：格仍占用，箱码可重排",
        "value": {"items": [{"cell_no": 5, "box_code": "BOX-P2-002", "result": "damaged"}]},
    },
}
