"""Qunar (去哪儿网) flight scraper implementation.

This module provides a scraper implementation for Qunar flight search
using Playwright for browser automation, with login QR code support.
"""

import asyncio
import logging
import random
import base64
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import List, Optional, Dict
from urllib.parse import quote

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

logger = logging.getLogger(__name__)


class LoginRequiredError(ScraperError):
    """Raised when login is required to access the page."""
    pass


class QunarScraper(FlightScraper):
    """Qunar flight data scraper using Playwright.

    This scraper navigates to Qunar's flight search page, extracts flight
    information and prices, and returns structured data.

    Supports:
    - Automatic login QR code detection and display
    - Cookie injection for authenticated sessions
    - Round-trip flight search

    Attributes:
        headless: Whether to run browser in headless mode.
        timeout: Page load timeout in milliseconds.
        max_retries: Maximum number of retry attempts.
        cookies: Optional cookies for authentication.
    """

    def __init__(
        self,
        headless: bool = True,
        timeout: int = 30000,
        max_retries: int = 3,
        cookies: Optional[List[Dict]] = None,
    ):
        """Initialize the scraper.

        Args:
            headless: Whether to run browser in headless mode.
            timeout: Page load timeout in milliseconds.
            max_retries: Maximum number of retry attempts.
            cookies: Optional list of cookies for authentication.
        """
        self.headless = headless
        self.timeout = timeout
        self.max_retries = max_retries
        self.cookies = cookies or []
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
                    "--disable-web-security",
                    "--disable-features=IsolateOrigins,site-per-process",
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
                extra_http_headers={
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                },
            )

            # Anti-detection: Override navigator properties
            await self._context.add_init_script("""
                // Remove webdriver flag
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => false,
                });

                // Mock chrome property
                window.chrome = {
                    runtime: {},
                };

                // Mock plugins
                Object.defineProperty(navigator, 'plugins', {
                    get: () => [1, 2, 3, 4, 5],
                });

                // Mock languages
                Object.defineProperty(navigator, 'languages', {
                    get: () => ['zh-CN', 'zh', 'en'],
                });

                // Mock permissions
                const originalQuery = window.navigator.permissions.query;
                window.navigator.permissions.query = (parameters) => (
                    parameters.name === 'notifications' ?
                        Promise.resolve({ state: Notification.permission }) :
                        originalQuery(parameters)
                );
            """)

            # Add cookies if provided
            if self.cookies:
                await self._context.add_cookies(self.cookies)
                logger.info(f"Injected {len(self.cookies)} cookies")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def search_flights(self, params: SearchParams) -> List[FlightPrice]:
        """Search for flights on Qunar.

        Args:
            params: Search parameters including cities and dates.

        Returns:
            List of flight prices found.

        Raises:
            NetworkTimeoutError: When network request times out.
            ParseError: When page parsing fails.
            AntiCrawlerDetectedError: When anti-crawler mechanism blocks access.
            LoginRequiredError: When login is required and no cookies provided.
        """
        await self._ensure_browser()

        page: Optional[Page] = None
        try:
            # Create new page
            page = await self._context.new_page()

            # Construct search URL
            url = self._build_search_url(params)
            logger.info(f"Navigating to: {url}")

            # Navigate with retry (use domcontentloaded to handle login popup faster)
            await page.goto(url, wait_until="domcontentloaded", timeout=self.timeout)

            # Add random delay to avoid detection
            await asyncio.sleep(random.uniform(2, 5))

            # Check for login requirement
            if await self._is_login_required(page):
                logger.warning("Login required detected")

                # Capture and display QR code
                qr_code_data = await self._capture_login_qr_code(page)
                if qr_code_data:
                    # Save QR code to file
                    qr_path = "qunar_login_qr.png"
                    with open(qr_path, "wb") as f:
                        f.write(base64.b64decode(qr_code_data))
                    logger.info(f"Login QR code saved to {qr_path}")

                    if self.headless:
                        # In headless mode, try to open the QR code with system viewer
                        logger.info("Opening QR code with system viewer...")
                        try:
                            import subprocess
                            import platform

                            system = platform.system()
                            if system == "Darwin":  # macOS
                                subprocess.Popen(["open", qr_path])
                            elif system == "Windows":
                                subprocess.Popen(["start", qr_path], shell=True)
                            elif system == "Linux":
                                subprocess.Popen(["xdg-open", qr_path])
                        except Exception as e:
                            logger.warning(f"Could not open QR code automatically: {e}")

                        logger.info("Please scan the QR code to login...")
                        logger.info("Waiting for login to complete (timeout: 120 seconds)...")

                        # Wait for login to complete
                        login_success = await self._wait_for_login(page, timeout=120)

                        if not login_success:
                            raise LoginRequiredError(
                                "Login timeout. Please scan the QR code and try again."
                            )

                        logger.info("Login successful! Continuing with scraping...")

                        # Add delay to ensure page is ready after login
                        await asyncio.sleep(2)

                        # Save cookies for future use
                        cookies = await self._context.cookies()
                        logger.info(f"Saved {len(cookies)} cookies from successful login")

                    else:
                        # In non-headless mode, wait for user to login manually
                        logger.info("Please login in the browser window...")
                        await asyncio.sleep(60)  # Wait for user to login

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
        except (AntiCrawlerDetectedError, LoginRequiredError):
            raise
        except Exception as e:
            logger.error(f"Error searching flights: {e}")
            raise ParseError(f"Failed to parse flight data: {e}") from e
        finally:
            if page:
                await page.close()

    def _build_search_url(self, params: SearchParams) -> str:
        """Build Qunar search URL from parameters.

        Args:
            params: Search parameters.

        Returns:
            Qunar flight search URL.
        """
        # Format dates
        from_date = params.departure_date.strftime("%Y-%m-%d")

        # URL encode city names
        from_city_encoded = quote(params.departure_city)
        to_city_encoded = quote(params.arrival_city)

        if params.return_date:
            # Round-trip flight
            to_date = params.return_date.strftime("%Y-%m-%d")
            base_url = "https://flight.qunar.com/site/roundtrip_list_new.htm"
            url = (
                f"{base_url}?"
                f"fromCity={from_city_encoded}&"
                f"toCity={to_city_encoded}&"
                f"fromDate={from_date}&"
                f"toDate={to_date}&"
                f"from=flight_dom_search"
            )
        else:
            # One-way flight
            base_url = "https://flight.qunar.com/site/oneway_list.htm"
            url = (
                f"{base_url}?"
                f"fromCity={from_city_encoded}&"
                f"toCity={to_city_encoded}&"
                f"fromDate={from_date}&"
                f"from=flight_dom_search"
            )

        return url

    async def _is_login_required(self, page: Page) -> bool:
        """Check if login is required.

        Args:
            page: Playwright page object.

        Returns:
            True if login is required, False otherwise.
        """
        try:
            # PRIORITY 1: Check for login button (if visible, user is NOT logged in)
            # Based on actual DOM: <a id="__headerInfo_login__" href="...">登录</a>
            login_button = await page.query_selector("#__headerInfo_login__")
            if login_button:
                is_visible = await login_button.is_visible()
                if is_visible:
                    logger.info("Detected login button (#__headerInfo_login__) - user not logged in")
                    return True
                else:
                    logger.info("Login button exists but not visible - user appears to be logged in")
                    return False

            # PRIORITY 2: Check for Qunar-specific login popup
            # The .login_QR_imgs div appears when login popup is already open
            login_qr_popup = await page.query_selector(".login_QR_imgs")
            if login_qr_popup:
                is_visible = await login_qr_popup.is_visible()
                if is_visible:
                    logger.info("Detected Qunar login popup (.login_QR_imgs)")
                    return True

            # PRIORITY 3: Check for login modal/popup
            login_modal_selectors = [
                ".login-modal[style*='display: block']",
                ".login-modal[style*='display:block']",
                ".login_container",  # Qunar specific
                "[class*='login'][class*='popup'][style*='display']",
                "[class*='login'][class*='modal'][style*='display']",
                "[class*='login'][class*='dialog'][style*='display']",
            ]

            for selector in login_modal_selectors:
                try:
                    modal = await page.query_selector(selector)
                    if modal:
                        is_visible = await modal.is_visible()
                        if is_visible:
                            logger.info(f"Detected login modal: {selector}")
                            return True
                except:
                    continue

            # PRIORITY 4: Check URL for login redirect
            # After clicking login button, page may redirect to login.jsp
            current_url = page.url
            if "user.qunar.com/passport/login" in current_url:
                logger.info(f"Detected Qunar login page: {current_url}")
                return True
            elif "login" in current_url.lower() and "passport" in current_url.lower():
                logger.info(f"Detected login redirect in URL: {current_url}")
                return True

            # PRIORITY 5: Check for QR code image (if visible, login is required)
            qr_img = await page.query_selector("img[src*='qcode/show']")
            if qr_img:
                is_visible = await qr_img.is_visible()
                if is_visible:
                    logger.info("Detected visible QR code image")
                    return True

            logger.info("No login indicators found - user appears to be logged in")
            return False
        except Exception as e:
            logger.warning(f"Error checking login requirement: {e}")
            return False

    async def _capture_login_qr_code(self, page: Page) -> Optional[str]:
        """Capture login QR code from the page.

        Args:
            page: Playwright page object.

        Returns:
            Base64 encoded QR code image data, or None if not found.
        """
        try:
            logger.info("Attempting to capture login QR code...")

            # Step 0: Click login button to trigger popup if not already open
            # Check if login popup is already visible
            login_popup = await page.query_selector(".login_QR_imgs")
            popup_visible = login_popup and await login_popup.is_visible()

            if not popup_visible:
                logger.info("Login popup not visible, attempting to click login button...")
                # Try to click the login button to trigger popup
                login_button = await page.query_selector("#__headerInfo_login__")
                if login_button:
                    try:
                        await login_button.click()
                        logger.info("✓ Clicked login button (#__headerInfo_login__)")
                        # Wait for page redirect and QR code to load
                        logger.info("Waiting for login page to load...")
                        await asyncio.sleep(5)  # Increased wait time for page redirect
                    except Exception as e:
                        logger.warning(f"Failed to click login button: {e}")
                else:
                    logger.warning("Login button not found, popup may appear automatically")

            # Step 1: Qunar-specific selectors (highest priority based on actual behavior)
            # After clicking login button, page redirects to login.jsp with QR code directly in page
            qunar_specific_selectors = [
                "img[src*='qcode/show']",              # Direct QR code image (MOST RELIABLE)
                "img[src*='user.qunar.com/qcode']",    # QR code URL pattern
                ".login_QR_imgs img",                   # QR code in container (modal scenario)
                ".login_QR_imgs",                       # Container (modal scenario)
            ]

            # Try Qunar-specific selectors first with longer wait
            logger.info("Checking for Qunar-specific login QR code...")
            logger.info(f"Current page URL: {page.url}")

            for selector in qunar_specific_selectors:
                try:
                    logger.info(f"Trying selector: {selector}")
                    qr_element = await page.wait_for_selector(
                        selector, timeout=5000, state="visible"
                    )
                    if qr_element:
                        logger.info(f"✓ Found QR code with Qunar-specific selector: {selector}")

                        # If it's the container, find the img inside
                        if selector == ".login_QR_imgs":
                            img_in_container = await qr_element.query_selector("img")
                            if img_in_container:
                                qr_element = img_in_container
                                logger.info("  Found img element inside .login_QR_imgs")

                        # Get the image
                        tag_name = await qr_element.evaluate("el => el.tagName.toLowerCase()")
                        logger.info(f"  Element tag: {tag_name}")

                        if tag_name == "img":
                            src = await qr_element.get_attribute("src")
                            logger.info(f"  Image src: {src[:100] if src else 'None'}")

                            if src:
                                if src.startswith("data:image"):
                                    # Base64 encoded
                                    logger.info("  QR code is base64 encoded")
                                    return src.split(",")[1] if "," in src else src
                                elif src.startswith("http"):
                                    # External URL - take screenshot
                                    logger.info("  QR code is external URL, taking screenshot")
                                    screenshot = await qr_element.screenshot()
                                    return base64.b64encode(screenshot).decode("utf-8")

                            # Fallback: screenshot the element
                            screenshot = await qr_element.screenshot()
                            return base64.b64encode(screenshot).decode("utf-8")

                except Exception as e:
                    logger.debug(f"  Selector {selector} not found: {e}")
                    continue

            # Step 2: Generic QR code selectors
            logger.info("Trying generic QR code selectors...")
            generic_selectors = [
                "img[class*='qrcode']",
                "img[class*='QRCode']",
                "img[src*='qrcode']",
                "img[src*='QR']",
                "img[alt*='二维码']",
                "canvas[class*='qrcode']",
                "[class*='qrcode'] img",
                "[class*='login'] img[src^='http']",
                "[class*='modal'] img[src^='http']",
            ]

            for selector in generic_selectors:
                try:
                    qr_element = await page.wait_for_selector(
                        selector, timeout=2000, state="visible"
                    )
                    if qr_element:
                        logger.info(f"✓ Found QR code with generic selector: {selector}")
                        screenshot = await qr_element.screenshot()
                        return base64.b64encode(screenshot).decode("utf-8")
                except:
                    continue

            # Step 3: Check iframes
            logger.info("Checking iframes for QR code...")
            for i, frame in enumerate(page.frames):
                if frame != page.main_frame:
                    try:
                        # Try Qunar-specific selectors in iframe
                        for selector in qunar_specific_selectors:
                            try:
                                qr_in_frame = await frame.wait_for_selector(
                                    selector, timeout=1000, state="visible"
                                )
                                if qr_in_frame:
                                    logger.info(f"✓ Found QR code in iframe {i}: {selector}")
                                    screenshot = await qr_in_frame.screenshot()
                                    return base64.b64encode(screenshot).decode("utf-8")
                            except:
                                continue
                    except:
                        continue

            # Step 4: Screenshot login area
            logger.info("Trying to screenshot login area...")
            login_area_selectors = [
                ".login_container",
                ".login-container",
                "[class*='login'][class*='modal']",
                "[class*='login'][class*='dialog']",
                "[class*='login'][class*='popup']",
                "[role='dialog']",
                ".modal-content",
            ]

            for selector in login_area_selectors:
                try:
                    login_area = await page.wait_for_selector(
                        selector, timeout=2000, state="visible"
                    )
                    if login_area:
                        logger.info(f"✓ Found login area: {selector}")
                        screenshot = await login_area.screenshot()
                        return base64.b64encode(screenshot).decode("utf-8")
                except:
                    continue

            # Step 5: Last resort - full page screenshot
            logger.warning("Could not find specific QR code, taking full page screenshot")
            screenshot = await page.screenshot(full_page=False)
            return base64.b64encode(screenshot).decode("utf-8")

        except Exception as e:
            logger.error(f"Error capturing login QR code: {e}", exc_info=True)
            return None

    async def _is_blocked(self, page: Page) -> bool:
        """Check if access is blocked by anti-crawler mechanism.

        Args:
            page: Playwright page object.

        Returns:
            True if blocked, False otherwise.
        """
        try:
            title = await page.title()
            if "验证" in title or "验证码" in title or "blocked" in title.lower():
                return True

            # Check for CAPTCHA elements
            captcha = await page.query_selector(
                ".captcha, .verify-code, #captcha, [class*='captcha']"
            )
            if captcha:
                return True

            return False
        except Exception:
            return False

    async def _wait_for_login(self, page: Page, timeout: int = 120) -> bool:
        """Wait for login to complete by polling the page.

        Args:
            page: Playwright page object.
            timeout: Maximum wait time in seconds (default 120).

        Returns:
            True if login succeeded, False if timeout.
        """
        start_time = asyncio.get_event_loop().time()
        poll_interval = 2  # Check every 2 seconds

        while (asyncio.get_event_loop().time() - start_time) < timeout:
            try:
                # Check if login modal/requirement disappeared
                if not await self._is_login_required(page):
                    logger.info("Login modal disappeared - login likely successful")
                    return True

                # Check for user info indicators (successful login)
                user_info = await page.query_selector(
                    "[class*='user'], [class*='username'], [class*='avatar']"
                )
                if user_info:
                    is_visible = await user_info.is_visible()
                    if is_visible:
                        logger.info("User info detected - login successful")
                        return True

                # Wait before next poll
                await asyncio.sleep(poll_interval)

            except Exception as e:
                logger.warning(f"Error during login polling: {e}")
                await asyncio.sleep(poll_interval)

        logger.warning(f"Login timeout after {timeout} seconds")
        return False

    async def _wait_for_results(self, page: Page) -> None:
        """Wait for flight results to load on the page.

        Args:
            page: Playwright page object.
        """
        try:
            # Wait for flight list container
            await page.wait_for_selector(
                ".flight-list, [class*='flight-item'], [class*='flightItem']",
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
            flight_elements = await page.query_selector_all(
                ".flight-item, [class*='flight-item'], [class*='flightItem']"
            )

            if not flight_elements:
                logger.warning("No flight elements found on page")
                # Save screenshot for debugging
                await page.screenshot(path="qunar_debug_no_flights.png")
                return flight_prices

            for element in flight_elements:
                try:
                    # Parse both departure and return flights if round-trip
                    if params.return_date:
                        # Parse departure flight
                        dep_flight = await self._parse_flight_element(
                            element, params, FlightDirection.DEPARTURE
                        )
                        if dep_flight:
                            flight_prices.append(dep_flight)

                        # Parse return flight
                        ret_flight = await self._parse_flight_element(
                            element, params, FlightDirection.RETURN
                        )
                        if ret_flight:
                            flight_prices.append(ret_flight)
                    else:
                        # One-way flight
                        flight_price = await self._parse_flight_element(
                            element, params, FlightDirection.DEPARTURE
                        )
                        if flight_price:
                            flight_prices.append(flight_price)
                except Exception as e:
                    logger.warning(f"Error parsing flight element: {e}")
                    continue

        except Exception as e:
            logger.error(f"Error parsing flights: {e}")
            # Save screenshot for debugging
            await page.screenshot(path="qunar_debug_parse_error.png")
            raise ParseError(f"Failed to parse flights: {e}")

        return flight_prices

    async def _parse_flight_element(
        self, element, params: SearchParams, direction: FlightDirection
    ) -> Optional[FlightPrice]:
        """Parse a single flight element.

        Args:
            element: Playwright element handle.
            params: Search parameters.
            direction: Flight direction (departure or return).

        Returns:
            Parsed FlightPrice or None if parsing fails.
        """
        try:
            # Determine which section to parse based on direction
            section_class = (
                "[class*='depart'], [class*='go']" if direction == FlightDirection.DEPARTURE
                else "[class*='return'], [class*='back']"
            )

            # Try to find the specific flight section
            flight_section = await element.query_selector(section_class)
            if not flight_section:
                flight_section = element

            # Extract flight number
            flight_no_elem = await flight_section.query_selector(
                "[class*='flightNo'], [class*='flight-no'], [class*='flightNumber']"
            )
            flight_no = await flight_no_elem.inner_text() if flight_no_elem else "UNKNOWN"

            # Extract airline
            airline_elem = await flight_section.query_selector(
                "[class*='airline'], [class*='airlineName'], [class*='carrier']"
            )
            airline = await airline_elem.inner_text() if airline_elem else "未知航空公司"

            # Extract departure and arrival times
            dep_time_elem = await flight_section.query_selector(
                "[class*='depTime'], [class*='depart-time'], [class*='deptime']"
            )
            dep_time = await dep_time_elem.inner_text() if dep_time_elem else "00:00"

            arr_time_elem = await flight_section.query_selector(
                "[class*='arrTime'], [class*='arrival-time'], [class*='arrtime']"
            )
            arr_time = await arr_time_elem.inner_text() if arr_time_elem else "00:00"

            # Extract price
            price_elem = await element.query_selector(
                "[class*='price'], [class*='Price']"
            )
            price_text = await price_elem.inner_text() if price_elem else "0"
            # Extract numeric price (remove currency symbols)
            price_str = "".join(c for c in price_text if c.isdigit() or c == ".")
            price = Decimal(price_str) if price_str else Decimal("0")

            # Extract seat class
            seat_class_elem = await flight_section.query_selector(
                "[class*='cabin'], [class*='seat-class'], [class*='cabinName']"
            )
            seat_class = await seat_class_elem.inner_text() if seat_class_elem else "经济舱"

            # Determine departure date based on direction
            dep_date = (
                params.departure_date if direction == FlightDirection.DEPARTURE
                else params.return_date
            )

            # Create FlightInfo
            flight_info = FlightInfo(
                flight_no=flight_no.strip(),
                airline=airline.strip(),
                departure_city=params.departure_city,
                arrival_city=params.arrival_city,
                departure_time=dep_time.strip(),
                arrival_time=arr_time.strip(),
                departure_date=dep_date,
                direction=direction,
            )

            # Create FlightPrice
            return FlightPrice(
                flight_info=flight_info,
                price=price,
                currency="CNY",
                seat_class=seat_class.strip(),
                available_seats=None,
                scraped_at=datetime.now(timezone.utc),
                source="qunar",
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