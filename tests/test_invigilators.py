"""
Integration tests for the /api/invigilators endpoints.

Coverage
--------
* POST   /api/invigilators         — create (valid + validation errors)
* GET    /api/invigilators/{id}    — fetch by id (found / 404)
* GET    /api/invigilators         — list with search, department filter, status filter, pagination
* PUT    /api/invigilators/{id}    — update (name, status change)
* DELETE /api/invigilators/{id}    — soft-delete; record disappears from GET
"""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invigilator import Invigilator, InvigilatorStatus


# ── CREATE ────────────────────────────────────────────────────────────────────


async def test_create_invigilator_minimal(client: AsyncClient):
    resp = await client.post("/api/invigilators", json={"name": "Jane Doe", "status": "available"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "Jane Doe"
    assert body["status"] == "available"
    assert uuid.UUID(body["id"])


async def test_create_invigilator_full(client: AsyncClient):
    resp = await client.post(
        "/api/invigilators",
        json={
            "name": "Prof Smith",
            "department": "Physics",
            "institute": "Central Uni",
            "mobile": "+447911123456",
            "email": "smith@example.com",
            "status": "available",
            "remarks": "Senior examiner",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["department"] == "Physics"
    assert body["email"] == "smith@example.com"


async def test_create_invigilator_blank_name_rejected(client: AsyncClient):
    resp = await client.post("/api/invigilators", json={"name": "   ", "status": "available"})
    assert resp.status_code == 422


async def test_create_invigilator_invalid_mobile_rejected(client: AsyncClient):
    resp = await client.post(
        "/api/invigilators",
        json={"name": "Bad Mobile", "mobile": "not-a-phone", "status": "available"},
    )
    assert resp.status_code == 422


async def test_create_invigilator_defaults_to_available(client: AsyncClient):
    resp = await client.post("/api/invigilators", json={"name": "Default Status"})
    assert resp.status_code == 201
    assert resp.json()["status"] == "available"


# ── GET BY ID ─────────────────────────────────────────────────────────────────


async def test_get_invigilator_found(client: AsyncClient, invigilator: Invigilator):
    resp = await client.get(f"/api/invigilators/{invigilator.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == str(invigilator.id)
    assert body["name"] == invigilator.name


async def test_get_invigilator_not_found(client: AsyncClient):
    resp = await client.get(f"/api/invigilators/{uuid.uuid4()}")
    assert resp.status_code == 404


async def test_get_invigilator_soft_deleted_returns_404(
    client: AsyncClient, db: AsyncSession, invigilator: Invigilator
):
    invigilator.is_deleted = True
    await db.flush()
    resp = await client.get(f"/api/invigilators/{invigilator.id}")
    assert resp.status_code == 404


# ── LIST / SEARCH / FILTER ────────────────────────────────────────────────────


async def test_list_returns_all(
    client: AsyncClient,
    invigilator: Invigilator,
    invigilator2: Invigilator,
):
    resp = await client.get("/api/invigilators")
    assert resp.status_code == 200
    body = resp.json()
    ids = [d["id"] for d in body["data"]]
    assert str(invigilator.id) in ids
    assert str(invigilator2.id) in ids
    assert body["meta"]["total"] >= 2


async def test_list_excludes_soft_deleted(
    client: AsyncClient,
    db: AsyncSession,
    invigilator: Invigilator,
    invigilator2: Invigilator,
):
    invigilator2.is_deleted = True
    await db.flush()

    resp = await client.get("/api/invigilators")
    ids = [d["id"] for d in resp.json()["data"]]
    assert str(invigilator.id) in ids
    assert str(invigilator2.id) not in ids


async def test_search_by_name(
    client: AsyncClient,
    invigilator: Invigilator,
    invigilator2: Invigilator,
):
    resp = await client.get("/api/invigilators?search=Alice")
    assert resp.status_code == 200
    names = [d["name"] for d in resp.json()["data"]]
    assert "Alice Smith" in names
    assert "Bob Jones" not in names


async def test_search_case_insensitive(
    client: AsyncClient,
    invigilator: Invigilator,
):
    resp = await client.get("/api/invigilators?search=alice")
    names = [d["name"] for d in resp.json()["data"]]
    assert "Alice Smith" in names


async def test_filter_by_status_available(
    client: AsyncClient,
    invigilator: Invigilator,
    invigilator_unavailable: Invigilator,
):
    resp = await client.get("/api/invigilators?status=available")
    assert resp.status_code == 200
    for d in resp.json()["data"]:
        assert d["status"] == "available"


async def test_filter_by_status_unavailable(
    client: AsyncClient,
    invigilator: Invigilator,
    invigilator_unavailable: Invigilator,
):
    resp = await client.get("/api/invigilators?status=unavailable")
    assert resp.status_code == 200
    names = [d["name"] for d in resp.json()["data"]]
    assert "Dave Sick" in names
    assert "Alice Smith" not in names


async def test_filter_by_department(client: AsyncClient, db: AsyncSession):
    from app.models.invigilator import Invigilator as Inv, InvigilatorStatus

    math_inv = Inv(name="Math Prof", department="Mathematics", status=InvigilatorStatus.available)
    other_inv = Inv(name="Other Prof", department="History", status=InvigilatorStatus.available)
    db.add_all([math_inv, other_inv])
    await db.flush()

    resp = await client.get("/api/invigilators?department=Mathematics")
    names = [d["name"] for d in resp.json()["data"]]
    assert "Math Prof" in names
    assert "Other Prof" not in names


async def test_filter_department_partial_match(client: AsyncClient, db: AsyncSession):
    from app.models.invigilator import Invigilator as Inv, InvigilatorStatus

    inv = Inv(name="Applied Sci", department="Applied Sciences", status=InvigilatorStatus.available)
    db.add(inv)
    await db.flush()

    resp = await client.get("/api/invigilators?department=Applied")
    names = [d["name"] for d in resp.json()["data"]]
    assert "Applied Sci" in names


async def test_pagination_limit(
    client: AsyncClient,
    invigilator: Invigilator,
    invigilator2: Invigilator,
    invigilator3: Invigilator,
):
    resp = await client.get("/api/invigilators?limit=2&page=1")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["data"]) <= 2
    assert body["meta"]["limit"] == 2


async def test_pagination_meta_fields(client: AsyncClient, invigilator: Invigilator):
    resp = await client.get("/api/invigilators")
    meta = resp.json()["meta"]
    assert "total" in meta
    assert "pages" in meta
    assert "page" in meta
    assert "limit" in meta


# ── UPDATE ────────────────────────────────────────────────────────────────────


async def test_update_name(client: AsyncClient, invigilator: Invigilator):
    resp = await client.put(
        f"/api/invigilators/{invigilator.id}",
        json={"name": "Alice Updated"},
    )
    assert resp.status_code == 200
    assert resp.json()["invigilator"]["name"] == "Alice Updated"


async def test_update_returns_affected_assignments(
    client: AsyncClient, invigilator: Invigilator
):
    resp = await client.put(
        f"/api/invigilators/{invigilator.id}",
        json={"name": "Alice New"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "affected_assignments" in body


async def test_update_status_to_unavailable(client: AsyncClient, invigilator: Invigilator):
    resp = await client.put(
        f"/api/invigilators/{invigilator.id}",
        json={"status": "unavailable"},
    )
    assert resp.status_code == 200
    assert resp.json()["invigilator"]["status"] == "unavailable"


async def test_update_nonexistent_returns_404(client: AsyncClient):
    resp = await client.put(
        f"/api/invigilators/{uuid.uuid4()}",
        json={"name": "Ghost"},
    )
    assert resp.status_code == 404


async def test_update_blank_name_rejected(client: AsyncClient, invigilator: Invigilator):
    resp = await client.put(
        f"/api/invigilators/{invigilator.id}",
        json={"name": "  "},
    )
    assert resp.status_code == 422


# ── DELETE (soft delete) ──────────────────────────────────────────────────────


async def test_delete_invigilator(client: AsyncClient, invigilator: Invigilator):
    resp = await client.delete(f"/api/invigilators/{invigilator.id}")
    assert resp.status_code == 204


async def test_deleted_invigilator_not_accessible(
    client: AsyncClient, invigilator: Invigilator
):
    await client.delete(f"/api/invigilators/{invigilator.id}")
    resp = await client.get(f"/api/invigilators/{invigilator.id}")
    assert resp.status_code == 404


async def test_delete_nonexistent_returns_404(client: AsyncClient):
    resp = await client.delete(f"/api/invigilators/{uuid.uuid4()}")
    assert resp.status_code == 404
