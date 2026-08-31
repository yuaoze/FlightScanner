"""Regression tests for Ctrip connecting-flight itinerary parsing."""

from copy import deepcopy
from datetime import date
from decimal import Decimal

from flightscanner.core.route_filter import arrival_day_offset, arrival_within_limit
from flightscanner.core.services.route_service import RouteService
from flightscanner.interfaces import SearchParams
from flightscanner.models.database import Flight, PriceHistory, Route, init_db
from flightscanner.scrapers.ctrip_scraper import CtripScraper

DEPARTURE_DATE = date(2026, 10, 7)


def _chiang_mai_to_shanghai_itinerary() -> dict:
    """Return a synthetic copy of the route-21 Ctrip response shape."""
    return {
        "flightSegments": [
            {
                "segmentNo": 1,
                "airlineName": "泰国狮航",
                "flightList": [
                    {
                        "flightNo": "SL521",
                        "marketAirlineName": "泰国狮航",
                        "departureAirportCode": "CNX",
                        "arrivalAirportCode": "DMK",
                        "depAirportName": "清迈国际机场",
                        "arrAirportName": "廊曼国际机场",
                        "departureDateTime": "2026-10-07 21:30:00",
                        "arrivalDateTime": "2026-10-07 22:45:00",
                    },
                    {
                        "flightNo": "SL926",
                        "marketAirlineName": "泰国狮航",
                        "departureAirportCode": "DMK",
                        "arrivalAirportCode": "PVG",
                        "depAirportName": "廊曼国际机场",
                        "arrAirportName": "浦东国际机场",
                        "departureDateTime": "2026-10-08 19:25:00",
                        "arrivalDateTime": "2026-10-09 01:05:00",
                    },
                ],
            }
        ],
        "priceList": [
            {
                "adultPrice": 2910,
                "adultTax": 0,
                "cabin": "Y",
                "seatsLeft": 1,
            }
        ],
    }


def _parse(itineraries: list[dict]):
    scraper = CtripScraper(headless=True)
    params = SearchParams("清迈", "上海", DEPARTURE_DATE)
    return scraper._try_parse_response(
        {"data": {"flightItineraryList": itineraries}},
        params,
    )


def test_ctrip_transfer_uses_first_departure_and_final_arrival() -> None:
    results = _parse([_chiang_mai_to_shanghai_itinerary()])

    assert len(results) == 1
    result = results[0]
    flight = result.flight_info
    assert flight.flight_no == "SL521+SL926"
    assert flight.airline == "泰国狮航"
    assert flight.departure_city == "清迈"
    assert flight.arrival_city == "上海"
    assert flight.departure_time == "21:30"
    assert flight.arrival_time == "01:05"
    assert flight.departure_airport_code == "CNX"
    assert flight.arrival_airport_code == "PVG"
    assert flight.departure_airport == "清迈国际机场"
    assert flight.arrival_airport == "浦东国际机场"
    assert flight.departure_date == DEPARTURE_DATE
    assert flight.arrival_date == date(2026, 10, 9)
    assert arrival_day_offset(flight) == 2
    assert result.price == Decimal("2910")


def test_ctrip_transfer_uses_final_arrival_for_cumulative_day_limit() -> None:
    flight = _parse([_chiang_mai_to_shanghai_itinerary()])[0].flight_info

    assert arrival_within_limit(flight, 0) is False
    assert arrival_within_limit(flight, 1) is False
    assert arrival_within_limit(flight, 2) is True


def test_shared_first_leg_connections_persist_as_distinct_full_itineraries() -> None:
    first_itinerary = _chiang_mai_to_shanghai_itinerary()
    second_itinerary = deepcopy(first_itinerary)
    second_leg = second_itinerary["flightSegments"][0]["flightList"][1]
    second_leg.update(
        {
            "flightNo": "SL928",
            "arrivalAirportCode": "SHA",
            "arrAirportName": "虹桥国际机场",
            "arrivalDateTime": "2026-10-08 23:55:00",
        }
    )
    results = _parse([first_itinerary, second_itinerary])

    _, session_factory = init_db("sqlite:///:memory:")
    session = session_factory()
    try:
        route = Route(
            origin="清迈",
            destination="上海",
            target_date=DEPARTURE_DATE,
            target_price=Decimal("2500"),
            scrape_interval=6,
            is_active=1,
        )
        session.add(route)
        session.commit()

        service = RouteService(session)
        for result in results:
            service.save_price_for_route(route.id, result)

        flights = session.query(Flight).order_by(Flight.flight_no).all()
        assert [flight.flight_no for flight in flights] == [
            "SL521+SL926",
            "SL521+SL928",
        ]
        assert {(flight.arrival_airport_code, flight.arrival_date) for flight in flights} == {
            ("PVG", date(2026, 10, 9)),
            ("SHA", date(2026, 10, 8)),
        }
        assert session.query(PriceHistory).filter_by(route_id=route.id).count() == 2
    finally:
        session.close()
