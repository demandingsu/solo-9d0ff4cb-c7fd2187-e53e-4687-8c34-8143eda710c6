#!/usr/bin/env python3
"""Dependency-free HTTP smoke test for the sample splitting API."""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

BASE = sys.argv[1] if len(sys.argv) > 1 else os.getenv("BASE_URL", "http://127.0.0.1:8000")
SUFFIX = uuid.uuid4().hex[:8]


def call(method: str, path: str, payload=None, parse_json: bool = True):
    data = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            raw = response.read().decode()
            return response.status, json.loads(raw) if parse_json else raw
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        return exc.code, json.loads(raw) if parse_json else raw


for _ in range(50):
    try:
        status, health = call("GET", "/health")
        if status == 200:
            break
    except Exception:
        time.sleep(0.2)
else:
    raise SystemExit(f"API at {BASE} did not become healthy")

checks: list[tuple[str, bool]] = []

status, docs = call("GET", "/docs", parse_json=False)
checks.append(("swagger_docs", status == 200 and "Swagger UI" in docs))

status, spec = call("GET", "/openapi.json")
media = spec["paths"]["/api/plans"]["post"]["requestBody"]["content"]["application/json"]
checks.append((
    "openapi_examples",
    status == 200
    and set(media.get("examples", {}))
    == {"valid_two_batches", "infeasible_gap", "existing_batch_ids"},
))

status, infeasible = call("POST", "/api/plans", {
    "batches": [{"batch_code": f"SMOKE-NOGO-{SUFFIX}", "quantity": 55}],
    "container_capacity": 60,
    "min_load": 30,
    "max_load": 50,
})
checks.append((
    "infeasible_422",
    status == 422 and infeasible["error"]["code"] == "NO_FEASIBLE_PACKING" and bool(infeasible.get("detail")),
))

status, plan = call("POST", "/api/plans", {
    "batches": [
        {"batch_code": f"SMOKE-A-{SUFFIX}", "quantity": 125},
        {"batch_code": f"SMOKE-B-{SUFFIX}", "quantity": 73},
    ],
    "container_capacity": 60,
    "min_load": 25,
    "max_load": 50,
})
assert status == 201, plan
checks.append(("plan_created", len(plan["containers"]) == 5))
checks.append((
    "no_cross_batch_shape",
    all(
        len(container["allocations"]) == 1
        and container["batch_code"] == container["allocations"][0]["batch_code"]
        for container in plan["containers"]
    ),
))

status, boundary = call("GET", f"/api/plans/{plan['id']}/boundary-check")
checks.append(("boundary_check", status == 200 and boundary["valid"] is True))

status, cross = call("POST", "/api/boundary/validate-container", {
    "allocations": [
        {"batch_code": "A", "source_start": 100, "source_end": 125, "quantity": 25},
        {"batch_code": "B", "source_start": 0, "source_end": 20, "quantity": 20},
    ],
})
checks.append((
    "cross_batch_rejected",
    status == 422 and cross["error"]["code"] == "CROSS_BATCH_BOUNDARY",
))

status, batch = call("POST", "/api/batches", {
    "batch_code": f"SMOKE-FLOW-{SUFFIX}",
    "quantity": 80,
})
assert status == 201, batch
status, flow_plan = call("POST", "/api/plans", {
    "batch_ids": [batch["id"]],
    "container_capacity": 50,
    "min_load": 20,
    "max_load": 40,
})
assert status == 201, flow_plan
flow_id = flow_plan["id"]

status, early = call("POST", f"/api/plans/{flow_id}/outbound", {})
checks.append((
    "outbound_requires_confirm",
    status == 409 and early["error"]["code"] == "PLAN_NOT_CONFIRMED",
))
status, confirmed = call("POST", f"/api/plans/{flow_id}/confirm")
checks.append(("confirm_ok", status == 200 and confirmed["status"] == "confirmed"))
status, shipped = call("POST", f"/api/plans/{flow_id}/outbound", {"reference": f"OUT-{SUFFIX}"})
checks.append(("outbound_ok", status == 200 and shipped["status"] == "outbound"))

status, stale_batch = call("POST", "/api/batches", {
    "batch_code": f"SMOKE-STALE-{SUFFIX}",
    "quantity": 125,
})
assert status == 201, stale_batch
status, plan_v1 = call("POST", "/api/plans", {
    "batch_ids": [stale_batch["id"]],
    "container_capacity": 60,
    "min_load": 25,
    "max_load": 50,
})
assert status == 201, plan_v1

status, changed = call("PATCH", f"/api/batches/{stale_batch['id']}", {"quantity": 126})
assert status == 200, changed
status, plan_v1_after = call("GET", f"/api/plans/{plan_v1['id']}")
checks.append((
    "change_expires",
    plan_v1_after["status"] == "expired" and plan_v1_after["stale"] is True,
))

status, plan_v2 = call("POST", "/api/plans", {
    "batch_ids": [stale_batch["id"]],
    "container_capacity": 60,
    "min_load": 25,
    "max_load": 50,
})
assert status == 201, plan_v2
status, restored = call("PATCH", f"/api/batches/{stale_batch['id']}", {"quantity": 125})
checks.append(("restore_increments_revision", status == 200 and restored["revision"] == 3))

status, plan_v2_after = call("GET", f"/api/plans/{plan_v2['id']}")
status, confirm_blocked = call("POST", f"/api/plans/{plan_v2['id']}/confirm")
status, outbound_blocked = call("POST", f"/api/plans/{plan_v2['id']}/outbound", {})
checks.append((
    "restore_and_blocks",
    plan_v2_after["status"] == "expired"
    and confirm_blocked["error"]["code"] == "PLAN_EXPIRED"
    and outbound_blocked["error"]["code"] == "PLAN_EXPIRED",
))

status, demo = call("POST", "/api/demo/run")
checks.append(("demo_run_all_passed", status == 200 and demo["all_passed"] is True))

width = max(len(name) for name, _ in checks)
for name, passed in checks:
    print(f"{'PASS' if passed else 'FAIL'}  {name.ljust(width)}")

if not all(passed for _, passed in checks):
    raise SystemExit(1)
print(f"\n{len(checks)} live HTTP checks passed against {BASE}")
