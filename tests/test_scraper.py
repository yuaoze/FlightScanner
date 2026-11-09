"""Unit tests for CtripScraper.

Note: These tests mock Playwright interactions to avoid actual web scraping.
For integration tests with real scraping, use a separate test suite.
"""

import asyncio
import os
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch, MagicMock, mock_open, call

import pytest
from playwright.async_api import ElementHandle, Locator, Page

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from flightscanner.interfaces import AntiCrawlerDetectedError, FlightDirection, ScraperError, SearchParams
from flightscanner.scrapers import CtripScraper


@pytest.fixture
def scraper(tmp_path):
    scraper = CtripScraper(
        headless=True, timeout=30000, max_retries=3,
        cookies_file=str(tmp_path / "no-ctrip-cookies.json"),
    )
    with patch.object(scraper, "_start_virtual_display", return_value={"DISPLAY": ":99"}):
        yield scraper


@pytest.fixture
def search_params():
    """Create sample search parameters."""
    return SearchParams(
        departure_city="北京",
        arrival_city="上海",
        departure_date=date.today() + timedelta(days=7),
    )


class TestCtripScraper:
    """Test cases for CtripScraper."""

    def test_init_with_defaults(self):
        """Test CtripScraper initialization with default values."""
        scraper = CtripScraper()

        assert scraper.headless is True
        assert scraper.timeout == 30000
        assert scraper.max_retries == 3

    def test_init_with_custom_values(self):
        """Test CtripScraper initialization with custom values."""
        scraper = CtripScraper(headless=False, timeout=60000, max_retries=5)

        assert scraper.headless is False
        assert scraper.timeout == 60000
        assert scraper.max_retries == 5

    def test_cookie_writer_requires_authentication_and_uses_private_files(self, tmp_path):
        from scripts.ctrip_login import _has_authenticated_cookie, _write_cookies_atomically

        assert _has_authenticated_cookie([{"name": "GUID", "value": "anonymous"}]) is False
        assert _has_authenticated_cookie([{"name": "uid", "value": "12345"}]) is False
        assert _has_authenticated_cookie([{"name": "uin", "value": "12345"}]) is False
        assert _has_authenticated_cookie([{"name": "ibu_uid", "value": "0"}]) is False
        assert _has_authenticated_cookie([{"name": "login_uid", "value": ""}]) is False
        assert _has_authenticated_cookie([{"name": "cticket", "value": "ticket"}]) is True
        cookies = [{"name": "ibu_uid", "value": "12345"}]
        assert _has_authenticated_cookie(cookies) is True

        output = tmp_path / "ctrip_cookies.json"
        _write_cookies_atomically(output, cookies)
        assert output.stat().st_mode & 0o777 == 0o600

    @pytest.mark.asyncio
    @pytest.mark.parametrize("selector", ["[data-testid='qrCodeBox']", "canvas"])
    async def test_qr_capture_supports_current_svg_and_legacy_canvas(self, selector, tmp_path):
        from scripts.ctrip_login import _save_qr_png

        element = AsyncMock()
        element.is_visible.return_value = True
        element.screenshot.side_effect = lambda path: Path(path).write_bytes(b"png-data")
        page = AsyncMock()

        async def query_selector_all(candidate):
            return [element] if candidate == selector else []

        page.query_selector_all.side_effect = query_selector_all
        output = tmp_path / "ctrip_qr.png"

        assert await _save_qr_png(page, str(output)) is True
        element.screenshot.assert_awaited_once_with(path=str(output))
        assert output.read_bytes() == b"png-data"

    def test_build_search_url_one_way(self, scraper: CtripScraper, search_params: SearchParams):
        """Test URL building for one-way flights using IATA city codes."""
        url = scraper._build_search_url(search_params)

        assert "flights.ctrip.com" in url
        assert "oneway" in url
        # 北京 → bjs，上海 → sha（携程使用 IATA 城市代码构建 URL）
        assert "bjs" in url
        assert "sha" in url
        assert "depdate" in url

    def test_build_search_url_round_trip(self, scraper: CtripScraper):
        """Test URL building for round-trip flights."""
        params = SearchParams(
            departure_city="北京",
            arrival_city="上海",
            departure_date=date.today() + timedelta(days=7),
            return_date=date.today() + timedelta(days=14),
        )

        url = scraper._build_search_url(params)

        assert "flights.ctrip.com" in url
        assert "round" in url
        assert "bjs" in url
        assert "sha" in url

    @pytest.mark.asyncio
    async def test_close_cleans_up_resources(self, scraper: CtripScraper):
        """Test that close() properly cleans up browser resources."""
        # Mock playwright objects
        scraper._playwright = AsyncMock()
        scraper._browser = AsyncMock()
        scraper._context = AsyncMock()

        # Save references before close() resets them to None
        mock_context = scraper._context
        mock_browser = scraper._browser
        mock_playwright = scraper._playwright

        await scraper.close()

        # Verify cleanup methods were called
        mock_context.close.assert_called_once()
        mock_browser.close.assert_called_once()
        mock_playwright.stop.assert_called_once()

        # Verify attributes are reset
        assert scraper._context is None
        assert scraper._browser is None
        assert scraper._playwright is None

    @pytest.mark.asyncio
    async def test_existing_browser_recreates_closed_context(self, scraper: CtripScraper):
        context = AsyncMock()
        scraper._browser = AsyncMock()
        scraper._browser.new_context.return_value = context
        scraper._context = None

        with patch("flightscanner.scrapers.ctrip_scraper.async_playwright") as playwright:
            await scraper._ensure_browser()

        playwright.assert_not_called()
        scraper._browser.new_context.assert_awaited_once()
        assert scraper._context is context

    @pytest.mark.asyncio
    async def test_close_handles_exceptions(self, scraper: CtripScraper):
        """Test that close() handles exceptions gracefully."""
        # Mock playwright objects that raise exceptions
        scraper._context = AsyncMock()
        scraper._context.close.side_effect = Exception("Context close error")

        # Should not raise exception
        await scraper.close()

    @pytest.mark.asyncio
    async def test_search_flights_returns_empty_on_no_results(self, scraper: CtripScraper, search_params: SearchParams):
        """Test that search_flights returns empty list when no flights found."""
        with patch.object(scraper, '_ensure_browser'), \
             patch.object(scraper, '_is_blocked', return_value=False), \
             patch.object(scraper, '_parse_api_responses', return_value=[]), \
             patch.object(scraper, '_parse_flights_from_dom', return_value=[]), \
             patch('builtins.open', mock_open()), \
             patch('asyncio.sleep'):

            # Mock page
            mock_page = AsyncMock()
            mock_page.on = Mock()
            mock_page.remove_listener = Mock()
            mock_context = AsyncMock()
            mock_context.new_page.return_value = mock_page
            scraper._context = mock_context

            results = await scraper.search_flights(search_params)

            assert results == []
            mock_page.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_search_flights_handles_timeout(self, scraper: CtripScraper, search_params: SearchParams):
        """Test that search_flights handles timeout errors."""
        import asyncio
        from flightscanner.interfaces import NetworkTimeoutError

        with patch.object(scraper, '_ensure_browser'), \
             patch('asyncio.sleep'):
            # Mock page that times out
            mock_page = AsyncMock()
            mock_page.on = Mock()
            mock_page.remove_listener = Mock()
            mock_page.goto.side_effect = asyncio.TimeoutError("Page load timeout")

            mock_context = AsyncMock()
            mock_context.new_page.return_value = mock_page
            scraper._context = mock_context

            with pytest.raises(NetworkTimeoutError):
                await scraper.search_flights(search_params)

            mock_page.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_search_flights_handles_anti_crawler(self, scraper: CtripScraper, search_params: SearchParams):
        """Test that search_flights detects anti-crawler mechanisms."""
        from flightscanner.interfaces import AntiCrawlerDetectedError

        with patch.object(scraper, '_ensure_browser'), \
             patch.object(scraper, '_is_blocked', return_value=True), \
             patch('asyncio.sleep'):

            # Mock page
            mock_page = AsyncMock()
            mock_page.on = Mock()
            mock_page.remove_listener = Mock()
            mock_context = AsyncMock()
            mock_context.new_page.return_value = mock_page
            scraper._context = mock_context

            with pytest.raises(AntiCrawlerDetectedError):
                await scraper.search_flights(search_params)

            mock_page.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_is_blocked_detects_captcha(self, scraper: CtripScraper):
        """Test that _is_blocked detects CAPTCHA pages."""
        # Mock page with CAPTCHA
        mock_page = AsyncMock()
        mock_page.title = AsyncMock(return_value="验证码 - 携程")

        result = await scraper._is_blocked(mock_page)

        assert result is True

    @pytest.mark.asyncio
    async def test_is_blocked_detects_current_verification_modal(self, scraper: CtripScraper):
        mock_page = AsyncMock()
        mock_page.title.return_value = "上海到悉尼机票查询"
        modal = Mock()
        mock_page.query_selector.side_effect = lambda selector: (
            modal if "text=为保障您的安全访问" in selector else None
        )

        assert await scraper._is_blocked(mock_page) is True

    @pytest.mark.asyncio
    async def test_is_blocked_detects_normal_page(self, scraper: CtripScraper):
        """Test that _is_blocked returns False for normal pages."""
        # Mock normal page
        mock_page = AsyncMock()
        mock_page.title = AsyncMock(return_value="北京到上海机票查询")
        mock_page.query_selector = AsyncMock(return_value=None)

        result = await scraper._is_blocked(mock_page)

        assert result is False

    @pytest.mark.asyncio
    async def test_ensure_browser_initializes_playwright(self, scraper: CtripScraper):
        """Test that _ensure_browser initializes Playwright components."""
        with patch('flightscanner.scrapers.ctrip_scraper.async_playwright') as mock_playwright_func:
            # Mock playwright
            mock_playwright = AsyncMock()
            mock_playwright_func.return_value.start = AsyncMock(return_value=mock_playwright)

            mock_browser = AsyncMock()
            mock_playwright.chromium.launch = AsyncMock(return_value=mock_browser)

            mock_context = AsyncMock()
            mock_browser.new_context = AsyncMock(return_value=mock_context)

            await scraper._ensure_browser()

            # Verify playwright was initialized
            assert scraper._playwright is not None
            assert scraper._browser is not None
            assert scraper._context is not None

    @pytest.mark.asyncio
    async def test_parse_flight_element_handles_missing_data(self, scraper: CtripScraper, search_params: SearchParams):
        """Test that _parse_flight_element handles missing data gracefully."""
        # Mock element with minimal data
        mock_element = AsyncMock()
        mock_element.query_selector = AsyncMock(return_value=None)

        result = await scraper._parse_flight_element(mock_element, search_params)

        # Should return None or a FlightPrice with default values
        # The actual behavior depends on implementation
        assert result is None or result.flight_info.flight_no == "UNKNOWN"

    @pytest.mark.asyncio
    async def test_search_flights_retries_on_failure(self, scraper: CtripScraper, search_params: SearchParams):
        """Test that search_flights raises ParseError on unexpected failure."""
        from flightscanner.interfaces import ParseError

        with patch.object(scraper, '_ensure_browser'), \
             patch.object(scraper, '_is_blocked', return_value=False), \
             patch('asyncio.sleep'):

            # Mock page
            mock_page = AsyncMock()
            mock_page.on = Mock()
            mock_page.remove_listener = Mock()
            mock_page.goto = AsyncMock()

            mock_context = AsyncMock()
            mock_context.new_page.return_value = mock_page
            scraper._context = mock_context

            # _parse_api_responses raises unexpected exception
            with patch.object(scraper, '_parse_api_responses', side_effect=RuntimeError("unexpected")):
                with pytest.raises(ParseError):
                    await scraper.search_flights(search_params)

    def test_parse_itinerary_rejects_roundtrip_without_selected_return(self, scraper: CtripScraper):
        itinerary = {
            "flightSegments": [
                {
                    "segmentNo": 1,
                    "airlineName": "深圳航空",
                    "flightList": [{
                        "flightNo": "ZH4835",
                        "marketAirlineName": "深圳航空",
                        "departureAirportCode": "PVG",
                        "arrivalAirportCode": "CTU",
                        "departureDateTime": "2026-04-01 10:00:00",
                        "arrivalDateTime": "2026-04-01 13:00:00",
                    }],
                },
            ],
            "priceList": [{"adultPrice": 2300, "cabin": "Y", "seatsLeft": 5}],
        }
        params = SearchParams(
            departure_city="上海",
            arrival_city="成都",
            departure_date=date.today() + timedelta(days=7),
            return_date=date.today() + timedelta(days=14),
        )

        result = scraper._parse_itinerary(itinerary, params)

        assert result == []

    def test_parse_itinerary_oneway_no_return_flight_info(self, scraper: CtripScraper):
        """单程搜索时 return_flight_info 应为 None。"""
        itinerary = {
            "flightSegments": [{
                "segmentNo": 1,
                "airlineName": "深圳航空",
                "flightList": [{
                    "flightNo": "ZH4835",
                    "departureAirportCode": "PVG",
                    "arrivalAirportCode": "CTU",
                    "departureDateTime": "2026-04-01 10:00:00",
                    "arrivalDateTime": "2026-04-01 13:00:00",
                }],
            }],
            "priceList": [{"adultPrice": 1150, "cabin": "Y", "seatsLeft": 5}],
        }
        params = SearchParams(
            departure_city="上海",
            arrival_city="成都",
            departure_date=date.today() + timedelta(days=7),
            return_date=None,
        )

        result = scraper._parse_itinerary(itinerary, params)

        assert len(result) == 1
        assert result[0].return_flight_info is None

    def test_parse_itinerary_filters_no_seats(self, scraper: CtripScraper):
        """seatsLeft=0 的记录应被过滤（已售罄）；seatsLeft=None 表示 API 未返回座位信息，应保留。"""
        itinerary = {
            "flightSegments": [{
                "segmentNo": 1,
                "airlineName": "深圳航空",
                "flightList": [{
                    "flightNo": "ZH4835",
                    "departureAirportCode": "PVG",
                    "arrivalAirportCode": "CTU",
                    "departureDateTime": "2026-04-01 10:00:00",
                    "arrivalDateTime": "2026-04-01 13:00:00",
                }],
            }],
            "priceList": [
                {"adultPrice": 1150, "cabin": "Y", "seatsLeft": None},  # API 未返回，保留
                {"adultPrice": 1150, "cabin": "Y", "seatsLeft": 0},      # 已售罄，过滤
                {"adultPrice": 1500, "cabin": "Y", "seatsLeft": 5},      # 有座位，保留
            ],
        }
        params = SearchParams(
            departure_city="上海",
            arrival_city="成都",
            departure_date=date.today() + timedelta(days=7),
            return_date=None,
        )

        result = scraper._parse_itinerary(itinerary, params)

        # 每个行程只保留最低价；seatsLeft=None 的 ¥1150 条目优先于 ¥1500
        assert len(result) == 1
        assert result[0].price == 1150
        assert result[0].available_seats is None


@pytest.fixture
def recovery_page(scraper, monkeypatch):
    """A browser-free page with Playwright's synchronous factory/event methods."""
    page = AsyncMock(spec=Page)
    page.on = Mock()
    page.remove_listener = Mock()
    page.is_closed = Mock(return_value=False)
    page.goto.return_value = Mock(status=200)
    page.locator = Mock(return_value=Mock(spec=Locator))
    page.locator.return_value.count.return_value = 1
    page.request = Mock(fetch=AsyncMock())
    scraper._context = Mock()
    scraper._context.new_page = AsyncMock(return_value=page)
    scraper._context.route = AsyncMock()
    scraper._context.request = Mock(fetch=AsyncMock())
    monkeypatch.setattr(scraper, "reload_cookies_if_changed", AsyncMock(return_value=False))
    monkeypatch.setattr(scraper, "_ensure_browser", AsyncMock())
    return page


def _mock_dom_card(fields=None, *, locator=False, text="往返含税价"):
    """Keep Locator and ElementHandle APIs distinct, as in real Playwright."""
    if locator:
        card = Mock(spec=Locator)
        card.inner_text.return_value = text
        card.element_handle.return_value = _mock_dom_card(fields, text=text)
        return card
    values = {
        ".plane-No": "MU561 波音777(大)",
        ".airline-name span": "中国东方航空",
        ".depart-box .time": "20:15",
        ".arrive-box .time": "06:30 +1天",
        ".depart-box .airport": "浦东国际机场T1",
        ".arrive-box .airport": "悉尼金斯福德史密斯机场T1",
        ".flight-price .price": "¥2,345.60",
    }
    values.update(fields or {})
    card = Mock(spec=ElementHandle)
    card.inner_text.return_value = text

    def text_node(selector):
        value = values.get(selector)
        if value is None:
            return None
        node = Mock(spec=ElementHandle)
        node.inner_text.return_value = value
        return node

    card.query_selector.side_effect = text_node
    plane = text_node(".plane-No")
    card.query_selector_all.return_value = [plane] if plane is not None else []
    return card


class TestCtripRecovery:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("headless", [True, False])
    async def test_browser_has_no_fingerprint_or_security_overrides(self, scraper, headless):
        scraper.headless = headless
        scraper.cookies = []
        context = AsyncMock()
        browser = AsyncMock()
        browser.new_context.return_value = context
        playwright = Mock()
        playwright.chromium.launch = AsyncMock(return_value=browser)

        with patch("flightscanner.scrapers.ctrip_scraper.async_playwright") as factory:
            factory.return_value.start = AsyncMock(return_value=playwright)
            await scraper._ensure_browser()

        virtual = headless and sys.platform.startswith("linux")
        playwright.chromium.launch.assert_awaited_once_with(
            headless=False if virtual else headless,
            env={"DISPLAY": ":99"} if virtual else None,
        )
        browser.new_context.assert_awaited_once_with(
            viewport={"width": 1440, "height": 900},
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
        )
        context.add_init_script.assert_not_called()
        context.set_extra_http_headers.assert_not_called()
        context.new_cdp_session.assert_not_called()
        assert scraper._context is context

    @pytest.mark.asyncio
    async def test_passively_captures_responses_without_request_replay(
        self, scraper, search_params, recovery_page, mock_flight_price
    ):
        page = recovery_page
        paths = [
            "/international/search/api/search/batchSearch",
            "/restapi/soa2/flightSearch",
            "/online/process",
            "/itinerary/api/products",
        ]
        responses = [
            Mock(
                url=f"https://flights.ctrip.com{path}?session=not-persisted",
                ok=True,
                json=AsyncMock(return_value={"data": {"index": index}}),
            )
            for index, path in enumerate(paths)
        ]
        ignored = [
            Mock(url="https://flights.ctrip.com/itinerary/api/products", ok=False),
            Mock(url="https://flights.ctrip.com.attacker.invalid/itinerary/api/products", ok=True),
            Mock(url="https://example.invalid/itinerary/api/products", ok=True),
            Mock(url="https://flights.ctrip.com/static/settings.json", ok=True),
        ]
        for response in ignored:
            response.json = AsyncMock()
        unreadable = Mock(
            url="https://flights.ctrip.com/itinerary/api/broken", ok=True,
            json=AsyncMock(side_effect=ValueError("not JSON")),
        )
        non_object = Mock(
            url="https://flights.ctrip.com/itinerary/api/list", ok=True,
            json=AsyncMock(return_value=[]),
        )

        async def navigate(*args, **kwargs):
            event, listener = page.on.call_args.args
            assert event == "response"
            for response in responses + ignored + [unreadable, non_object]:
                assert listener(response) is None
            return Mock(status=200)

        page.goto.side_effect = navigate
        with patch.object(scraper, "_wait_for_search_results", new_callable=AsyncMock), \
             patch.object(scraper, "_parse_api_responses", return_value=[mock_flight_price]) as parse, \
             patch.object(scraper, "_parse_flights_from_dom", new_callable=AsyncMock) as dom:
            results = await scraper.search_flights(search_params)

        assert results == [mock_flight_price]
        parse.assert_called_once_with([
            {"url": f"https://flights.ctrip.com{path}", "data": {"data": {"index": index}}}
            for index, path in enumerate(paths)
        ], search_params)
        for response in responses + [unreadable, non_object]:
            response.json.assert_awaited_once_with()
        for response in ignored:
            response.json.assert_not_called()
        dom.assert_not_called()
        page.route.assert_not_called()
        scraper._context.route.assert_not_called()
        page.request.fetch.assert_not_called()
        scraper._context.request.fetch.assert_not_called()
        page.on.assert_called_once()
        page.remove_listener.assert_called_once_with("response", page.on.call_args.args[1])
        page.close.assert_awaited_once_with()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("api_available", [True, False])
    async def test_stalled_response_preserves_completed_capture_and_dom_fallback(
        self, scraper, search_params, recovery_page, mock_flight_price, api_available
    ):
        scraper.timeout = 1
        page = recovery_page
        cancelled = asyncio.Event()

        async def stalled_body():
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        completed = Mock(
            url="https://flights.ctrip.com/international/search/api/complete",
            ok=True, json=AsyncMock(return_value={"data": {"flightList": []}}),
        )
        stalled = Mock(
            url="https://flights.ctrip.com/international/search/api/stalled",
            ok=True, json=AsyncMock(side_effect=stalled_body),
        )

        async def navigate(*args, **kwargs):
            listener = page.on.call_args.args[1]
            listener(completed)
            listener(stalled)
            return Mock(status=200)

        page.goto.side_effect = navigate
        with patch.object(scraper, "_wait_for_search_results", new_callable=AsyncMock), \
             patch.object(scraper, "_parse_api_responses", return_value=[mock_flight_price] if api_available else []) as parse, \
             patch.object(scraper, "_parse_flights_from_dom", return_value=[mock_flight_price]) as dom:
            assert await scraper.search_flights(search_params) == [mock_flight_price]

        parse.assert_called_once_with([
            {"url": completed.url, "data": {"data": {"flightList": []}}}
        ], search_params)
        assert dom.await_count == (0 if api_available else 1)
        assert cancelled.is_set()
        page.close.assert_awaited_once()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [403, 432])
    async def test_http_rejection_reports_status_and_closes_page(
        self, scraper, search_params, recovery_page, status
    ):
        page = recovery_page
        page.goto.return_value = Mock(status=status)
        with patch.object(scraper, "_wait_for_search_results", new_callable=AsyncMock) as wait:
            with pytest.raises(AntiCrawlerDetectedError, match=f"HTTP {status}") as error:
                await scraper.search_flights(search_params)

        assert "不会自动打开桌面窗口" in str(error.value)
        assert "验证" in str(error.value)
        wait.assert_not_called()
        page.close.assert_awaited_once_with()
        page.remove_listener.assert_called_once_with("response", page.on.call_args.args[1])

    @pytest.mark.asyncio
    async def test_headed_verification_keeps_same_page_until_clear_and_cards_exist(
        self, scraper, search_params, recovery_page, mock_flight_price
    ):
        scraper.headless = False
        page = recovery_page
        cards = page.locator.return_value
        cards.count.side_effect = [0, 2]
        clock = Mock(time=Mock(side_effect=range(10)))
        with patch.object(scraper, "_is_blocked", side_effect=[True, True, False, False]) as blocked, \
             patch.object(scraper, "_parse_api_responses", return_value=[mock_flight_price]), \
             patch("flightscanner.scrapers.ctrip_scraper.asyncio.get_running_loop", return_value=clock), \
             patch("flightscanner.scrapers.ctrip_scraper.asyncio.sleep", new_callable=AsyncMock) as sleep:
            assert await scraper.search_flights(search_params) == [mock_flight_price]

        assert blocked.await_args_list == [call(page)] * 4
        assert cards.count.await_count == 2
        assert sleep.await_args_list == [call(1), call(1)]
        assert page.locator.call_args_list == [call(".flight-item")] * 2
        scraper._context.new_page.assert_awaited_once_with()
        page.goto.assert_awaited_once()
        page.reload.assert_not_called()
        page.close.assert_awaited_once_with()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("captcha_remains", [True, False])
    async def test_manual_verification_times_out_without_both_requirements(
        self, scraper, recovery_page, captcha_remains
    ):
        scraper.headless = False
        page = recovery_page
        # Existing cards cannot compensate for a CAPTCHA; a clear page needs cards.
        page.locator.return_value.count.return_value = 1 if captcha_remains else 0
        clock = Mock(time=Mock(side_effect=[0, 0, 1, 2, 3]))
        with patch.object(scraper, "_is_blocked", return_value=captcha_remains) as blocked, \
             patch("flightscanner.scrapers.ctrip_scraper.asyncio.get_running_loop", return_value=clock), \
             patch("flightscanner.scrapers.ctrip_scraper.asyncio.sleep", new_callable=AsyncMock) as sleep:
            with pytest.raises(AntiCrawlerDetectedError, match="人工验证未完成"):
                await scraper._wait_for_manual_verification(page, timeout=3)

        assert blocked.await_args_list == [call(page)] * 3
        assert sleep.await_args_list == [call(1)] * 3
        page.goto.assert_not_called()
        page.reload.assert_not_called()
        page.close.assert_not_called()
        scraper._context.new_page.assert_not_called()

    @pytest.mark.asyncio
    async def test_manual_verification_stops_when_same_page_is_closed(self, scraper, recovery_page):
        scraper.headless = False
        recovery_page.is_closed.return_value = True
        with patch.object(scraper, "_is_blocked", new_callable=AsyncMock) as blocked, \
             patch("asyncio.sleep", new_callable=AsyncMock) as sleep:
            with pytest.raises(AntiCrawlerDetectedError, match="人工验证未完成"):
                await scraper._wait_for_manual_verification(recovery_page)
        blocked.assert_not_called()
        sleep.assert_not_called()
        scraper._context.new_page.assert_not_called()

    @pytest.mark.asyncio
    async def test_headless_captcha_raises_without_waiting_for_manual_verification(
        self, scraper, search_params, recovery_page
    ):
        with patch.object(scraper, "_is_blocked", return_value=True), \
             patch.object(scraper, "_wait_for_manual_verification", new_callable=AsyncMock) as manual:
            with pytest.raises(AntiCrawlerDetectedError, match="不会自动打开桌面窗口"):
                await scraper.search_flights(search_params)
        manual.assert_not_called()
        recovery_page.close.assert_awaited_once_with()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("day_offset", [0, 1, 2])
    async def test_dom_arrival_day_and_airports(self, scraper, day_offset):
        params = SearchParams("上海", "悉尼", date(2030, 1, 10))
        suffix = f"\n+{day_offset}天" if day_offset else ""
        card = _mock_dom_card({".arrive-box .time": f"06:30{suffix}"})

        result = await scraper._parse_flight_element(card, params)

        assert result is not None
        assert result.price == Decimal("2345.60")
        info = result.flight_info
        assert info.flight_no == "MU561"
        assert info.airline == "中国东方航空"
        assert (info.departure_time, info.arrival_time) == ("20:15", "06:30")
        assert info.departure_date == params.departure_date
        assert info.arrival_date == params.departure_date + timedelta(days=day_offset)
        assert info.departure_airport == "浦东国际机场T1"
        assert info.arrival_airport == "悉尼金斯福德史密斯机场T1"
        assert info.direction == FlightDirection.DEPARTURE
        assert result.return_flight_info is None

    @pytest.mark.asyncio
    async def test_dom_preserves_all_transfer_flight_numbers(self, scraper):
        card = _mock_dom_card({".arrow-box": "转1次 转广州1h30m"})
        first, second = Mock(spec=ElementHandle), Mock(spec=ElementHandle)
        first.inner_text.return_value = "CZ3526 空客321(中)"
        second.inner_text.return_value = "CZ325 空客350(大)"
        card.query_selector_all.return_value = [first, second]

        result = await scraper._parse_flight_element(
            card, SearchParams("上海", "悉尼", date(2030, 1, 10))
        )

        assert result.flight_info.flight_no == "CZ3526+CZ325"
        assert result.flight_info.arrival_date == date(2030, 1, 11)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("transfer", ["转1次", "转2次"])
    async def test_dom_rejects_missing_transfer_leg_numbers(self, scraper, transfer):
        card = _mock_dom_card({".arrow-box": transfer})
        result = await scraper._parse_flight_element(
            card, SearchParams("上海", "悉尼", date(2030, 1, 10))
        )
        assert result is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("price", ["¥0", "¥0.00", "¥-1", "-¥1,234.56", "NaN", "Infinity", "-Infinity"])
    async def test_dom_rejects_nonpositive_and_nonfinite_prices(self, scraper, price):
        card = _mock_dom_card({".flight-price .price": price})
        params = SearchParams("上海", "悉尼", date(2030, 1, 10))
        assert await scraper._parse_flight_element(card, params) is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("missing", [
        {".plane-No": None},
        {".plane-No": "待定"},
        {".depart-box .time": None},
        {".arrive-box .time": None},
        {".transfer-info-group": "中转 广州 2小时"},
    ])
    async def test_dom_declines_incomplete_or_transfer_cards(self, scraper, missing):
        card = _mock_dom_card(missing)
        params = SearchParams("上海", "悉尼", date(2030, 1, 10))
        assert await scraper._parse_flight_element(card, params) is None

    @pytest.mark.asyncio
    async def test_roundtrip_pairs_real_flights_at_displayed_total_and_reselects_outbound(
        self, scraper
    ):
        params = SearchParams("上海", "悉尼", date(2030, 1, 10), date(2030, 1, 20))
        inbound_params = SearchParams("悉尼", "上海", params.return_date)

        def card(flight_no, price, *, inbound=False, labelled=True):
            fields = {".plane-No": flight_no, ".flight-price .price": price}
            if inbound:
                fields.update({
                    ".depart-box .airport": "悉尼金斯福德史密斯机场T1",
                    ".arrive-box .airport": "浦东国际机场T1",
                    ".depart-box .time": "21:00",
                    ".arrive-box .time": "07:45 +1天",
                })
            return _mock_dom_card(
                fields, locator=True, text="往返含税价" if labelled else "单程含税价"
            )

        outbound_cards = [card("MU561", "¥2000"), card("MU563", "¥1900")]
        inbound_cards = [
            [card("MU562", "¥4000", inbound=True),
             card("MU564", "¥4200", inbound=True),
             card("MU568", "¥1", inbound=True, labelled=False)],
            [card("MU566", "¥3800", inbound=True),
             card(None, "¥1000", inbound=True),  # Labelled but incomplete flight data.
             card("MU570", "¥1", inbound=True, labelled=False)],
        ]
        state = {"outbound": None}
        transitions = []
        for index, card in enumerate(outbound_cards):
            button = Mock(spec=Locator)
            card.get_by_text.return_value = button

            async def select_outbound(index=index):
                assert state["outbound"] is None
                state["outbound"] = index
                transitions.append(("select", index))

            button.click.side_effect = select_outbound

        cards = Mock(spec=Locator)

        def current_cards():
            selected = state["outbound"]
            return outbound_cards if selected is None else inbound_cards[selected]

        cards.count.side_effect = lambda: len(current_cards())
        cards.nth.side_effect = lambda index: current_cards()[index]
        selected_tab = Mock(spec=Locator)
        research_link = Mock(spec=Locator)
        booking_buttons = Mock(spec=Locator)
        booking_buttons.filter.return_value = Mock(spec=Locator)
        booking_buttons.filter.return_value.first = Mock(spec=Locator)

        async def reselect():
            transitions.append(("reselect", state["outbound"]))
            state["outbound"] = None

        research_link.click.side_effect = reselect
        page = Mock(spec=Page)
        page.locator.side_effect = {
            ".flight-item": cards,
            ".flight-item .btn-book": booking_buttons,
            ".segment_tab_selected_flight": selected_tab,
            ".segment_tab_selected_flight .research_link": research_link,
        }.__getitem__
        page.get_by_text.return_value = Mock(spec=Locator)
        page.get_by_text.return_value.first = Mock(spec=Locator)

        with patch.object(scraper, "_parse_flight_element", wraps=scraper._parse_flight_element) as parse, \
             patch.object(scraper, "_wait_for_search_results", new_callable=AsyncMock) as wait:
            results = await scraper._parse_roundtrip_from_dom(page, params)

        assert [(result.flight_info.flight_no, result.return_flight_info.flight_no, result.price)
                for result in results] == [
            ("MU563", "MU566", Decimal("3800")),
            ("MU561", "MU562", Decimal("4000")),
            ("MU561", "MU564", Decimal("4200")),
        ]
        for result in results:
            outbound, inbound = result.flight_info, result.return_flight_info
            assert (outbound.departure_city, outbound.arrival_city) == ("上海", "悉尼")
            assert (inbound.departure_city, inbound.arrival_city) == ("悉尼", "上海")
            assert outbound.departure_date == params.departure_date
            assert inbound.departure_date == params.return_date
            assert inbound.arrival_date == params.return_date + timedelta(days=1)
            assert outbound.direction == FlightDirection.DEPARTURE
            assert inbound.direction == FlightDirection.RETURN
            assert (inbound.departure_time, inbound.arrival_time) == ("21:00", "07:45")
            assert inbound.departure_airport == outbound.arrival_airport
            assert inbound.arrival_airport == outbound.departure_airport
        assert transitions == [("select", 0), ("reselect", 0), ("select", 1), ("reselect", 1)]
        assert wait.await_args_list == [call(page, return_selection=True)] * 2
        assert selected_tab.wait_for.await_args_list == [call(timeout=scraper.timeout)] * 2
        assert research_link.click.await_count == 2
        assert page.get_by_text.return_value.first.wait_for.await_count == 2
        for card in outbound_cards:
            card.get_by_text.assert_called_once_with("选为去程", exact=False)
            assert call(card.element_handle.return_value, params) in parse.await_args_list
        for group in inbound_cards:
            for labelled in group[:2]:
                assert call(labelled.element_handle.return_value, inbound_params) in parse.await_args_list
            group[2].element_handle.assert_not_called()


class TestCtripVirtualDisplay:
    @pytest.mark.asyncio
    async def test_missing_display_support_never_opens_desktop(self, tmp_path):
        scraper = CtripScraper(cookies_file=str(tmp_path / "missing.json"))
        with patch("flightscanner.scrapers.ctrip_scraper.sys.platform", "linux"), \
             patch("flightscanner.scrapers.ctrip_scraper.shutil.which", return_value=None), \
             patch("flightscanner.scrapers.ctrip_scraper.async_playwright") as playwright:
            with pytest.raises(ScraperError, match="不会打开桌面浏览器"):
                await scraper._ensure_browser()
        playwright.assert_not_called()
        assert scraper._display_directory is None

    @pytest.mark.asyncio
    async def test_display_is_private_and_does_not_change_global_environment(self, tmp_path):
        scraper = CtripScraper(cookies_file=str(tmp_path / "missing.json"))
        auth = Mock(returncode=0, communicate=AsyncMock(return_value=(b"", b"")))
        display = Mock(returncode=None)
        display.stdout.readline = AsyncMock(return_value=b"101\n")
        display.communicate = AsyncMock(return_value=(b"", b""))
        before = dict(os.environ)
        with patch("flightscanner.scrapers.ctrip_scraper.shutil.which", side_effect=lambda name, **kw: f"/usr/bin/{name}"), \
             patch("flightscanner.scrapers.ctrip_scraper.asyncio.create_subprocess_exec", side_effect=[auth, display, auth]) as spawn:
            environment = await scraper._start_virtual_display()

        assert dict(os.environ) == before
        assert environment["DISPLAY"] == ":101"
        assert "WAYLAND_DISPLAY" not in environment
        auth_path = Path(environment["XAUTHORITY"])
        assert auth_path.parent.stat().st_mode & 0o777 == 0o700
        command = spawn.call_args_list[1].args
        assert "-auth" in command
        assert "-nolisten" in command and "tcp" in command
        assert "-ac" not in command
        assert spawn.call_args_list[0].args[-2:] == ("source", "-")
        await scraper.close()
        display.terminate.assert_called_once()
        display.communicate.assert_awaited_once()
        assert not auth_path.parent.exists()
        assert dict(os.environ) == before

    @pytest.mark.asyncio
    async def test_browser_launch_failure_cleans_up_private_display(self, tmp_path):
        scraper = CtripScraper(cookies_file=str(tmp_path / "missing.json"))
        process = Mock(returncode=None, communicate=AsyncMock(return_value=(b"", b"")))
        directory = Mock()
        playwright = Mock()
        playwright.stop = AsyncMock()
        playwright.chromium.launch = AsyncMock(side_effect=RuntimeError("launch failed"))

        async def start_display():
            scraper._display_process = process
            scraper._display_directory = directory
            return {"DISPLAY": ":101"}

        with patch.object(scraper, "_start_virtual_display", side_effect=start_display), \
             patch("flightscanner.scrapers.ctrip_scraper.sys.platform", "linux"), \
             patch("flightscanner.scrapers.ctrip_scraper.async_playwright") as factory:
            factory.return_value.start = AsyncMock(return_value=playwright)
            with pytest.raises(RuntimeError, match="launch failed"):
                await scraper._ensure_browser()

        playwright.chromium.launch.assert_awaited_once_with(headless=False, env={"DISPLAY": ":101"})
        playwright.stop.assert_awaited_once()
        process.terminate.assert_called_once()
        directory.cleanup.assert_called_once()
        assert scraper._display_process is None

    @pytest.mark.asyncio
    async def test_cancelled_initialization_cleans_display_before_retry(self, tmp_path):
        scraper = CtripScraper(cookies_file=str(tmp_path / "missing.json"))
        processes = [
            Mock(returncode=None, communicate=AsyncMock(return_value=(b"", b"")))
            for _ in range(2)
        ]
        directories = [Mock(), Mock()]
        attempts = 0

        async def start_display():
            nonlocal attempts
            scraper._display_process = processes[attempts]
            scraper._display_directory = directories[attempts]
            attempts += 1
            if attempts == 1:
                raise asyncio.CancelledError()
            return {"DISPLAY": ":102"}

        playwright = Mock(stop=AsyncMock())
        browser = AsyncMock()
        playwright.chromium.launch = AsyncMock(return_value=browser)
        with patch.object(scraper, "_start_virtual_display", side_effect=start_display), \
             patch("flightscanner.scrapers.ctrip_scraper.sys.platform", "linux"), \
             patch("flightscanner.scrapers.ctrip_scraper.async_playwright") as factory:
            factory.return_value.start = AsyncMock(return_value=playwright)
            with pytest.raises(asyncio.CancelledError):
                await scraper._ensure_browser()
            processes[0].terminate.assert_called_once()
            directories[0].cleanup.assert_called_once()
            assert scraper._display_process is None
            await scraper._ensure_browser()
            await scraper.close()

        for process, directory in zip(processes, directories):
            process.terminate.assert_called_once()
            process.communicate.assert_awaited_once()
            directory.cleanup.assert_called_once()


@pytest.mark.asyncio
async def test_scraper_integration_mock():
    """Integration test with mocked Playwright.

    This test simulates the full scraping flow without actually
    launching a browser or connecting to Ctrip.
    """
    scraper = CtripScraper(headless=True, timeout=5000)

    # Mock all Playwright components; do not write production debug artifacts.
    with patch('flightscanner.scrapers.ctrip_scraper.async_playwright') as mock_playwright_func, \
         patch.object(scraper, '_start_virtual_display', return_value={'DISPLAY': ':99'}), \
         patch('builtins.open', mock_open()), \
         patch('asyncio.sleep'):
        # Setup mock playwright
        mock_playwright = AsyncMock()
        mock_playwright_func.return_value.start = AsyncMock(return_value=mock_playwright)

        mock_browser = AsyncMock()
        mock_playwright.chromium.launch = AsyncMock(return_value=mock_browser)

        mock_context = AsyncMock()
        mock_browser.new_context = AsyncMock(return_value=mock_context)

        mock_page = AsyncMock()
        mock_page.on = Mock()
        mock_page.remove_listener = Mock()
        mock_context.new_page.return_value = mock_page

        # Mock page methods
        mock_page.goto = AsyncMock()
        mock_page.title = AsyncMock(return_value="Flight Search Results")
        mock_page.query_selector = AsyncMock(return_value=None)
        mock_page.query_selector_all = AsyncMock(return_value=[])
        mock_page.wait_for_selector = AsyncMock()
        mock_page.close = AsyncMock()

        # Perform search
        params = SearchParams(
            departure_city="北京",
            arrival_city="上海",
            departure_date=date.today() + timedelta(days=7),
        )

        results = await scraper.search_flights(params)

        # Verify browser was launched and page was created
        assert scraper._browser is not None
        assert scraper._context is not None

        # Clean up
        await scraper.close()
