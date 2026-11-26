"""Tests for A4 box-label imposition and per-cell reporting."""

from __future__ import annotations


def _confirmed_plan(client, batch_code: str, quantity: int = 125) -> dict:
    plan = client.post(
        "/api/plans",
        json={
            "batches": [{"batch_code": batch_code, "quantity": quantity}],
            "container_capacity": 60,
            "min_load": 25,
            "max_load": 50,
        },
    ).json()
    confirmed = client.post(f"/api/plans/{plan['id']}/confirm")
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "confirmed"
    return confirmed.json()


def _box_codes(plan: dict) -> list[str]:
    return [container["container_code"] for container in plan["containers"]]


def _register(client, sheet_code: str = "SHEET-A", cells=None):
    body = {"sheet_code": sheet_code}
    if cells is not None:
        body["available_cells"] = cells
    return client.post("/api/label-sheets", json=body)


def _impose(client, sheet_code: str, items: list[dict]):
    return client.post(
        "/api/label-layouts",
        json={"sheet_code": sheet_code, "items": items},
    )


def test_register_sheet_grid_and_available_cells(client):
    created = _register(client, "A4", cells=[2, 5, 7])
    assert created.status_code == 201, created.text
    sheet = created.json()
    assert sheet["rows"] == 8
    assert sheet["columns"] == 3
    assert sheet["total_cells"] == 24
    assert sheet["available_cells"] == [2, 5, 7]
    assert sheet["free_cells"] == [2, 5, 7]
    assert sheet["unavailable_cells"] == [n for n in range(1, 25) if n not in (2, 5, 7)]

    by_no = {cell["cell_no"]: cell for cell in sheet["cells"]}
    # Cell numbering is row-major over 3 columns.
    assert by_no[1]["row"] == 1 and by_no[1]["column"] == 1
    assert by_no[3]["row"] == 1 and by_no[3]["column"] == 3
    assert by_no[4]["row"] == 2 and by_no[4]["column"] == 1
    assert by_no[24]["row"] == 8 and by_no[24]["column"] == 3

    # Default registration uses all 24 cells.
    full = _register(client, "FULL").json()
    assert full["free_cells"] == list(range(1, 25))


def test_sheet_registration_is_idempotent_and_conflicts(client):
    assert _register(client, "S1").status_code == 201
    repeat = _register(client, "S1")
    assert repeat.status_code == 200
    assert repeat.json()["free_cells"] == list(range(1, 25))

    conflict = _register(client, "S1", cells=[1, 2])
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "LABEL_SHEET_EXISTS"


def test_sheet_registration_rejects_invalid_grid_and_cells(client):
    bad_geometry = client.post(
        "/api/label-sheets", json={"sheet_code": "B1", "rows": 4, "columns": 6}
    )
    assert bad_geometry.status_code == 422
    assert bad_geometry.json()["error"]["code"] == "REQUEST_VALIDATION_ERROR"

    assert _register(client, "B2", cells=[1, 1]).status_code == 422
    assert _register(client, "B3", cells=[0]).status_code == 422
    assert _register(client, "B4", cells=[25]).status_code == 422
    assert _register(client, "B5", cells=[]).status_code == 422


def test_impose_two_plans_leaves_unassigned_cells_blank(client):
    plan_a = _confirmed_plan(client, "B-A")
    plan_b = _confirmed_plan(client, "B-B", quantity=73)
    _register(client, "SHEET-A", cells=[2, 5, 7])

    response = _impose(
        client,
        "SHEET-A",
        [
            {"plan_id": plan_a["id"], "box_codes": _box_codes(plan_a)[:2]},
            {"plan_id": plan_b["id"], "box_codes": _box_codes(plan_b)[:1]},
        ],
    )
    assert response.status_code == 201, response.text
    layout = response.json()

    assert layout["layout_no"].startswith("LBL-")
    assert layout["placement_count"] == 3
    # Layout fills free cells in ascending cell order.
    assert [(p["cell_no"], p["status"]) for p in layout["placements"]] == [
        (2, "reserved"),
        (5, "reserved"),
        (7, "reserved"),
    ]
    assert [p["box_code"] for p in layout["placements"]] == (
        _box_codes(plan_a)[:2] + _box_codes(plan_b)[:1]
    )

    # 3 columns x 8 rows page, unassigned cells are null in place.
    page = layout["page"]
    assert len(page) == 8 and all(len(row) == 3 for row in page)
    assert page[0][1]["box_code"] == _box_codes(plan_a)[0]  # cell 2
    assert page[0][0] is None  # cell 1 stays blank
    assert page[1][1]["box_code"] == _box_codes(plan_a)[1]  # cell 5
    assert page[2][0]["box_code"] == _box_codes(plan_b)[0]  # cell 7
    assert page[2][1] is None and page[2][2] is None  # cells 8, 9 stay blank

    sheet = client.get("/api/label-sheets/SHEET-A").json()
    assert sheet["free_cells"] == []
    assert sheet["occupied_cells"] == [2, 5, 7]

    # Printable HTML page renders without escaping issues.
    printed = client.get(f"/api/label-layouts/{layout['layout_no']}/print")
    assert printed.status_code == 200
    assert printed.headers["content-type"].startswith("text/html")
    assert layout["layout_no"] in printed.text
    assert _box_codes(plan_a)[0] in printed.text


def test_impose_does_not_assign_same_cell_or_box_twice(client):
    plan = _confirmed_plan(client, "B-DUP")
    box_codes = _box_codes(plan)
    _register(client, "S", cells=[1, 2, 3])

    first = _impose(client, "S", [{"plan_id": plan["id"], "box_codes": box_codes[:2]}])
    assert first.status_code == 201
    # Second layout gets the next free cell, not an already used one.
    second = _impose(client, "S", [{"plan_id": plan["id"], "box_codes": box_codes[2:3]}])
    assert second.status_code == 201
    assert [p["cell_no"] for p in second.json()["placements"]] == [3]

    # A printed/reserved box code cannot be imposed again.
    again = _impose(client, "S", [{"plan_id": plan["id"], "box_codes": box_codes[:1]}])
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "LABEL_BOX_CODE_IN_USE"

    # The same box code twice in one request is rejected up front.
    duplicate = _impose(
        client,
        "S",
        [
            {"plan_id": plan["id"], "box_codes": box_codes[:1]},
            {"plan_id": plan["id"], "box_codes": box_codes[:1]},
        ],
    )
    assert duplicate.status_code in (409, 422)
    assert duplicate.json()["error"]["code"] in {
        "LABEL_BOX_CODE_IN_USE",
        "REQUEST_VALIDATION_ERROR",
    }


def test_impose_requires_confirmed_plan_and_valid_box_codes(client):
    plan = _confirmed_plan(client, "B-OK")
    generated = client.post(
        "/api/plans",
        json={
            "batches": [{"batch_code": "B-GEN", "quantity": 60}],
            "container_capacity": 60,
            "min_load": 20,
            "max_load": 40,
        },
    ).json()
    _register(client, "S")

    not_confirmed = _impose(
        client,
        "S",
        [{"plan_id": generated["id"], "box_codes": _box_codes(generated)[:1]}],
    )
    assert not_confirmed.status_code == 409
    assert not_confirmed.json()["error"]["code"] == "PLAN_NOT_CONFIRMED"

    wrong_plan = _impose(client, "S", [{"plan_id": plan["id"], "box_codes": ["NO-SUCH-BOX"]}])
    assert wrong_plan.status_code == 422
    assert wrong_plan.json()["error"]["code"] == "BOX_CODE_NOT_IN_PLAN"

    missing_sheet = _impose(
        client, "UNKNOWN", [{"plan_id": plan["id"], "box_codes": _box_codes(plan)[:1]}]
    )
    assert missing_sheet.status_code == 404
    assert missing_sheet.json()["error"]["code"] == "LABEL_SHEET_NOT_FOUND"


def test_expired_plan_cannot_be_imposed_after_batch_change(client):
    batch = client.post("/api/batches", json={"batch_code": "B-EXPIRE", "quantity": 125}).json()
    plan = client.post(
        "/api/plans",
        json={
            "batch_ids": [batch["id"]],
            "container_capacity": 60,
            "min_load": 25,
            "max_load": 50,
        },
    ).json()
    client.post(f"/api/plans/{plan['id']}/confirm")
    boxes = [c["container_code"] for c in plan["containers"]]
    _register(client, "S")

    client.patch(f"/api/batches/{batch['id']}", json={"quantity": 126})
    response = _impose(client, "S", [{"plan_id": plan["id"], "box_codes": boxes[:1]}])
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "PLAN_EXPIRED"
    # Nothing was reserved.
    assert client.get("/api/label-sheets/S").json()["free_cells"] == list(range(1, 25))
    assert client.get("/api/label-layouts").json() == []


def test_capacity_shortage_rejects_whole_layout_and_reserves_nothing(client):
    plan = _confirmed_plan(client, "B-CAP")
    boxes = _box_codes(plan)  # 3 boxes
    _register(client, "S", cells=[1, 2])

    response = _impose(client, "S", [{"plan_id": plan["id"], "box_codes": boxes}])
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "LABEL_SHEET_CAPACITY_EXCEEDED"
    assert error["details"]["requested_box_count"] == 3
    assert error["details"]["free_cell_count"] == 2

    # Neither cells nor box codes are consumed; the same box codes can go on a
    # bigger sheet afterwards.
    assert client.get("/api/label-sheets/S").json()["free_cells"] == [1, 2]
    assert client.get("/api/label-layouts").json() == []
    _register(client, "BIG")
    ok = _impose(client, "BIG", [{"plan_id": plan["id"], "box_codes": boxes}])
    assert ok.status_code == 201


def test_report_batches_and_exact_repeat_is_idempotent(client):
    plan = _confirmed_plan(client, "B-REP")
    boxes = _box_codes(plan)
    _register(client, "S", cells=[1, 2, 3])
    layout = _impose(client, "S", [{"plan_id": plan["id"], "box_codes": boxes}]).json()
    layout_no = layout["layout_no"]

    first = client.post(
        f"/api/label-layouts/{layout_no}/report",
        json={"items": [{"cell_no": 1, "box_code": boxes[0], "result": "printed"}]},
    )
    assert first.status_code == 200
    assert [(r["cell_no"], r["result"], r["idempotent"]) for r in first.json()["results"]] == [
        (1, "printed", False)
    ]

    second = client.post(
        f"/api/label-layouts/{layout_no}/report",
        json={"items": [{"cell_no": 2, "box_code": boxes[1], "result": "blank"}]},
    )
    assert second.status_code == 200
    sheet = second.json()["sheet"]
    assert sheet["occupied_cells"] == [1, 3]
    assert sheet["free_cells"] == [2]

    # Exact re-report of the first batch returns the first result unchanged.
    repeat = client.post(
        f"/api/label-layouts/{layout_no}/report",
        json={"items": [{"cell_no": 1, "box_code": boxes[0], "result": "printed"}]},
    )
    assert repeat.status_code == 200
    assert repeat.json()["results"][0]["result"] == "printed"
    assert repeat.json()["results"][0]["idempotent"] is True


def test_changing_a_reported_result_rejects_the_whole_report(client):
    plan = _confirmed_plan(client, "B-CONF")
    boxes = _box_codes(plan)
    _register(client, "S", cells=[1, 2, 3])
    layout = _impose(client, "S", [{"plan_id": plan["id"], "box_codes": boxes}]).json()
    layout_no = layout["layout_no"]

    client.post(
        f"/api/label-layouts/{layout_no}/report",
        json={"items": [{"cell_no": 1, "box_code": boxes[0], "result": "printed"}]},
    )

    # One item conflicts -> the otherwise-valid cell 2 item must not be written.
    conflict = client.post(
        f"/api/label-layouts/{layout_no}/report",
        json={
            "items": [
                {"cell_no": 1, "box_code": boxes[0], "result": "damaged"},
                {"cell_no": 2, "box_code": boxes[1], "result": "printed"},
            ]
        },
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "LABEL_REPORT_CONFLICT"

    layout_after = client.get(f"/api/label-layouts/{layout_no}").json()
    status_by_cell = {p["cell_no"]: p["status"] for p in layout_after["placements"]}
    assert status_by_cell[1] == "printed"
    assert status_by_cell[2] == "reserved"


def test_report_validates_cell_and_box_code(client):
    plan = _confirmed_plan(client, "B-VAL")
    boxes = _box_codes(plan)
    _register(client, "S", cells=[1, 2])
    layout = _impose(client, "S", [{"plan_id": plan["id"], "box_codes": boxes[:2]}]).json()

    wrong_code = client.post(
        f"/api/label-layouts/{layout['layout_no']}/report",
        json={"items": [{"cell_no": 1, "box_code": "WRONG", "result": "printed"}]},
    )
    assert wrong_code.status_code == 422
    assert wrong_code.json()["error"]["code"] == "LABEL_BOX_CODE_MISMATCH"

    wrong_cell = client.post(
        f"/api/label-layouts/{layout['layout_no']}/report",
        json={"items": [{"cell_no": 9, "box_code": boxes[0], "result": "printed"}]},
    )
    assert wrong_cell.status_code == 422
    assert wrong_cell.json()["error"]["code"] == "LABEL_CELL_NOT_IN_LAYOUT"


def test_blank_cell_and_box_are_reusable_immediately(client):
    plan_a = _confirmed_plan(client, "B-FREE-A")
    plan_b = _confirmed_plan(client, "B-FREE-B")
    boxes_a = _box_codes(plan_a)
    boxes_b = _box_codes(plan_b)
    _register(client, "S", cells=[1, 2])
    layout_a = _impose(client, "S", [{"plan_id": plan_a["id"], "box_codes": boxes_a[:2]}]).json()

    # Confirm cell 1 blank: both the cell and its box code are released.
    client.post(
        f"/api/label-layouts/{layout_a['layout_no']}/report",
        json={"items": [{"cell_no": 1, "box_code": boxes_a[0], "result": "blank"}]},
    )
    sheet = client.get("/api/label-sheets/S").json()
    assert sheet["free_cells"] == [1]
    assert sheet["occupied_cells"] == [2]

    # The freed cell can be used by another plan right away.
    reuse_cell = _impose(client, "S", [{"plan_id": plan_b["id"], "box_codes": boxes_b[:1]}])
    assert reuse_cell.status_code == 201
    assert [p["cell_no"] for p in reuse_cell.json()["placements"]] == [1]

    # The released box code can also be re-imposed on another free cell.
    _register(client, "S2", cells=[5])
    reuse_code = _impose(client, "S2", [{"plan_id": plan_a["id"], "box_codes": boxes_a[:1]}])
    assert reuse_code.status_code == 201
    assert [p["box_code"] for p in reuse_code.json()["placements"]] == [boxes_a[0]]
    assert reuse_code.json()["placements"][0]["reprinted"] is True


def test_damaged_cell_keeps_cell_but_releases_box_code(client):
    plan = _confirmed_plan(client, "B-DMG")
    boxes = _box_codes(plan)
    _register(client, "S", cells=[1, 2])
    layout = _impose(client, "S", [{"plan_id": plan["id"], "box_codes": boxes[:2]}]).json()

    damaged = client.post(
        f"/api/label-layouts/{layout['layout_no']}/report",
        json={"items": [{"cell_no": 1, "box_code": boxes[0], "result": "damaged"}]},
    )
    assert damaged.status_code == 200
    sheet = damaged.json()["sheet"]
    # Damaged cell still occupies its physical cell, but not the box code.
    assert sheet["occupied_cells"] == [1, 2]
    assert sheet["free_cells"] == []
    cell_one = next(cell for cell in sheet["cells"] if cell["cell_no"] == 1)
    assert cell_one["placement_status"] == "damaged"

    # The box code is free to be reprinted on another sheet.
    _register(client, "S2", cells=[9])
    reprint = _impose(client, "S2", [{"plan_id": plan["id"], "box_codes": boxes[:1]}])
    assert reprint.status_code == 201
    assert reprint.json()["placements"][0]["reprinted"] is True


def test_printed_cell_and_box_stay_occupied(client):
    plan = _confirmed_plan(client, "B-PRT")
    boxes = _box_codes(plan)
    _register(client, "S", cells=[1, 2])
    layout = _impose(client, "S", [{"plan_id": plan["id"], "box_codes": boxes[:2]}]).json()
    client.post(
        f"/api/label-layouts/{layout['layout_no']}/report",
        json={"items": [{"cell_no": 1, "box_code": boxes[0], "result": "printed"}]},
    )

    sheet = client.get("/api/label-sheets/S").json()
    assert sheet["free_cells"] == []
    assert sheet["occupied_cells"] == [1, 2]

    # Printed cell cannot be reused, and the printed box code cannot be re-imposed.
    blocked = _impose(client, "S", [{"plan_id": plan["id"], "box_codes": [boxes[0]]}])
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "LABEL_BOX_CODE_IN_USE"


def test_one_layout_cannot_take_a_cell_from_another_layout(client):
    plan_a = _confirmed_plan(client, "B-L1")
    plan_b = _confirmed_plan(client, "B-L2")
    boxes_a = _box_codes(plan_a)
    boxes_b = _box_codes(plan_b)
    _register(client, "S", cells=[1, 2])

    first = _impose(client, "S", [{"plan_id": plan_a["id"], "box_codes": boxes_a[:1]}]).json()
    second = _impose(client, "S", [{"plan_id": plan_b["id"], "box_codes": boxes_b[:1]}]).json()
    assert [p["cell_no"] for p in first["placements"]] == [1]
    assert [p["cell_no"] for p in second["placements"]] == [2]

    # A layout cannot reach into the other layout's cell even if it names it.
    sheet = client.get("/api/label-sheets/S").json()
    ownership = {
        cell["cell_no"]: cell["layout_no"] for cell in sheet["cells"] if cell["state"] == "occupied"
    }
    assert ownership == {1: first["layout_no"], 2: second["layout_no"]}
