"""Unit tests for QunarScraper.

Covers URL building (v1.0.2 fix), airport code lookup,
non-headless login flow behaviour, anti-detection settings,
API response parsing, and network interception fallback.
"""

import sys
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from flightscanner.interfaces import SearchParams
from flightscanner.scrapers.qunar_scraper import QunarScraper
from flightscanner.utils.city_codes import CITY_CODE_MAP


@pytest.fixture
def scraper():
    return QunarScraper(headless=True, timeout=30000, max_retries=1)


@pytest.fixture
def one_way_params():
    return SearchParams(
        departure_city="上海",
        arrival_city="成都",
        departure_date=date(2026, 3, 21),
    )


@pytest.fixture
def round_trip_params():
    return SearchParams(
        departure_city="北京",
        arrival_city="广州",
        departure_date=date(2026, 4, 1),
        return_date=date(2026, 4, 8),
    )


class TestAirportCodeLookup:
    """Tests for _get_airport_code()."""

    def test_known_city_returns_iata_code(self, scraper: QunarScraper):
        assert scraper._get_airport_code("上海") == "SHA"
        assert scraper._get_airport_code("成都") == "CTU"
        assert scraper._get_airport_code("北京") == "BJS"
        assert scraper._get_airport_code("广州") == "CAN"

    def test_unknown_city_returns_city_name(self, scraper: QunarScraper):
        assert scraper._get_airport_code("未知城市") == "未知城市"

    def test_all_mapped_cities_have_three_letter_codes(self, scraper: QunarScraper):
        for city, code in CITY_CODE_MAP.items():
            assert len(code) == 3, f"{city} -> {code} should be 3 letters"
            assert code.isupper(), f"{city} -> {code} should be uppercase"


class TestBuildSearchUrl:
    """Tests for _build_search_url() — v1.0.2 correct parameter names."""

    def test_one_way_uses_correct_parameter_names(
        self, scraper: QunarScraper, one_way_params: SearchParams
    ):
        url = scraper._build_search_url(one_way_params)

        # Must use the new parameter names, not the old ones
        assert "searchDepartureAirport=" in url
        assert "searchArrivalAirport=" in url
        assert "searchDepartureTime=" in url
        # Old (broken) parameter names must be absent
        assert "fromCity=" not in url
        assert "toCity=" not in url
        assert "fromDate=" not in url

    def test_one_way_contains_required_flags(
        self, scraper: QunarScraper, one_way_params: SearchParams
    ):
        url = scraper._build_search_url(one_way_params)

        assert "startSearch=true" in url
        assert "nextNDays=0" in url
        assert "lowestPrice=null" in url
        assert "from=flight_dom_search" in url

    def test_one_way_contains_airport_codes(
        self, scraper: QunarScraper, one_way_params: SearchParams
    ):
        url = scraper._build_search_url(one_way_params)

        assert "fromCode=SHA" in url
        assert "toCode=CTU" in url

    def test_one_way_contains_city_names_encoded(
        self, scraper: QunarScraper, one_way_params: SearchParams
    ):
        url = scraper._build_search_url(one_way_params)

        # URL-encoded Chinese: 上海 → %E4%B8%8A%E6%B5%B7
        assert "%E4%B8%8A%E6%B5%B7" in url or "上海" in url
        assert "%E6%88%90%E9%83%BD" in url or "成都" in url

    def test_one_way_contains_date(
        self, scraper: QunarScraper, one_way_params: SearchParams
    ):
        url = scraper._build_search_url(one_way_params)
        assert "2026-03-21" in url

    def test_one_way_uses_correct_base_url(
        self, scraper: QunarScraper, one_way_params: SearchParams
    ):
        url = scraper._build_search_url(one_way_params)
        assert url.startswith("https://flight.qunar.com/site/oneway_list.htm")

    def test_one_way_does_not_include_return_date(
        self, scraper: QunarScraper, one_way_params: SearchParams
    ):
        url = scraper._build_search_url(one_way_params)
        assert "searchReturnTime" not in url

    def test_round_trip_uses_roundtrip_base_url(
        self, scraper: QunarScraper, round_trip_params: SearchParams
    ):
        url = scraper._build_search_url(round_trip_params)
        assert url.startswith("https://flight.qunar.com/site/roundtrip_list_new.htm")

    def test_round_trip_contains_return_date(
        self, scraper: QunarScraper, round_trip_params: SearchParams
    ):
        url = scraper._build_search_url(round_trip_params)
        assert "searchReturnTime=2026-04-08" in url
        assert "searchDepartureTime=2026-04-01" in url

    def test_round_trip_airport_codes(
        self, scraper: QunarScraper, round_trip_params: SearchParams
    ):
        url = scraper._build_search_url(round_trip_params)
        assert "fromCode=BJS" in url
        assert "toCode=CAN" in url

    def test_url_matches_user_confirmed_format(self, scraper: QunarScraper):
        """Verify the built URL matches the format confirmed to work by the user."""
        params = SearchParams(
            departure_city="上海",
            arrival_city="成都",
            departure_date=date(2026, 3, 21),
        )
        url = scraper._build_search_url(params)

        # These are the key parts of the user's confirmed working URL
        assert "flight.qunar.com/site/oneway_list.htm" in url
        assert "searchDepartureAirport=" in url
        assert "searchArrivalAirport=" in url
        assert "searchDepartureTime=2026-03-21" in url
        assert "fromCode=SHA" in url
        assert "toCode=CTU" in url
        assert "startSearch=true" in url


class TestBuildInterroundtripCompareUrl:
    """Tests for _build_interroundtrip_compare_url()."""

    def test_uses_interroundtrip_compare_base(self, scraper: QunarScraper):
        params = SearchParams(
            departure_city="上海",
            arrival_city="马尼拉",
            departure_date=date(2026, 5, 1),
            return_date=date(2026, 5, 5),
        )
        url = scraper._build_interroundtrip_compare_url(params)
        assert url.startswith("https://flight.qunar.com/site/interroundtrip_compare.htm")

    def test_uses_fromcity_tocity_parameters(self, scraper: QunarScraper):
        """interroundtrip_compare.htm 使用 fromCity/toCity，不用 searchDepartureAirport。"""
        params = SearchParams(
            departure_city="上海",
            arrival_city="马尼拉",
            departure_date=date(2026, 5, 1),
            return_date=date(2026, 5, 5),
        )
        url = scraper._build_interroundtrip_compare_url(params)
        assert "fromCity=" in url
        assert "toCity=" in url
        assert "fromDate=2026-05-01" in url
        assert "toDate=2026-05-05" in url
        # 不应包含 roundtrip_list_inter.htm 的参数格式
        assert "searchDepartureAirport=" not in url
        assert "searchArrivalAirport=" not in url

    def test_contains_correct_airport_codes(self, scraper: QunarScraper):
        params = SearchParams(
            departure_city="上海",
            arrival_city="马尼拉",
            departure_date=date(2026, 5, 1),
            return_date=date(2026, 5, 5),
        )
        url = scraper._build_interroundtrip_compare_url(params)
        assert "fromCode=SHA" in url
        assert "toCode=MNL" in url
        assert "isInter=true" in url
        assert "adultNum=1" in url
        assert "childNum=0" in url

    def test_matches_user_confirmed_url_format(self, scraper: QunarScraper):
        """验证 URL 格式与用户确认的真实 URL 一致。"""
        params = SearchParams(
            departure_city="上海",
            arrival_city="马尼拉",
            departure_date=date(2026, 5, 1),
            return_date=date(2026, 5, 5),
        )
        url = scraper._build_interroundtrip_compare_url(params)
        # 用户提供的真实 URL 中包含的关键片段
        assert "interroundtrip_compare.htm" in url
        assert "fromCode=SHA" in url
        assert "toCode=MNL" in url
        assert "fromDate=2026-05-01" in url
        assert "toDate=2026-05-05" in url
        assert "isInter=true" in url
        assert "from=flight_dom_search" in url
        assert "adultNum=1" in url
        assert "childNum=0" in url


class TestNonHeadlessLoginFlow:
    """Tests for the fixed non-headless login flow."""

    @pytest.mark.asyncio
    async def test_non_headless_waits_for_login_and_navigates_back(
        self, scraper: QunarScraper
    ):
        """After login in non-headless mode, scraper must navigate back to search URL."""
        scraper.headless = False

        params = SearchParams(
            departure_city="上海",
            arrival_city="成都",
            departure_date=date(2026, 3, 21),
        )
        expected_url = scraper._build_search_url(params)

        navigated_urls = []

        mock_page = AsyncMock()
        mock_page.url = "https://user.qunar.com/passport/login.jsp"

        async def fake_goto(url, **kwargs):
            navigated_urls.append(url)

        mock_page.goto = AsyncMock(side_effect=fake_goto)

        mock_context = AsyncMock()
        mock_context.new_page = AsyncMock(return_value=mock_page)
        mock_context.cookies = AsyncMock(return_value=[])
        scraper._context = mock_context

        with (
            patch.object(scraper, "_ensure_browser"),
            patch.object(scraper, "_is_login_required", return_value=True),
            patch.object(
                scraper, "_capture_login_qr_code", return_value="dGVzdA=="  # valid base64 for "test"
            ),
            patch("builtins.open", mock_open()),
            patch.object(scraper, "_wait_for_login", return_value=True),
            patch.object(scraper, "_is_blocked", return_value=False),
            patch.object(scraper, "_wait_for_results"),
            patch.object(scraper, "_parse_flights", return_value=[]),
        ):
            await scraper.search_flights(params)

        # The last goto call should be back to the search URL
        assert any(expected_url in nav_url for nav_url in navigated_urls), (
            f"Expected navigation back to {expected_url}, got {navigated_urls}"
        )

    @pytest.mark.asyncio
    async def test_non_headless_raises_on_login_timeout(self, scraper: QunarScraper):
        """If user doesn't login within timeout, LoginRequiredError is raised."""
        from flightscanner.scrapers.qunar_scraper import LoginRequiredError

        scraper.headless = False

        params = SearchParams(
            departure_city="上海",
            arrival_city="成都",
            departure_date=date(2026, 3, 21),
        )

        mock_page = AsyncMock()
        mock_context = AsyncMock()
        mock_context.new_page = AsyncMock(return_value=mock_page)
        scraper._context = mock_context

        with (
            patch.object(scraper, "_ensure_browser"),
            patch.object(scraper, "_is_login_required", return_value=True),
            patch.object(
                scraper, "_capture_login_qr_code", return_value="dGVzdA=="  # valid base64 for "test"
            ),
            patch("builtins.open", mock_open()),
            patch.object(scraper, "_wait_for_login", return_value=False),
        ):
            with pytest.raises(LoginRequiredError):
                await scraper.search_flights(params)


# ---------------------------------------------------------------------------
# Helper: build a minimal mock playwright stack for _ensure_browser() tests
# ---------------------------------------------------------------------------

def _make_playwright_mocks():
    """Return (mock_playwright, mock_browser, mock_context) triple."""
    mock_context = AsyncMock()
    mock_browser = AsyncMock()
    mock_browser.new_context = AsyncMock(return_value=mock_context)
    mock_browser_type = AsyncMock()
    mock_browser_type.launch = AsyncMock(return_value=mock_browser)
    mock_playwright = AsyncMock()
    mock_playwright.chromium = mock_browser_type
    return mock_playwright, mock_browser, mock_browser_type, mock_context


class TestAntiDetectionSettings:
    """Verify _ensure_browser() uses bot-evasion browser configuration."""

    @pytest.mark.asyncio
    async def test_user_agent_is_macos(self, scraper: QunarScraper):
        mock_playwright, mock_browser, mock_browser_type, mock_context = _make_playwright_mocks()

        mock_ap_instance = AsyncMock()
        mock_ap_instance.start = AsyncMock(return_value=mock_playwright)

        with patch(
            "flightscanner.scrapers.qunar_scraper.async_playwright",
            return_value=mock_ap_instance,
        ):
            await scraper._ensure_browser()

        call_kwargs = mock_browser.new_context.call_args.kwargs
        ua = call_kwargs.get("user_agent", "")
        assert "Macintosh" in ua, f"Expected macOS UA, got: {ua}"
        assert "Windows" not in ua, f"UA must not contain Windows: {ua}"

    @pytest.mark.asyncio
    async def test_sec_ch_ua_headers_present(self, scraper: QunarScraper):
        mock_playwright, mock_browser, mock_browser_type, mock_context = _make_playwright_mocks()

        mock_ap_instance = AsyncMock()
        mock_ap_instance.start = AsyncMock(return_value=mock_playwright)

        with patch(
            "flightscanner.scrapers.qunar_scraper.async_playwright",
            return_value=mock_ap_instance,
        ):
            await scraper._ensure_browser()

        call_kwargs = mock_browser.new_context.call_args.kwargs
        headers = call_kwargs.get("extra_http_headers", {})
        assert "sec-ch-ua" in headers, "sec-ch-ua header missing"
        assert "sec-ch-ua-mobile" in headers, "sec-ch-ua-mobile header missing"
        assert "sec-ch-ua-platform" in headers, "sec-ch-ua-platform header missing"
        assert '"macOS"' in headers["sec-ch-ua-platform"], (
            f"sec-ch-ua-platform should declare macOS, got: {headers['sec-ch-ua-platform']}"
        )

    @pytest.mark.asyncio
    async def test_automation_flag_disabled_in_launch_args(self, scraper: QunarScraper):
        mock_playwright, mock_browser, mock_browser_type, mock_context = _make_playwright_mocks()

        mock_ap_instance = AsyncMock()
        mock_ap_instance.start = AsyncMock(return_value=mock_playwright)

        with patch(
            "flightscanner.scrapers.qunar_scraper.async_playwright",
            return_value=mock_ap_instance,
        ):
            await scraper._ensure_browser()

        launch_kwargs = mock_browser_type.launch.call_args.kwargs
        args = " ".join(launch_kwargs.get("args", []))
        assert "--disable-blink-features=AutomationControlled" in args
        # --disable-web-security was removed because it is a bot signal
        assert "--disable-web-security" not in args

    @pytest.mark.asyncio
    async def test_init_script_is_added(self, scraper: QunarScraper):
        """An init script (JS injection) must be registered on the context."""
        mock_playwright, mock_browser, mock_browser_type, mock_context = _make_playwright_mocks()

        mock_ap_instance = AsyncMock()
        mock_ap_instance.start = AsyncMock(return_value=mock_playwright)

        with patch(
            "flightscanner.scrapers.qunar_scraper.async_playwright",
            return_value=mock_ap_instance,
        ):
            await scraper._ensure_browser()

        mock_context.add_init_script.assert_called_once()
        script_arg = mock_context.add_init_script.call_args.args[0]
        # The script must patch navigator.webdriver
        assert "webdriver" in script_arg
        # The script must provide a full chrome object
        assert "window.chrome" in script_arg


class TestParseApiResponses:
    """Unit tests for _parse_api_responses() — heuristic JSON flight extractor."""

    @pytest.fixture
    def params(self):
        return SearchParams(
            departure_city="上海",
            arrival_city="成都",
            departure_date=date(2026, 3, 21),
        )

    def test_extracts_flight_with_price(self, scraper: QunarScraper, params):
        responses = [
            {
                "url": "https://flight.qunar.com/api/flights",
                "data": {
                    "flightList": [
                        {
                            "flightNo": "MU5132",
                            "airline": "中国东方航空",
                            "price": 680,
                            "depTime": "08:00",
                            "arrTime": "10:30",
                        }
                    ]
                },
            }
        ]
        results = scraper._parse_api_responses(responses, params)

        assert len(results) == 1
        fp = results[0]
        assert fp.price == Decimal("680")
        assert fp.flight_info.flight_no == "MU5132"
        assert fp.flight_info.airline == "中国东方航空"
        assert fp.source == "qunar_api"
        assert fp.currency == "CNY"

    def test_extracts_multiple_flights(self, scraper: QunarScraper, params):
        responses = [
            {
                "url": "https://flight.qunar.com/api/flights",
                "data": {
                    "flightList": [
                        {"flightNo": "MU5132", "price": 680},
                        {"flightNo": "CA4102", "price": 750},
                        {"flightNo": "3U8888", "price": 520},
                    ]
                },
            }
        ]
        results = scraper._parse_api_responses(responses, params)
        assert len(results) == 3
        prices = {r.price for r in results}
        assert prices == {Decimal("680"), Decimal("750"), Decimal("520")}

    def test_handles_deeply_nested_data(self, scraper: QunarScraper, params):
        responses = [
            {
                "url": "https://api.qunar.com/data",
                "data": {
                    "result": {
                        "data": {
                            "flights": [
                                {"flightNo": "MU5132", "price": 680},
                            ]
                        }
                    }
                },
            }
        ]
        results = scraper._parse_api_responses(responses, params)
        assert len(results) == 1
        assert results[0].price == Decimal("680")

    def test_skips_records_without_price_field(self, scraper: QunarScraper, params):
        responses = [
            {
                "url": "https://flight.qunar.com/api/flights",
                "data": {
                    "flightList": [
                        {"flightNo": "MU5132", "airline": "东方航空"},  # no price
                    ]
                },
            }
        ]
        results = scraper._parse_api_responses(responses, params)
        assert results == []

    def test_returns_empty_for_no_responses(self, scraper: QunarScraper, params):
        assert scraper._parse_api_responses([], params) == []

    def test_returns_empty_for_non_flight_json(self, scraper: QunarScraper, params):
        responses = [
            {
                "url": "https://flight.qunar.com/api/config",
                "data": {"version": "1.0", "debug": False},
            }
        ]
        assert scraper._parse_api_responses(responses, params) == []

    def test_fills_search_params_into_flight_info(self, scraper: QunarScraper, params):
        responses = [
            {
                "url": "https://flight.qunar.com/api/flights",
                "data": {"flightList": [{"flightNo": "MU5132", "price": 680}]},
            }
        ]
        results = scraper._parse_api_responses(responses, params)
        assert len(results) == 1
        fi = results[0].flight_info
        assert fi.departure_city == "上海"
        assert fi.arrival_city == "成都"
        assert fi.departure_date == date(2026, 3, 21)

    def test_multiple_responses_aggregated(self, scraper: QunarScraper, params):
        responses = [
            {
                "url": "https://flight.qunar.com/api/page1",
                "data": {"flights": [{"flightNo": "MU5132", "price": 680}]},
            },
            {
                "url": "https://flight.qunar.com/api/page2",
                "data": {"flights": [{"flightNo": "CA4102", "price": 750}]},
            },
        ]
        results = scraper._parse_api_responses(responses, params)
        assert len(results) == 2


class TestNetworkInterceptionFallback:
    """Tests for the network response capture and DOM→API fallback chain."""

    @pytest.mark.asyncio
    async def test_response_handler_registered_on_page(self, scraper: QunarScraper):
        """search_flights() must call page.on('response', ...) for API capture."""
        mock_page = AsyncMock()
        registered_events: dict = {}

        def capture_on(event, handler):
            registered_events[event] = handler

        mock_page.on = MagicMock(side_effect=capture_on)

        mock_context = AsyncMock()
        mock_context.new_page = AsyncMock(return_value=mock_page)
        scraper._context = mock_context

        params = SearchParams(
            departure_city="上海",
            arrival_city="成都",
            departure_date=date(2026, 3, 21),
        )

        with (
            patch.object(scraper, "_ensure_browser"),
            patch.object(scraper, "_is_login_required", return_value=False),
            patch.object(scraper, "_is_blocked", return_value=False),
            patch.object(scraper, "_wait_for_results"),
            patch.object(scraper, "_parse_flights", return_value=[]),
            patch.object(scraper, "_parse_api_responses", return_value=[]),
            patch("builtins.open", mock_open()),
        ):
            await scraper.search_flights(params)

        assert "response" in registered_events, (
            "page.on('response', ...) was never called — API interception not wired up"
        )

    @pytest.mark.asyncio
    async def test_api_fallback_invoked_when_captured_responses_present(
        self, scraper: QunarScraper
    ):
        """When DOM is empty and API responses were captured, _parse_api_responses is called."""
        mock_page = AsyncMock()
        registered_handler: dict = {}

        def capture_on(event, handler):
            registered_handler[event] = handler

        mock_page.on = MagicMock(side_effect=capture_on)

        mock_context = AsyncMock()
        mock_context.new_page = AsyncMock(return_value=mock_page)
        scraper._context = mock_context

        # Fake network response that looks like a Qunar JSON API response
        fake_response = AsyncMock()
        fake_response.url = "https://flight.qunar.com/api/flights"
        fake_response.ok = True
        fake_response.headers = {"content-type": "application/json"}
        fake_response.json = AsyncMock(
            return_value={"flightList": [{"flightNo": "MU5132", "price": 680}]}
        )

        async def fake_wait_for_results(page):
            # Simulate the browser receiving a network response during page load
            if "response" in registered_handler:
                await registered_handler["response"](fake_response)

        mock_parse_api = MagicMock(return_value=[])

        params = SearchParams(
            departure_city="上海",
            arrival_city="成都",
            departure_date=date(2026, 3, 21),
        )

        with (
            patch.object(scraper, "_ensure_browser"),
            patch.object(scraper, "_is_login_required", return_value=False),
            patch.object(scraper, "_is_blocked", return_value=False),
            patch.object(scraper, "_wait_for_results", side_effect=fake_wait_for_results),
            patch.object(scraper, "_parse_flights", return_value=[]),
            patch.object(scraper, "_parse_api_responses", mock_parse_api),
            patch("builtins.open", mock_open()),
        ):
            await scraper.search_flights(params)

        mock_parse_api.assert_called_once()
        captured = mock_parse_api.call_args.args[0]
        assert len(captured) == 1
        assert captured[0]["url"] == "https://flight.qunar.com/api/flights"

    @pytest.mark.asyncio
    async def test_api_fallback_not_called_when_dom_has_results(
        self, scraper: QunarScraper
    ):
        """_parse_api_responses must NOT be called when DOM parsing succeeds."""
        from flightscanner.interfaces import FlightPrice, FlightInfo, FlightDirection
        from datetime import datetime, timezone

        mock_page = AsyncMock()
        mock_page.on = MagicMock()

        mock_context = AsyncMock()
        mock_context.new_page = AsyncMock(return_value=mock_page)
        scraper._context = mock_context

        dummy_fp = FlightPrice(
            flight_info=FlightInfo(
                flight_no="MU5132",
                airline="东方航空",
                departure_city="上海",
                arrival_city="成都",
                departure_time="08:00",
                arrival_time="10:30",
                departure_date=date(2026, 3, 21),
                direction=FlightDirection.DEPARTURE,
            ),
            price=Decimal("680"),
            currency="CNY",
            seat_class="经济舱",
            available_seats=None,
            scraped_at=datetime.now(timezone.utc),
            source="qunar",
        )

        mock_parse_api = MagicMock(return_value=[])

        params = SearchParams(
            departure_city="上海",
            arrival_city="成都",
            departure_date=date(2026, 3, 21),
        )

        with (
            patch.object(scraper, "_ensure_browser"),
            patch.object(scraper, "_is_login_required", return_value=False),
            patch.object(scraper, "_is_blocked", return_value=False),
            patch.object(scraper, "_wait_for_results"),
            patch.object(scraper, "_parse_flights", return_value=[dummy_fp]),
            patch.object(scraper, "_parse_api_responses", mock_parse_api),
        ):
            results = await scraper.search_flights(params)

        mock_parse_api.assert_not_called()
        assert results == [dummy_fp]
