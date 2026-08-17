"""Regression coverage for platform-scoped flight batch queries."""

from datetime import date, datetime, timezone
from decimal import Decimal

from flightscanner.api.routers.routes import get_route_flights
from flightscanner.models.database import Flight, PriceHistory, Route, init_db


def test_get_route_flights_filters_same_batch_by_source():
    _, session_factory = init_db("sqlite:///:memory:")
    session = session_factory()
    try:
        route = Route(
            origin="上海",
            destination="成都",
            target_date=date(2026, 9, 10),
            target_price=Decimal("600"),
            scrape_interval=6,
            is_active=1,
        )
        qunar_flight = Flight(
            flight_no="QW1001",
            airline="测试航司",
            departure_city="上海",
            arrival_city="成都",
            departure_time="08:00",
            arrival_time="11:00",
            departure_date=route.target_date,
            direction="departure",
        )
        tongcheng_flight = Flight(
            flight_no="TC1002",
            airline="测试航司",
            departure_city="上海",
            arrival_city="成都",
            departure_time="09:00",
            arrival_time="12:00",
            departure_date=route.target_date,
            direction="departure",
        )
        session.add_all([route, qunar_flight, tongcheng_flight])
        session.flush()
        scraped_at = datetime(2026, 8, 13, tzinfo=timezone.utc)
        session.add_all(
            [
                PriceHistory(
                    route_id=route.id,
                    flight_id=qunar_flight.id,
                    batch_id="shared-batch",
                    source="qunar",
                    price=Decimal("450"),
                    currency="CNY",
                    seat_class="经济舱",
                    scraped_at=scraped_at,
                ),
                PriceHistory(
                    route_id=route.id,
                    flight_id=tongcheng_flight.id,
                    batch_id="shared-batch",
                    source="tongcheng",
                    price=Decimal("500"),
                    currency="CNY",
                    seat_class="经济舱",
                    scraped_at=scraped_at,
                ),
            ]
        )
        session.commit()

        result = get_route_flights(
            route_id=route.id,
            batch_id="shared-batch",
            source="tongcheng",
            limit=10,
            db=session,
        )

        assert [flight.source for flight in result.flights] == ["tongcheng"]
        assert [flight.flight_no for flight in result.flights] == ["TC1002"]
    finally:
        session.close()


def test_get_route_flights_resolves_latest_batch_within_source():
    _, session_factory = init_db("sqlite:///:memory:")
    session = session_factory()
    try:
        route = Route(
            origin="上海",
            destination="成都",
            target_date=date(2026, 9, 10),
            target_price=Decimal("600"),
            scrape_interval=6,
            is_active=1,
        )
        flight = Flight(
            flight_no="TC1002",
            airline="测试航司",
            departure_city="上海",
            arrival_city="成都",
            departure_time="09:00",
            arrival_time="12:00",
            departure_date=route.target_date,
            direction="departure",
        )
        session.add_all([route, flight])
        session.flush()
        session.add(
            PriceHistory(
                route_id=route.id,
                flight_id=flight.id,
                batch_id="tongcheng-latest",
                source="tongcheng",
                price=Decimal("500"),
                currency="CNY",
                seat_class="经济舱",
                scraped_at=datetime(2026, 8, 13, tzinfo=timezone.utc),
            )
        )
        session.commit()

        result = get_route_flights(
            route_id=route.id,
            batch_id=None,
            source="tongcheng",
            limit=10,
            db=session,
        )

        assert result.batch_id == "tongcheng-latest"
        assert len(result.flights) == 1
    finally:
        session.close()
