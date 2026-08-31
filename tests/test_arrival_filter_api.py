"""API and aggregation regressions for arrival-day route limits."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import fastapi.routing
import httpx
import pytest
from fastapi import FastAPI

from flightscanner.api.deps import get_db
from flightscanner.api.routers import analytics, routes, stats
from flightscanner.models.database import (
    AIPredictionLog,
    Flight,
    PriceHistory,
    Route,
    init_db,
)


@pytest.fixture
def api_context(monkeypatch):
    engine, session_factory = init_db("sqlite:///:memory:")
    session = session_factory()
    app = FastAPI()
    app.include_router(routes.router, prefix="/api")
    app.include_router(analytics.router, prefix="/api")
    app.include_router(stats.router, prefix="/api")

    async def override_db():
        yield session

    async def run_inline(function, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr(fastapi.routing, "run_in_threadpool", run_inline)
    monkeypatch.setattr(routes, "_register_route_with_scheduler", lambda _route: None)
    monkeypatch.setattr(routes, "_get_live_monitor", lambda: None)
    app.dependency_overrides[get_db] = override_db
    yield app, session
    session.close()
    engine.dispose()


@pytest.fixture
async def api_client(api_context):
    app, session = api_context
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client, session


def _route_payload(
    *,
    outbound_limit: int | None,
    return_limit: int | None,
) -> dict:
    return {
        "origin": "上海",
        "destination": "北京",
        "target_date": "2030-09-10",
        "return_date": "2030-09-17",
        "trip_type": "roundtrip",
        "target_price": 800,
        "max_arrival_day_offset": outbound_limit,
        "ret_max_arrival_day_offset": return_limit,
    }


def _add_route(
    session,
    *,
    outbound_limit: int | None,
    return_limit: int | None = None,
    trip_type: str = "oneway",
) -> Route:
    target_date = date.today() + timedelta(days=120)
    route = Route(
        origin="上海",
        destination="北京",
        target_date=target_date,
        return_date=(
            target_date + timedelta(days=7) if trip_type == "roundtrip" else None
        ),
        trip_type=trip_type,
        target_price=Decimal("800"),
        scrape_interval=6,
        is_active=1,
        max_arrival_day_offset=outbound_limit,
        ret_max_arrival_day_offset=return_limit,
    )
    session.add(route)
    session.flush()
    return route


def _add_quote(
    session,
    route: Route,
    *,
    flight_no: str,
    outbound_offset: int,
    price: str,
    batch_id: str,
    scraped_at: datetime,
    return_offset: int | None = None,
    source: str = "tongcheng",
) -> PriceHistory:
    outbound = Flight(
        flight_no=flight_no,
        airline="测试航空",
        departure_city=route.origin,
        arrival_city=route.destination,
        departure_time="08:00",
        arrival_time="10:00",
        departure_date=route.target_date,
        arrival_date=route.target_date + timedelta(days=outbound_offset),
        direction="departure",
    )
    session.add(outbound)
    session.flush()

    return_flight_id = None
    if return_offset is not None:
        return_date = route.return_date or route.target_date + timedelta(days=7)
        return_flight = Flight(
            flight_no=f"{flight_no}-R",
            airline="测试航空",
            departure_city=route.destination,
            arrival_city=route.origin,
            departure_time="13:00",
            arrival_time="15:00",
            departure_date=return_date,
            arrival_date=return_date + timedelta(days=return_offset),
            direction="return",
        )
        session.add(return_flight)
        session.flush()
        return_flight_id = return_flight.id

    quote = PriceHistory(
        route_id=route.id,
        flight_id=outbound.id,
        return_flight_id=return_flight_id,
        price=Decimal(price),
        currency="CNY",
        seat_class="经济舱",
        available_seats=5,
        source=source,
        batch_id=batch_id,
        scraped_at=scraped_at,
    )
    session.add(quote)
    return quote


@pytest.mark.parametrize(
    ("outbound_limit", "return_limit"),
    [
        (0, 2),
        (1, 1),
        (2, 0),
        (None, None),
    ],
)
async def test_create_route_persists_arrival_day_limits(
    api_client,
    outbound_limit: int | None,
    return_limit: int | None,
) -> None:
    client, session = api_client

    response = await client.post(
        "/api/routes",
        json=_route_payload(
            outbound_limit=outbound_limit,
            return_limit=return_limit,
        ),
    )

    assert response.status_code == 201, response.text
    stored = session.query(Route).filter_by(id=response.json()["id"]).one()
    assert stored.max_arrival_day_offset == outbound_limit
    assert stored.ret_max_arrival_day_offset == return_limit


@pytest.mark.parametrize(
    ("outbound_limit", "return_limit"),
    [
        (0, 2),
        (1, 1),
        (2, 0),
        (None, None),
    ],
)
async def test_patch_route_sets_or_clears_arrival_day_limits(
    api_client,
    outbound_limit: int | None,
    return_limit: int | None,
) -> None:
    client, session = api_client
    route = _add_route(session, outbound_limit=2, return_limit=2)
    session.commit()

    response = await client.patch(
        f"/api/routes/{route.id}",
        json={
            "max_arrival_day_offset": outbound_limit,
            "ret_max_arrival_day_offset": return_limit,
        },
    )

    assert response.status_code == 200, response.text
    session.refresh(route)
    assert route.max_arrival_day_offset == outbound_limit
    assert route.ret_max_arrival_day_offset == return_limit


async def test_patch_omitted_return_limit_remains_unchanged(api_client) -> None:
    client, session = api_client
    route = _add_route(session, outbound_limit=2, return_limit=1)
    session.commit()

    response = await client.patch(
        f"/api/routes/{route.id}",
        json={"max_arrival_day_offset": 0},
    )

    assert response.status_code == 200, response.text
    session.refresh(route)
    assert route.max_arrival_day_offset == 0
    assert route.ret_max_arrival_day_offset == 1


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("max_arrival_day_offset", -1),
        ("max_arrival_day_offset", 3),
        ("ret_max_arrival_day_offset", -1),
        ("ret_max_arrival_day_offset", 3),
    ],
)
async def test_create_and_patch_reject_invalid_arrival_day_limits(
    api_client,
    field_name: str,
    invalid_value: int,
) -> None:
    client, session = api_client
    create_payload = _route_payload(outbound_limit=None, return_limit=None)
    create_payload[field_name] = invalid_value

    create_response = await client.post("/api/routes", json=create_payload)

    assert create_response.status_code == 422
    route = _add_route(session, outbound_limit=1, return_limit=1)
    session.commit()

    patch_response = await client.patch(
        f"/api/routes/{route.id}",
        json={field_name: invalid_value},
    )

    assert patch_response.status_code == 422
    session.refresh(route)
    assert route.max_arrival_day_offset == 1
    assert route.ret_max_arrival_day_offset == 1


async def test_dashboard_latest_price_and_both_legs_come_from_same_filtered_record(
    api_client,
) -> None:
    client, session = api_client
    route = _add_route(
        session,
        outbound_limit=1,
        return_limit=1,
        trip_type="roundtrip",
    )
    now = datetime.now(timezone.utc)
    _add_quote(
        session,
        route,
        flight_no="OLD-VALID",
        outbound_offset=0,
        return_offset=0,
        price="300",
        batch_id="old-batch",
        scraped_at=now - timedelta(days=1),
    )
    _add_quote(
        session,
        route,
        flight_no="NEW-INVALID",
        outbound_offset=2,
        return_offset=0,
        price="100",
        batch_id="new-batch",
        scraped_at=now,
    )
    _add_quote(
        session,
        route,
        flight_no="NEW-EXPENSIVE",
        outbound_offset=0,
        return_offset=0,
        price="600",
        batch_id="new-batch",
        scraped_at=now,
    )
    _add_quote(
        session,
        route,
        flight_no="NEW-CHEAPEST",
        outbound_offset=1,
        return_offset=1,
        price="500",
        batch_id="new-batch",
        scraped_at=now,
    )
    session.commit()

    response = await client.get("/api/routes")

    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body) == 1
    card = body[0]
    assert card["latest_price"] == 500
    assert card["flight_info"]["flight_no"] == "NEW-CHEAPEST"
    assert card["flight_info"]["arrival_day_offset"] == 1
    assert card["flight_info"]["arrival_date"] == (
        route.target_date + timedelta(days=1)
    ).isoformat()
    assert card["return_flight_info"]["flight_no"] == "NEW-CHEAPEST-R"
    assert card["return_flight_info"]["arrival_day_offset"] == 1
    assert card["return_flight_info"]["arrival_date"] == (
        route.return_date + timedelta(days=1)
    ).isoformat()
    assert 100 not in [point["price"] for point in card["sparkline"]]


async def test_dashboard_does_not_fall_back_when_latest_batch_has_no_match(
    api_client,
) -> None:
    client, session = api_client
    route = _add_route(session, outbound_limit=0)
    now = datetime.now(timezone.utc)
    _add_quote(
        session,
        route,
        flight_no="OLD-VALID",
        outbound_offset=0,
        price="350",
        batch_id="old-valid-batch",
        scraped_at=now - timedelta(days=1),
    )
    _add_quote(
        session,
        route,
        flight_no="NEW-INVALID",
        outbound_offset=1,
        price="100",
        batch_id="new-invalid-batch",
        scraped_at=now,
    )
    session.commit()

    response = await client.get("/api/routes")

    assert response.status_code == 200, response.text
    card = response.json()[0]
    assert card["latest_price"] is None
    assert card["flight_info"] is None
    assert card["latest_scraped_at"] is not None
    # Matching historical data remains available for trend context, but must
    # never be presented as the current itinerary or current price.
    assert [point["price"] for point in card["sparkline"]] == [350.0]

    stats_response = await client.get("/api/stats")
    assert stats_response.status_code == 200, stats_response.text
    stats_body = stats_response.json()
    assert stats_body["total_monitors"] == 1
    assert stats_body["buy_count"] == 0
    assert stats_body["hold_count"] == 1


async def test_filtered_out_status_suppresses_an_older_matching_batch(
    api_client,
) -> None:
    client, session = api_client
    route = _add_route(session, outbound_limit=0)
    route.last_flight_status = "filtered_out"
    _add_quote(
        session,
        route,
        flight_no="PREVIOUS-VALID",
        outbound_offset=0,
        price="300",
        batch_id="previous-valid-batch",
        scraped_at=datetime.now(timezone.utc) - timedelta(hours=6),
    )
    session.add(
        AIPredictionLog(
            route_id=route.id,
            price_at_prediction=Decimal("300"),
            days_until_flight=120,
            recommended_action="Buy",
            reason="旧预测建议购买",
            confidence=Decimal("0.900"),
            llm_source="test",
            outcome_status="pending",
        )
    )
    session.commit()

    response = await client.get("/api/routes")

    assert response.status_code == 200, response.text
    card = response.json()[0]
    assert card["last_flight_status"] == "filtered_out"
    assert card["latest_scraped_at"] is not None
    assert card["latest_price"] is None
    assert card["flight_info"] is None
    assert card["status"] == "建议观望"
    assert card["prediction_text"] == "最新采集批次暂无符合当前过滤条件的航班"

    stats_response = await client.get("/api/stats")
    assert stats_response.status_code == 200, stats_response.text
    assert stats_response.json()["buy_count"] == 0


async def test_flights_filters_before_applying_limit(api_client) -> None:
    client, session = api_client
    route = _add_route(session, outbound_limit=0)
    now = datetime.now(timezone.utc)
    _add_quote(
        session,
        route,
        flight_no="INVALID-1",
        outbound_offset=1,
        price="100",
        batch_id="limit-batch",
        scraped_at=now,
    )
    _add_quote(
        session,
        route,
        flight_no="INVALID-2",
        outbound_offset=1,
        price="200",
        batch_id="limit-batch",
        scraped_at=now,
    )
    _add_quote(
        session,
        route,
        flight_no="VALID-3",
        outbound_offset=0,
        price="300",
        batch_id="limit-batch",
        scraped_at=now,
    )
    session.commit()

    response = await client.get(
        f"/api/routes/{route.id}/flights",
        params={"batch_id": "limit-batch", "source": "tongcheng", "limit": 1},
    )

    assert response.status_code == 200, response.text
    flights = response.json()["flights"]
    assert len(flights) == 1
    assert flights[0]["flight_no"] == "VALID-3"
    assert flights[0]["price"] == 300
    assert flights[0]["arrival_day_offset"] == 0


async def test_batches_aggregate_only_matching_flights(api_client) -> None:
    client, session = api_client
    route = _add_route(session, outbound_limit=0)
    now = datetime.now(timezone.utc)
    _add_quote(
        session,
        route,
        flight_no="MIXED-INVALID",
        outbound_offset=1,
        price="100",
        batch_id="mixed-batch",
        scraped_at=now - timedelta(hours=1),
    )
    _add_quote(
        session,
        route,
        flight_no="MIXED-VALID",
        outbound_offset=0,
        price="400",
        batch_id="mixed-batch",
        scraped_at=now - timedelta(hours=1),
    )
    _add_quote(
        session,
        route,
        flight_no="ONLY-INVALID",
        outbound_offset=2,
        price="80",
        batch_id="invalid-only-batch",
        scraped_at=now,
    )
    session.commit()

    response = await client.get(f"/api/routes/{route.id}/batches")

    assert response.status_code == 200, response.text
    assert response.json()["batches"] == [
        {
            "batch_id": "mixed-batch",
            "source": "tongcheng",
            "scraped_at": response.json()["batches"][0]["scraped_at"],
            "flight_count": 1,
            "min_price": 400.0,
        }
    ]


async def test_calendar_aggregates_only_matching_flights(api_client) -> None:
    client, session = api_client
    route = _add_route(session, outbound_limit=0)
    today = date.today()
    mixed_day = datetime(today.year, today.month, 10, 4, tzinfo=timezone.utc)
    invalid_day = mixed_day + timedelta(days=1)
    _add_quote(
        session,
        route,
        flight_no="CAL-INVALID",
        outbound_offset=1,
        price="100",
        batch_id="calendar-mixed",
        scraped_at=mixed_day,
    )
    _add_quote(
        session,
        route,
        flight_no="CAL-VALID",
        outbound_offset=0,
        price="400",
        batch_id="calendar-mixed",
        scraped_at=mixed_day,
    )
    _add_quote(
        session,
        route,
        flight_no="CAL-ONLY-INVALID",
        outbound_offset=2,
        price="80",
        batch_id="calendar-invalid-only",
        scraped_at=invalid_day,
    )
    session.commit()

    response = await client.get(
        f"/api/routes/{route.id}/calendar",
        params={"month": f"{today.year}-{today.month:02d}"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["days"] == [
        {
            "date": mixed_day.date().isoformat(),
            "min_price": 400.0,
            "max_price": 400.0,
            "avg_price": 400.0,
            "record_count": 1,
        }
    ]
