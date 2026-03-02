"""Ctrip flight scraper implementation.

This module provides a scraper implementation for Ctrip (携程) flight search
using Playwright for browser automation.
"""

import asyncio
import logging
import random
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import List, Optional

from playwright.async_api import async_playwright, Browser, Page, BrowserContext
from tenacity import retry, stop_after_attempt, wait_exponential

from flightscanner.interfaces import (
    FlightDirection,
    FlightInfo,
    FlightPrice,
    FlightScraper,
    SearchParams,
    ScraperError,
    NetworkTimeoutError,
    ParseError,
    AntiCrawlerDetectedError,
)
from flightscanner.utils.config import settings

logger = logging.getLogger(__name__)


class CtripScraper(FlightScraper):
    """Ctrip flight data scraper using Playwright.

    This scraper navigates to Ctrip's flight search page, extracts flight
    information and prices, and returns structured data.

    Attributes:
        headless: Whether to run browser in headless mode.
        timeout: Page load timeout in milliseconds.
        max_retries: Maximum number of retry attempts.
    """

    def __init__(
        self,
        headless: bool = True,
        timeout: int = 30000,
        max_retries: int = 3,
    ):
        """Initialize the scraper.

        Args:
            headless: Whether to run browser in headless mode.
            timeout: Page load timeout in milliseconds.
            max_retries: Maximum number of retry attempts.
        """
        self.headless = headless
        self.timeout = timeout
        self.max_retries = max_retries
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None

    async def _ensure_browser(self) -> None:
        """Ensure browser is initialized."""
        if not self._browser:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=self.headless,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ],
            )
            self._context = await self._browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
            )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def search_flights(self, params: SearchParams) -> List[FlightPrice]:
        """Search for flights on Ctrip.

        Args:
            params: Search parameters including cities and dates.

        Returns:
            List of flight prices found.

        Raises:
            NetworkTimeoutError: When network request times out.
            ParseError: When page parsing fails.
            AntiCrawlerDetectedError: When anti-crawler mechanism blocks access.
        """
        await self._ensure_browser()

        page: Optional[Page] = None
        try:
            # Create new page
            page = await self._context.new_page()

            # Construct search URL
            url = self._build_search_url(params)
            logger.info(f"Navigating to: {url}")

            # Navigate with retry
            await page.goto(url, wait_until="networkidle", timeout=self.timeout)

            # Add random delay to avoid detection
            await asyncio.sleep(random.uniform(2, 5))

            # Check for anti-crawler detection
            if await self._is_blocked(page):
                raise AntiCrawlerDetectedError(
                    "Anti-crawler mechanism detected. Access blocked."
                )

            # Wait for flight results to load
            await self._wait_for_results(page)

            # Parse flight data
            flight_prices = await self._parse_flights(page, params)

            logger.info(f"Found {len(flight_prices)} flights")
            return flight_prices

        except asyncio.TimeoutError as e:
            logger.error(f"Timeout while loading page: {e}")
            raise NetworkTimeoutError(f"Page load timeout: {e}") from e
        except AntiCrawlerDetectedError:
            raise
        except Exception as e:
            logger.error(f"Error searching flights: {e}")
            raise ParseError(f"Failed to parse flight data: {e}") from e
        finally:
            if page:
                await page.close()

    def _build_search_url(self, params: SearchParams) -> str:
        """Build Ctrip search URL from parameters.

        Args:
            params: Search parameters.

        Returns:
            Ctrip flight search URL.
        """
        # Format date as YYYY-MM-DD
        dep_date = params.departure_date.strftime("%Y-%m-%d")

        # Build URL for one-way flight search
        # Note: This is a simplified URL structure, may need adjustment based on actual Ctrip URL
        base_url = "https://flights.ctrip.com/online/list/oneway"
        url = (
            f"{base_url}"
            f"?depdate={dep_date}"
            f"&depcity={params.departure_city}"
            f"&arrcity={params.arrival_city}"
        )

        if params.return_date:
            # For round-trip, would need different URL structure
            ret_date = params.return_date.strftime("%Y-%m-%d")
            url += f"&retdate={ret_date}"

        return url

    async def _is_blocked(self, page: Page) -> bool:
        """Check if access is blocked by anti-crawler mechanism.

        Args:
            page: Playwright page object.

        Returns:
            True if blocked, False otherwise.
        """
        # Check for common blocking indicators
        try:
            title = await page.title()
            if "验证" in title or "验证码" in title or "blocked" in title.lower():
                return True

            # Check for CAPTCHA elements
            captcha = await page.query_selector(".captcha, .verify-code, #captcha")
            if captcha:
                return True

            return False
        except Exception:
            return False

    async def _wait_for_results(self, page: Page) -> None:
        """Wait for flight results to load on the page.

        Args:
            page: Playwright page object.
        """
        try:
            # Wait for flight list container
            # Note: Selector may need adjustment based on actual Ctrip page structure
            await page.wait_for_selector(
                ".flight-item, .flight-list, [class*='flight']",
                timeout=self.timeout // 2,
            )
        except Exception as e:
            logger.warning(f"Timeout waiting for results selector: {e}")
            # Continue anyway, might still be able to parse

    async def _parse_flights(
        self, page: Page, params: SearchParams
    ) -> List[FlightPrice]:
        """Parse flight information from the page.

        Args:
            page: Playwright page object.
            params: Original search parameters.

        Returns:
            List of parsed flight prices.
        """
        flight_prices = []

        try:
            # Get all flight elements
            # Note: These selectors are placeholders and need to be updated
            # based on the actual Ctrip page structure
            flight_elements = await page.query_selector_all(
                ".flight-item, [class*='flight-item'], [class*='flightItem']"
            )

            if not flight_elements:
                logger.warning("No flight elements found on page")
                # Save screenshot for debugging
                await page.screenshot(path="debug_no_flights.png")
                return flight_prices

            for element in flight_elements:
                try:
                    flight_price = await self._parse_flight_element(element, params)
                    if flight_price:
                        flight_prices.append(flight_price)
                except Exception as e:
                    logger.warning(f"Error parsing flight element: {e}")
                    continue

        except Exception as e:
            logger.error(f"Error parsing flights: {e}")
            # Save screenshot for debugging
            await page.screenshot(path="debug_parse_error.png")
            raise ParseError(f"Failed to parse flights: {e}")

        return flight_prices

    async def _parse_flight_element(
        self, element, params: SearchParams
    ) -> Optional[FlightPrice]:
        """Parse a single flight element.

        Args:
            element: Playwright element handle.
            params: Search parameters.

        Returns:
            Parsed FlightPrice or None if parsing fails.
        """
        try:
            # Note: These selectors are placeholders and need to be updated
            # based on the actual Ctrip page structure

            # Extract flight number
            flight_no_elem = await element.query_selector(
                "[class*='flightNo'], [class*='flight-no'], .flight-number"
            )
            flight_no = await flight_no_elem.inner_text() if flight_no_elem else "UNKNOWN"

            # Extract airline
            airline_elem = await element.query_selector(
                "[class*='airline'], [class*='airlineName']"
            )
            airline = await airline_elem.inner_text() if airline_elem else "未知航空公司"

            # Extract departure and arrival times
            dep_time_elem = await element.query_selector(
                "[class*='depTime'], [class*='depart-time']"
            )
            dep_time = await dep_time_elem.inner_text() if dep_time_elem else "00:00"

            arr_time_elem = await element.query_selector(
                "[class*='arrTime'], [class*='arrival-time']"
            )
            arr_time = await arr_time_elem.inner_text() if arr_time_elem else "00:00"

            # Extract price
            price_elem = await element.query_selector(
                "[class*='price'], .flight-price"
            )
            price_text = await price_elem.inner_text() if price_elem else "0"
            # Extract numeric price (remove currency symbols)
            price_str = "".join(c for c in price_text if c.isdigit() or c == ".")
            price = Decimal(price_str) if price_str else Decimal("0")

            # Extract seat class
            seat_class_elem = await element.query_selector(
                "[class*='cabin'], [class*='seat-class']"
            )
            seat_class = await seat_class_elem.inner_text() if seat_class_elem else "经济舱"

            # Create FlightInfo
            flight_info = FlightInfo(
                flight_no=flight_no.strip(),
                airline=airline.strip(),
                departure_city=params.departure_city,
                arrival_city=params.arrival_city,
                departure_time=dep_time.strip(),
                arrival_time=arr_time.strip(),
                departure_date=params.departure_date,
                direction=FlightDirection.DEPARTURE,
            )

            # Create FlightPrice
            return FlightPrice(
                flight_info=flight_info,
                price=price,
                currency="CNY",
                seat_class=seat_class.strip(),
                available_seats=None,  # Ctrip doesn't always show seat count
                scraped_at=datetime.now(timezone.utc),
                source="ctrip",
            )

        except Exception as e:
            logger.warning(f"Error parsing flight element: {e}")
            return None

    async def close(self) -> None:
        """Clean up browser resources."""
        try:
            if self._context:
                await self._context.close()
            if self._browser:
                await self._browser.close()
            if self._playwright:
                await self._playwright.stop()
        except Exception as e:
            logger.error(f"Error closing browser: {e}")
        finally:
            self._context = None
            self._browser = None
            self._playwright = None
