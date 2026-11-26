import pytest

from app.errors import CrossBatchBoundaryError, InfeasiblePackingError
from app.packing import build_packing, validate_no_cross_batch_allocation


def test_build_packing_balances_loads_and_preserves_intervals():
    result = build_packing(
        [("B-A", 125), ("B-B", 73)],
        container_capacity=60,
        min_load=25,
        max_load=50,
    )

    assert [item.batch_code for item in result] == ["B-A", "B-B"]
    alpha, beta = result
    assert alpha.container_count == 3
    assert [load.load_quantity for load in alpha.loads] == [42, 42, 41]
    assert [(load.source_start, load.source_end) for load in alpha.loads] == [
        (0, 42),
        (42, 84),
        (84, 125),
    ]
    assert beta.container_count == 2
    assert [load.load_quantity for load in beta.loads] == [37, 36]
    assert [(load.source_start, load.source_end) for load in beta.loads] == [
        (0, 37),
        (37, 73),
    ]

    for batch in result:
        cursor = 0
        for load in batch.loads:
            assert load.source_start == cursor
            assert load.source_end == cursor + load.load_quantity
            assert 25 <= load.load_quantity <= 50
            assert load.load_quantity <= 60
            cursor = load.source_end
        assert cursor == batch.quantity


def test_infeasible_batch_reports_all_failures():
    with pytest.raises(InfeasiblePackingError) as exc_info:
        build_packing(
            [("BAD", 55)],
            container_capacity=60,
            min_load=30,
            max_load=50,
        )

    details = exc_info.value.details
    failure = details["failures"][0]
    assert failure["batch_code"] == "BAD"
    assert failure["reason"] == "NO_CONTAINER_COUNT_SATISFIES_BOUNDS"
    assert failure["minimum_container_count"] == 2
    assert failure["maximum_container_count_by_lower_limit"] == 1


def test_cross_batch_manual_allocation_is_rejected():
    with pytest.raises(CrossBatchBoundaryError):
        validate_no_cross_batch_allocation(
            [
                {"batch_code": "A", "source_start": 90, "source_end": 100, "quantity": 10},
                {"batch_code": "B", "source_start": 0, "source_end": 5, "quantity": 5},
            ]
        )


def test_same_batch_intervals_pass_boundary_validation():
    result = validate_no_cross_batch_allocation(
        [
            {"batch_code": "A", "source_start": 0, "source_end": 40, "quantity": 40},
            {"batch_code": "A", "source_start": 40, "source_end": 80, "quantity": 40},
        ],
        batch_quantities={"A": 100},
    )
    assert result["valid"] is True
    assert result["no_cross_batch"] is True
