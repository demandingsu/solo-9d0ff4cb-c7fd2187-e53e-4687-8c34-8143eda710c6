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
