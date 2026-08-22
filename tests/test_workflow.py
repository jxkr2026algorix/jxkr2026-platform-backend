"""운영 절차 강제 — 승인·연락·보고의 순서가 지켜지는가."""

from __future__ import annotations

import pytest

APPROVER = {"Authorization": "Bearer test-ap"}
FIELD = {"Authorization": "Bearer test-fd"}


async def _incident(client) -> str:
    response = await client.post(
        "/api/v1/incidents",
        json={"title": "테스트 상황", "region_code": "47750", "hazard": "landslide", "level": 2},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _plan(client, incident_id: str, communities, shelter_id=None) -> str:
    items = [
        {"community_id": c["id"], "residents": c["residents"], "action": "prepare"}
        for c in communities
    ]
    if shelter_id:
        items[0]["shelter_id"] = shelter_id
    response = await client.post(
        f"/api/v1/incidents/{incident_id}/plans",
        json={"rationale": "노출 순", "items": items},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _communities(client) -> list[dict]:
    response = await client.get("/api/v1/communities", params={"region_code": "47750"})
    assert response.status_code == 200
    return response.json()["items"]


async def test_contact_requires_approved_plan(client, seeded):
    incident_id = await _incident(client)
    await _plan(client, incident_id, await _communities(client))

    response = await client.post(
        f"/api/v1/incidents/{incident_id}/contacts", json={"channel": "call"}
    )
    assert response.status_code == 409
    assert "승인" in response.json()["detail"]


async def test_operator_cannot_approve(client, seeded):
    incident_id = await _incident(client)
    plan_id = await _plan(client, incident_id, await _communities(client))

    response = await client.post(f"/api/v1/plans/{plan_id}/approve", json={"approver": "본인"})
    assert response.status_code == 403


async def test_approver_can_approve_and_contact_opens(client, seeded):
    incident_id = await _incident(client)
    plan_id = await _plan(client, incident_id, await _communities(client))

    response = await client.post(
        f"/api/v1/plans/{plan_id}/approve",
        json={"approver": "김과장", "acknowledge_caveats": True},
        headers=APPROVER,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "approved"
    assert body["is_actionable"] is True
    assert body["approved_by"] == "김과장"

    response = await client.post(
        f"/api/v1/incidents/{incident_id}/contacts", json={"channel": "call_sms"}
    )
    assert response.status_code == 201
    assert len(response.json()) == 4


async def test_double_approval_conflicts(client, seeded):
    incident_id = await _incident(client)
    plan_id = await _plan(client, incident_id, await _communities(client))
    payload = {"approver": "김과장"}
    assert (
        await client.post(f"/api/v1/plans/{plan_id}/approve", json=payload, headers=APPROVER)
    ).status_code == 200
    second = await client.post(f"/api/v1/plans/{plan_id}/approve", json=payload, headers=APPROVER)
    assert second.status_code == 409


async def test_field_report_with_constraints_forces_reapproval(client, seeded):
    """현장 보고가 계획을 되돌리고, 그때까지 연락 개시가 막힌다."""
    incident_id = await _incident(client)
    plan_id = await _plan(client, incident_id, await _communities(client))
    await client.post(
        f"/api/v1/plans/{plan_id}/approve", json={"approver": "김과장"}, headers=APPROVER
    )
    await client.post(f"/api/v1/incidents/{incident_id}/contacts", json={"channel": "call"})

    task = await client.post(
        f"/api/v1/incidents/{incident_id}/tasks",
        json={"title": "교량 확인", "kind": "verify_route", "priority": 1},
    )
    assert task.status_code == 201
    task_id = task.json()["id"]

    report = await client.post(
        f"/api/v1/tasks/{task_id}/reports",
        json={
            "body": "부남교 유실",
            "observation": "route_blocked",
            "access_constraints": [{"kind": "bridge_unsafe", "location": "부남교"}],
        },
        headers=FIELD,
    )
    assert report.status_code == 201
    assert report.json()["triggered_replan"] is True

    current = await client.get(f"/api/v1/incidents/{incident_id}/plans/current")
    assert current.json()["status"] == "reapproval_required"
    assert current.json()["is_actionable"] is False

    blocked = await client.post(
        f"/api/v1/incidents/{incident_id}/contacts", json={"channel": "call"}
    )
    assert blocked.status_code == 409


async def test_unreachable_becomes_field_task(client, seeded):
    """미확인 세대는 사라지지 않고 현장 확인 임무가 된다."""
    incident_id = await _incident(client)
    plan_id = await _plan(client, incident_id, await _communities(client))
    await client.post(
        f"/api/v1/plans/{plan_id}/approve", json={"approver": "김과장"}, headers=APPROVER
    )
    contacts = (
        await client.post(f"/api/v1/incidents/{incident_id}/contacts", json={"channel": "call"})
    ).json()

    await client.patch(
        f"/api/v1/contacts/{contacts[0]['id']}",
        json={"response": "unreachable", "households_confirmed": 0},
    )
    await client.patch(
        f"/api/v1/contacts/{contacts[1]['id']}",
        json={"response": "evacuating", "households_confirmed": 5},
    )

    rollup = (await client.get(f"/api/v1/incidents/{incident_id}/contacts/rollup")).json()
    assert rollup["unreachable"] == 1
    assert rollup["needs_field_verification"] == 1

    generated = await client.post(f"/api/v1/incidents/{incident_id}/tasks/from-unreachable")
    assert generated.status_code == 200
    tasks = generated.json()
    assert len(tasks) == 1
    assert tasks[0]["priority"] == 1

    # 두 번 눌러도 중복 생성되지 않는다
    again = await client.post(f"/api/v1/incidents/{incident_id}/tasks/from-unreachable")
    assert again.json() == []


async def test_new_plan_supersedes_previous(client, seeded):
    incident_id = await _incident(client)
    communities = await _communities(client)
    first = await _plan(client, incident_id, communities)
    await client.post(
        f"/api/v1/plans/{first}/approve", json={"approver": "김과장"}, headers=APPROVER
    )

    second = await _plan(client, incident_id, communities[:2])
    plans = (await client.get(f"/api/v1/incidents/{incident_id}/plans")).json()
    by_id = {p["id"]: p for p in plans}

    assert by_id[first]["status"] == "superseded"
    assert by_id[first]["approved_by"] == "김과장"  # 승인 이력은 지워지지 않는다
    assert by_id[second]["version"] == 2
    assert by_id[second]["status"] == "draft"


async def test_timeline_records_every_decision(client, seeded):
    incident_id = await _incident(client)
    plan_id = await _plan(client, incident_id, await _communities(client))
    await client.post(
        f"/api/v1/plans/{plan_id}/approve", json={"approver": "김과장"}, headers=APPROVER
    )

    timeline = (await client.get(f"/api/v1/incidents/{incident_id}/timeline")).json()
    actions = {event["action"] for event in timeline}
    assert {"incident.declared", "plan.drafted", "plan.approved"} <= actions

    approval = next(e for e in timeline if e["action"] == "plan.approved")
    assert approval["payload"]["approver"] == "김과장"


@pytest.mark.parametrize("bad_role_header", [{"Authorization": "Bearer nope"}, {}])
async def test_auth_required(client, seeded, bad_role_header):
    response = await client.get(
        "/api/v1/communities", headers={**bad_role_header, "Authorization": "Bearer nope"}
    )
    assert response.status_code == 401
