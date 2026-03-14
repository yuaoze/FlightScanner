"""Unit tests for ScraperRegistry and multi-source aggregation logic."""

import asyncio
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from flightscanner.interfaces import (
    FlightDirection,
    FlightInfo,
    FlightPrice,
    FlightScraper,
    SearchParams,
)
from flightscanner.scrapers import ScraperRegistry
from flightscanner.scrapers.ctrip_scraper import CtripScraper
from flightscanner.scrapers.qunar_scraper import QunarScraper


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_flight_price(flight_no: str, seat_class: str, price: float, source: str = "test") -> FlightPrice:
    """Create a minimal FlightPrice for test purposes."""
    info = FlightInfo(
        flight_no=flight_no,
        airline="测试航空",
        departure_city="北京",
        arrival_city="上海",
        departure_time="08:00",
        arrival_time="10:00",
        departure_date=date.today(),
        direction=FlightDirection.DEPARTURE,
    )
    return FlightPrice(
        flight_info=info,
        price=Decimal(str(price)),
        currency="CNY",
        seat_class=seat_class,
        available_seats=None,
        scraped_at=datetime.now(timezone.utc),
        source=source,
    )


# ── ScraperRegistry Tests ─────────────────────────────────────────────────────

class TestScraperRegistry:
    """Test cases for ScraperRegistry factory class."""

    def test_list_platforms_returns_sorted_list(self):
        """list_platforms() should return a sorted list of registered names."""
        platforms = ScraperRegistry.list_platforms()

        assert isinstance(platforms, list)
        assert "ctrip" in platforms
        assert "qunar" in platforms
        assert platforms == sorted(platforms)

    def test_get_ctrip_returns_ctrip_scraper(self):
        """get('ctrip') should return a CtripScraper instance."""
        scraper = ScraperRegistry.get("ctrip", headless=True, timeout=5000)

        assert isinstance(scraper, CtripScraper)
        assert scraper.headless is True
        assert scraper.timeout == 5000

    def test_get_qunar_returns_qunar_scraper(self):
        """get('qunar') should return a QunarScraper instance."""
        scraper = ScraperRegistry.get("qunar", headless=False)

        assert isinstance(scraper, QunarScraper)
        assert scraper.headless is False

    def test_get_is_case_insensitive(self):
        """get() should handle uppercase platform names."""
        scraper = ScraperRegistry.get("CTRIP")

        assert isinstance(scraper, CtripScraper)

    def test_get_raises_for_unknown_platform(self):
        """get() should raise ValueError for unknown platform names."""
        with pytest.raises(ValueError, match="未知爬虫平台"):
            ScraperRegistry.get("nonexistent_platform")

    def test_build_enabled_returns_list_in_order(self):
        """build_enabled() should return scrapers in the same order as input."""
        scrapers = ScraperRegistry.build_enabled(["ctrip", "qunar"])

        assert len(scrapers) == 2
        assert isinstance(scrapers[0], CtripScraper)
        assert isinstance(scrapers[1], QunarScraper)

    def test_build_enabled_empty_list(self):
        """build_enabled([]) should return an empty list without raising."""
        scrapers = ScraperRegistry.build_enabled([])

        assert scrapers == []

    def test_build_enabled_raises_for_unknown_platform(self):
        """build_enabled() should raise ValueError if any platform is unknown."""
        with pytest.raises(ValueError):
            ScraperRegistry.build_enabled(["qunar", "unknown_platform"])

    def test_register_adds_custom_scraper(self):
        """register() should add a custom scraper class to the registry."""
        # Create a minimal custom scraper subclass
        class MockScraper(FlightScraper):
            async def search_flights(self, params): return []
            async def close(self): pass

        ScraperRegistry.register("mock_test_platform", MockScraper)

        try:
            scraper = ScraperRegistry.get("mock_test_platform")
            assert isinstance(scraper, MockScraper)
        finally:
            # Clean up: remove the test entry from registry
            registry = ScraperRegistry._get_registry()
            registry.pop("mock_test_platform", None)

    def test_register_raises_for_non_scraper_class(self):
        """register() should raise TypeError for classes not inheriting FlightScraper."""
        class NotAScraper:
            pass

        with pytest.raises(TypeError):
            ScraperRegistry.register("bad_platform", NotAScraper)

    def test_build_enabled_kwargs_passed_to_all(self):
        """build_enabled() should pass kwargs to all scraper constructors."""
        scrapers = ScraperRegistry.build_enabled(["ctrip"], headless=False, timeout=60000)

        assert scrapers[0].headless is False
        assert scrapers[0].timeout == 60000


# ── PriceMonitorScheduler._deduplicate Tests ─────────────────────────────────

class TestDeduplicateLogic:
    """Test the deduplication logic used in multi-source aggregation."""

    def _get_deduplicate(self):
        """Import the static method from PriceMonitorScheduler."""
        from flightscanner.scheduler.price_monitor import PriceMonitorScheduler
        return PriceMonitorScheduler._deduplicate

    def test_deduplicate_empty_list(self):
        """_deduplicate([]) should return []."""
        deduplicate = self._get_deduplicate()
        assert deduplicate([]) == []

    def test_deduplicate_keeps_only_cheapest_within_same_source(self):
        """Same (flight_no, seat_class, source) should keep only the cheapest."""
        deduplicate = self._get_deduplicate()

        prices = [
            _make_flight_price("CA1234", "经济舱", 900, "qunar"),
            _make_flight_price("CA1234", "经济舱", 800, "qunar"),  # cheaper, same source
        ]

        result = deduplicate(prices)

        assert len(result) == 1
        assert result[0].price == Decimal("800")
        assert result[0].source == "qunar"

    def test_deduplicate_preserves_different_platforms_for_same_flight(self):
        """Same (flight_no, seat_class) from different sources should BOTH be kept."""
        deduplicate = self._get_deduplicate()

        prices = [
            _make_flight_price("CA1234", "经济舱", 800, "qunar"),
            _make_flight_price("CA1234", "经济舱", 750, "ctrip"),
        ]

        result = deduplicate(prices)

        assert len(result) == 2
        sources = {fp.source for fp in result}
        assert sources == {"qunar", "ctrip"}

    def test_deduplicate_keeps_different_seat_classes_separately(self):
        """Different seat_class for same flight should be kept as separate entries."""
        deduplicate = self._get_deduplicate()

        prices = [
            _make_flight_price("MU5678", "经济舱", 600, "qunar"),
            _make_flight_price("MU5678", "商务舱", 1500, "ctrip"),
        ]

        result = deduplicate(prices)

        assert len(result) == 2
        seat_classes = {fp.seat_class for fp in result}
        assert seat_classes == {"经济舱", "商务舱"}

    def test_deduplicate_different_flights_all_kept(self):
        """Different flight numbers should all be kept."""
        deduplicate = self._get_deduplicate()

        prices = [
            _make_flight_price("CA1001", "经济舱", 500),
            _make_flight_price("MU2002", "经济舱", 600),
            _make_flight_price("CZ3003", "经济舱", 450),
        ]

        result = deduplicate(prices)

        assert len(result) == 3

    def test_deduplicate_result_sorted_by_price(self):
        """_deduplicate() should return results sorted by price ascending."""
        deduplicate = self._get_deduplicate()

        prices = [
            _make_flight_price("CA1001", "经济舱", 800),
            _make_flight_price("MU2002", "经济舱", 400),
            _make_flight_price("CZ3003", "经济舱", 600),
        ]

        result = deduplicate(prices)

        assert [fp.price for fp in result] == [
            Decimal("400"), Decimal("600"), Decimal("800")
        ]

    def test_deduplicate_multi_source_dedup_scenario(self):
        """Simulate real multi-source scenario: Qunar + Ctrip same flights.

        Different platforms produce independent entries; only within-platform
        duplicates are collapsed to the cheapest price.
        """
        deduplicate = self._get_deduplicate()

        # Qunar results
        qunar_prices = [
            _make_flight_price("CA1234", "经济舱", 820, "qunar"),
            _make_flight_price("MU5678", "经济舱", 710, "qunar"),
            _make_flight_price("MU5678", "商务舱", 2100, "qunar"),
        ]
        # Ctrip results (same flights, different prices)
        ctrip_prices = [
            _make_flight_price("CA1234", "经济舱", 799, "ctrip"),
            _make_flight_price("MU5678", "经济舱", 750, "ctrip"),
        ]

        result = deduplicate(qunar_prices + ctrip_prices)

        # 5 unique (flight_no, seat_class, source) combos — each platform kept separately
        assert len(result) == 5

        # Both sources for CA1234/经济舱 are preserved
        ca1234_prices = [fp for fp in result if fp.flight_info.flight_no == "CA1234"]
        assert len(ca1234_prices) == 2
        ca1234_sources = {fp.source for fp in ca1234_prices}
        assert ca1234_sources == {"qunar", "ctrip"}

        # Both sources for MU5678/经济舱 are preserved
        mu5678_eco = [
            fp for fp in result
            if fp.flight_info.flight_no == "MU5678" and fp.seat_class == "经济舱"
        ]
        assert len(mu5678_eco) == 2
        assert {fp.source for fp in mu5678_eco} == {"qunar", "ctrip"}

        # MU5678/商务舱: only on Qunar (2100)
        mu5678_biz = [
            fp for fp in result
            if fp.flight_info.flight_no == "MU5678" and fp.seat_class == "商务舱"
        ]
        assert len(mu5678_biz) == 1
        assert mu5678_biz[0].source == "qunar"
        assert mu5678_biz[0].price == Decimal("2100")

    def test_deduplicate_collapses_within_platform_duplicates(self):
        """Within the same platform, only the cheapest price for a key is kept."""
        deduplicate = self._get_deduplicate()

        prices = [
            # Same flight, same class, same platform — three prices scraped at diff times
            _make_flight_price("CA1234", "经济舱", 900, "qunar"),
            _make_flight_price("CA1234", "经济舱", 820, "qunar"),
            _make_flight_price("CA1234", "经济舱", 860, "qunar"),
            # Same flight on ctrip
            _make_flight_price("CA1234", "经济舱", 799, "ctrip"),
        ]

        result = deduplicate(prices)

        # qunar collapses to 820; ctrip keeps 799 → 2 total
        assert len(result) == 2
        qunar_entry = next(fp for fp in result if fp.source == "qunar")
        assert qunar_entry.price == Decimal("820")


# ── PriceMonitorScheduler 调度修复 Tests ──────────────────────────────────────

def _make_monitor() -> object:
    """Create a bare PriceMonitorScheduler instance bypassing __init__."""
    from flightscanner.scheduler.price_monitor import PriceMonitorScheduler
    monitor = object.__new__(PriceMonitorScheduler)
    monitor.scrapers = []
    monitor.notifiers = []
    monitor.scrape_route = AsyncMock()
    mock_session = MagicMock()
    mock_session.__enter__ = MagicMock(return_value=mock_session)
    mock_session.__exit__ = MagicMock(return_value=False)
    monitor._SessionLocal = MagicMock(return_value=mock_session)
    return monitor


class TestStartupCatchup:
    """Tests for _startup_catchup() overdue-route detection logic."""

    def _make_route_with_price(
        self,
        route_id: int,
        scrape_interval: int,
        latest_scraped_at: datetime | None,
        is_active: bool = True,
    ):
        """Create a RouteWithLatestPrice dataclass for test purposes."""
        from flightscanner.core.services import RouteWithLatestPrice
        now = datetime.now(timezone.utc)
        return RouteWithLatestPrice(
            id=route_id,
            origin="北京",
            destination="上海",
            target_date=date.today(),
            target_price=Decimal("800"),
            scrape_interval=scrape_interval,
            is_active=is_active,
            created_at=now,
            latest_price=None,
            latest_scraped_at=latest_scraped_at,
            price_count=0,
        )

    @pytest.mark.asyncio
    async def test_never_scraped_route_is_overdue(self):
        """A route with latest_scraped_at=None is always considered overdue."""
        now = datetime.now(timezone.utc)
        monitor = _make_monitor()

        route_never = self._make_route_with_price(1, 6, None)
        mock_orm_route = MagicMock()
        mock_orm_route.id = 1
        mock_orm_route.is_active = True

        mock_rs = MagicMock()
        mock_rs.get_all_routes.return_value = [route_never]
        mock_rs.get_active_routes.return_value = [mock_orm_route]

        with patch("flightscanner.scheduler.price_monitor.RouteService", return_value=mock_rs), \
             patch("asyncio.sleep"):
            await monitor._startup_catchup()

        monitor.scrape_route.assert_called_once_with(mock_orm_route)

    @pytest.mark.asyncio
    async def test_overdue_route_is_scraped(self):
        """Routes whose (now - last_scraped) >= scrape_interval are scraped."""
        now = datetime.now(timezone.utc)
        monitor = _make_monitor()

        # 7 hours ago, interval 6 → overdue
        route_overdue = self._make_route_with_price(1, 6, now - timedelta(hours=7))
        mock_orm_route = MagicMock()
        mock_orm_route.id = 1
        mock_orm_route.is_active = True

        mock_rs = MagicMock()
        mock_rs.get_all_routes.return_value = [route_overdue]
        mock_rs.get_active_routes.return_value = [mock_orm_route]

        with patch("flightscanner.scheduler.price_monitor.RouteService", return_value=mock_rs), \
             patch("asyncio.sleep"):
            await monitor._startup_catchup()

        monitor.scrape_route.assert_called_once_with(mock_orm_route)

    @pytest.mark.asyncio
    async def test_fresh_route_is_not_scraped(self):
        """Routes scraped within the interval are skipped."""
        now = datetime.now(timezone.utc)
        monitor = _make_monitor()

        # 3 hours ago, interval 6 → not overdue
        route_fresh = self._make_route_with_price(1, 6, now - timedelta(hours=3))

        mock_rs = MagicMock()
        mock_rs.get_all_routes.return_value = [route_fresh]

        with patch("flightscanner.scheduler.price_monitor.RouteService", return_value=mock_rs):
            await monitor._startup_catchup()

        monitor.scrape_route.assert_not_called()

    @pytest.mark.asyncio
    async def test_inactive_route_is_not_scraped(self):
        """Inactive routes are ignored regardless of last scrape time."""
        now = datetime.now(timezone.utc)
        monitor = _make_monitor()

        route_inactive = self._make_route_with_price(1, 6, now - timedelta(hours=24), is_active=False)

        mock_rs = MagicMock()
        mock_rs.get_all_routes.return_value = [route_inactive]

        with patch("flightscanner.scheduler.price_monitor.RouteService", return_value=mock_rs):
            await monitor._startup_catchup()

        monitor.scrape_route.assert_not_called()

    @pytest.mark.asyncio
    async def test_mixed_routes_only_overdue_scraped(self):
        """Only overdue routes are scraped; fresh and inactive ones are skipped."""
        now = datetime.now(timezone.utc)
        monitor = _make_monitor()

        route_overdue = self._make_route_with_price(1, 6, now - timedelta(hours=8))   # overdue
        route_never   = self._make_route_with_price(2, 4, None)                        # overdue (never scraped)
        route_fresh   = self._make_route_with_price(3, 6, now - timedelta(hours=2))   # fresh
        route_inactive = self._make_route_with_price(4, 6, now - timedelta(hours=12), is_active=False)

        mock_orm_1 = MagicMock(); mock_orm_1.id = 1; mock_orm_1.is_active = True
        mock_orm_2 = MagicMock(); mock_orm_2.id = 2; mock_orm_2.is_active = True

        mock_rs = MagicMock()
        mock_rs.get_all_routes.return_value = [route_overdue, route_never, route_fresh, route_inactive]
        mock_rs.get_active_routes.return_value = [mock_orm_1, mock_orm_2]

        with patch("flightscanner.scheduler.price_monitor.RouteService", return_value=mock_rs), \
             patch("asyncio.sleep"):
            await monitor._startup_catchup()

        assert monitor.scrape_route.call_count == 2
        scraped_ids = {call.args[0].id for call in monitor.scrape_route.call_args_list}
        assert scraped_ids == {1, 2}


class TestScheduleRouteClosureFix:
    """Tests that schedule_route() closure uses route_id, not a stale ORM object."""

    @pytest.mark.asyncio
    async def test_job_fetches_fresh_route_from_db(self):
        """When the scheduled job fires, it fetches a fresh Route from DB via route_id."""
        from flightscanner.scheduler.price_monitor import PriceMonitorScheduler
        monitor = _make_monitor()
        monitor.scheduler = MagicMock()
        monitor.scheduler.get_job.return_value = None

        fresh_route = MagicMock()
        fresh_route.id = 42
        fresh_route.is_active = True

        mock_rs = MagicMock()
        mock_rs.get_route_by_id.return_value = fresh_route

        stale_route = MagicMock()
        stale_route.id = 42
        stale_route.origin = "北京"
        stale_route.destination = "上海"
        stale_route.scrape_interval = 6

        with patch("flightscanner.scheduler.price_monitor.RouteService", return_value=mock_rs):
            monitor.schedule_route(stale_route)

        # Extract the coroutine function registered with the scheduler
        job_func = monitor.scheduler.add_job.call_args.args[0]

        with patch("flightscanner.scheduler.price_monitor.RouteService", return_value=mock_rs):
            await job_func()

        # Must have looked up the route by ID (fresh from DB)
        mock_rs.get_route_by_id.assert_called_once_with(42)
        # Must have scraped using the FRESH object, not stale_route
        monitor.scrape_route.assert_called_once_with(fresh_route)

    @pytest.mark.asyncio
    async def test_job_skips_deactivated_route(self):
        """If the route becomes inactive before the job fires, it is skipped."""
        from flightscanner.scheduler.price_monitor import PriceMonitorScheduler
        monitor = _make_monitor()
        monitor.scheduler = MagicMock()
        monitor.scheduler.get_job.return_value = None

        # Route was deactivated between schedule_route() and the job firing
        deactivated_route = MagicMock()
        deactivated_route.id = 99
        deactivated_route.is_active = False

        mock_rs = MagicMock()
        mock_rs.get_route_by_id.return_value = deactivated_route

        stale_route = MagicMock()
        stale_route.id = 99
        stale_route.origin = "上海"
        stale_route.destination = "广州"
        stale_route.scrape_interval = 6

        with patch("flightscanner.scheduler.price_monitor.RouteService", return_value=mock_rs):
            monitor.schedule_route(stale_route)

        job_func = monitor.scheduler.add_job.call_args.args[0]

        with patch("flightscanner.scheduler.price_monitor.RouteService", return_value=mock_rs):
            await job_func()

        monitor.scrape_route.assert_not_called()
