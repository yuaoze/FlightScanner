"""同程旅行采集器的离线契约测试。"""

from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse

import pytest

from flightscanner.interfaces import FlightDirection, NetworkTimeoutError, SearchParams
from flightscanner.scrapers.tongcheng_scraper import TongchengScraper


@pytest.fixture
def scraper(tmp_path):
    return TongchengScraper(
        timeout=1000,
        max_results=20,
        cookies_file=str(tmp_path / "missing.json"),
    )


@pytest.fixture
def params():
    return SearchParams("上海", "成都", date(2026, 9, 10))


@pytest.fixture
def real_shape_response():
    """2026-08 抓取的 getflightlist 响应字段形状（值已裁剪/脱敏）。"""
    return {
        "resCode": 0,
        "body": {
            "FlightInfoSimpleList": [
                {
                    "flightNo": "9C7685",
                    "airCompanyName": "春秋航空",
                    "flyOffTime": "2026-09-10 23:20",
                    "arrivalTime": "2026-09-11 02:40",
                    "originAirportCode": "SHA",
                    "arriveAirportCode": "CTU",
                    "originAirportShortName": "虹桥国际机场",
                    "arriveAirportShortName": "天府国际机场",
                    "boardPoint": "T1",
                    "atsn": "T2",
                    "lcp": 640,
                    "lcn": "Y",
                    "lcd": "2.1折经济舱",
                    # 真实响应中该字段常为 0，但该报价仍然可售。
                    "availableTickets": 0,
                    "productPrices": {"0": 640},
                },
                {
                    "flightNo": "HO1039",
                    "airCompanyName": "吉祥航空",
                    "flyOffTime": "2026-09-10 09:20",
                    "arrivalTime": "2026-09-10 12:35",
                    "originAirportCode": "SHA",
                    "arriveAirportCode": "CTU",
                    "lcp": 730,
                    "lecp": 699,
                    "lecn": "C",
                    "lecd": "商务舱",
                    "seatCount": "仅剩2张",
                },
            ]
        },
    }


def test_build_search_url_uses_official_oneway_route(scraper, params):
    parsed = urlparse(scraper._build_search_url(params))

    assert parsed.scheme == "https"
    assert parsed.netloc == "www.ly.com"
    assert parsed.path == "/flights/itinerary/oneway/SHA-CTU"
    assert parse_qs(parsed.query) == {"date": ["2026-09-10"]}


def test_build_iflight_url_uses_official_international_contract(scraper):
    params = SearchParams("上海", "香港", date(2026, 9, 5))

    parsed = urlparse(scraper._build_iflight_search_url(params))
    query = parse_qs(parsed.query, keep_blank_values=True)

    assert parsed.scheme == "https"
    assert parsed.netloc == "www.ly.com"
    assert parsed.path == "/iflight/book1.html"
    assert query["advanced"] == ["false"]
    assert query["para"] == ["SHA*HKG*2026-09-05**OW*1_0_0*Y"]
    assert query["departureCityIsInter"] == ["0"]
    assert query["arrivalCity"] == ["中国香港"]
    assert query["arrivalCityIsInter"] == ["1"]


def test_build_iflight_roundtrip_url_uses_rt_contract(scraper):
    params = SearchParams("上海", "香港", date(2026, 9, 5), date(2026, 9, 9))

    query = parse_qs(
        urlparse(scraper._build_iflight_search_url(params)).query,
        keep_blank_values=True,
    )

    assert query["para"] == ["SHA*HKG*2026-09-05*2026-09-09*RT*1_0_0*Y"]


def test_beijing_uses_tongcheng_platform_alias(scraper):
    params = SearchParams("北京", "上海", date(2026, 9, 10))

    assert "/PEK-SHA" in scraper._build_search_url(params)


def test_unknown_city_is_rejected(scraper):
    params = SearchParams("不存在的城市", "上海", date(2026, 9, 10))

    with pytest.raises(ValueError, match="未知城市"):
        scraper._build_search_url(params)


@pytest.mark.asyncio
async def test_return_date_before_departure_is_rejected(scraper):
    params = SearchParams("上海", "成都", date(2026, 9, 10), date(2026, 9, 9))

    with pytest.raises(ValueError, match="回程日期"):
        await scraper.search_flights(params)


def test_parse_real_api_shape(scraper, params, real_shape_response):
    results = scraper._parse_api_response(real_shape_response, params)

    assert len(results) == 2
    first = results[0]
    assert first.flight_info.flight_no == "9C7685"
    assert first.flight_info.airline == "春秋航空"
    assert first.flight_info.departure_time == "23:20"
    assert first.flight_info.arrival_time == "02:40"
    assert first.flight_info.arrival_date == date(2026, 9, 11)
    assert first.flight_info.departure_airport == "虹桥国际机场T1"
    assert first.flight_info.arrival_airport == "天府国际机场T2"
    assert first.flight_info.departure_airport_code == "SHA"
    assert first.flight_info.arrival_airport_code == "CTU"
    assert first.price == Decimal("640")
    assert first.currency == "CNY"
    assert first.seat_class == "经济舱"
    assert first.available_seats is None
    assert first.source == "tongcheng"
    assert first.scraped_at.tzinfo is not None


def test_parse_iflight_real_incremental_response_uses_tax_included_price(scraper):
    params = SearchParams("上海", "香港", date(2026, 9, 5))
    response = {
        "url": "https://www.ly.com/miflightapi/ts/list",
        "status": 200,
        "data": {
            "code": 200,
            "data": {
                "result": True,
                "done": True,
                "tid": "redacted",
                "res": [
                    {
                        "acs": [{"an": "HO1291", "acn": "吉祥航空", "ac": "HO"}],
                        "dants": [{"an": "浦东国际机场", "at": "T2", "ac": "PVG"}],
                        "aants": [{"an": "香港国际机场", "at": "T1", "ac": "HKG"}],
                        "fdate": ["2026-09-05"],
                        "ftime": ["08:40"],
                        "adate": ["2026-09-05"],
                        "atime": ["11:25"],
                        "dt": "08:40",
                        "at": "11:25",
                        "sp": 318,
                        "tp": 543,
                        "cb": "Y",
                    }
                ],
            },
        },
    }

    results = scraper._parse_iflight_responses([response], params)

    assert len(results) == 1
    result = results[0]
    assert result.flight_info.flight_no == "HO1291"
    assert result.flight_info.airline == "吉祥航空"
    assert result.flight_info.departure_airport == "浦东国际机场T2"
    assert result.flight_info.arrival_airport == "香港国际机场T1"
    assert result.flight_info.departure_airport_code == "PVG"
    assert result.flight_info.arrival_airport_code == "HKG"
    assert result.price == Decimal("543")
    assert result.price != Decimal("318")
    assert result.currency == "CNY"
    assert result.seat_class == "经济舱"
    assert result.source == "tongcheng"


def test_parse_iflight_multisegment_itinerary_preserves_all_flight_numbers(scraper):
    params = SearchParams("上海", "悉尼", date(2026, 9, 5))
    row = {
        "acs": [
            {"an": "CZ3548", "acn": "中国南方航空"},
            {"an": "CZ301", "acn": "中国南方航空"},
        ],
        "dants": [{"an": "虹桥国际机场", "at": "T2", "ac": "SHA"}],
        "aants": [{"an": "悉尼机场", "at": "T1", "ac": "SYD"}],
        "fdate": ["2026-09-05", "2026-09-05"],
        "adate": ["2026-09-05", "2026-09-06"],
        "dt": "08:00",
        "at": "09:20",
        "tp": 2888,
    }

    result = scraper._parse_iflight_row(row, params, FlightDirection.DEPARTURE)

    assert result is not None
    assert result.flight_info.flight_no == "CZ3548+CZ301"
    assert result.flight_info.airline == "中国南方航空"
    assert result.flight_info.arrival_date == date(2026, 9, 6)


def test_iflight_does_not_mix_untaxed_sp_with_domestic_prices(scraper):
    params = SearchParams("上海", "香港", date(2026, 9, 5))
    row = {
        "acs": [{"an": "HO1291", "acn": "吉祥航空"}],
        "dt": "08:40",
        "at": "11:25",
        "sp": 318,
    }

    assert scraper._parse_iflight_row(row, params, FlightDirection.DEPARTURE) is None


def test_iflight_valid_empty_is_distinct_from_antispider_empty_object(scraper):
    valid_empty = [{
        "status": 200,
        "data": {"code": 200, "data": {"result": True, "done": True, "res": []}},
    }]
    protected = [{"status": 200, "data": {}}]

    assert scraper._has_valid_empty_iflight_result(valid_empty) is True
    assert scraper._has_valid_empty_iflight_result(protected) is False
    assert scraper._is_malformed_iflight_payload(
        scraper._unwrap_iflight_payload(protected[0]["data"])
    ) is True


def test_iflight_polling_keeps_latest_complete_snapshot_and_resolves_patch(scraper):
    complete = {
        "flightKey": "HO1291D05",
        "acs": [{"an": "HO1291"}],
        "dt": "16:45",
        "at": "19:40",
        "tp": 543,
    }
    responses = [
        {"status": 200, "data": {"result": True, "done": False, "res": [complete]}},
        {"status": 200, "data": {"result": True, "done": False, "res": []}},
        {
            "status": 200,
            "data": {
                "result": True,
                "done": True,
                "res": [{"pc": 1, "flightKey": "HO1291D05"}],
            },
        },
    ]

    assert scraper._latest_iflight_rows(responses) == [complete]


def test_parse_iflight_roundtrip_detail_uses_real_two_leg_product(scraper):
    params = SearchParams("上海", "香港", date(2026, 9, 5), date(2026, 9, 9))
    summary = {"tp": 1200, "cb": "Y", "rt": 3}
    detail = {
        "flights": [
            [
                {
                    "flightNumber": "MU725",
                    "aircode": "MU",
                    "departureCode": "PVG",
                    "departureTerminal": "T1",
                    "departureDate": "2026-09-05 21:05",
                    "arrivalCode": "HKG",
                    "arrivalTerminal": "T1",
                    "arrivalDate": "2026-09-05 23:45",
                }
            ],
            [
                {
                    "flightNumber": "MU508",
                    "aircode": "MU",
                    "departureCode": "HKG",
                    "departureTerminal": "T1",
                    "departureDate": "2026-09-09 13:50",
                    "arrivalCode": "PVG",
                    "arrivalTerminal": "T1",
                    "arrivalDate": "2026-09-09 16:35",
                }
            ],
        ],
        "products": [
            {
                "memberOnly": True,
                "priceInfos": {"ADT": {"saleTotalPrice": 899}},
            },
            {
                "priceInfos": {
                    "ADT": {"saleTotalPrice": 950, "couponOnly": True}
                },
            },
            {"priceInfos": {"ADT": {"saleTotalPrice": 1280}}},
            {"priceInfos": {"ADT": {"saleTotalPrice": 1151}}},
            {"priceInfos": {"ADT": {"saleTotalPrice": 0}}},
        ],
    }

    result = scraper._parse_iflight_roundtrip_detail(detail, summary, params)

    assert result is not None
    assert result.price == Decimal("1151")
    assert result.flight_info.flight_no == "MU725"
    assert result.flight_info.departure_date == date(2026, 9, 5)
    assert result.return_flight_info is not None
    assert result.return_flight_info.flight_no == "MU508"
    assert result.return_flight_info.departure_date == date(2026, 9, 9)
    assert result.available_seats == 3


def test_parse_iflight_roundtrip_detail_falls_back_to_list_total(scraper):
    params = SearchParams("上海", "香港", date(2026, 9, 5), date(2026, 9, 9))

    def segments(no, dep, arr, dep_code, arr_code):
        return [{
            "flightNumber": no,
            "aircode": no[:2],
            "departureCode": dep_code,
            "departureDate": dep,
            "arrivalCode": arr_code,
            "arrivalDate": arr,
        }]
    detail = {
        "flights": [
            segments("MU725", "2026-09-05 21:05", "2026-09-05 23:45", "PVG", "HKG"),
            segments("MU508", "2026-09-09 13:50", "2026-09-09 16:35", "HKG", "PVG"),
        ],
        "products": [],
    }

    result = scraper._parse_iflight_roundtrip_detail(detail, {"tp": 1200}, params)

    assert result is not None
    assert result.price == Decimal("1200")


def test_selects_lowest_public_fare_and_matching_cabin(scraper, params, real_shape_response):
    result = scraper._parse_api_response(real_shape_response, params)[1]

    assert result.price == Decimal("699")
    assert result.seat_class == "商务舱"
    assert result.available_seats == 2


@pytest.mark.parametrize("value", [0, -1, None, "", "NaN", float("inf")])
def test_invalid_prices_are_rejected(value):
    assert TongchengScraper._to_positive_decimal(value) is None


def test_product_prices_is_only_a_fallback(scraper, params):
    row = {
        "flightNo": "MU5101",
        "flyOffTime": "2026-09-10 08:00",
        "arrivalTime": "2026-09-10 11:00",
        "lcp": 800,
        "productPrices": {"member": 500},
    }

    result = scraper._parse_api_row(row, params, FlightDirection.DEPARTURE)

    assert result is not None
    assert result.price == Decimal("800")


def test_bad_record_does_not_hide_good_record(scraper, params, real_shape_response):
    rows = real_shape_response["body"]["FlightInfoSimpleList"]
    rows.insert(0, {"flightNo": "", "lcp": 100})
    rows.insert(1, {"flightNo": "CA1234", "lcp": "NaN"})

    results = scraper._parse_api_response(real_shape_response, params)

    assert {result.flight_info.flight_no for result in results} == {"9C7685", "HO1039"}


@pytest.mark.parametrize(
    "payload",
    [None, [], {}, {"resCode": 1, "body": {}}, {"resCode": 0, "body": []}],
)
def test_business_error_or_unknown_wrapper_returns_empty(scraper, params, payload):
    assert scraper._parse_api_response(payload, params) == []


def test_explicit_sold_out_row_is_skipped(scraper, params):
    row = {
        "flightNo": "CA1234",
        "flyOffTime": "2026-09-10 08:00",
        "arrivalTime": "2026-09-10 11:00",
        "lcp": 800,
        "saleStatus": "已售罄",
    }

    assert scraper._parse_api_row(row, params, FlightDirection.DEPARTURE) is None


def test_response_filter_ignores_unrelated_analytics_json(scraper, params, real_shape_response):
    responses = [
        {"url": "https://analytics.example/collect", "data": real_shape_response},
        {"url": "https://www.ly.com/flights/api/getflightlist", "data": real_shape_response},
    ]

    assert len(scraper._parse_api_responses(responses, params)) == 2


def test_dom_fallback_parses_real_card_shape(scraper, params):
    html = """
    <section class="flight-item">
      <div class="flight-item-name">吉祥航空HO1039</div>
      <div class="f-startTime"><strong>09:20</strong><em>虹桥T2</em></div>
      <div class="f-endTime"><strong>12:35</strong><em>天府T2</em></div>
      <div class="head-prices"><strong><em>¥500</em></strong><i>1.8折经济舱</i></div>
      <span>仅剩2张</span>
    </section>
    """

    results = scraper._parse_dom_html(html, params)

    assert len(results) == 1
    result = results[0]
    assert result.flight_info.flight_no == "HO1039"
    assert result.flight_info.airline == "吉祥航空"
    assert result.flight_info.departure_airport == "虹桥T2"
    assert result.flight_info.arrival_airport == "天府T2"
    assert result.price == Decimal("500")
    assert result.available_seats == 2


def test_dom_fallback_skips_hidden_skeleton_soldout_and_malformed(scraper, params):
    html = """
    <div class="flight-item skeleton"></div>
    <div class="flight-item" style="display:none">CA1234 08:00 10:00 ¥100</div>
    <div class="flight-item">MU5101 08:00 10:00 已售罄 ¥200</div>
    <div class="flight-item">没有航班号 08:00 10:00 ¥300</div>
    """

    assert scraper._parse_dom_html(html, params) == []


@pytest.mark.asyncio
async def test_roundtrip_runs_two_reversed_oneway_searches(scraper):
    params = SearchParams("上海", "成都", date(2026, 9, 10), date(2026, 9, 17))
    scraper._ensure_browser = AsyncMock()
    scraper._search_oneway = AsyncMock(side_effect=[[], []])

    await scraper.search_flights(params)

    assert scraper._search_oneway.await_count == 2
    outbound_call, inbound_call = scraper._search_oneway.await_args_list
    outbound, outbound_direction = outbound_call.args
    inbound, inbound_direction = inbound_call.args
    assert (outbound.departure_city, outbound.arrival_city, outbound.departure_date) == (
        "上海",
        "成都",
        date(2026, 9, 10),
    )
    assert outbound_direction is FlightDirection.DEPARTURE
    assert (inbound.departure_city, inbound.arrival_city, inbound.departure_date) == (
        "成都",
        "上海",
        date(2026, 9, 17),
    )
    assert inbound_direction is FlightDirection.RETURN


@pytest.mark.asyncio
async def test_international_route_uses_iflight_branch(scraper):
    params = SearchParams("上海", "香港", date(2026, 9, 5))
    scraper._search_iflight_oneway = AsyncMock(return_value=[])
    scraper._search_domestic_oneway = AsyncMock(return_value=[])

    assert await scraper._search_oneway(params, FlightDirection.DEPARTURE) == []

    scraper._search_iflight_oneway.assert_awaited_once_with(
        params, FlightDirection.DEPARTURE
    )
    scraper._search_domestic_oneway.assert_not_awaited()


@pytest.mark.asyncio
async def test_iflight_configures_consistent_chrome_client_hints(scraper):
    page = AsyncMock()
    session = AsyncMock()
    context = AsyncMock()
    context.new_cdp_session.return_value = session
    scraper._context = context

    await scraper._configure_iflight_client_hints(page)

    session.send.assert_awaited_once()
    method, payload = session.send.await_args.args
    assert method == "Network.setUserAgentOverride"
    assert "HeadlessChrome" not in payload["userAgent"]
    assert payload["platform"] == "Win32"
    assert {brand["brand"] for brand in payload["userAgentMetadata"]["brands"]} == {
        "Not/A)Brand",
        "Chromium",
        "Google Chrome",
    }


@pytest.mark.asyncio
async def test_international_roundtrip_is_not_split_into_two_oneway_prices(scraper):
    params = SearchParams("上海", "香港", date(2026, 9, 5), date(2026, 9, 9))
    scraper._ensure_browser = AsyncMock()
    scraper._search_iflight_oneway = AsyncMock(return_value=[])

    assert await scraper.search_flights(params) == []

    scraper._search_iflight_oneway.assert_awaited_once_with(
        params, FlightDirection.DEPARTURE
    )


@pytest.mark.asyncio
async def test_transient_timeout_is_retried(scraper, params, monkeypatch):
    scraper.max_retries = 1
    scraper._ensure_browser = AsyncMock()
    scraper._search_oneway = AsyncMock(side_effect=[NetworkTimeoutError("temporary"), []])
    sleep = AsyncMock()
    monkeypatch.setattr("flightscanner.scrapers.tongcheng_scraper.asyncio.sleep", sleep)

    assert await scraper._search_with_retries(params, FlightDirection.DEPARTURE) == []
    assert scraper._search_oneway.await_count == 2
    sleep.assert_awaited_once_with(1.0)


def test_normalise_results_deduplicates_but_preserves_directions(
    scraper, params, real_shape_response
):
    outbound = scraper._parse_api_response(real_shape_response, params, FlightDirection.DEPARTURE)[
        0
    ]
    duplicate = scraper._parse_api_response(real_shape_response, params, FlightDirection.DEPARTURE)[
        0
    ]
    duplicate.price = Decimal("999")
    inbound = scraper._parse_api_response(real_shape_response, params, FlightDirection.RETURN)[0]

    results = scraper._normalise_results([duplicate, outbound, inbound])

    assert len(results) == 2
    assert {result.flight_info.direction for result in results} == {
        FlightDirection.DEPARTURE,
        FlightDirection.RETURN,
    }
    assert min(result.price for result in results) == Decimal("640")


def test_cookie_loader_supports_raw_string(tmp_path):
    path = tmp_path / "cookies.txt"
    path.write_text("Cookie: foo=bar; baz=qux", encoding="utf-8")

    cookies = TongchengScraper.load_cookies_from_file(str(path))

    assert len(cookies) == 4
    assert {cookie["domain"] for cookie in cookies} == {".ly.com", "www.ly.com"}


@pytest.mark.asyncio
async def test_deleted_cookie_file_clears_runtime_context(tmp_path):
    path = tmp_path / "cookies.txt"
    path.write_text("foo=bar", encoding="utf-8")
    scraper = TongchengScraper(cookies_file=str(path))
    context = AsyncMock()
    scraper._context = context
    path.unlink()

    assert await scraper.reload_cookies_if_changed() is True
    assert scraper.cookies == []
    context.close.assert_awaited_once()
    assert scraper._context is None


@pytest.mark.asyncio
async def test_hidden_captcha_component_is_not_treated_as_block(scraper):
    page = AsyncMock()
    page.title.return_value = "上海到成都机票预订 - 同程旅行"
    hidden = AsyncMock()
    hidden.is_visible.return_value = False
    page.query_selector_all.return_value = [hidden]

    assert await scraper._is_blocked(page) is False


@pytest.mark.asyncio
async def test_visible_captcha_component_is_treated_as_block(scraper):
    page = AsyncMock()
    page.title.return_value = "同程旅行"
    visible = AsyncMock()
    visible.is_visible.return_value = True
    page.query_selector_all.return_value = [visible]

    assert await scraper._is_blocked(page) is True


@pytest.mark.asyncio
async def test_close_is_idempotent(scraper):
    scraper._context = AsyncMock()
    scraper._browser = AsyncMock()
    scraper._playwright = AsyncMock()

    await scraper.close()
    await scraper.close()

    assert scraper._context is None
    assert scraper._browser is None
    assert scraper._playwright is None
