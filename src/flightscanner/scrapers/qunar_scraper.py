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
from flightscanner.utils.city_codes import get_city_code, is_international_route

logger = logging.getLogger(__name__)


class LoginRequiredError(ScraperError):
    """Raised when login is required to access the page."""
    pass


# Mapping from Chinese city names to IATA airport codes
# Used to build valid Qunar search URLs
CITY_AIRPORT_CODES: Dict[str, str] = {
    "北京": "BJS",
    "上海": "SHA",
    "广州": "CAN",
    "深圳": "SZX",
    "成都": "CTU",
    "重庆": "CKG",
    "武汉": "WUH",
    "西安": "XIY",
    "杭州": "HGH",
    "南京": "NKG",
    "厦门": "XMN",
    "昆明": "KMG",
    "三亚": "SYX",
    "青岛": "TAO",
    "大连": "DLC",
    "哈尔滨": "HRB",
    "沈阳": "SHE",
    "长沙": "CSX",
    "郑州": "CGO",
    "天津": "TSN",
    "合肥": "HFE",
    "贵阳": "KWE",
    "南宁": "NNG",
    "长春": "CGQ",
    "太原": "TYN",
    "石家庄": "SJW",
    "福州": "FOC",
    "济南": "TNA",
    "南昌": "KHN",
    "海口": "HAK",
    "兰州": "LHW",
    "西宁": "XNN",
    "乌鲁木齐": "URC",
    "呼和浩特": "HET",
    "银川": "INC",
    "珠海": "ZUH",
    "温州": "WNZ",
    "宁波": "NGB",
    "烟台": "YNT",
    "桂林": "KWL",
}


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

    #: 默认 Cookie 文件路径，放在项目根目录即可自动加载
    DEFAULT_COOKIES_FILE = "qunar_cookies.json"

    def __init__(
        self,
        headless: bool = True,
        timeout: int = 30000,
        max_retries: int = 3,
        cookies: Optional[List[Dict]] = None,
        cookies_file: Optional[str] = None,
    ):
        """Initialize the scraper.

        Args:
            headless: Whether to run browser in headless mode.
            timeout: Page load timeout in milliseconds.
            max_retries: Maximum number of retry attempts.
            cookies: Optional list of cookies for authentication.
            cookies_file: Path to a cookie file.  If None and ``cookies`` is
                empty, falls back to ``DEFAULT_COOKIES_FILE`` in the working
                directory.
        """
        self.headless = headless
        self.timeout = timeout
        self.max_retries = max_retries

        # Cookie 加载优先级：
        #   1. 显式传入的 cookies 列表
        #   2. cookies_file 指定的文件
        #   3. 工作目录下的 qunar_cookies.json（若存在）
        if cookies:
            self.cookies = cookies
        else:
            path = cookies_file or self.DEFAULT_COOKIES_FILE
            self.cookies = self.load_cookies_from_file(path)
            if self.cookies:
                logger.info(f"从 {path} 加载了 {len(self.cookies)} 条 Cookie")

        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None

    @staticmethod
    def load_cookies_from_file(path: str) -> List[Dict]:
        """从文件加载 Cookie，支持两种格式：

        **格式一：JSON 数组（推荐，来自 Cookie Editor 等浏览器扩展导出）**

        .. code-block:: json

            [
              {"name": "QN1", "value": "xxx", "domain": ".qunar.com", "path": "/"},
              {"name": "_qnauthtoken", "value": "yyy", "domain": ".qunar.com", "path": "/"}
            ]

        **格式二：原始 Cookie 字符串（从 Chrome DevTools → Network → 请求头复制）**

        .. code-block:: text

            QN1=xxx; _qnauthtoken=yyy; QN300=zzz

        （也可以包含 "Cookie: " 前缀，会自动去除）

        Args:
            path: Cookie 文件路径。

        Returns:
            Playwright 格式的 cookie 字典列表，文件不存在时返回空列表。
        """
        import json as _json
        from pathlib import Path as _Path

        cookie_file = _Path(path)
        if not cookie_file.exists():
            return []

        content = cookie_file.read_text(encoding="utf-8").strip()
        if not content:
            return []

        # ── 格式一：JSON 数组 ──────────────────────────────────────────────
        if content.lstrip().startswith("["):
            try:
                raw = _json.loads(content)
                result: List[Dict] = []
                for c in raw:
                    if not isinstance(c, dict) or "name" not in c or "value" not in c:
                        continue
                    cookie: Dict = {
                        "name": c["name"],
                        "value": c["value"],
                        "domain": c.get("domain", ".qunar.com"),
                        "path": c.get("path", "/"),
                    }
                    # 可选字段
                    for opt in ("expires", "httpOnly", "secure", "sameSite"):
                        if opt in c:
                            cookie[opt] = c[opt]
                    result.append(cookie)
                return result
            except (_json.JSONDecodeError, Exception) as e:
                logger.warning(f"Cookie JSON 解析失败: {e}")
                return []

        # ── 格式二：原始 Cookie 字符串 ────────────────────────────────────
        # 去除 "Cookie: " 前缀（如有）
        if content.lower().startswith("cookie:"):
            content = content[7:].strip()

        # 将 name=value 对解析后，分别注册到所有相关域名上
        # （.qunar.com 带点前缀 = 对所有子域名生效，包括 flight.qunar.com）
        pairs: List[tuple] = []
        for pair in content.split(";"):
            pair = pair.strip()
            if "=" not in pair:
                continue
            name, _, value = pair.partition("=")
            name = name.strip()
            value = value.strip()
            if name:
                pairs.append((name, value))

        if not pairs:
            return []

        cookies: List[Dict] = []
        # 注册到所有去哪儿相关域名，确保子域名都能收到 Cookie
        for domain in (".qunar.com", "flight.qunar.com", "m.flight.qunar.com"):
            for name, value in pairs:
                cookies.append({
                    "name": name,
                    "value": value,
                    "domain": domain,
                    "path": "/",
                })
        return cookies

    @staticmethod
    async def refresh_cookies_via_login(
        output_path: str = "qunar_cookies.json",
        timeout_seconds: int = 300,
    ) -> bool:
        """弹出浏览器窗口让用户扫码登录，成功后自动保存 Cookie 到文件。

        典型用法（命令行）::

            python scripts/qunar_login.py

        或在代码里手动调用::

            import asyncio
            from flightscanner.scrapers.qunar_scraper import QunarScraper
            asyncio.run(QunarScraper.refresh_cookies_via_login())

        Args:
            output_path: Cookie 保存路径，默认覆盖 ``qunar_cookies.json``。
            timeout_seconds: 等待扫码的最长秒数，默认 300 秒（5 分钟）。

        Returns:
            登录成功并保存 Cookie 返回 True，超时或异常返回 False。
        """
        import json as _json
        from playwright.async_api import async_playwright as _ap

        _LOGIN_URL = (
            "https://user.qunar.com/passport/login.jsp"
            "?ret=https%3A%2F%2Fwww.qunar.com%2F"
        )
        # 检测登录成功的特征 Cookie（QN44 = 用户名，quinn = 会话令牌）
        _LOGIN_COOKIES = {"QN44", "quinn", "_qnauthtoken"}

        print("=" * 60)
        print("[去哪儿] 正在打开浏览器登录窗口…")
        print("[去哪儿] 请在弹出的窗口中扫描二维码完成登录")
        print(f"[去哪儿] 最多等待 {timeout_seconds // 60} 分钟")
        print("=" * 60)

        try:
            async with _ap() as p:
                browser = await p.chromium.launch(
                    headless=False,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--no-sandbox",
                        "--disable-infobars",
                    ],
                )
                context = await browser.new_context(
                    viewport={"width": 1280, "height": 800},
                    user_agent=(
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                )
                page = await context.new_page()
                await page.goto(_LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
                print("[去哪儿] 浏览器已就绪，等待扫码…")

                # 每 0.5 秒检查一次，直到出现登录成功的特征 Cookie
                logged_in = False
                for _ in range(timeout_seconds * 2):
                    all_cookies = await context.cookies()
                    cookie_names = {c["name"] for c in all_cookies}
                    if cookie_names & _LOGIN_COOKIES:
                        logged_in = True
                        break
                    await asyncio.sleep(0.5)

                if not logged_in:
                    print("[去哪儿] 等待超时，未检测到登录成功，请重试。")
                    await browser.close()
                    return False

                # 等待一小会儿让所有 Cookie 写入完毕
                await asyncio.sleep(2)

                all_cookies = await context.cookies()
                qunar_cookies = [
                    {
                        "name": c["name"],
                        "value": c["value"],
                        "domain": c["domain"],
                        "path": c.get("path", "/"),
                    }
                    for c in all_cookies
                    if "qunar.com" in c.get("domain", "")
                ]

                with open(output_path, "w", encoding="utf-8") as _f:
                    _json.dump(qunar_cookies, _f, ensure_ascii=False, indent=2)

                username = next(
                    (c["value"] for c in all_cookies if c["name"] == "QN44"),
                    "（未知）",
                )
                print(f"[去哪儿] 登录成功！账号：{username}")
                print(
                    f"[去哪儿] 已保存 {len(qunar_cookies)} 条 Cookie → {output_path}"
                )
                await browser.close()
                return True

        except Exception as e:
            logger.error(f"[去哪儿] 登录流程异常: {e}")
            print(f"[去哪儿] 登录出错: {e}")
            return False

    async def _ensure_browser(self) -> None:
        """Ensure browser is initialized with comprehensive anti-detection settings."""
        if not self._browser:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=self.headless,
                args=[
                    # Suppress automation banners and flags
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    # Suppress "Chrome is being controlled by automated software" bar
                    "--disable-infobars",
                    "--start-maximized",
                ],
            )
            self._context = await self._browser.new_context(
                # Typical macOS laptop resolution — consistent with macOS UA
                viewport={"width": 1440, "height": 900},
                # macOS Chrome UA — must match sec-ch-ua-platform below
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
                # Chrome sends these Client Hints headers automatically;
                # Playwright does not — adding them closes a major detection gap
                extra_http_headers={
                    "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
                    "Accept": (
                        "text/html,application/xhtml+xml,application/xml;"
                        "q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,"
                        "application/signed-exchange;v=b3;q=0.7"
                    ),
                    "sec-ch-ua": (
                        '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"'
                    ),
                    "sec-ch-ua-mobile": "?0",
                    "sec-ch-ua-platform": '"macOS"',
                },
            )

            # Comprehensive anti-bot JS injection — runs before any page script
            await self._context.add_init_script("""
                // 1. webdriver must be undefined, not false.
                //    Bot detectors use: typeof navigator.webdriver !== 'undefined'
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

                // 2. Delete CDP-specific properties injected by Chrome automation
                for (const k of [
                    'cdc_adoQpoasnfa76pfcZLmcfl_Array',
                    'cdc_adoQpoasnfa76pfcZLmcfl_Promise',
                    'cdc_adoQpoasnfa76pfcZLmcfl_Symbol',
                ]) { try { delete window[k]; } catch (_) {} }

                // 3. Full chrome object — bare {runtime:{}} is a known bot signal
                window.chrome = {
                    app: {
                        isInstalled: false,
                        InstallState: {
                            DISABLED: 'disabled', INSTALLED: 'installed',
                            NOT_INSTALLED: 'not_installed',
                        },
                        RunningState: {
                            CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run',
                            RUNNING: 'running',
                        },
                    },
                    runtime: {
                        PlatformOs: {
                            ANDROID: 'android', CROS: 'cros', LINUX: 'linux',
                            MAC: 'mac', OPENBSD: 'openbsd', WIN: 'win',
                        },
                        PlatformArch: {
                            ARM: 'arm', ARM64: 'arm64', X86_32: 'x86-32', X86_64: 'x86-64',
                        },
                    },
                    loadTimes: () => ({}),
                    csi: () => ({}),
                };

                // 4. Realistic plugin list (not the bare [1,2,3,4,5])
                const _pluginData = [
                    ['PDF Viewer', 'Portable Document Format', 'internal-pdf-viewer'],
                    ['Chrome PDF Viewer', 'Portable Document Format', 'internal-pdf-viewer'],
                    ['Chromium PDF Viewer', 'Portable Document Format', 'internal-pdf-viewer'],
                    ['Microsoft Edge PDF Viewer', 'Portable Document Format', 'internal-pdf-viewer'],
                    ['WebKit built-in PDF', 'Portable Document Format', 'internal-pdf-viewer'],
                ];
                const _plugins = _pluginData.map(([name, desc, filename]) => {
                    const p = { name, description: desc, filename, length: 0 };
                    return p;
                });
                Object.defineProperty(navigator, 'plugins', { get: () => _plugins });

                // 5. Languages
                Object.defineProperty(navigator, 'languages', {
                    get: () => ['zh-CN', 'zh', 'en-US', 'en'],
                });

                // 6. Platform — must match macOS UA
                Object.defineProperty(navigator, 'platform', { get: () => 'MacIntel' });

                // 7. Hardware signals
                Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
                Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });
                Object.defineProperty(navigator, 'maxTouchPoints', { get: () => 0 });

                // 8. Permissions API spoof
                const _origPermQuery = window.navigator.permissions.query.bind(
                    window.navigator.permissions
                );
                window.navigator.permissions.query = (params) =>
                    params.name === 'notifications'
                        ? Promise.resolve({ state: Notification.permission })
                        : _origPermQuery(params);
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

        # ── 国际往返程：使用专用接口，直接获取组合价格 ─────────────────────
        if params.return_date and is_international_route(
            params.departure_city, params.arrival_city
        ):
            logger.info(
                "[往返] 国际往返程: %s→%s, 去程 %s 回程 %s",
                params.departure_city, params.arrival_city,
                params.departure_date, params.return_date,
            )
            return await self._search_inter_roundtrip(params)

        page: Optional[Page] = None
        captured_api_responses: List[Dict] = []

        try:
            # Create new page
            page = await self._context.new_page()

            # ── Network response interception ──────────────────────────────────
            # Qunar loads flight data via AJAX JSON endpoints.
            # Capturing these responses gives us structured data independent of
            # DOM structure and bypasses page-rendering bot-detection.
            async def _on_response(response) -> None:
                try:
                    if "qunar.com" not in response.url:
                        return
                    if not response.ok:
                        return
                    ct = response.headers.get("content-type", "")
                    if "json" not in ct:
                        return
                    data = await response.json()
                    captured_api_responses.append({"url": response.url, "data": data})
                    logger.debug(f"Captured API response: {response.url}")
                except Exception:
                    pass

            page.on("response", _on_response)
            # ──────────────────────────────────────────────────────────────────

            # ── Request detail logging for key AJAX endpoints ─────────────────
            # Logs POST body / headers for wbdflightlist so we can diagnose
            # empty-data issues without running a full network capture.
            async def _on_request(request) -> None:
                try:
                    if "wbdflightlist" in request.url:
                        post_data = (request.post_data or "")[:300]
                        logger.info(
                            f"wbdflightlist request → method={request.method} "
                            f"post_data={post_data!r}"
                        )
                except Exception:
                    pass

            page.on("request", _on_request)
            # ──────────────────────────────────────────────────────────────────

            # Construct search URL
            url = self._build_search_url(params)
            logger.info(f"Navigating to: {url}")

            # ── Session warm-up ───────────────────────────────────────────────
            # Qunar's flight-search API returns empty data when called without
            # established session cookies.  Visiting the homepage first lets
            # Qunar set the required cookies (qtkn, QN1, etc.) before we
            # navigate to the search URL.
            logger.info("Warming up session via Qunar homepage...")
            try:
                await page.goto(
                    "https://www.qunar.com/",
                    wait_until="domcontentloaded",
                    timeout=self.timeout,
                )
                await asyncio.sleep(random.uniform(1, 3))
                logger.info("Session warm-up complete")
            except Exception as e:
                logger.warning(f"Homepage warm-up failed ({e}), continuing anyway")
            # ──────────────────────────────────────────────────────────────────

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
                        # In non-headless mode, use the same polling mechanism to detect
                        # login completion, then navigate back to the search URL.
                        logger.info("Please login in the browser window...")
                        login_success = await self._wait_for_login(page, timeout=120)

                        if not login_success:
                            raise LoginRequiredError(
                                "Login timeout. Please login in the browser window and try again."
                            )

                        logger.info("Login successful! Navigating back to search results...")

                        # Save cookies for future use
                        cookies = await self._context.cookies()
                        logger.info(f"Saved {len(cookies)} cookies from successful login")

                        # Navigate back to the original search URL
                        await asyncio.sleep(2)
                        await page.goto(url, wait_until="domcontentloaded", timeout=self.timeout)
                        await asyncio.sleep(random.uniform(2, 4))

            # Check for anti-crawler detection
            if await self._is_blocked(page):
                raise AntiCrawlerDetectedError(
                    "Anti-crawler mechanism detected. Access blocked."
                )

            # Wait for flight results to load
            await self._wait_for_results(page)

            # Parse flight data from DOM
            flight_prices = await self._parse_flights(page, params)

            # If DOM parsing yielded nothing, try the captured API responses
            if not flight_prices and captured_api_responses:
                logger.info(
                    f"DOM parsing empty — trying {len(captured_api_responses)} "
                    "captured API response(s)"
                )
                flight_prices = self._parse_api_responses(captured_api_responses, params)

            # If still nothing, try mobile API (requires cookies / login state)
            if not flight_prices:
                import json as _json
                debug_api_path = "qunar_debug_api_responses.json"
                with open(debug_api_path, "w", encoding="utf-8") as f:
                    _json.dump(
                        captured_api_responses, f,
                        ensure_ascii=False, indent=2, default=str,
                    )
                logger.warning(
                    f"桌面端未获取到航班. 调试文件: qunar_debug_no_flights.png, "
                    f"qunar_debug_no_flights.html, {debug_api_path} "
                    f"({len(captured_api_responses)} 条 API 响应已保存)"
                )

                if self.cookies:
                    logger.info("已检测到 Cookie，切换移动端 API 重试...")
                    flight_prices = await self._search_via_mobile_api(params)
                    if not flight_prices:
                        logger.warning(
                            "移动端 API 也未返回数据。"
                            "请确认 Cookie 是否有效且未过期。"
                        )
                        flight_prices = await self._maybe_refresh_and_retry(
                            params, page
                        )
                else:
                    logger.warning(
                        "未提供 Cookie。请将有效的 Qunar 登录 Cookie 放入 "
                        "qunar_cookies.json 文件后重试。"
                    )
                    flight_prices = await self._maybe_refresh_and_retry(
                        params, page
                    )

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

    def _get_airport_code(self, city: str) -> str:
        """Get IATA airport code for a Chinese city name.

        Args:
            city: Chinese city name.

        Returns:
            IATA code if found, otherwise the city name itself.
        """
        # 优先使用去哪儿专用映射，不存在时回退到共享城市代码表
        return CITY_AIRPORT_CODES.get(city) or get_city_code(city) or city

    def _build_search_url(self, params: SearchParams) -> str:
        """Build Qunar search URL from parameters.

        Qunar 页面区分：
        - oneway_list.htm / roundtrip_list_new.htm：两端都在中国
        - oneway_list_inter.htm / roundtrip_list_inter.htm：至少一端不在中国
                (包含真正的国际航班、非中国→非中国 等)

        所有情况都使用 searchDepartureAirport / searchArrivalAirport 参数格式，
        只是根据城市类型选择不同的页面。

        Args:
            params: Search parameters.

        Returns:
            Qunar flight search URL.
        """
        from_date = params.departure_date.strftime("%Y-%m-%d")
        from_city_encoded = quote(params.departure_city)
        to_city_encoded = quote(params.arrival_city)
        from_code = self._get_airport_code(params.departure_city)
        to_code = self._get_airport_code(params.arrival_city)

        intl = is_international_route(params.departure_city, params.arrival_city)

        # ── 根据路由类型选择页面：纯国内 vs 涉及非中国城市 ───────────────
        if params.return_date:
            to_date = params.return_date.strftime("%Y-%m-%d")
            # 往返：国内用 roundtrip_list_new.htm，跨国用 roundtrip_list_inter.htm
            base_url = (
                "https://flight.qunar.com/site/roundtrip_list_inter.htm"
                if intl
                else "https://flight.qunar.com/site/roundtrip_list_new.htm"
            )
            url = (
                f"{base_url}?"
                f"searchDepartureAirport={from_city_encoded}&"
                f"searchArrivalAirport={to_city_encoded}&"
                f"searchDepartureTime={from_date}&"
                f"searchReturnTime={to_date}&"
                f"nextNDays=0&"
                f"startSearch=true&"
                f"fromCode={from_code}&"
                f"toCode={to_code}&"
                f"from=flight_dom_search&"
                f"lowestPrice=null"
            )
        else:
            # 单程：国内用 oneway_list.htm，跨国用 oneway_list_inter.htm
            base_url = (
                "https://flight.qunar.com/site/oneway_list_inter.htm"
                if intl
                else "https://flight.qunar.com/site/oneway_list.htm"
            )
            url = (
                f"{base_url}?"
                f"searchDepartureAirport={from_city_encoded}&"
                f"searchArrivalAirport={to_city_encoded}&"
                f"searchDepartureTime={from_date}&"
                f"nextNDays=0&"
                f"startSearch=true&"
                f"fromCode={from_code}&"
                f"toCode={to_code}&"
                f"from=flight_dom_search&"
                f"lowestPrice=null"
            )

        return url

    def _build_mobile_search_url(self, params: SearchParams) -> str:
        """构建移动端去哪儿机票搜索 URL。

        移动端页面（m.flight.qunar.com）使用 depCity/arrCity/goDate 参数，
        通过 touchInnerList API 加载航班数据，对 Bella 令牌的验证相对宽松，
        登录状态下可正常返回数据。

        Args:
            params: 搜索参数。

        Returns:
            移动端搜索 URL。
        """
        dep = quote(params.departure_city)
        arr = quote(params.arrival_city)
        go_date = params.departure_date.strftime("%Y-%m-%d")

        url = (
            f"https://m.flight.qunar.com/ncs/page/flightlist"
            f"?depCity={dep}&arrCity={arr}&goDate={go_date}"
            f"&from=touch_index_search&child=0&baby=0&cabinType=0"
        )
        return url

    async def _maybe_refresh_and_retry(
        self, params: "SearchParams", page: "Page"
    ) -> "List[FlightPrice]":
        """交互式提示刷新 Cookie，然后重试搜索（仅限终端交互模式）。

        非终端环境（定时任务、管道输入等）时立即返回空列表。

        Args:
            params: 原始搜索参数。
            page: 当前 Playwright 页面（已打开，用于重试导航）。

        Returns:
            重试后的航班列表，用户拒绝或失败时返回空列表。
        """
        import sys as _sys

        if not _sys.stdin.isatty():
            return []

        print("\n[去哪儿] 未获取到航班数据，Cookie 可能已失效。")
        try:
            answer = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: input(
                    "[去哪儿] 是否立即打开浏览器重新登录并重试？[Y/n] "
                ),
            )
        except (EOFError, Exception):
            return []

        if answer.strip().lower() not in ("", "y", "yes"):
            print("[去哪儿] 已跳过。如需刷新 Cookie，可运行: python scripts/qunar_login.py")
            return []

        success = await QunarScraper.refresh_cookies_via_login(
            self.DEFAULT_COOKIES_FILE
        )
        if not success:
            return []

        # 重新加载并注入新 Cookie
        self.cookies = self.load_cookies_from_file(self.DEFAULT_COOKIES_FILE)
        if self.cookies and self._context:
            await self._context.add_cookies(self.cookies)
            logger.info(f"[刷新] 已注入 {len(self.cookies)} 条新 Cookie")

        # 用同一个 page 对象重新导航搜索页
        url = self._build_search_url(params)
        logger.info(f"[刷新] 重新导航至搜索页: {url}")
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=self.timeout)
            await asyncio.sleep(random.uniform(2, 4))
            await self._wait_for_results(page)
            flight_prices = await self._parse_flights(page, params)
            logger.info(f"[刷新] 重试后获得 {len(flight_prices)} 条航班")
            return flight_prices
        except Exception as e:
            logger.error(f"[刷新] 重试搜索失败: {e}")
            return []

    async def _search_via_mobile_api(
        self, params: SearchParams
    ) -> List[FlightPrice]:
        """通过移动端 touchInnerList API 抓取航班数据。

        使用 page.route() 拦截 touchInnerList 请求，在页面跳转登录页之前
        捕获 API 响应体，并解析为 FlightPrice 列表。

        仅在 Cookie 已注入（登录状态）时调用，否则 API 会返回空数据。

        Args:
            params: 搜索参数。

        Returns:
            解析得到的 FlightPrice 列表，失败时返回空列表。
        """
        page: Optional[Page] = None
        intercepted: List[Dict] = []

        try:
            page = await self._context.new_page()

            # ── 拦截 touchInnerList 响应 ───────────────────────────────────
            async def _intercept_touch(route, request):
                try:
                    resp = await route.fetch()
                    body = await resp.body()
                    import json as _j
                    logger.debug(
                        f"[移动端] 原始响应体 len={len(body)} "
                        f"前200字节={body[:200]!r}"
                    )
                    data = _j.loads(body.decode("utf-8", errors="replace"))
                    inner_data = data.get("data")
                    inner_len = len(inner_data) if isinstance(inner_data, str) else (
                        "dict" if isinstance(inner_data, dict) else type(inner_data).__name__
                    )
                    intercepted.append({"url": request.url, "data": data})
                    logger.info(
                        f"[移动端] touchInnerList 响应已捕获 "
                        f"ret={data.get('ret')} code={data.get('code')} "
                        f"data字段长度/类型={inner_len}"
                    )
                    await route.fulfill(
                        status=resp.status,
                        headers=dict(resp.headers),
                        body=body,
                    )
                except Exception as e:
                    logger.warning(f"[移动端] 路由拦截异常: {e}")
                    await route.continue_()

            await page.route("**/touchInnerList**", _intercept_touch)
            # ──────────────────────────────────────────────────────────────

            mobile_url = self._build_mobile_search_url(params)
            logger.info(f"[移动端] 导航到: {mobile_url}")

            try:
                await page.goto(
                    mobile_url,
                    wait_until="domcontentloaded",
                    timeout=self.timeout,
                )
            except Exception as e:
                # 页面可能跳转到登录页导致 navigation 异常，属正常情况
                logger.debug(f"[移动端] goto 异常（可能是登录跳转）: {e}")

            # 等待有效响应（data 字段非空），最多 30 秒
            # Qunar 移动端是两阶段响应：首次 data="" 表示搜索中，后续 data="..." 才是数据
            def _has_valid_response() -> bool:
                for resp in intercepted:
                    raw = resp.get("data", {}).get("data", "")
                    if isinstance(raw, str) and raw.strip():
                        return True
                    if isinstance(raw, dict):
                        return True
                return False

            for _poll_i in range(60):  # 最多等 30 秒（每次 0.5s）
                if _has_valid_response():
                    logger.info(f"[移动端] 有效响应已就绪，轮询退出（第{_poll_i+1}次）")
                    break
                if _poll_i % 10 == 0:  # 每 5 秒打一条进度日志
                    logger.info(
                        f"[移动端] 等待有效响应… 已等待{_poll_i*0.5:.0f}s "
                        f"拦截数={len(intercepted)}"
                    )
                await asyncio.sleep(0.5)
            else:
                logger.warning("[移动端] 等待 30 秒后仍无有效响应，超时退出")

            if not intercepted:
                logger.warning("[移动端] 未捕获到 touchInnerList 响应")
                return []

            # 取第一条有效（非空 data）的响应
            target = None
            for resp in intercepted:
                raw = resp.get("data", {}).get("data", "")
                if isinstance(raw, str) and raw.strip():
                    target = resp["data"]
                    break
                if isinstance(raw, dict):
                    target = resp["data"]
                    break

            if target is None:
                logger.warning(
                    f"[移动端] 所有 {len(intercepted)} 条响应 data 字段均为空，"
                    "可能 Cookie 已失效或需要重新登录"
                )
                return []

            result = self._parse_touch_inner_list_response(target, params)
            logger.info(f"[移动端] 解析得到 {len(result)} 条航班")
            return result

        except Exception as e:
            logger.error(f"[移动端] 搜索异常: {e}")
            return []
        finally:
            if page:
                await page.close()

    def _parse_touch_inner_list_response(
        self, data: Dict, params: SearchParams
    ) -> List[FlightPrice]:
        """解析 touchInnerList API 响应，提取 FlightPrice 列表。

        Qunar 移动端 API 的真实结构（经实测确认）：

        顶层：
          ret: true / false
          code: 0 (成功)
          data: <string>  ← 注意是 JSON 字符串，需要二次 json.loads()

        内层 JSON（data 字段解析后）：
          flights: [
            {
              code: "EU6674",          ← 航班号
              minPrice: "500",         ← 最低价（字符串）
              binfo: {
                depTime: "12:55",      ← 起飞时刻
                arrTime: "18:00",      ← 到达时刻
                name: ["成都航EU6674", "空客320(中)"]  ← name[0] 含航空公司+航班号
              }
            },
            ...
          ]

        Args:
            data: touchInnerList API 返回的顶层 JSON dict。
            params: 原始搜索参数。

        Returns:
            解析得到的 FlightPrice 列表。
        """
        import re as _re
        import json as _json

        results: List[FlightPrice] = []

        if not isinstance(data, dict):
            return results

        # ret=false → 明确失败
        if data.get("ret") is False:
            logger.warning(
                f"[移动端] touchInnerList ret=false: {data.get('msg', '')}"
            )
            return results

        raw_inner = data.get("data")

        # ── data 字段是 JSON 字符串 → 二次解析 ────────────────────────────
        if isinstance(raw_inner, str):
            if not raw_inner.strip():
                logger.warning("[移动端] data 字段为空字符串（搜索仍在进行中）")
                return results
            try:
                inner = _json.loads(raw_inner)
            except _json.JSONDecodeError as e:
                # "Extra data" 表示字符串里有多余内容跟在有效 JSON 之后
                # （Qunar 有时会在 JSON 后面拼接额外数据），尝试用 raw_decode 截取前部分
                if "Extra data" in str(e):
                    try:
                        decoder = _json.JSONDecoder()
                        inner, end_idx = decoder.raw_decode(raw_inner.strip())
                        logger.debug(
                            "[移动端] 使用 raw_decode 解析 data 字段成功，type=%s end=%d",
                            type(inner).__name__, end_idx,
                        )
                    except _json.JSONDecodeError as e2:
                        logger.warning(
                            "[移动端] data 字段 JSON 解析失败: %s（前100字符: %r）",
                            e2, raw_inner[:100],
                        )
                        return results
                else:
                    logger.warning(
                        "[移动端] data 字段 JSON 解析失败: %s（前100字符: %r）",
                        e, raw_inner[:100],
                    )
                    return results
            # 二次解析结果必须是 dict；若是字符串则需要三次解析
            if isinstance(inner, str):
                try:
                    inner = _json.loads(inner)
                except _json.JSONDecodeError as e3:
                    logger.warning(
                        "[移动端] data 三次解析失败: %s（前100字符: %r）",
                        e3, inner[:100],
                    )
                    return results
            if not isinstance(inner, dict):
                logger.warning(
                    "[移动端] data 解析结果类型异常: %s（前100字符: %r）",
                    type(inner).__name__, str(inner)[:100],
                )
                return results
        elif isinstance(raw_inner, dict):
            inner = raw_inner
        else:
            logger.warning(
                f"[移动端] data 字段类型异常: {type(raw_inner).__name__}"
            )
            return results

        # ── 取航班列表 ─────────────────────────────────────────────────────
        flight_list = inner.get("flights") or []
        if not isinstance(flight_list, list) or not flight_list:
            logger.warning(
                f"[移动端] 航班列表为空，inner keys={list(inner.keys())[:10]}"
            )
            return results

        logger.info(f"[移动端] 找到 {len(flight_list)} 条原始记录")

        for record in flight_list:
            if not isinstance(record, dict):
                continue
            try:
                # ── 价格：minPrice 字段（字符串型） ───────────────────────
                price_raw = str(record.get("minPrice") or record.get("price") or "")
                price_str = "".join(c for c in price_raw if c.isdigit() or c == ".")
                if not price_str:
                    continue
                price = Decimal(price_str)

                # ── 航班号：code 字段 ──────────────────────────────────────
                flight_no = str(record.get("code") or "UNKNOWN").strip()

                # ── binfo 子对象：时刻 + 航空公司 ──────────────────────────
                binfo = record.get("binfo") or {}
                dep_time = str(binfo.get("depTime") or "00:00")[:5]
                arr_time = str(binfo.get("arrTime") or "00:00")[:5]

                # binfo.name[0] = "成都航EU6674"，去掉航班号部分得到航空公司名
                name_list = binfo.get("name") or []
                airline_raw = str(name_list[0]) if name_list else "未知航空"
                # 去除末尾的大写字母+数字组合（即航班号）
                airline = _re.sub(r"[A-Z0-9]{2}\d{3,4}$", "", airline_raw).strip()
                if not airline:
                    airline = airline_raw  # 无法解析时保留原始值

                flight_info = FlightInfo(
                    flight_no=flight_no,
                    airline=airline,
                    departure_city=params.departure_city,
                    arrival_city=params.arrival_city,
                    departure_time=dep_time,
                    arrival_time=arr_time,
                    departure_date=params.departure_date,
                    direction=FlightDirection.DEPARTURE,
                    departure_airport=record.get("depAirport") or record.get("depAirportName"),
                    arrival_airport=record.get("arrAirport") or record.get("arrAirportName"),
                )
                results.append(FlightPrice(
                    flight_info=flight_info,
                    price=price,
                    currency="CNY",
                    seat_class="经济舱",
                    available_seats=None,
                    scraped_at=datetime.now(timezone.utc),
                    source="qunar_mobile",
                ))
            except Exception as e:
                logger.debug(f"[移动端] 记录解析异常: {e}")
                continue

        return results

    async def _is_login_required(self, page: Page) -> bool:
        """Check if login is required.

        Qunar shows flight prices to non-logged-in users — the persistent
        header "登录" button does NOT block content.  We only treat login
        as required when a blocking condition is present:
        - The page was redirected to a login URL
        - A login QR-code popup is actively visible
        - The page already has flight data loaded (definitively not blocked)

        Args:
            page: Playwright page object.

        Returns:
            True only when a blocking login condition prevents scraping.
        """
        try:
            # PRIORITY 1: If flight data is already on the page, no login needed.
            # This short-circuits all other checks and avoids false positives.
            flight_els = await page.query_selector_all(".b-airfly")
            if flight_els:
                logger.debug(
                    f"Found {len(flight_els)} flight element(s) — login not required"
                )
                return False

            # PRIORITY 2: URL redirect to Qunar login page
            current_url = page.url
            if "user.qunar.com/passport/login" in current_url:
                logger.info(f"Detected Qunar login page: {current_url}")
                return True
            if "login" in current_url.lower() and "passport" in current_url.lower():
                logger.info(f"Detected login redirect in URL: {current_url}")
                return True

            # PRIORITY 3: Qunar QR-code login popup (.login_QR_imgs visible)
            login_qr_popup = await page.query_selector(".login_QR_imgs")
            if login_qr_popup and await login_qr_popup.is_visible():
                logger.info("Detected Qunar login popup (.login_QR_imgs)")
                return True

            # PRIORITY 4: Visible QR code image (qcode/show URL)
            qr_img = await page.query_selector("img[src*='qcode/show']")
            if qr_img and await qr_img.is_visible():
                logger.info("Detected visible QR code image")
                return True

            # PRIORITY 5: Login modal explicitly displayed
            for selector in [
                ".login-modal[style*='display: block']",
                ".login-modal[style*='display:block']",
                "[class*='login'][class*='popup'][style*='display']",
                "[class*='login'][class*='modal'][style*='display']",
            ]:
                try:
                    modal = await page.query_selector(selector)
                    if modal and await modal.is_visible():
                        logger.info(f"Detected login modal: {selector}")
                        return True
                except Exception:
                    continue

            logger.info("No blocking login indicator — proceeding with scrape")
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

        Strategy 1: wait for Qunar's domestic flight card element (.b-airfly).
        Strategy 2: JS scan covering both domestic and potential international
                    flight-list containers.
        Strategy 3: fixed wait as last resort — API response capture takes over.

        Args:
            page: Playwright page object.
        """
        # Strategy 1: wait for Qunar's domestic flight card element
        try:
            await page.wait_for_selector(".b-airfly", timeout=20000)
            logger.info("Flight list loaded (.b-airfly elements detected)")
            return
        except Exception as e:
            logger.warning(f".b-airfly wait timed out ({e}), trying JS scan")

        # Strategy 2: JS function — covers domestic, mobile variant, and
        # international page containers (broad net).
        try:
            await page.wait_for_function(
                """
                () => {
                    if (document.querySelector('.b-airfly, .m-airfly-lst')) return true;
                    // International / alternate flight-list containers
                    if (document.querySelector(
                        '.flight-list, .flight-item, .inter-flight-item, '
                        + '.b-flight-item, [class*="flightItem"], [class*="flight-item"]'
                    )) return true;
                    return false;
                }
                """,
                timeout=10000,
            )
            logger.info("Flight list container found (JS scan)")
            return
        except Exception as e:
            logger.warning(f"JS flight-list detection timed out ({e}), using fixed delay")

        # Strategy 3: last-resort fixed wait (API response capture will handle data)
        await asyncio.sleep(5)

    @staticmethod
    def _build_flight_info_from_trip(
        trip: Dict,
        direction: FlightDirection,
    ) -> Optional[FlightInfo]:
        """Parse a wwwsearch ``trips`` element into a :class:`FlightInfo`.

        中转航班将多段航班号以 ``/`` 连接；出发/到达机场取首段/末段。

        Args:
            trip: ``journey.trips[n]`` dict from the wwwsearch API.
            direction: DEPARTURE for outbound, RETURN for return leg.

        Returns:
            Populated FlightInfo, or None if required fields are missing.
        """
        segs: List[Dict] = trip.get("flightSegments") or []
        if not segs:
            return None

        first = segs[0]
        last = segs[-1]

        flight_no = "/".join(s.get("code", "") for s in segs if s.get("code"))
        airline = first.get("carrierShortName") or first.get("carrierCode", "")

        dep_date_str = first.get("depDate", "")
        try:
            dep_date = date.fromisoformat(dep_date_str)
        except ValueError:
            return None

        return FlightInfo(
            flight_no=flight_no,
            airline=airline,
            departure_city=first.get("depCityName", ""),
            arrival_city=last.get("arrCityName", ""),
            departure_date=dep_date,
            departure_time=first.get("depTime"),
            arrival_time=last.get("arrTime"),
            direction=direction,
            departure_airport=first.get("depAirportName"),
            arrival_airport=last.get("arrAirportName"),
            departure_airport_code=first.get("depAirportCode"),
            arrival_airport_code=last.get("arrAirportCode"),
        )

    def _parse_inter_roundtrip_result(
        self,
        result: Dict,
        scraped_at: datetime,
    ) -> List[FlightPrice]:
        """将 wwwsearch API 的 result 解析为含双程信息的 FlightPrice 列表。

        ``flightPrices`` 中每条记录已包含去程（trips[0]）和回程（trips[1]），
        以及往返总价（price.lowTotalPrice）。

        Args:
            result: wwwsearch 接口 ``response.result`` dict。
            scraped_at: 采集时间戳。

        Returns:
            往返组合 FlightPrice 列表，price 为含税往返总价。
        """
        fps: Dict = result.get("flightPrices") or {}
        if not isinstance(fps, dict):
            return []

        prices: List[FlightPrice] = []
        for key, entry in fps.items():
            if entry.get("hidden"):
                continue

            journey = entry.get("journey") or {}
            trips: List[Dict] = journey.get("trips") or []
            if len(trips) < 2:
                continue

            price_obj = entry.get("price") or {}
            total_price = price_obj.get("lowTotalPrice") or price_obj.get("avgPrice")
            if not total_price:
                continue

            outbound_info = self._build_flight_info_from_trip(
                trips[0], FlightDirection.DEPARTURE
            )
            return_info = self._build_flight_info_from_trip(
                trips[1], FlightDirection.RETURN
            )
            if outbound_info is None or return_info is None:
                continue

            seat_info = journey.get("seatInfo") or {}
            available: Optional[int] = seat_info.get("nums") or None

            prices.append(
                FlightPrice(
                    flight_info=outbound_info,
                    price=Decimal(str(total_price)),
                    currency=price_obj.get("currencyCode", "CNY"),
                    seat_class="经济舱",
                    available_seats=available,
                    scraped_at=scraped_at,
                    source="qunar",
                    return_flight_info=return_info,
                )
            )

        logger.info("[往返] 解析得到 %d 条往返组合价格（共 %d 条原始）", len(prices), len(fps))
        return prices

    async def _search_inter_roundtrip(self, params: SearchParams) -> List[FlightPrice]:
        """通过 interroundtrip_compare 页面采集国际往返航班价格。

        拦截 ``touch/api/inter/wwwsearch`` 接口响应，轮询直到 flightPrices
        非空，解析为含 return_flight_info 和往返总价的 FlightPrice 列表。

        Args:
            params: 搜索参数，必须包含 return_date。

        Returns:
            往返组合 FlightPrice 列表，失败时返回空列表。
        """
        import json as _j

        page: Optional[Page] = None
        best_result: Dict = {}
        scraped_at = datetime.now(timezone.utc)

        try:
            page = await self._context.new_page()
            url = self._build_search_url(params)
            logger.info("[往返] 导航到: %s", url)

            # ── 拦截 wwwsearch 接口，保存含数据的响应 ─────────────────────
            async def _intercept(route, request) -> None:
                try:
                    resp = await route.fetch()
                    body = await resp.body()
                    data = _j.loads(body.decode("utf-8", errors="replace"))
                    fps = (data.get("result") or {}).get("flightPrices") or {}
                    if fps:
                        best_result.update(data.get("result", {}))
                        logger.info("[往返] wwwsearch 返回 %d 条组合", len(fps))
                    await route.fulfill(
                        status=resp.status,
                        headers=dict(resp.headers),
                        body=body,
                    )
                except Exception as exc:
                    logger.warning("[往返] wwwsearch 拦截异常: %s", exc)
                    await route.continue_()

            await page.route("**/touch/api/inter/wwwsearch**", _intercept)
            # ──────────────────────────────────────────────────────────────

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=self.timeout)
            except Exception as exc:
                logger.debug("[往返] goto 异常（忽略）: %s", exc)

            # 轮询最多 30s 等待数据
            for _ in range(60):
                await asyncio.sleep(0.5)
                if best_result.get("flightPrices"):
                    break
            else:
                logger.warning("[往返] 30s 内未收到有效 flightPrices 数据")

        except Exception as exc:
            logger.error("[往返] 采集异常: %s", exc, exc_info=True)
        finally:
            if page:
                try:
                    await page.close()
                except Exception:
                    pass

        if not best_result:
            return []

        return self._parse_inter_roundtrip_result(best_result, scraped_at)

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
            # Get all flight elements using Qunar's actual CSS class
            flight_elements = await page.query_selector_all(".b-airfly")

            if not flight_elements:
                logger.warning("No flight elements found on page")
                # Save screenshot and HTML for debugging (best-effort, don't block)
                try:
                    await page.screenshot(path="qunar_debug_no_flights.png", timeout=5000)
                    html_content = await page.content()
                    with open("qunar_debug_no_flights.html", "w", encoding="utf-8") as f:
                        f.write(html_content)
                    logger.info(
                        "Debug files saved: qunar_debug_no_flights.png / qunar_debug_no_flights.html"
                    )
                except Exception as _e:
                    logger.debug("Debug screenshot/html skipped: %s", _e)
                return flight_prices

            for element in flight_elements:
                try:
                    # 每个 .b-airfly 元素代表一段航班（去程或单程），
                    # 统一按 DEPARTURE 方向解析。往返程页面的航班配对由
                    # PriceMonitorScheduler._combine_roundtrip_prices() 在存库前完成。
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
            # Save screenshot for debugging (best-effort)
            try:
                await page.screenshot(path="qunar_debug_parse_error.png", timeout=5000)
            except Exception:
                pass
            raise ParseError(f"Failed to parse flights: {e}")

        return flight_prices

    async def _parse_flight_element(
        self, element, params: SearchParams, direction: FlightDirection
    ) -> Optional[FlightPrice]:
        """Parse a single flight element.

        Uses selectors derived from Qunar's actual React-rendered DOM:
        - Container:      div.b-airfly
        - Airline:        img.air-logo[title]
        - Flight number:  div.num span.n  (first occurrence)
        - Depart time:    div.sep-lf h2
        - Arrive time:    div.sep-rt h2
        - Price:          span.fix_price[title]  (digits in DOM are CSS-obfuscated;
                          the title attribute always holds the real numeric price)

        Args:
            element: Playwright element handle for a div.b-airfly.
            params: Search parameters.
            direction: Flight direction (departure or return).

        Returns:
            Parsed FlightPrice or None if parsing fails.
        """
        import re as _re
        try:
            # ── Airline ──────────────────────────────────────────────────────
            # img.air-logo has a title attribute with the airline name
            airline_img = await element.query_selector("img.air-logo")
            airline = (
                await airline_img.get_attribute("title") if airline_img else None
            ) or "未知航空"

            # ── Flight number ────────────────────────────────────────────────
            # div.num span.n — first span is the flight number code
            flight_no_elem = await element.query_selector("div.num span.n")
            flight_no = (
                (await flight_no_elem.inner_text()).strip() if flight_no_elem else "UNKNOWN"
            )

            # ── Times ─────────────────────────────────────────────────────────
            # div.sep-lf h2 = departure time, div.sep-rt h2 = arrival time
            dep_time_elem = await element.query_selector("div.sep-lf h2")
            dep_time = (
                (await dep_time_elem.inner_text()).strip()[:5]
                if dep_time_elem else "00:00"
            )
            arr_time_elem = await element.query_selector("div.sep-rt h2")
            arr_time = (
                (await arr_time_elem.inner_text()).strip()[:5]
                if arr_time_elem else "00:00"
            )

            # ── Price ─────────────────────────────────────────────────────────
            # Qunar uses CSS-obfuscated digits inside span.fix_price;
            # the real price is always in the title attribute (e.g. title="499").
            price = Decimal("0")
            fp_elem = await element.query_selector("span.fix_price")
            if fp_elem:
                title_val = await fp_elem.get_attribute("title") or ""
                price_str = "".join(c for c in title_val if c.isdigit() or c == ".")
                if price_str:
                    price = Decimal(price_str)

            if price == Decimal("0"):
                # Fallback: aria-label on p.prc = "报价：499元。..."
                prc_elem = await element.query_selector("p.prc")
                if prc_elem:
                    aria = await prc_elem.get_attribute("aria-label") or ""
                    m = _re.search(r"(\d{3,5})", aria)
                    if m:
                        price = Decimal(m.group(1))

            # ── Seat class ───────────────────────────────────────────────────
            # Qunar doesn't expose cabin class in the list view; default to 经济舱
            seat_class = "经济舱"

            # ── Departure date ───────────────────────────────────────────────
            dep_date = (
                params.departure_date if direction == FlightDirection.DEPARTURE
                else params.return_date
            )

            # ── Airport info (best-effort DOM extraction) ─────────────────────
            dep_airport_el = await element.query_selector("div.sep-lf .airportname, .dep-airport")
            arr_airport_el = await element.query_selector("div.sep-rt .airportname, .arr-airport")
            dep_airport = (
                (await dep_airport_el.inner_text()).strip() if dep_airport_el else None
            )
            arr_airport = (
                (await arr_airport_el.inner_text()).strip() if arr_airport_el else None
            )

            flight_info = FlightInfo(
                flight_no=flight_no,
                airline=airline.strip(),
                departure_city=params.departure_city,
                arrival_city=params.arrival_city,
                departure_time=dep_time,
                arrival_time=arr_time,
                departure_date=dep_date,
                direction=direction,
                departure_airport=dep_airport,
                arrival_airport=arr_airport,
            )

            return FlightPrice(
                flight_info=flight_info,
                price=price,
                currency="CNY",
                seat_class=seat_class,
                available_seats=None,
                scraped_at=datetime.now(timezone.utc),
                source="qunar",
            )

        except Exception as e:
            logger.warning(f"Error parsing flight element: {e}")
            return None

    def _parse_api_responses(
        self, responses: List[Dict], params: SearchParams
    ) -> List[FlightPrice]:
        """Attempt to extract flight prices from captured API JSON responses.

        Qunar loads flight data via AJAX.  This method walks every captured
        response and looks for arrays that look like flight records (containing
        a price field and a flight-number field).  It is intentionally lenient
        so it can cope with Qunar changing their API structure over time.

        The captured response data is also saved to
        ``qunar_debug_api_responses.json`` by the caller when no flights are
        found, making it easy to inspect the real response schema and improve
        this method later.

        Args:
            responses: List of {"url": str, "data": any} dicts captured from
                       network responses during page load.
            params: Original search parameters (used to fill in missing fields).

        Returns:
            List of FlightPrice objects extracted from the API data.
        """
        import re as _re

        results: List[FlightPrice] = []

        # Regex patterns for heuristic field detection
        _price_keys = _re.compile(r"price|Price|fare|Fare|amount|Amount", _re.I)
        _flightno_keys = _re.compile(r"flightNo|flight_no|flightNum|flightNumber|fn", _re.I)
        _airline_keys = _re.compile(r"airline|carrier|Airline|Carrier", _re.I)
        _time_keys = _re.compile(r"dep.*time|depart.*time|takeoff|startTime", _re.I)
        _arr_keys = _re.compile(r"arr.*time|arrive.*time|landTime|endTime", _re.I)

        def _find_flight_arrays(obj, depth: int = 0) -> List[List]:
            """Recursively find list-of-dicts that look like flight records."""
            if depth > 6:
                return []
            found = []
            if isinstance(obj, list) and len(obj) > 0 and isinstance(obj[0], dict):
                keys = set(obj[0].keys())
                # Heuristic: a flight record should have at least a price field
                if any(_price_keys.search(k) for k in keys):
                    found.append(obj)
            elif isinstance(obj, dict):
                for v in obj.values():
                    found.extend(_find_flight_arrays(v, depth + 1))
            elif isinstance(obj, list):
                for item in obj:
                    found.extend(_find_flight_arrays(item, depth + 1))
            return found

        def _get(record: dict, pattern) -> Optional[str]:
            for k, v in record.items():
                if pattern.search(k) and v is not None:
                    return str(v)
            return None

        for resp in responses:
            data = resp.get("data")
            url = resp.get("url", "")

            # ── Qunar wbdflightlist: known response schema ─────────────────────
            # data.data.flights is the canonical list; field names are known
            # from Qunar's mobile API.
            if "wbdflightlist" in url and isinstance(data, dict):
                inner = data.get("data") or {}
                qunar_flights = inner.get("flights", []) if isinstance(inner, dict) else []
                if qunar_flights:
                    logger.info(
                        f"API parser (wbdflightlist): {len(qunar_flights)} flight record(s)"
                    )
                    for record in qunar_flights:
                        if not isinstance(record, dict):
                            continue
                        try:
                            # Price — try all common field names
                            price_raw = (
                                record.get("price")
                                or record.get("lowestPrice")
                                or record.get("cheapestPrice")
                                or record.get("minPrice")
                                or record.get("floorPrice")
                                or ""
                            )
                            if not price_raw:
                                continue
                            price_str = "".join(
                                c for c in str(price_raw) if c.isdigit() or c == "."
                            )
                            if not price_str:
                                continue
                            price = Decimal(price_str)

                            # Flight number
                            flight_no = str(
                                record.get("flightNo")
                                or record.get("flight_no")
                                or record.get("fn")
                                or record.get("flightNumber")
                                or "UNKNOWN"
                            ).strip()

                            # Airline name
                            airline = str(
                                record.get("airlineName")
                                or record.get("airline_name")
                                or record.get("airline")
                                or record.get("carrier")
                                or "未知航空"
                            ).strip()

                            # Departure / arrival times
                            dep_time = str(
                                record.get("takeoffTime")
                                or record.get("departureTime")
                                or record.get("startTime")
                                or record.get("depTime")
                                or "00:00"
                            )[:5]
                            arr_time = str(
                                record.get("landTime")
                                or record.get("arrivalTime")
                                or record.get("endTime")
                                or record.get("arrTime")
                                or "00:00"
                            )[:5]

                            flight_info = FlightInfo(
                                flight_no=flight_no,
                                airline=airline,
                                departure_city=params.departure_city,
                                arrival_city=params.arrival_city,
                                departure_time=dep_time,
                                arrival_time=arr_time,
                                departure_date=params.departure_date,
                                direction=FlightDirection.DEPARTURE,
                            )
                            results.append(FlightPrice(
                                flight_info=flight_info,
                                price=price,
                                currency="CNY",
                                seat_class="经济舱",
                                available_seats=None,
                                scraped_at=datetime.now(timezone.utc),
                                source="qunar_api",
                            ))
                        except Exception as e:
                            logger.debug(f"wbdflightlist record parse error: {e}")
                            continue
                # Skip the generic heuristic for this URL (already handled above)
                continue
            # ──────────────────────────────────────────────────────────────────

            flight_arrays = _find_flight_arrays(data)

            for flight_list in flight_arrays:
                logger.info(
                    f"API parser: found {len(flight_list)} candidate record(s) in {url}"
                )
                for record in flight_list:
                    try:
                        # Extract price
                        price_raw = _get(record, _price_keys)
                        if not price_raw:
                            continue
                        price_str = "".join(c for c in price_raw if c.isdigit() or c == ".")
                        if not price_str:
                            continue
                        price = Decimal(price_str)

                        # Extract other fields (best-effort)
                        flight_no = _get(record, _flightno_keys) or "UNKNOWN"
                        airline = _get(record, _airline_keys) or "未知航空"
                        dep_time = _get(record, _time_keys) or "00:00"
                        arr_time = _get(record, _arr_keys) or "00:00"

                        flight_info = FlightInfo(
                            flight_no=flight_no.strip(),
                            airline=airline.strip(),
                            departure_city=params.departure_city,
                            arrival_city=params.arrival_city,
                            departure_time=dep_time[:5],
                            arrival_time=arr_time[:5],
                            departure_date=params.departure_date,
                            direction=FlightDirection.DEPARTURE,
                        )
                        results.append(FlightPrice(
                            flight_info=flight_info,
                            price=price,
                            currency="CNY",
                            seat_class="经济舱",
                            available_seats=None,
                            scraped_at=datetime.now(timezone.utc),
                            source="qunar_api",
                        ))
                    except Exception as e:
                        logger.debug(f"API record parse error: {e}")
                        continue

        if results:
            logger.info(f"API parser extracted {len(results)} flight(s)")
        return results

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