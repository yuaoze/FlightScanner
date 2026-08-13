"""HTTP contract tests for the v2.2 purchase workflow."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest

pytest.importorskip("fastapi")

from fastapi import FastAPI
import fastapi.routing

from flightscanner.api.deps import get_db
from flightscanner.api.routers import purchases
from flightscanner.core.services import PurchaseService
from flightscanner.models.database import (
    BuyPointAnalysis,
    PurchaseRecord,
    Route,
    init_db,
)


@pytest.fixture
def api_context(monkeypatch):
    _, SessionLocal = init_db("sqlite:///:memory:")
    session = SessionLocal()
    route = Route(
        origin="北京",
        destination="上海",
        target_date=date.today() + timedelta(days=10),
        target_price=Decimal("500"),
        scrape_interval=6,
        is_active=1,
    )
    session.add(route)
    session.commit()
    session.refresh(route)

    app = FastAPI()
    app.include_router(purchases.router, prefix="/api")

    async def override_db():
        yield session

    # This execution environment cannot start AnyIO worker threads. Running
    # sync FastAPI handlers inline keeps the test focused on validation,
    # serialization, and persistence rather than the thread-pool runtime.
    async def run_inline(function, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr(fastapi.routing, "run_in_threadpool", run_inline)
    app.dependency_overrides[get_db] = override_db
    yield app, session, route
    session.close()


@pytest.fixture
async def api_client(api_context):
    app, session, route = api_context
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client, session, route


async def test_instant_buy_round_trips_new_money_and_time_fields(api_client):
    client, session, route = api_client
    purchased_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    payload = {
        "route_id": route.id,
        "unit_price": 544.5,
        "price": 544.5,
        "total_paid": 1068,
        "currency": "cny",
        "seat_class": "经济舱",
        "passengers": 2,
        "purchased_at": purchased_at.isoformat(),
        "notes": "使用优惠券",
    }

    response = await client.post("/api/purchases/instant", json=payload)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["purchase_price"] == 544.5
    assert body["total_paid"] == 1068
    assert body["currency"] == "CNY"
    assert body["passengers"] == 2
    stored = session.query(PurchaseRecord).filter_by(id=body["id"]).one()
    assert float(stored.total_paid) == 1068
    assert stored.purchased_at.replace(tzinfo=timezone.utc) == purchased_at


async def test_purchase_request_rejects_unknown_or_inconsistent_fields(api_client):
    client, _, route = api_client
    base = {
        "route_id": route.id,
        "unit_price": 500,
        "price": 500,
        "total_paid": 500,
        "purchased_at": datetime.now(timezone.utc).isoformat(),
    }

    unknown = await client.post(
        "/api/purchases/instant", json={**base, "totla_paid": 500}
    )
    inconsistent = await client.post(
        "/api/purchases/instant", json={**base, "price": 900}
    )
    naive_time = await client.post(
        "/api/purchases/instant", json={**base, "purchased_at": "2026-01-01T12:00:00"}
    )

    assert unknown.status_code == 422
    assert inconsistent.status_code == 422
    assert naive_time.status_code == 422


async def test_plan_confirmation_http_retry_is_idempotent(api_client):
    client, session, route = api_client
    service = PurchaseService(session)
    plan = service.create_plan(route.id, plan_price=600)
    service.check_plans_for_route(route, current_min_price=550)
    payload = {
        "unit_price": 550,
        "actual_price": 550,
        "price": 550,
        "total_paid": 1100,
        "passengers": 2,
        "purchased_at": datetime.now(timezone.utc).isoformat(),
    }

    first = await client.post(f"/api/plans/{plan.id}/confirm", json=payload)
    second = await client.post(f"/api/plans/{plan.id}/confirm", json=payload)

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert second.json()["id"] == first.json()["id"]
    assert session.query(PurchaseRecord).filter_by(plan_id=plan.id).count() == 1


async def test_purchase_response_exposes_analysis_quality_metadata(api_client):
    client, session, route = api_client
    purchase = PurchaseService(session).instant_buy(route.id, unit_price=500)
    purchase.status = "completed"
    session.add(
        BuyPointAnalysis(
            purchase_id=purchase.id,
            verdict="good",
            analysis_status="final",
            sample_size=4,
            coverage_hours=Decimal("72"),
            data_quality="good",
        )
    )
    session.commit()

    response = await client.get(f"/api/purchases/{purchase.id}")

    assert response.status_code == 200, response.text
    analysis = response.json()["analysis"]
    assert analysis["analysis_status"] == "final"
    assert analysis["sample_size"] == 4
    assert analysis["coverage_hours"] == 72
    assert analysis["data_quality"] == "good"


async def test_purchase_lists_have_bounded_pagination(api_client):
    client, session, route = api_client
    service = PurchaseService(session)
    for price in (500, 510, 520):
        service.instant_buy(route.id, unit_price=price)

    page = await client.get("/api/purchases", params={"offset": 1, "limit": 1})
    oversized = await client.get("/api/purchases", params={"limit": 501})

    assert page.status_code == 200, page.text
    assert len(page.json()) == 1
    assert oversized.status_code == 422
