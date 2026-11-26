def test_health_and_openapi(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"

    docs = client.get("/docs")
    assert docs.status_code == 200

    openapi = client.get("/openapi.json")
    assert openapi.status_code == 200
    paths = openapi.json()["paths"]
    assert "/api/plans" in paths
    assert "/api/plans/{plan_id}/confirm" in paths
    assert "/api/plans/{plan_id}/outbound" in paths


def test_generate_plan_never_crosses_batches_and_preserves_intervals(client):
    response = client.post(
        "/api/plans",
        json={
            "batches": [
                {"batch_code": "B-ALPHA", "quantity": 125},
                {"batch_code": "B-BETA", "quantity": 73},
            ],
            "container_capacity": 60,
            "min_load": 25,
            "max_load": 50,
        },
    )
    assert response.status_code == 201, response.text
    plan = response.json()

    assert plan["status"] == "generated"
    assert plan["outbound_allowed"] is False
    assert len(plan["containers"]) == 5

    quantities_by_batch = {"B-ALPHA": 0, "B-BETA": 0}
    for container in plan["containers"]:
        assert container["batch_code"] in quantities_by_batch
        assert len(container["allocations"]) == 1
        allocation = container["allocations"][0]
        assert allocation["batch_code"] == container["batch_code"]
        assert allocation["batch_id"] == container["batch_id"]
        assert allocation["source_start"] == container["source_start"]
        assert allocation["source_end"] == container["source_end"]
        assert allocation["quantity"] == container["load_quantity"]
        assert 25 <= container["load_quantity"] <= 50
        assert container["load_quantity"] <= 60
        quantities_by_batch[container["batch_code"]] += container["load_quantity"]

    assert quantities_by_batch == {"B-ALPHA": 125, "B-BETA": 73}

    beta_containers = [c for c in plan["containers"] if c["batch_code"] == "B-BETA"]
    assert [(c["global_start"], c["global_end"]) for c in beta_containers] == [
        (125, 162),
        (162, 198),
    ]

    check = client.get(f"/api/plans/{plan['id']}/boundary-check")
    assert check.status_code == 200
    body = check.json()
    assert body["valid"] is True
    assert body["no_cross_batch"] is True
    assert all(body["checks"].values())


def test_infeasible_packing_returns_422_and_no_plan(client):
    response = client.post(
        "/api/plans",
        json={
            "batches": [{"batch_code": "B-NO-GO", "quantity": 55}],
            "container_capacity": 60,
            "min_load": 30,
            "max_load": 50,
        },
    )
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "NO_FEASIBLE_PACKING"
    assert error["details"]["failures"][0]["batch_code"] == "B-NO-GO"

    plans = client.get("/api/plans").json()
    assert plans == []


def test_cross_batch_validation_endpoint_rejects_mix(client):
    response = client.post(
        "/api/boundary/validate-container",
        json={
            "container_code": "BAD-MIX-01",
            "allocations": [
                {"batch_code": "A", "source_start": 100, "source_end": 125, "quantity": 25},
                {"batch_code": "B", "source_start": 0, "source_end": 20, "quantity": 20},
            ],
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "CROSS_BATCH_BOUNDARY"


def test_outbound_requires_confirmation_then_succeeds(client):
    batch = client.post("/api/batches", json={"batch_code": "B-FLOW", "quantity": 80}).json()
    plan = client.post(
        "/api/plans",
        json={
            "batch_ids": [batch["id"]],
            "container_capacity": 50,
            "min_load": 20,
            "max_load": 40,
        },
    ).json()

    before_confirm = client.post(f"/api/plans/{plan['id']}/outbound", json={})
    assert before_confirm.status_code == 409
    assert before_confirm.json()["error"]["code"] == "PLAN_NOT_CONFIRMED"

    confirmed = client.post(f"/api/plans/{plan['id']}/confirm")
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "confirmed"
    assert confirmed.json()["outbound_allowed"] is True

    outbound = client.post(
        f"/api/plans/{plan['id']}/outbound",
        json={"reference": "OUT-1", "operator": "tester"},
    )
    assert outbound.status_code == 200, outbound.text
    body = outbound.json()
    assert body["status"] == "outbound"
    assert body["outbound"]["reference"] == "OUT-1"

    repeated = client.post(f"/api/plans/{plan['id']}/outbound", json={})
    assert repeated.status_code == 409
    assert repeated.json()["error"]["code"] == "PLAN_ALREADY_OUTBOUND"


def test_modifying_batch_even_same_quantity_expires_plan(client):
    batch = client.post("/api/batches", json={"batch_code": "B-SAME", "quantity": 125}).json()
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

    patched = client.patch(f"/api/batches/{batch['id']}", json={"quantity": 125})
    assert patched.status_code == 200
    patched_body = patched.json()
    assert patched_body["revision"] == 2
    assert patched_body["expired_plan_count"] == 1

    queried = client.get(f"/api/plans/{plan['id']}").json()
    assert queried["status"] == "expired"
    assert queried["is_expired"] is True
    assert queried["outbound_allowed"] is False
    assert queried["batches"][0]["differs_from_current"] is True

    confirm = client.post(f"/api/plans/{plan['id']}/confirm")
    assert confirm.status_code == 409
    assert confirm.json()["error"]["code"] == "PLAN_EXPIRED"

    outbound = client.post(f"/api/plans/{plan['id']}/outbound", json={})
    assert outbound.status_code == 409
    assert outbound.json()["error"]["code"] == "PLAN_EXPIRED"


def test_quantity_away_and_restored_still_invalidates_each_old_plan(client):
    batch = client.post("/api/batches", json={"batch_code": "B-RESTORE", "quantity": 125}).json()

    plan_v1 = client.post(
        "/api/plans",
        json={
            "batch_ids": [batch["id"]],
            "container_capacity": 60,
            "min_load": 25,
            "max_load": 50,
        },
    ).json()

    changed = client.patch(f"/api/batches/{batch['id']}", json={"quantity": 126})
    assert changed.json()["revision"] == 2
    assert client.get(f"/api/plans/{plan_v1['id']}").json()["status"] == "expired"

    plan_v2 = client.post(
        "/api/plans",
        json={
            "batch_ids": [batch["id"]],
            "container_capacity": 60,
            "min_load": 25,
            "max_load": 50,
        },
    ).json()
    assert plan_v2["batches"][0]["quantity"] == 126
    assert plan_v2["batches"][0]["revision"] == 2

    restored = client.patch(f"/api/batches/{batch['id']}", json={"quantity": 125})
    assert restored.json()["revision"] == 3

    plan_v2_body = client.get(f"/api/plans/{plan_v2['id']}").json()
    assert plan_v2_body["status"] == "expired"
    blocked = client.post(f"/api/plans/{plan_v2['id']}/outbound", json={})
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "PLAN_EXPIRED"


def test_demo_one_click_scenario_passes(client):
    response = client.post("/api/demo/run")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["all_passed"] is True
    assert all(body["checks"].values())
