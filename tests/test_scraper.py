"""Unit tests for CtripScraper.

Note: These tests mock Playwright interactions to avoid actual web scraping.
For integration tests with real scraping, use a separate test suite.
"""

import sys
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch, MagicMock

import pytest

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from flightscanner.interfaces import SearchParams
from flightscanner.scrapers import CtripScraper


@pytest.fixture
def scraper():
    """Create a CtripScraper instance."""
    return CtripScraper(headless=True, timeout=30000, max_retries=3)


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
             patch('asyncio.sleep'):

            # Mock page
            mock_page = AsyncMock()
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
            mock_page.goto = AsyncMock()

            mock_context = AsyncMock()
            mock_context.new_page.return_value = mock_page
            scraper._context = mock_context

            # _parse_api_responses raises unexpected exception
            with patch.object(scraper, '_parse_api_responses', side_effect=RuntimeError("unexpected")):
                with pytest.raises(ParseError):
                    await scraper.search_flights(search_params)

    def test_parse_itinerary_roundtrip_sets_return_flight_info(self, scraper: CtripScraper):
        """往返搜索时 _parse_itinerary 应创建虚拟占位符标记 return_flight_info，
        表示此记录已包含往返合计价格。Ctrip API 往返搜索时只返回去程 segment，
        回程信息被合并到 adultPrice 中，无法单独提取。
        """
        from flightscanner.interfaces import FlightDirection

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

        assert len(result) == 1
        fp = result[0]
        assert fp.price == 2300
        assert fp.flight_info.flight_no == "ZH4835"
        assert fp.flight_info.direction == FlightDirection.DEPARTURE
        # 虚拟占位符标记此为已合并的往返记录
        assert fp.return_flight_info is not None, "return_flight_info 应为虚拟占位符"
        assert fp.return_flight_info.flight_no == "VIRTUAL_RETURN"
        assert fp.return_flight_info.direction == FlightDirection.RETURN

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
        """无座位信息的记录应被过滤掉（售罄或过期数据）。"""
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
                {"adultPrice": 1150, "cabin": "Y", "seatsLeft": None},  # 无座位，应过滤
                {"adultPrice": 1150, "cabin": "Y", "seatsLeft": 0},      # 无座位，应过滤
                {"adultPrice": 1500, "cabin": "Y", "seatsLeft": 5},      # 有座位，应保留
            ],
        }
        params = SearchParams(
            departure_city="上海",
            arrival_city="成都",
            departure_date=date.today() + timedelta(days=7),
            return_date=None,
        )

        result = scraper._parse_itinerary(itinerary, params)

        # 仅保留有座位的记录
        assert len(result) == 1
        assert result[0].price == 1500
        assert result[0].available_seats == 5


@pytest.mark.asyncio
async def test_scraper_integration_mock():
    """Integration test with mocked Playwright.

    This test simulates the full scraping flow without actually
    launching a browser or connecting to Ctrip.
    """
    scraper = CtripScraper(headless=True, timeout=5000)

    # Mock all Playwright components
    with patch('flightscanner.scrapers.ctrip_scraper.async_playwright') as mock_playwright_func, \
         patch('asyncio.sleep'):
        # Setup mock playwright
        mock_playwright = AsyncMock()
        mock_playwright_func.return_value.start = AsyncMock(return_value=mock_playwright)

        mock_browser = AsyncMock()
        mock_playwright.chromium.launch = AsyncMock(return_value=mock_browser)

        mock_context = AsyncMock()
        mock_browser.new_context = AsyncMock(return_value=mock_context)

        mock_page = AsyncMock()
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
