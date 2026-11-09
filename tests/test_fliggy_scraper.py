"""Offline contracts using the observed domestic JSONP and international poller shapes.

Values are fixed examples, not current fares. No browser or network is started.
"""

import asyncio
import json
import os
from copy import deepcopy
from dataclasses import replace
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from flightscanner.interfaces import (
    AntiCrawlerDetectedError,
    FlightDirection,
    NetworkTimeoutError,
    ParseError,
    ScraperError,
    SearchParams,
)
from flightscanner.scrapers import fliggy_scraper as module
from flightscanner.scrapers.fliggy_scraper import FliggyScraper

DOMESTIC_API = "https://sjipiao.fliggy.com/searchow/search.htm?callback=flightSearch"
INTERNATIONAL_API = "https://sijipiao.fliggy.com/ie/flight_search_result_poller.do"
DAY = date(2026, 12, 10)


@pytest.fixture(autouse=True)
def prohibit_real_browser(monkeypatch):
    monkeypatch.setattr(
        module, "async_playwright", Mock(side_effect=AssertionError("real browser forbidden"))
    )


@pytest.fixture
def scraper():
    # Explicit cookies prevent reading an actual developer login file.
    return FliggyScraper(cookies=[], timeout=1000)


@pytest.fixture
def params():
    return SearchParams("上海", "成都", DAY)


@pytest.fixture
def international_params():
    return SearchParams("上海", "东京", DAY)


@pytest.fixture
def domestic_response():
    """Observed /searchow/search.htm fields: fares and taxes are yuan."""
    return {
        "data": {
            "airportMap": {"SHA": "虹桥机场", "TFU": "天府机场", "KMG": "长水机场"},
            "aircodeNameMap": {"MU": "东方航空", "HO": "吉祥航空"},
            "flight": [
                {
                    "flightNo": "MU5401",
                    "depTime": "2026-12-10 23:20:00",
                    "arrTime": "2026-12-11 02:40:00",
                    "depAirport": "SHA",
                    "arrAirport": "TFU",
                    "airlineCode": "MU",
                    "buildPrice": 50,
                    "oilPrice": 20,
                    "isTransfer": False,
                    "cabin": {"price": "640.50", "cabinNum": "3", "specialType": "经济舱"},
                }
            ],
        }
    }


@pytest.fixture
def domestic_transfer_response(domestic_response):
    """transferFlight entries are alternatives, not independently priced legs."""
    domestic_response = deepcopy(domestic_response)
    first = domestic_response["data"]["flight"][0]
    first.update(
        isTransfer=True,
        arrAirport="KMG",
        arrTime="2026-12-11 02:40:00",
        transferFlight=[
            {
                "flightNo": "MU5843",
                "airlineCode": "MU",
                "depAirport": "KMG",
                "arrAirport": "TFU",
                "depTime": "2026-12-11 08:00:00",
                "arrTime": "2026-12-11 09:30:00",
                "buildPrice": 50,
                "oilPrice": 20,
                "cabin": {"price": 300, "cabinNum": "5"},
                "totalInfo": {"lowestPrice": "880.50", "buildOilPrice": 140, "cabinNum": "2"},
            },
            {
                "flightNo": "HO1234",
                "airlineCode": "HO",
                "depAirport": "KMG",
                "arrAirport": "TFU",
                "depTime": "2026-12-12 08:00:00",
                "arrTime": "2026-12-12 09:30:00",
                "totalInfo": {"lowestPrice": 900, "buildOilPrice": 140, "cabinNum": "1"},
            },
        ],
    )
    return domestic_response


@pytest.fixture
def international_response():
    """Observed poller fields: totalAdultPrice already includes adultTax, in cents."""
    return {
        "status": 200,
        "data": {
            "isContinue": False,
            "flightItems": [
                {
                    "adultTax": 23600,
                    "totalAdultPrice": 123456,
                    "quantity": "4",
                    "flightInfo": [
                        {
                            "flightSegments": [
                                {
                                    "marketingFlightNo": "MU523",
                                    "marketingAirlineName": "东方航空",
                                    "depTimeStr": "2026-12-10 23:30:00",
                                    "arrTimeStr": "2026-12-11 03:15:00",
                                    "depAirportCode": "PVG",
                                    "arrAirportCode": "NRT",
                                    "depAirportName": "浦东国际机场",
                                    "arrAirportName": "成田国际机场",
                                }
                            ]
                        }
                    ],
                }
            ],
        },
    }


@pytest.fixture
def international_transfer_response(international_response):
    international_response = deepcopy(international_response)
    segments = international_response["data"]["flightItems"][0]["flightInfo"][0]["flightSegments"]
    segments[0].update(arrAirportCode="HKG", arrAirportName="香港国际机场")
    segments.extend(
        [
            {
                "marketingFlightNo": "CX450",
                "marketingAirlineName": "国泰航空",
                "depTimeStr": "2026-12-11 10:00:00",
                "arrTimeStr": "2026-12-11 12:00:00",
                "depAirportCode": "HKG",
                "arrAirportCode": "TPE",
                "depAirportName": "香港国际机场",
                "arrAirportName": "桃园国际机场",
            },
            {
                "marketingFlightNo": "JL802",
                "marketingAirlineName": "日本航空",
                "depTimeStr": "2026-12-12 08:00:00",
                "arrTimeStr": "2026-12-12 12:15:00",
                "depAirportCode": "TPE",
                "arrAirportCode": "NRT",
                "depAirportName": "桃园国际机场",
                "arrAirportName": "成田国际机场",
            },
        ]
    )
    return international_response


@pytest.mark.parametrize(
    ("origin", "destination", "dep_code", "arr_code", "host", "path"),
    [
        ("上海", "成都", "SHA", "CTU", "sjipiao.fliggy.com", "/flight_search_result.htm"),
        ("北京", "上海", "BJS", "SHA", "sjipiao.fliggy.com", "/flight_search_result.htm"),
        ("上海", "东京", "SHA", "TYO", "sijipiao.fliggy.com", "/ie/flight_search_result.htm"),
        ("东京", "上海", "TYO", "SHA", "sijipiao.fliggy.com", "/ie/flight_search_result.htm"),
        ("上海", "香港", "SHA", "HKG", "sijipiao.fliggy.com", "/ie/flight_search_result.htm"),
        ("马尼拉", "宿务", "MNL", "CEB", "sijipiao.fliggy.com", "/ie/flight_search_result.htm"),
    ],
)
def test_search_urls_are_official_single_leg_city_and_date_contracts(
    origin, destination, dep_code, arr_code, host, path
):
    url = urlsplit(FliggyScraper._build_search_url(SearchParams(origin, destination, DAY)))
    assert (url.scheme, url.netloc, url.path) == ("https", host, path)
    assert parse_qs(url.query) == {
        "tripType": ["0"],
        "depCity": [dep_code],
        "arrCity": [arr_code],
        "depDate": ["2026-12-10"],
        "depCityName": [origin],
        "arrCityName": [destination],
    }


def test_search_url_rejects_native_roundtrip(params):
    with pytest.raises(ValueError, match="单程"):
        FliggyScraper._build_search_url(replace(params, return_date=date(2026, 12, 15)))


@pytest.mark.parametrize("origin,destination", [("不存在的城市", "上海"), ("上海", "不存在的城市")])
def test_search_url_rejects_unknown_cities(origin, destination):
    with pytest.raises(ScraperError, match="城市"):
        FliggyScraper._build_search_url(SearchParams(origin, destination, DAY))


@pytest.mark.parametrize("wrapper", ["{}", "flightSearch({});", "window.$flight_123(\n{}\n)"])
def test_decode_json_and_jsonp_without_evaluation(wrapper, domestic_response, monkeypatch):
    monkeypatch.setattr("builtins.eval", Mock(side_effect=AssertionError("eval forbidden")))
    body = json.dumps(domestic_response, ensure_ascii=False)
    assert FliggyScraper._decode_response(" \n" + wrapper.format(body) + " \n") == domestic_response


@pytest.mark.parametrize(
    "text",
    [
        "",
        "[]",
        "null",
        "true",
        "<html>登录</html>",
        '{"data":',
        "callback([])",
        'callback({"x": undefined})',
        "callback({'x': 1})",
        'prefix callback({"data": {}}); suffix',
        "callback({}); alert(1)",
        "callback({}); callback({})",
        '__import__("os").system("false")',
        'callback(__import__("os"))',
        'callback({"data": {}})//comment',
        '123({"data": {}})',
        'foo..bar({"data": {}})',
    ],
)
def test_decode_rejects_garbage_and_non_json_callback_bodies(text):
    with pytest.raises(ParseError):
        FliggyScraper._decode_response(text)


def test_domestic_yuan_price_adds_both_taxes_and_preserves_metadata(
    scraper, params, domestic_response
):
    results, finished = scraper._parse_response(domestic_response, params, international=False)
    assert finished is True
    assert len(results) == 1
    price = results[0]
    assert price.price == Decimal("710.50")
    assert (price.currency, price.source, price.seat_class, price.available_seats) == (
        "CNY",
        "fliggy",
        "经济舱",
        3,
    )
    assert price.scraped_at.utcoffset().total_seconds() == 0
    assert price.return_flight_info is None
    info = price.flight_info
    assert (info.flight_no, info.airline) == ("MU5401", "东方航空")
    assert (info.departure_city, info.arrival_city) == ("上海", "成都")
    assert (info.departure_date, info.arrival_date) == (DAY, date(2026, 12, 11))
    assert (info.departure_time, info.arrival_time) == ("23:20", "02:40")
    assert (info.departure_airport_code, info.arrival_airport_code) == ("SHA", "TFU")
    assert (info.departure_airport, info.arrival_airport) == ("虹桥机场", "天府机场")
    assert info.direction == FlightDirection.DEPARTURE


def test_domestic_transfer_uses_combined_fare_once_and_keeps_each_alternative(
    scraper, params, domestic_transfer_response
):
    results, finished = scraper._parse_response(
        domestic_transfer_response, params, international=False
    )
    assert finished is True
    assert [p.price for p in results] == [Decimal("1020.50"), Decimal("1040")]
    assert [p.available_seats for p in results] == [2, 1]
    assert [p.flight_info.flight_no for p in results] == ["MU5401+MU5843", "MU5401+HO1234"]
    assert [p.flight_info.arrival_date for p in results] == [date(2026, 12, 11), date(2026, 12, 12)]
    assert [p.flight_info.airline for p in results] == ["东方航空", "东方航空/吉祥航空"]
    for price in results:
        info = price.flight_info
        assert (info.departure_date, info.departure_time, info.arrival_time) == (
            DAY,
            "23:20",
            "09:30",
        )
        assert (info.departure_airport_code, info.arrival_airport_code) == ("SHA", "TFU")
        assert (info.departure_airport, info.arrival_airport) == ("虹桥机场", "天府机场")


def test_international_total_is_cents_and_tax_is_not_added_twice(
    scraper, international_params, international_response
):
    results, finished = scraper._parse_response(
        international_response, international_params, international=True
    )
    assert finished is True
    assert len(results) == 1
    price = results[0]
    assert price.price == Decimal("1234.56")
    assert (price.currency, price.source, price.available_seats) == ("CNY", "fliggy", 4)
    assert price.return_flight_info is None
    info = price.flight_info
    assert (info.flight_no, info.airline) == ("MU523", "东方航空")
    assert (info.departure_date, info.arrival_date) == (DAY, date(2026, 12, 11))
    assert (info.departure_time, info.arrival_time) == ("23:30", "03:15")
    assert (info.departure_airport_code, info.arrival_airport_code) == ("PVG", "NRT")
    assert (info.departure_airport, info.arrival_airport) == ("浦东国际机场", "成田国际机场")


def test_international_transfer_keeps_all_segment_ids_and_final_arrival_day(
    scraper, international_params, international_transfer_response
):
    results, _ = scraper._parse_response(
        international_transfer_response, international_params, international=True
    )
    assert len(results) == 1
    price = results[0]
    assert price.price == Decimal("1234.56")
    info = price.flight_info
    assert info.flight_no == "MU523+CX450+JL802"
    assert info.airline == "东方航空/国泰航空/日本航空"
    assert (info.departure_date, info.arrival_date) == (DAY, date(2026, 12, 12))
    assert (info.departure_time, info.arrival_time) == ("23:30", "12:15")
    assert (info.departure_airport_code, info.arrival_airport_code) == ("PVG", "NRT")


@pytest.mark.parametrize("field", ["price", "buildPrice", "oilPrice"])
@pytest.mark.parametrize(
    "value", [None, True, False, "NaN", "Infinity", "-Infinity", -1, "not-a-price"]
)
def test_domestic_rejects_invalid_fares_or_taxes(scraper, params, domestic_response, field, value):
    item = domestic_response["data"]["flight"][0]
    (item["cabin"] if field == "price" else item)[field] = value
    with pytest.raises(ParseError, match="含税"):
        scraper._parse_response(domestic_response, params, international=False)


@pytest.mark.parametrize("field", ["adultTax", "totalAdultPrice"])
@pytest.mark.parametrize(
    "value", [None, True, False, "NaN", "Infinity", "-Infinity", -1, "not-a-price"]
)
def test_international_rejects_invalid_total_or_tax(
    scraper, international_params, international_response, field, value
):
    international_response["data"]["flightItems"][0][field] = value
    with pytest.raises(ParseError, match="含税"):
        scraper._parse_response(international_response, international_params, international=True)


@pytest.mark.parametrize("field", ["buildPrice", "oilPrice"])
def test_domestic_missing_tax_is_not_fabricated(scraper, params, domestic_response, field):
    del domestic_response["data"]["flight"][0][field]
    with pytest.raises(ParseError, match="含税"):
        scraper._parse_response(domestic_response, params, international=False)


def test_international_missing_tax_is_not_assumed_zero(
    scraper, international_params, international_response
):
    del international_response["data"]["flightItems"][0]["adultTax"]
    with pytest.raises(ParseError, match="含税"):
        scraper._parse_response(international_response, international_params, international=True)


@pytest.mark.parametrize("international", [False, True])
def test_explicit_zero_tax_is_valid(
    scraper, params, international_params, domestic_response, international_response, international
):
    if international:
        response, search = international_response, international_params
        response["data"]["flightItems"][0]["adultTax"] = 0
        expected = Decimal("1234.56")
    else:
        response, search = domestic_response, params
        response["data"]["flight"][0].update(buildPrice=0, oilPrice=0)
        expected = Decimal("640.50")
    prices, _ = scraper._parse_response(response, search, international=international)
    assert [p.price for p in prices] == [expected]


@pytest.mark.parametrize("international", [False, True])
@pytest.mark.parametrize("reason", ["zero-total", "sold-out"])
def test_nonpositive_total_and_sold_out_cannot_be_emitted(
    scraper,
    params,
    international_params,
    domestic_response,
    international_response,
    international,
    reason,
):
    if international:
        response, search = international_response, international_params
        item = response["data"]["flightItems"][0]
        item.update({"totalAdultPrice": 0} if reason == "zero-total" else {"quantity": "0"})
    else:
        response, search = domestic_response, params
        item = response["data"]["flight"][0]
        if reason == "zero-total":
            item.update(buildPrice=0, oilPrice=0)
            item["cabin"]["price"] = 0
        else:
            item["cabin"]["cabinNum"] = "0"
    with pytest.raises(ParseError, match="含税"):
        scraper._parse_response(response, search, international=international)


@pytest.mark.parametrize(
    "field,value", [("lowestPrice", None), ("buildOilPrice", None), ("cabinNum", "0")]
)
def test_transfer_total_must_be_complete_and_available(
    scraper, params, domestic_transfer_response, field, value
):
    item = domestic_transfer_response["data"]["flight"][0]
    item["transferFlight"] = item["transferFlight"][:1]
    item["transferFlight"][0]["totalInfo"][field] = value
    with pytest.raises(ParseError, match="含税"):
        scraper._parse_response(domestic_transfer_response, params, international=False)


@pytest.mark.parametrize("international", [False, True])
def test_unknown_seat_count_is_not_fabricated(
    scraper, params, international_params, domestic_response, international_response, international
):
    if international:
        response, search = international_response, international_params
        del response["data"]["flightItems"][0]["quantity"]
    else:
        response, search = domestic_response, params
        del response["data"]["flight"][0]["cabin"]["cabinNum"]
    prices, _ = scraper._parse_response(response, search, international=international)
    assert len(prices) == 1
    assert prices[0].available_seats is None


@pytest.mark.parametrize("leg", [0, 1])
def test_domestic_transfer_requires_every_leg_id(scraper, params, domestic_transfer_response, leg):
    first = domestic_transfer_response["data"]["flight"][0]
    first["transferFlight"] = first["transferFlight"][:1]
    segment = first if leg == 0 else first["transferFlight"][0]
    del segment["flightNo"]
    with pytest.raises(ParseError, match="含税"):
        scraper._parse_response(domestic_transfer_response, params, international=False)


@pytest.mark.parametrize("leg", [0, 1, 2])
def test_international_transfer_requires_every_leg_id(
    scraper, international_params, international_transfer_response, leg
):
    del international_transfer_response["data"]["flightItems"][0]["flightInfo"][0][
        "flightSegments"
    ][leg]["marketingFlightNo"]
    with pytest.raises(ParseError, match="含税"):
        scraper._parse_response(
            international_transfer_response, international_params, international=True
        )


@pytest.mark.parametrize("international", [False, True])
def test_wrong_search_date_is_rejected(
    scraper, params, international_params, domestic_response, international_response, international
):
    response = international_response if international else domestic_response
    search = international_params if international else params
    with pytest.raises(ParseError, match="含税"):
        scraper._parse_response(
            response, replace(search, departure_date=date(2026, 12, 9)), international=international
        )


@pytest.mark.parametrize("leg,field", [(0, "arrTime"), (1, "depTime")])
def test_domestic_transfer_requires_valid_intermediate_dates(
    scraper, params, domestic_transfer_response, leg, field
):
    first = domestic_transfer_response["data"]["flight"][0]
    first["transferFlight"] = first["transferFlight"][:1]
    segment = first if leg == 0 else first["transferFlight"][0]
    segment[field] = "not-a-date"
    with pytest.raises(ParseError, match="含税"):
        scraper._parse_response(domestic_transfer_response, params, international=False)


@pytest.mark.parametrize(
    "leg,field", [(0, "arrTimeStr"), (1, "depTimeStr"), (1, "arrTimeStr"), (2, "depTimeStr")]
)
def test_international_transfer_requires_valid_intermediate_dates(
    scraper, international_params, international_transfer_response, leg, field
):
    international_transfer_response["data"]["flightItems"][0]["flightInfo"][0]["flightSegments"][
        leg
    ][field] = "not-a-date"
    with pytest.raises(ParseError, match="含税"):
        scraper._parse_response(
            international_transfer_response, international_params, international=True
        )


@pytest.mark.parametrize("international", [False, True])
def test_parser_rejects_roundtrip_search_params(
    scraper, params, international_params, domestic_response, international_response, international
):
    response = international_response if international else domestic_response
    search = international_params if international else params
    with pytest.raises(ParseError, match="往返"):
        scraper._parse_response(
            response, replace(search, return_date=date(2026, 12, 15)), international=international
        )


def test_native_international_roundtrip_total_is_never_used_as_oneway(
    scraper, international_params, international_response
):
    item = international_response["data"]["flightItems"][0]
    item["flightInfo"].append(deepcopy(item["flightInfo"][0]))
    item["totalAdultPrice"] = 246912
    with pytest.raises(ParseError, match="含税"):
        scraper._parse_response(international_response, international_params, international=True)


@pytest.mark.parametrize(
    "data,finished",
    [
        ({"isContinue": True}, False),
        ({"flightItems": [], "isContinue": True}, False),
        ({"flightItems": [], "isContinue": False}, True),
        ({"flightItems": []}, False),
    ],
)
def test_international_ongoing_and_explicit_empty_are_distinct(
    scraper, international_params, data, finished
):
    assert scraper._parse_response(
        {"status": 200, "data": data}, international_params, international=True
    ) == ([], finished)


def test_domestic_explicit_empty_is_a_confirmed_empty_result(scraper, params):
    assert scraper._parse_response({"data": {"flight": []}}, params, international=False) == (
        [],
        True,
    )


@pytest.mark.parametrize("international", [False, True])
@pytest.mark.parametrize(
    "response", [{}, {"data": None}, {"data": []}, {"data": {}}, {"data": {"price": 99}}]
)
def test_missing_api_shape_never_becomes_fabricated_fare_or_empty_success(
    scraper, params, response, international
):
    response = {"status": 200, **response}
    with pytest.raises(ParseError):
        scraper._parse_response(response, params, international=international)


@pytest.mark.parametrize(
    "response",
    [
        {"status": 500, "data": {"flight": []}},
        {"status": 403, "data": {"flight": []}},
        {"status": 200, "errorMsg": "请登录", "data": {"flight": []}},
    ],
)
def test_api_errors_are_not_no_ticket_success(scraper, params, response):
    with pytest.raises(ScraperError, match="未成功"):
        scraper._parse_response(response, params, international=False)


def test_invalid_items_do_not_hide_valid_observed_quotes(scraper, params, domestic_response):
    domestic_response["data"]["flight"][:0] = [None, "invalid", {"cabin": {"price": 1}}]
    prices, finished = scraper._parse_response(domestic_response, params, international=False)
    assert finished is True
    assert [p.price for p in prices] == [Decimal("710.50")]


@pytest.mark.parametrize("status", [None, 0, 403, "200"])
def test_international_requires_explicit_success_status(
    scraper, international_params, international_response, status
):
    international_response["status"] = status
    with pytest.raises(ScraperError, match="未成功"):
        scraper._parse_response(international_response, international_params, international=True)


@pytest.mark.parametrize("field", ["depCityCode", "arrCityCode"])
def test_response_city_metadata_must_match_query(scraper, params, domestic_response, field):
    domestic_response["data"][field] = "CAN"
    with pytest.raises(ParseError, match="城市"):
        scraper._parse_response(domestic_response, params, international=False)


@pytest.mark.parametrize("transfer", [False, True])
def test_domestic_zero_base_fare_is_not_made_positive_by_taxes(
    scraper, params, domestic_response, domestic_transfer_response, transfer
):
    response = deepcopy(domestic_transfer_response if transfer else domestic_response)
    item = response["data"]["flight"][0]
    if transfer:
        for alternative in item["transferFlight"]:
            alternative["totalInfo"]["lowestPrice"] = 0
    else:
        item["isTransfer"] = False
        item["cabin"]["price"] = 0
    with pytest.raises(ParseError, match="含税"):
        scraper._parse_response(response, params, international=False)


@pytest.mark.parametrize("international", [False, True])
@pytest.mark.parametrize("endpoint", ["departure", "arrival"])
def test_route_endpoints_must_match_without_top_level_city_metadata(
    scraper,
    params,
    international_params,
    domestic_transfer_response,
    international_transfer_response,
    international,
    endpoint,
):
    if international:
        response, search = international_transfer_response, international_params
        segments = response["data"]["flightItems"][0]["flightInfo"][0]["flightSegments"]
        segment = segments[0] if endpoint == "departure" else segments[-1]
        field = "depAirportCode" if endpoint == "departure" else "arrAirportCode"
    else:
        response, search = domestic_transfer_response, params
        first = response["data"]["flight"][0]
        first["transferFlight"] = first["transferFlight"][:1]
        segment = first if endpoint == "departure" else first["transferFlight"][0]
        field = "depAirport" if endpoint == "departure" else "arrAirport"
    assert "depCityCode" not in response["data"] and "arrCityCode" not in response["data"]
    # Leave the display name untouched: the airport code must determine the route.
    segment[field] = "CAN"
    with pytest.raises(ParseError):
        scraper._parse_response(response, search, international=international)


@pytest.mark.parametrize(
    "origin,destination,dep_code,arr_code",
    [
        ("上海", "成都", "PVG", "TFU"),
        ("上海", "成都", "SHA", "CTU"),
        ("成都", "北京", "TFU", "PEK"),
        ("成都", "北京", "CTU", "PKX"),
        ("北京", "上海", "PEK", "PVG"),
        ("北京", "上海", "PKX", "SHA"),
    ],
)
def test_multi_airport_city_endpoints_are_accepted(
    scraper, domestic_response, origin, destination, dep_code, arr_code
):
    domestic_response["data"]["flight"][0].update(depAirport=dep_code, arrAirport=arr_code)
    prices, finished = scraper._parse_response(
        domestic_response, SearchParams(origin, destination, DAY), international=False
    )
    assert finished is True
    assert len(prices) == 1
    info = prices[0].flight_info
    assert (info.departure_city, info.arrival_city) == (origin, destination)
    assert (info.departure_airport_code, info.arrival_airport_code) == (dep_code, arr_code)


@pytest.mark.parametrize(
    "international,field",
    [(False, "depTime"), (False, "arrTime"), (True, "depTimeStr"), (True, "arrTimeStr")],
)
def test_date_only_segment_timestamps_are_not_fabricated_as_midnight(
    scraper,
    params,
    international_params,
    domestic_response,
    international_response,
    international,
    field,
):
    if international:
        response, search = international_response, international_params
        segment = response["data"]["flightItems"][0]["flightInfo"][0]["flightSegments"][0]
    else:
        response, search = domestic_response, params
        segment = response["data"]["flight"][0]
    segment[field] = segment[field].split(" ")[0]
    with pytest.raises(ParseError):
        scraper._parse_response(response, search, international=international)


@pytest.mark.parametrize("defect", ["first-arrival", "second-arrival", "connection-departure"])
def test_domestic_transfer_rejects_reverse_chronology(
    scraper, params, domestic_transfer_response, defect
):
    first = domestic_transfer_response["data"]["flight"][0]
    first["transferFlight"] = first["transferFlight"][:1]
    second = first["transferFlight"][0]
    if defect == "first-arrival":
        first["arrTime"] = "2026-12-10 22:20:00"
    elif defect == "second-arrival":
        second["arrTime"] = "2026-12-11 07:00:00"
    else:
        second["depTime"] = "2026-12-11 02:00:00"
    with pytest.raises(ParseError):
        scraper._parse_response(domestic_transfer_response, params, international=False)


@pytest.mark.parametrize("departure_time,valid", [("08:00:00", True), ("02:00:00", False)])
def test_same_city_airport_change_still_requires_forward_connection_time(
    scraper, params, domestic_transfer_response, departure_time, valid
):
    first = domestic_transfer_response["data"]["flight"][0]
    first["transferFlight"] = first["transferFlight"][:1]
    first["arrAirport"] = "PEK"
    first["transferFlight"][0].update(depAirport="PKX", depTime=f"2026-12-11 {departure_time}")
    if valid:
        prices, _ = scraper._parse_response(domestic_transfer_response, params, international=False)
        assert [p.flight_info.flight_no for p in prices] == ["MU5401+MU5843"]
    else:
        with pytest.raises(ParseError):
            scraper._parse_response(domestic_transfer_response, params, international=False)


@pytest.mark.parametrize("international,leg", [(False, 1), (True, 1), (True, 2)])
def test_every_connection_must_have_continuous_airports(
    scraper,
    params,
    international_params,
    domestic_transfer_response,
    international_transfer_response,
    international,
    leg,
):
    if international:
        response, search = international_transfer_response, international_params
        segments = response["data"]["flightItems"][0]["flightInfo"][0]["flightSegments"]
        segments[leg]["depAirportCode"] = "CAN"
    else:
        response, search = domestic_transfer_response, params
        first = response["data"]["flight"][0]
        first["transferFlight"] = first["transferFlight"][:1]
        first["transferFlight"][0]["depAirport"] = "CAN"
    with pytest.raises(ParseError):
        scraper._parse_response(response, search, international=international)


@pytest.mark.parametrize("leg", [1, 2])
def test_international_connection_cannot_depart_before_arrival_at_same_airport(
    scraper, international_params, international_transfer_response, leg
):
    segments = international_transfer_response["data"]["flightItems"][0]["flightInfo"][0][
        "flightSegments"
    ]
    segments[leg]["depTimeStr"] = "2026-12-11 02:00:00"
    with pytest.raises(ParseError):
        scraper._parse_response(
            international_transfer_response, international_params, international=True
        )


@pytest.mark.parametrize(
    "flight_no,origin,destination,dep_code,arr_code",
    [
        ("JL6", "东京", "洛杉矶", "NRT", "LAX"),
        ("SQ26", "新加坡", "法兰克福", "SIN", "FRA"),
    ],
)
def test_short_valid_flight_numbers_are_preserved(
    scraper, international_response, flight_no, origin, destination, dep_code, arr_code
):
    segment = international_response["data"]["flightItems"][0]["flightInfo"][0]["flightSegments"][0]
    segment.update(marketingFlightNo=flight_no, depAirportCode=dep_code, arrAirportCode=arr_code)
    prices, finished = scraper._parse_response(
        international_response, SearchParams(origin, destination, DAY), international=True
    )
    assert finished is True
    assert [p.flight_info.flight_no for p in prices] == [flight_no]
    assert [p.price for p in prices] == [Decimal("1234.56")]


def test_international_dateline_crossing_preserves_earlier_local_arrival_date(
    scraper, international_response
):
    segment = international_response["data"]["flightItems"][0]["flightInfo"][0]["flightSegments"][0]
    segment.update(
        marketingFlightNo="JL701",
        marketingAirlineName="日本航空",
        depAirportCode="NRT",
        arrAirportCode="LAX",
        depAirportName="成田国际机场",
        arrAirportName="洛杉矶国际机场",
        depTimeStr="2026-12-10 00:30:00",
        arrTimeStr="2026-12-09 17:30:00",
    )
    prices, finished = scraper._parse_response(
        international_response, SearchParams("东京", "洛杉矶", DAY), international=True
    )
    assert finished is True
    assert len(prices) == 1
    info = prices[0].flight_info
    assert (info.departure_airport_code, info.arrival_airport_code) == ("NRT", "LAX")
    assert (info.departure_date, info.arrival_date) == (DAY, date(2026, 12, 9))
    assert (info.departure_time, info.arrival_time) == ("00:30", "17:30")


class FakeClock:
    """Advance only the scraper's clock, without changing pytest's event loop."""

    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def time(self):
        return self.now

    async def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds
        await asyncio.sleep(0)


@pytest.fixture
def clock(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(
        module,
        "asyncio",
        SimpleNamespace(
            get_running_loop=lambda: clock,
            sleep=clock.sleep,
            create_task=asyncio.create_task,
            gather=asyncio.gather,
            wait_for=asyncio.wait_for,
            TimeoutError=asyncio.TimeoutError,
            CancelledError=asyncio.CancelledError,
        ),
    )
    return clock


class FakeResponse:
    def __init__(self, payload=None, *, url=DOMESTIC_API, status=200, text=None):
        self.url = url
        self.status = status
        self.ok = 200 <= status < 300
        self.text = AsyncMock(return_value=text if text is not None else json.dumps(payload))


class FakePage:
    """Only passive events are supported; interception and replay fail loudly."""

    def __init__(
        self, responses=(), *, document_status=200, redirect=None, frames=(), blocked_count=0
    ):
        self.responses = responses
        self.document_status = document_status
        self.redirect = redirect
        self.url = "about:blank"
        self.frames = [SimpleNamespace(url=url) for url in frames]
        self.listeners = {}
        self.events = []
        self.blocked_locator = SimpleNamespace(count=AsyncMock(return_value=blocked_count))
        self.selectors = []
        self.close = AsyncMock()
        self.goto = AsyncMock(side_effect=self._goto)
        self.route = Mock(side_effect=AssertionError("request interception forbidden"))
        self.evaluate = Mock(side_effect=AssertionError("script replay forbidden"))
        self.request = SimpleNamespace(
            get=Mock(side_effect=AssertionError("request replay forbidden")),
            post=Mock(side_effect=AssertionError("request replay forbidden")),
            fetch=Mock(side_effect=AssertionError("request replay forbidden")),
        )

    def on(self, event, callback):
        assert event == "response"
        self.events.append("listen")
        self.listeners[event] = callback

    def remove_listener(self, event, callback):
        assert self.listeners.pop(event) is callback
        self.events.append("unlisten")

    def locator(self, selector):
        self.selectors.append(selector)
        assert "iframe" in selector and "punish" in selector and "nc-container" in selector
        return self.blocked_locator

    async def _goto(self, url, **kwargs):
        assert "response" in self.listeners, "must subscribe before navigation"
        self.events.append("goto")
        self.url = self.redirect or url
        for response in self.responses:
            self.listeners["response"](response)
        await asyncio.sleep(0)
        return SimpleNamespace(ok=200 <= self.document_status < 300, status=self.document_status)

    def assert_clean_passive_capture(self):
        assert self.listeners == {}
        assert self.events == ["listen", "goto", "unlisten"]
        self.close.assert_awaited_once()
        self.route.assert_not_called()
        self.evaluate.assert_not_called()
        self.request.get.assert_not_called()
        self.request.post.assert_not_called()
        self.request.fetch.assert_not_called()


def attach_page(scraper, page):
    scraper._context = SimpleNamespace(new_page=AsyncMock(return_value=page), close=AsyncMock())


class BlockingCall:
    """A cooperative stall that records whether its awaiter was actually drained."""

    def __init__(self, result=None):
        self.result = result
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.finished = asyncio.Event()
        self.task = None

    async def __call__(self, *args, **kwargs):
        self.task = asyncio.current_task()
        self.started.set()
        try:
            await self.release.wait()
            return self.result
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        finally:
            self.finished.set()


@pytest.fixture
async def async_task_guard():
    """Drain regression-test tasks even when an outer watchdog or assertion fails."""
    existing = set(asyncio.all_tasks())

    def pending():
        return set(asyncio.all_tasks()) - existing - {asyncio.current_task()}

    try:
        yield pending
    finally:
        tasks = pending()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=1)


async def test_passive_domestic_jsonp_capture_no_route_or_replay(
    scraper, params, domestic_response, clock
):
    response = FakeResponse(text="flightSearch(" + json.dumps(domestic_response) + ");")
    page = FakePage([response])
    attach_page(scraper, page)
    prices = await scraper._search_oneway(params)
    assert [p.price for p in prices] == [Decimal("710.50")]
    response.text.assert_awaited_once()
    page.goto.assert_awaited_once_with(
        scraper._build_search_url(params), wait_until="domcontentloaded", timeout=1000
    )
    page.assert_clean_passive_capture()


async def test_passive_international_polling_deduplicates_cheapest_and_preserves_connections(
    scraper, international_params, international_response, clock
):
    initial = deepcopy(international_response)
    initial["data"]["isContinue"] = True
    initial["data"]["flightItems"][0]["totalAdultPrice"] = 150000
    another = deepcopy(international_response["data"]["flightItems"][0])
    another["flightInfo"][0]["flightSegments"][0]["marketingFlightNo"] = "JL876"
    another["totalAdultPrice"] = 200000
    international_response["data"]["flightItems"].append(another)
    expensive = deepcopy(international_response["data"]["flightItems"][0])
    expensive["totalAdultPrice"] = 180000
    international_response["data"]["flightItems"].append(expensive)
    page = FakePage(
        [
            FakeResponse({"status": 200, "data": {"isContinue": True}}, url=INTERNATIONAL_API),
            FakeResponse(initial, url=INTERNATIONAL_API),
            FakeResponse(international_response, url=INTERNATIONAL_API),
        ]
    )
    attach_page(scraper, page)
    prices = await scraper._search_oneway(international_params)
    assert [p.price for p in prices] == [Decimal("1234.56"), Decimal("2000")]
    assert [p.flight_info.flight_no for p in prices] == ["MU523", "JL876"]
    page.assert_clean_passive_capture()


@pytest.mark.parametrize(
    "url",
    [
        "https://sjipiao.fliggy.com.evil.example/searchow/search.htm",
        "https://evil.example/searchow/search.htm",
        "https://sjipiao.fliggy.com/searchrt/search.htm",
        "https://sjipiao.fliggy.com/searchow/search.htm/extra",
        INTERNATIONAL_API,
    ],
)
async def test_nonmatching_api_responses_are_ignored_not_replayed(
    scraper, params, domestic_response, clock, url
):
    response = FakeResponse(domestic_response, url=url)
    page = FakePage([response])
    attach_page(scraper, page)
    with pytest.raises(NetworkTimeoutError):
        await scraper._search_oneway(params)
    response.text.assert_not_awaited()
    page.assert_clean_passive_capture()


@pytest.mark.parametrize(
    "status,error",
    [
        (401, AntiCrawlerDetectedError),
        (403, AntiCrawlerDetectedError),
        (429, AntiCrawlerDetectedError),
        (500, ScraperError),
    ],
)
async def test_http_api_failures_surface_and_close_page(scraper, params, clock, status, error):
    response = FakeResponse(status=status)
    page = FakePage([response])
    attach_page(scraper, page)
    with pytest.raises(error):
        await scraper._search_oneway(params)
    response.text.assert_not_awaited()
    page.assert_clean_passive_capture()


@pytest.mark.parametrize("failure", ["garbage", "read-error", "api-error", "missing-data"])
async def test_bad_response_never_falls_back_to_fabricated_prices(scraper, params, clock, failure):
    response = FakeResponse(text="not json")
    if failure == "read-error":
        response.text.side_effect = RuntimeError("connection lost")
    elif failure == "api-error":
        response = FakeResponse({"status": 500, "data": {"flight": []}})
    elif failure == "missing-data":
        response = FakeResponse({"data": {}})
    page = FakePage([response])
    attach_page(scraper, page)
    with pytest.raises(ScraperError):
        await scraper._search_oneway(params)
    page.assert_clean_passive_capture()


async def test_no_response_times_out_without_sample_fallback(scraper, params, clock):
    page = FakePage()
    attach_page(scraper, page)
    with pytest.raises(NetworkTimeoutError):
        await scraper._search_oneway(params)
    assert clock.sleeps == [0.25] * 4
    page.assert_clean_passive_capture()


async def test_incomplete_poll_is_not_reported_as_success(
    scraper, international_params, international_response, clock
):
    international_response["data"]["isContinue"] = True
    page = FakePage([FakeResponse(international_response, url=INTERNATIONAL_API)])
    attach_page(scraper, page)
    with pytest.raises(NetworkTimeoutError, match="未完成全部响应"):
        await scraper._search_oneway(international_params)
    assert clock.sleeps == [0.25] * 4
    page.assert_clean_passive_capture()


@pytest.mark.parametrize("international", [False, True])
async def test_explicit_empty_api_completes_without_fallback(
    scraper, params, international_params, clock, international
):
    payload = (
        {"status": 200, "data": {"flightItems": [], "isContinue": False}}
        if international
        else {"data": {"flight": []}}
    )
    page = FakePage(
        [FakeResponse(payload, url=INTERNATIONAL_API if international else DOMESTIC_API)]
    )
    attach_page(scraper, page)
    assert await scraper._search_oneway(international_params if international else params) == []
    assert clock.sleeps == []
    page.assert_clean_passive_capture()


@pytest.mark.parametrize("failure_position", ["before-empty", "after-empty"])
async def test_observed_http_failure_is_not_hidden_by_terminal_empty(
    scraper, params, clock, failure_position
):
    failure = FakeResponse(status=500)
    empty = FakeResponse({"data": {"flight": []}})
    responses = [failure, empty] if failure_position == "before-empty" else [empty, failure]
    # Both responses are observed during goto, before the collector can finish.
    page = FakePage(responses)
    attach_page(scraper, page)
    with pytest.raises(ScraperError, match="HTTP 500"):
        await scraper._search_oneway(params)
    failure.text.assert_not_awaited()
    page.assert_clean_passive_capture()


async def test_terminal_empty_cannot_skip_an_earlier_unread_body(
    scraper, international_params, international_response, async_task_guard
):
    scraper.timeout = 30
    body = BlockingCall(json.dumps(international_response))
    earlier = FakeResponse(url=INTERNATIONAL_API)
    earlier.text.side_effect = body.__call__
    terminal = FakeResponse(
        {"status": 200, "data": {"flightItems": [], "isContinue": False}},
        url=INTERNATIONAL_API,
    )
    page = FakePage([earlier, terminal])
    attach_page(scraper, page)
    task = asyncio.create_task(scraper._search_oneway(international_params))
    try:
        with pytest.raises(NetworkTimeoutError):
            await asyncio.wait_for(asyncio.shield(task), timeout=1)
        terminal.text.assert_awaited_once()
        assert body.started.is_set()
        assert body.cancelled.is_set() and body.finished.is_set()
        assert body.task.done()
        page.assert_clean_passive_capture()
        assert not async_task_guard(), "the timed-out reader must not remain in the event loop"
    finally:
        body.release.set()


async def test_earlier_valid_body_released_after_terminal_empty_is_retained(
    scraper, international_params, international_response, async_task_guard
):
    international_response["data"]["isContinue"] = True
    body = BlockingCall(json.dumps(international_response))
    earlier = FakeResponse(url=INTERNATIONAL_API)
    earlier.text.side_effect = body.__call__
    terminal_read = asyncio.Event()

    async def terminal_text():
        terminal_read.set()
        return json.dumps({"status": 200, "data": {"flightItems": [], "isContinue": False}})

    terminal = FakeResponse(url=INTERNATIONAL_API)
    terminal.text.side_effect = terminal_text
    page = FakePage([earlier, terminal])
    attach_page(scraper, page)
    task = asyncio.create_task(scraper._search_oneway(international_params))
    try:
        await asyncio.wait_for(body.started.wait(), timeout=1)
        await asyncio.wait_for(terminal_read.wait(), timeout=1)
        done, _ = await asyncio.wait({task}, timeout=0.03)
        assert not done, "terminal empty must wait for an already-observed response body"
        assert not body.finished.is_set(), "the earlier body must not be cancelled on empty"
        body.release.set()
        prices = await asyncio.wait_for(asyncio.shield(task), timeout=1)
        assert [p.price for p in prices] == [Decimal("1234.56")]
        assert [p.flight_info.flight_no for p in prices] == ["MU523"]
        assert body.finished.is_set() and not body.cancelled.is_set()
        earlier.text.assert_awaited_once()
        terminal.text.assert_awaited_once()
        page.assert_clean_passive_capture()
        assert not async_task_guard()
    finally:
        body.release.set()


@pytest.mark.parametrize(
    "frames,count",
    [
        (["https://sjipiao.fliggy.com/_____tmd_____/punish"], 0),
        (["https://sec.taobao.com/punish?token=redacted"], 0),
        ([], 1),
    ],
)
async def test_blocked_iframe_is_detected_without_opening_desktop(
    scraper, params, clock, frames, count
):
    page = FakePage(frames=frames, blocked_count=count)
    attach_page(scraper, page)
    with pytest.raises(AntiCrawlerDetectedError, match="不会自动弹窗"):
        await scraper._search_oneway(params)
    module.async_playwright.assert_not_called()
    page.assert_clean_passive_capture()


async def test_login_redirect_asks_for_settings_login_without_desktop(scraper, params, clock):
    page = FakePage(redirect="https://login.taobao.com/member/login.jhtml")
    attach_page(scraper, page)
    with pytest.raises(ScraperError, match="登录"):
        await scraper._search_oneway(params)
    module.async_playwright.assert_not_called()
    page.assert_clean_passive_capture()


async def test_document_http_error_closes_page(scraper, params, clock):
    page = FakePage(document_status=503)
    attach_page(scraper, page)
    with pytest.raises(ScraperError, match="503"):
        await scraper._search_oneway(params)
    page.assert_clean_passive_capture()


async def test_navigation_timeout_converts_error_and_removes_listener(scraper, params, clock):
    page = FakePage()
    page.goto.side_effect = PlaywrightTimeoutError("stalled")
    attach_page(scraper, page)
    with pytest.raises(NetworkTimeoutError, match="超时"):
        await scraper._search_oneway(params)
    assert page.listeners == {}
    page.close.assert_awaited_once()


async def test_cancellation_cancels_pending_response_tasks_and_closes_page(scraper, params, clock):
    reading = asyncio.Event()
    release = asyncio.Event()
    cancelled = asyncio.Event()

    async def slow_text():
        reading.set()
        try:
            await release.wait()
        finally:
            cancelled.set()
        return "{}"

    response = FakeResponse()
    response.text.side_effect = slow_text
    page = FakePage([response])
    attach_page(scraper, page)
    task = asyncio.create_task(scraper._search_oneway(params))
    try:
        await reading.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cancelled.is_set(), "response reader must be cancelled and awaited"
        page.assert_clean_passive_capture()
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("stage", ["new-page", "navigation", "locator", "body"])
async def test_search_deadline_bounds_stalled_browser_operations(
    scraper, params, async_task_guard, stage
):
    scraper.timeout = 30
    stalled = BlockingCall()
    response = FakeResponse({"data": {"flight": []}})
    page = FakePage([response])
    attach_page(scraper, page)
    operation = {
        "new-page": scraper._context.new_page,
        "navigation": page.goto,
        "locator": page.blocked_locator.count,
        "body": response.text,
    }[stage]
    operation.side_effect = stalled.__call__
    task = asyncio.create_task(scraper._search_oneway(params))
    try:
        # Shield makes this a watchdog, not the source of the scraper's timeout.
        with pytest.raises(NetworkTimeoutError):
            await asyncio.wait_for(asyncio.shield(task), timeout=1)
        assert stalled.started.is_set()
        assert stalled.cancelled.is_set() and stalled.finished.is_set()
        assert stalled.task.done()
        assert page.listeners == {}
        if stage == "new-page":
            page.close.assert_not_awaited()
        else:
            page.close.assert_awaited_once()
        assert not async_task_guard(), "deadline expiry must cancel and drain pending work"
    finally:
        stalled.release.set()


@pytest.mark.parametrize("outcome", ["success", "timeout", "cancelled", "cancel-during-close"])
async def test_stalled_page_close_is_bounded_and_retires_session(
    scraper, params, async_task_guard, outcome
):
    scraper.timeout = 30
    body = BlockingCall()
    closing = BlockingCall()
    response = FakeResponse({"data": {"flight": []}})
    if outcome in {"timeout", "cancelled"}:
        response.text.side_effect = body.__call__
    page = FakePage([response])
    page.close.side_effect = closing.__call__
    attach_page(scraper, page)
    context = scraper._context
    browser = SimpleNamespace(close=AsyncMock())
    playwright = SimpleNamespace(stop=AsyncMock())
    scraper._browser, scraper._playwright = browser, playwright
    task = asyncio.create_task(scraper._search_oneway(params))
    try:
        if outcome in {"cancelled", "cancel-during-close"}:
            started = body.started if outcome == "cancelled" else closing.started
            await asyncio.wait_for(started.wait(), timeout=1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(asyncio.shield(task), timeout=1)
        elif outcome == "timeout":
            with pytest.raises(NetworkTimeoutError):
                await asyncio.wait_for(asyncio.shield(task), timeout=1)
        else:
            assert await asyncio.wait_for(asyncio.shield(task), timeout=1) == []
        assert closing.started.is_set(), "cleanup needs its own budget after search expiry"
        assert closing.cancelled.is_set() and closing.finished.is_set()
        assert closing.task.done()
        if outcome in {"timeout", "cancelled"}:
            assert body.cancelled.is_set() and body.finished.is_set()
            assert body.task.done()
        page.assert_clean_passive_capture()
        context.close.assert_awaited_once()
        browser.close.assert_awaited_once()
        playwright.stop.assert_awaited_once()
        assert scraper._context is scraper._browser is scraper._playwright is None
        assert not async_task_guard(), "cleanup must leave neither readers nor close tasks"
    finally:
        body.release.set()
        closing.release.set()


async def test_roundtrip_runs_two_true_oneway_queries_and_marks_return_only(
    scraper, params, domestic_response, clock
):
    inbound = deepcopy(domestic_response)
    inbound["data"]["flight"][0].update(
        flightNo="MU5402",
        depAirport="TFU",
        arrAirport="SHA",
        depTime="2026-12-15 10:00:00",
        arrTime="2026-12-15 12:40:00",
    )
    inbound["data"]["flight"][0]["cabin"]["price"] = 500
    pages = [FakePage([FakeResponse(domestic_response)]), FakePage([FakeResponse(inbound)])]
    scraper._context = SimpleNamespace(new_page=AsyncMock(side_effect=pages), close=AsyncMock())
    scraper._ensure_browser = AsyncMock()
    search = replace(params, return_date=date(2026, 12, 15))
    prices = await scraper.search_flights(search)
    assert [p.price for p in prices] == [Decimal("710.50"), Decimal("570")]
    assert [p.flight_info.direction for p in prices] == [
        FlightDirection.DEPARTURE,
        FlightDirection.RETURN,
    ]
    assert all(p.return_flight_info is None for p in prices), "pairing belongs to the scheduler"
    assert (prices[1].flight_info.departure_city, prices[1].flight_info.arrival_city) == (
        "成都",
        "上海",
    )
    assert prices[1].flight_info.departure_date == date(2026, 12, 15)
    for page, departure, arrival, day in zip(
        pages, ["SHA", "CTU"], ["CTU", "SHA"], ["2026-12-10", "2026-12-15"], strict=True
    ):
        query = parse_qs(urlsplit(page.goto.call_args.args[0]).query)
        assert query["tripType"] == ["0"]
        assert query["depCity"] == [departure]
        assert query["arrCity"] == [arrival]
        assert query["depDate"] == [day]
        assert "returnDate" not in query and "arrDate" not in query
        page.assert_clean_passive_capture()
    assert search.return_date == date(2026, 12, 15)


@pytest.mark.parametrize("missing_leg", ["outbound", "inbound"])
async def test_roundtrip_requires_both_legs(scraper, params, domestic_response, missing_leg):
    prices, _ = scraper._parse_response(domestic_response, params, international=False)
    scraper._ensure_browser = AsyncMock()
    scraper._search_oneway = AsyncMock(
        side_effect=[[]] if missing_leg == "outbound" else [prices, []]
    )
    assert await scraper.search_flights(replace(params, return_date=date(2026, 12, 15))) == []
    assert scraper._search_oneway.await_count == (1 if missing_leg == "outbound" else 2)


async def test_default_browser_is_headless_and_reused(scraper, monkeypatch):
    context = SimpleNamespace(add_cookies=AsyncMock(), close=AsyncMock())
    browser = SimpleNamespace(new_context=AsyncMock(return_value=context), close=AsyncMock())
    playwright = SimpleNamespace(
        chromium=SimpleNamespace(launch=AsyncMock(return_value=browser)), stop=AsyncMock()
    )
    start = AsyncMock(return_value=playwright)
    monkeypatch.setattr(module, "async_playwright", Mock(return_value=SimpleNamespace(start=start)))
    scraper.cookies = [
        {"name": "cookie2", "value": "test-only", "domain": ".taobao.com", "path": "/"}
    ]
    await scraper._ensure_browser()
    await scraper._ensure_browser()
    start.assert_awaited_once()
    playwright.chromium.launch.assert_awaited_once_with(headless=True)
    browser.new_context.assert_awaited_once_with(
        locale="zh-CN", timezone_id="Asia/Shanghai", viewport={"width": 1440, "height": 900}
    )
    context.add_cookies.assert_awaited_once_with(scraper.cookies)
    await scraper.close()
    await scraper.close()
    context.close.assert_awaited_once()
    browser.close.assert_awaited_once()
    playwright.stop.assert_awaited_once()
    assert scraper._context is scraper._browser is scraper._playwright is None


@pytest.mark.parametrize("stage", ["launch", "context", "cookies"])
@pytest.mark.parametrize("cancelled", [False, True])
async def test_browser_initialization_failure_releases_created_resources(
    scraper, monkeypatch, stage, cancelled
):
    context = SimpleNamespace(add_cookies=AsyncMock(), close=AsyncMock())
    browser = SimpleNamespace(new_context=AsyncMock(return_value=context), close=AsyncMock())
    playwright = SimpleNamespace(
        chromium=SimpleNamespace(launch=AsyncMock(return_value=browser)), stop=AsyncMock()
    )
    monkeypatch.setattr(
        module,
        "async_playwright",
        Mock(return_value=SimpleNamespace(start=AsyncMock(return_value=playwright))),
    )
    scraper.cookies = [{"name": "cookie2", "value": "test", "domain": ".taobao.com"}]
    error = asyncio.CancelledError if cancelled else RuntimeError
    operation = {
        "launch": playwright.chromium.launch,
        "context": browser.new_context,
        "cookies": context.add_cookies,
    }[stage]
    operation.side_effect = error("initialization interrupted")
    with pytest.raises(error):
        await scraper._ensure_browser()
    playwright.stop.assert_awaited_once()
    assert browser.close.await_count == (0 if stage == "launch" else 1)
    assert context.close.await_count == (1 if stage == "cookies" else 0)
    assert scraper._context is scraper._browser is scraper._playwright is None


async def test_close_continues_after_context_close_error(scraper):
    context = SimpleNamespace(close=AsyncMock(side_effect=RuntimeError("already disconnected")))
    browser = SimpleNamespace(close=AsyncMock())
    playwright = SimpleNamespace(stop=AsyncMock())
    scraper._context, scraper._browser, scraper._playwright = context, browser, playwright
    await scraper.close()
    browser.close.assert_awaited_once()
    playwright.stop.assert_awaited_once()
    assert scraper._context is scraper._browser is scraper._playwright is None


@pytest.mark.parametrize(
    "domain",
    [
        "fliggy.com",
        ".fliggy.com",
        "sjipiao.fliggy.com",
        ".taobao.com",
        "LOGIN.TAOBAO.COM",
        ".alitrip.com",
        "www.alitrip.com",
    ],
)
def test_cookie_scope_accepts_only_platform_roots_and_subdomains(domain):
    cookie = {
        "name": "cookie2",
        "value": "test-only",
        "domain": domain,
        "path": "/",
        "secure": True,
        "httpOnly": True,
        "sameSite": "None",
        "expires": 1900000000,
        "extra": "discard",
    }
    expected = {key: value for key, value in cookie.items() if key != "extra"}
    expected["domain"] = domain.lower()
    assert FliggyScraper._normalize_cookies([cookie]) == [expected]


@pytest.mark.parametrize(
    "domain",
    [
        "evilfliggy.com",
        "fliggy.com.evil.example",
        "taobao.com.evil.example",
        "notalitrip.com",
        ".qunar.com",
        ".ly.com",
        "",
        None,
    ],
)
def test_cookie_scope_rejects_foreign_and_lookalike_domains(domain):
    assert (
        FliggyScraper._normalize_cookies([{"name": "cookie2", "value": "test", "domain": domain}])
        == []
    )


def test_cookie_normalization_rejects_malformed_entries_without_mutation():
    cookies = [
        None,
        "raw",
        {},
        {"name": 1, "value": "bad"},
        {"name": "bad", "value": None},
        {"name": "cookie2", "value": "a=b"},
    ]
    original = deepcopy(cookies)
    assert FliggyScraper._normalize_cookies(cookies) == [
        {"name": "cookie2", "value": "a=b", "domain": ".fliggy.com", "path": "/"}
    ]
    assert cookies == original


@pytest.mark.parametrize(
    "text", ["cookie2=a=b; unb=123", "Cookie: cookie2=a=b; unb=123; malformed"]
)
def test_cookie_header_import_preserves_equals_in_values(tmp_path, text):
    path = tmp_path / "cookies.txt"
    path.write_text(text, encoding="utf-8")
    assert FliggyScraper.load_cookies_from_file(str(path)) == [
        {"name": "cookie2", "value": "a=b", "domain": ".fliggy.com", "path": "/"},
        {"name": "unb", "value": "123", "domain": ".fliggy.com", "path": "/"},
    ]


def test_cookie_json_import_filters_foreign_domains(tmp_path):
    path = tmp_path / "cookies.json"
    path.write_text(
        json.dumps(
            [
                {"name": "cookie2", "value": "test", "domain": ".taobao.com"},
                {"name": "foreign", "value": "test", "domain": ".example.com"},
            ]
        ),
        encoding="utf-8",
    )
    assert FliggyScraper.load_cookies_from_file(str(path)) == [
        {"name": "cookie2", "value": "test", "domain": ".taobao.com", "path": "/"}
    ]


@pytest.mark.parametrize("text", ["", "[broken-json", "[]"])
def test_unreadable_or_empty_cookie_file_has_no_fabricated_login(tmp_path, text):
    path = tmp_path / "cookies.json"
    assert FliggyScraper.load_cookies_from_file(str(path)) == []
    path.write_text(text, encoding="utf-8")
    assert FliggyScraper.load_cookies_from_file(str(path)) == []


async def test_cookie_change_then_deletion_retires_authenticated_context(tmp_path):
    path = tmp_path / "cookies.json"
    path.write_text('[{"name":"cookie2","value":"old","domain":".taobao.com"}]', encoding="utf-8")
    scraper = FliggyScraper(cookies_file=str(path))
    first = SimpleNamespace(close=AsyncMock())
    browser = SimpleNamespace(close=AsyncMock())
    scraper._context, scraper._browser = first, browser
    assert await scraper.reload_cookies_if_changed() is False
    first.close.assert_not_awaited()
    old_mtime = path.stat().st_mtime_ns
    path.write_text('[{"name":"cookie2","value":"new","domain":".taobao.com"}]', encoding="utf-8")
    os.utime(path, ns=(old_mtime + 1_000_000_000, old_mtime + 1_000_000_000))
    assert await scraper.reload_cookies_if_changed() is True
    assert [c["value"] for c in scraper.cookies] == ["new"]
    first.close.assert_awaited_once()
    assert scraper._context is None
    second = SimpleNamespace(close=AsyncMock())
    scraper._context = second
    path.unlink()
    assert await scraper.reload_cookies_if_changed() is True
    assert scraper.cookies == []
    assert scraper._context is None
    second.close.assert_awaited_once()
    assert await scraper.reload_cookies_if_changed() is False
    browser.close.assert_not_awaited()


async def test_cookie_file_appearance_is_loaded_without_restart(tmp_path):
    path = tmp_path / "cookies.json"
    scraper = FliggyScraper(cookies_file=str(path))
    assert scraper.cookies == []
    path.write_text("cookie2=new; unb=123", encoding="utf-8")
    assert await scraper.reload_cookies_if_changed() is True
    assert {c["name"] for c in scraper.cookies} == {"cookie2", "unb"}


async def test_explicit_cookie_list_never_reads_or_reloads_file(tmp_path, monkeypatch):
    reader = Mock(side_effect=AssertionError("must not read login file"))
    monkeypatch.setattr(FliggyScraper, "load_cookies_from_file", reader)
    scraper = FliggyScraper(cookies=[], cookies_file=str(tmp_path / "cookies.json"))
    assert scraper.cookies == []
    assert await scraper.reload_cookies_if_changed() is False
    reader.assert_not_called()
