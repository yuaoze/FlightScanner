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

import httpx

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
        max_results: int = 20,
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
            max_results: Target number of flight results to collect via
                scroll-loading.  Scrolling stops early when this count is
                reached or no new elements appear.
        """
        self.headless = headless
        self.timeout = timeout
        self.max_retries = max_retries
        self.max_results = max_results

        # Cookie 加载优先级：
        #   1. 显式传入的 cookies 列表
        #   2. cookies_file 指定的文件
        #   3. 工作目录下的 qunar_cookies.json（若存在）
        # _cookies_path / _cookies_mtime 记录文件来源，使 reload_cookies_if_changed
        # 能在文件被外部更新（如 UI 扫码刷新）后热重载，无需重启服务。
        self._cookies_path: Optional[str] = None
        self._cookies_mtime: Optional[float] = None
        if cookies:
            self.cookies = cookies
        else:
            path = cookies_file or self.DEFAULT_COOKIES_FILE
            self._cookies_path = path
            self.cookies = self.load_cookies_from_file(path)
            self._cookies_mtime = self._get_cookies_mtime()
            if self.cookies:
                logger.info(f"从 {path} 加载了 {len(self.cookies)} 条 Cookie")

        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None

    def _get_cookies_mtime(self) -> Optional[float]:
        """Return mtime of the cookie file, or None if path unset / file missing."""
        if not self._cookies_path:
            return None
        try:
            import os as _os
            return _os.path.getmtime(self._cookies_path)
        except OSError:
            return None

    async def reload_cookies_if_changed(self) -> bool:
        """检查 cookie 文件 mtime，发现变化时重读并关闭旧 context。

        被两处调用：
        - search_flights() 开头：周期性自检，覆盖外部修改文件的所有路径
        - /api/cookies/{platform}/upload 成功后：UI 扫码刷新立即生效

        Returns:
            True = 重新加载了 cookie；False = 没变化或没 path 来源。
        """
        if not self._cookies_path:
            return False
        current_mtime = self._get_cookies_mtime()
        if current_mtime is None:
            return False
        if self._cookies_mtime is not None and current_mtime <= self._cookies_mtime:
            return False

        new_cookies = self.load_cookies_from_file(self._cookies_path)
        self.cookies = new_cookies
        self._cookies_mtime = current_mtime
        logger.info(
            "[去哪儿] 检测到 cookie 文件已更新，已重新加载 %d 条；下次采集将使用新 cookie",
            len(new_cookies),
        )

        # 关闭当前 context — 下次 _ensure_browser 会重建并注入新 cookies
        if self._context is not None:
            try:
                await self._context.close()
            except Exception:
                logger.exception("[去哪儿] 关闭旧 browser context 失败（已忽略）")
            self._context = None
        return True

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
        # 文件 mtime 变了就在这里重新加载 cookie 并 close 旧 context；
        # _ensure_browser 接着会用新 cookie 重建。
        await self.reload_cookies_if_changed()
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
            results = await self._search_inter_roundtrip(params)
            if results:
                return results
            # interroundtrip_compare.htm 未返回 flightPrices 数据，
            # 降级为分别搜索去程和回程单程，由 _combine_roundtrip_prices 配对。
            logger.info(
                "[往返] interroundtrip_compare 无数据，降级为分程搜索: %s→%s / %s→%s",
                params.departure_city, params.arrival_city,
                params.arrival_city, params.departure_city,
            )
            return await self._search_inter_roundtrip_fallback(params)

        # ── 国内往返程：分别搜去程和回程两条单程，方向打 RETURN 让上游配对 ──
        # 国内线没有 interroundtrip_compare 这种组合 API，单程搜索是唯一路径。
        # 不做这一步的话，上游 _combine_roundtrip_prices 会只拿到去程，回程 0 条
        # 导致配对失败。
        if params.return_date:
            logger.info(
                "[往返] 国内往返程: %s→%s, 去程 %s 回程 %s，走分程搜索",
                params.departure_city, params.arrival_city,
                params.departure_date, params.return_date,
            )
            return await self._search_inter_roundtrip_fallback(params)

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

            # ── 优先使用 wbdflightlist API 数据（完整 147 条）─────────────────
            # DOM 只渲染约 20 个虚拟列表节点，scroll 无法加载更多；
            # 而 wbdflightlist 一次性返回所有航班，直接解析效率最高。
            api_flights_via_wbd: List[FlightPrice] = []
            has_wbd = any("wbdflightlist" in (r.get("url") or "") for r in captured_api_responses)
            if has_wbd:
                api_flights_via_wbd = self._parse_api_responses(captured_api_responses, params)
                if api_flights_via_wbd:
                    api_flights_via_wbd.sort(key=lambda fp: fp.price)
                    logger.info(
                        "wbdflightlist API 解析得到 %d 条，保留最低 %d 条",
                        len(api_flights_via_wbd), self.max_results,
                    )
                    logger.info(f"Found {len(api_flights_via_wbd[:self.max_results])} flights")
                    return api_flights_via_wbd[: self.max_results]
            # ─────────────────────────────────────────────────────────────────

            # Parse flight data from DOM (fallback when API not available)
            flight_prices = await self._parse_flights(page, params)

            # If DOM parsing yielded nothing, try the captured API responses
            if not flight_prices and captured_api_responses:
                logger.info(
                    f"DOM parsing empty — trying {len(captured_api_responses)} "
                    "captured API response(s)"
                )
                flight_prices = self._parse_api_responses(captured_api_responses, params)

            # If still nothing, try direct HTTP API first (bypasses fingerprint detection)
            if not flight_prices:
                import json as _json
                debug_api_path = "qunar_debug_api_responses.json"
                with open(debug_api_path, "w", encoding="utf-8") as f:
                    _json.dump(
                        captured_api_responses, f,
                        ensure_ascii=False, indent=2, default=str,
                    )
                logger.warning(
                    f"桌面端未获取到航班. 调试文件: {debug_api_path} "
                    f"({len(captured_api_responses)} 条 API 响应已保存)"
                )

                if self.cookies:
                    # 优先：HTTP 直连 touchInnerList（绕过 Playwright 指纹检测）
                    logger.info("已检测到 Cookie，尝试 HTTP 直连 touchInnerList...")
                    flight_prices = await self._search_domestic_via_http(params)

                    # 降级：Playwright 移动端页面
                    if not flight_prices:
                        logger.info("HTTP 直连无结果，切换 Playwright 移动端页面...")
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
        """Get IATA city-level code for a Chinese city name.

        Args:
            city: Chinese city name.

        Returns:
            IATA code if found, otherwise the city name itself.
        """
        # 使用共享城市代码表（城市级代码，不绑定特定机场）
        return get_city_code(city) or city

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

    def _build_interroundtrip_compare_url(self, params: SearchParams) -> str:
        """构建 interroundtrip_compare.htm 国际往返比价页面 URL。

        该页面使用 fromCity/toCity/fromDate/toDate 参数格式，并调用与
        ``roundtrip_list_inter.htm`` 不同的后端 API，可覆盖东南亚等
        ``wwwsearch`` 接口不返回数据的航线。

        Args:
            params: 搜索参数，必须包含 ``return_date``。

        Returns:
            interroundtrip_compare.htm 完整 URL。
        """
        from_city_encoded = quote(params.departure_city)
        to_city_encoded = quote(params.arrival_city)
        from_code = self._get_airport_code(params.departure_city)
        to_code = self._get_airport_code(params.arrival_city)
        from_date = params.departure_date.strftime("%Y-%m-%d")
        to_date = params.return_date.strftime("%Y-%m-%d")  # type: ignore[union-attr]
        return (
            f"https://flight.qunar.com/site/interroundtrip_compare.htm?"
            f"fromCity={from_city_encoded}&"
            f"toCity={to_city_encoded}&"
            f"fromDate={from_date}&"
            f"toDate={to_date}&"
            f"fromCode={from_code}&"
            f"toCode={to_code}&"
            f"from=flight_dom_search&"
            f"lowestPrice=null&"
            f"isInter=true&"
            f"favoriteKey=&"
            f"showTotalPr=null&"
            f"adultNum=1&"
            f"childNum=0&"
            f"cabinClass="
        )

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

    async def _search_domestic_via_http(self, params: SearchParams) -> List[FlightPrice]:
        """通过 httpx 直接调用 touchInnerList API 获取国内航班数据。

        绕过 Playwright 页面渲染，避免浏览器指纹检测问题。
        需要有效的登录 Cookie。

        Args:
            params: 搜索参数。

        Returns:
            解析得到的 FlightPrice 列表，失败时返回空列表。
        """
        if not self.cookies:
            logger.warning("[HTTP直连] 无 Cookie，跳过 HTTP 直连尝试")
            return []

        dep_city = params.departure_city
        arr_city = params.arrival_city
        go_date = params.departure_date.strftime("%Y-%m-%d")

        # 构造 Cookie 字符串
        cookie_str = "; ".join(
            f"{c['name']}={c['value']}" for c in self.cookies
            if c.get("domain", "").endswith("qunar.com")
        )

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                "Version/16.0 Mobile/15E148 Safari/604.1"
            ),
            "Referer": (
                f"https://m.flight.qunar.com/ncs/page/flightlist"
                f"?depCity={quote(dep_city)}&arrCity={quote(arr_city)}"
                f"&goDate={go_date}"
            ),
            "Cookie": cookie_str,
            "Accept": "application/json, text/plain, */*",
        }

        # touchInnerList API URL（移动端去哪儿航班列表接口）
        api_url = (
            f"https://m.flight.qunar.com/ncs/page/touchInnerList"
            f"?depCity={quote(dep_city)}&arrCity={quote(arr_city)}"
            f"&goDate={go_date}&child=0&baby=0&cabinType=0"
            f"&from=touch_index_search&queryType=0"
        )

        logger.info("[HTTP直连] 调用 touchInnerList: %s → %s, %s", dep_city, arr_city, go_date)

        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                # 首次请求可能返回空 data（搜索中），需要轮询
                for attempt in range(6):
                    resp = await client.get(api_url, headers=headers)
                    if resp.status_code != 200:
                        logger.warning(
                            "[HTTP直连] HTTP %d, 响应: %s",
                            resp.status_code, resp.text[:200],
                        )
                        return []

                    data = resp.json()
                    # 检查是否是有效响应（data 字段非空）
                    inner = data.get("data", "")
                    if isinstance(inner, str) and inner.strip():
                        logger.info("[HTTP直连] 获得有效响应（第%d次请求）", attempt + 1)
                        return self._parse_touch_inner_list_response(data, params)
                    if isinstance(inner, dict):
                        logger.info("[HTTP直连] 获得有效响应 dict（第%d次请求）", attempt + 1)
                        return self._parse_touch_inner_list_response(data, params)

                    # data 为空字符串 = 搜索中，等待后重试
                    if data.get("ret") is False:
                        logger.warning("[HTTP直连] ret=false: %s", data.get("msg", ""))
                        return []

                    logger.debug("[HTTP直连] 搜索中（data为空），等待 3s 后重试...")
                    await asyncio.sleep(3)

                logger.warning("[HTTP直连] 轮询 6 次后仍无有效数据")
                return []

        except httpx.HTTPError as e:
            logger.warning("[HTTP直连] HTTP 请求异常: %s", e)
            return []
        except Exception as e:
            logger.error("[HTTP直连] 未知异常: %s", e)
            return []

    async def _maybe_refresh_and_retry(
        self, params: "SearchParams", page: "Page"
    ) -> "List[FlightPrice]":
        """Cookie 失效时记录警告，立即返回空列表（不弹出交互提示）。

        Args:
            params: 原始搜索参数（保留签名兼容性，当前实现不使用）。
            page: 当前 Playwright 页面（保留签名兼容性，当前实现不使用）。

        Returns:
            空列表。
        """
        logger.warning(
            "[去哪儿] 未获取到航班数据，Cookie 可能已失效。"
            "如需刷新 Cookie，可运行: python scripts/qunar_login.py"
        )
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
            _raw_body_counter = {"n": 0}

            async def _intercept_touch(route, request):
                try:
                    resp = await route.fetch()
                    body = await resp.body()
                    import json as _j
                    # 保存首次拦截的原始 body 到文件，便于离线分析结构
                    if _raw_body_counter["n"] == 0:
                        try:
                            with open("qunar_debug_mobile_raw.bin", "wb") as _bf:
                                _bf.write(body)
                            logger.info(
                                f"[移动端] 原始 body 已保存到 qunar_debug_mobile_raw.bin "
                                f"(len={len(body)}, 状态码={resp.status})"
                            )
                        except Exception as _e:
                            logger.debug(f"[移动端] 保存原始 body 失败: {_e}")
                        _raw_body_counter["n"] += 1
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

            # 等待有效航班数据响应（跳过状态码响应如 "18"），最多 30 秒
            import json as _poll_json

            def _try_parse_inner(raw: str):
                """Parse inner `data` string as JSON, tolerating extra trailing data."""
                try:
                    return _poll_json.loads(raw)
                except (_poll_json.JSONDecodeError, ValueError):
                    try:
                        dec = _poll_json.JSONDecoder()
                        parsed, _ = dec.raw_decode(raw.strip())
                        return parsed
                    except Exception:
                        return None

            # 航班列表的候选 key 名（国际线用 flights；国内线可能用其他名称如
            # innerFlights / list / results — 全部接受以提升兼容性）。
            _FLIGHT_LIST_KEYS = ("flights", "innerFlights", "list", "results", "data")

            def _extract_flight_list(parsed) -> Optional[list]:
                """Return a non-empty flight list from parsed inner JSON, or None."""
                if not isinstance(parsed, dict):
                    return None
                for k in _FLIGHT_LIST_KEYS:
                    v = parsed.get(k)
                    if isinstance(v, list) and v:
                        return v
                return None

            def _has_valid_response() -> bool:
                for resp in intercepted:
                    raw = resp.get("data", {}).get("data", "")
                    if not isinstance(raw, str) or len(raw) < 50:
                        continue
                    parsed = _try_parse_inner(raw)
                    if _extract_flight_list(parsed) is not None:
                        return True
                    # 放宽兜底：任何 ≥ 10 KB 的可解析 dict 都视为有效，
                    # 由下游 parser 处理具体结构差异（国内外线路可能不同）。
                    if isinstance(parsed, dict) and len(raw) >= 10_000:
                        return True
                return False

            for _poll_i in range(60):  # 最多等 30 秒（每次 0.5s）
                if _has_valid_response():
                    logger.info(f"[移动端] 有效响应已就绪，轮询退出（第{_poll_i+1}次）")
                    break
                # 若已有响应但 data 字段无法解析（很可能是国内线反爬混淆），
                # 再等下去也是徒劳——提前退出去走 DOM 路径。
                if _poll_i >= 6 and intercepted:
                    logger.info(
                        "[移动端] 已捕获响应但无法识别为 JSON（疑似反爬混淆），"
                        "提前切换 DOM 解析路径"
                    )
                    break
                if _poll_i % 10 == 0:  # 每 5 秒打一条进度日志
                    logger.info(
                        f"[移动端] 等待有效响应… 已等待{_poll_i*0.5:.0f}s "
                        f"拦截数={len(intercepted)}"
                    )
                await asyncio.sleep(0.5)
            else:
                logger.warning("[移动端] 等待 30 秒后仍无有效响应，切换 DOM 解析路径")

            # ── 国内线兜底：从浏览器渲染完成的 DOM 里抓数据 ─────────────────
            # 去哪儿国内线对 touchInnerList 的 data 字段做了反爬混淆（scrambled
            # JSON 片段 + 尾部 IIFE 由浏览器 eval 重组），Python 端无法直接解析。
            # 但 React 会在页面上渲染真实的航班列表 <li class="list-row item">，
            # 所以改从 DOM 取数。国际线能继续走 API 路径（data 字段本身就是有效
            # JSON），DOM 解析作为兜底，两路都试。
            dom_results = await self._parse_mobile_dom(page, params)
            if dom_results:
                logger.info(f"[移动端] DOM 解析成功 {len(dom_results)} 条航班")
                return dom_results
            else:
                logger.info("[移动端] DOM 未解析到航班，回退到 API 响应路径")

            if not intercepted:
                logger.warning("[移动端] 未捕获到 touchInnerList 响应")
                return []

            # ── 选择有效响应：跳过 status code / 空响应，取真正的航班 JSON ──
            # 首个 touchInnerList 调用可能返回 data: "18"（状态码），
            # 真正的航班数据在后续调用中（3MB JSON blob）。
            # 保存所有拦截响应以便排查（含首块原始内容 + 顶层 keys）。
            _mobile_debug_path = "qunar_debug_mobile_responses.json"
            try:
                _debug_entries = []
                for r in intercepted:
                    raw = r.get("data", {}).get("data", "")
                    raw_str = raw if isinstance(raw, str) else ""
                    _parsed_preview = _try_parse_inner(raw_str) if raw_str else None
                    _top_keys = (
                        list(_parsed_preview.keys())[:20]
                        if isinstance(_parsed_preview, dict) else None
                    )
                    _debug_entries.append({
                        "url": r["url"],
                        "ret": r.get("data", {}).get("ret"),
                        "code": r.get("data", {}).get("code"),
                        "data_len": len(raw_str),
                        "data_head": raw_str[:2000] if raw_str else "",
                        "data_tail": raw_str[-2000:] if raw_str else "",
                        "parsed_top_keys": _top_keys,
                    })
                with open(_mobile_debug_path, "w", encoding="utf-8") as _fd:
                    _poll_json.dump(_debug_entries, _fd, ensure_ascii=False, indent=2, default=str)
                # 同时把第一个响应的完整 data 字段写入独立文件，方便离线 grep
                if intercepted:
                    first_raw = intercepted[0].get("data", {}).get("data", "")
                    if isinstance(first_raw, str):
                        with open("qunar_debug_mobile_data_full.txt", "w", encoding="utf-8") as _ff:
                            _ff.write(first_raw)
                        logger.info(
                            f"[移动端] 完整 data 字段已写入 qunar_debug_mobile_data_full.txt "
                            f"(len={len(first_raw)})"
                        )
            except Exception as _dbg_e:
                logger.debug(f"[移动端] 写入调试文件失败: {_dbg_e}")

            # 优先选中含已知航班列表 key 的响应；否则退回"最大的可解析 dict"。
            target = None
            best_fallback = None
            best_fallback_len = 0
            for resp in intercepted:
                raw = resp.get("data", {}).get("data", "")
                if not isinstance(raw, str) or len(raw) < 50:
                    continue
                parsed = _try_parse_inner(raw)
                if parsed is None:
                    continue
                fl = _extract_flight_list(parsed)
                if fl is not None:
                    target = resp["data"]
                    logger.info(
                        "[移动端] 选中有效航班响应: data_len=%d, 列表=%d 条, keys=%s",
                        len(raw), len(fl),
                        list(parsed.keys())[:10] if isinstance(parsed, dict) else None,
                    )
                    break
                if isinstance(parsed, dict) and len(raw) > best_fallback_len:
                    best_fallback = resp["data"]
                    best_fallback_len = len(raw)

            if target is None and best_fallback is not None:
                target = best_fallback
                logger.warning(
                    "[移动端] 未找到已知航班列表 key，退回最大响应（len=%d）交由 parser 处理",
                    best_fallback_len,
                )

            if target is None:
                logger.warning(
                    f"[移动端] 所有 {len(intercepted)} 条响应均无有效航班数据，"
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

    async def _parse_mobile_dom(
        self, page: Page, params: SearchParams
    ) -> List[FlightPrice]:
        """从移动端页面渲染后的 DOM 抓取航班列表。

        去哪儿国内线对 touchInnerList API 的 data 字段做了反爬混淆（scrambled
        JSON 片段 + 尾部混淆 JS 重组），Python 端无法直接解析，但浏览器渲染完
        成后真实数据会落到 ``<ul class="list-content"><li class="list-row item">``
        元素里，直接从 DOM 提取即可。

        DOM 结构（2026-04 实测）::

            <li class="list-row item">
              <div class="list-info">
                <div class="airpot-info">
                  <div class="from-info">
                    <p class="from-time">07:55</p>
                    <p class="from-place">浦东T2</p>
                  </div>
                  <div class="time-info">
                    <p class="howlong"><span class="time">2时30分</span></p>
                    <p><span class="stop-city"></span></p>   <!-- 空=直飞 -->
                  </div>
                  <div class="to-info">
                    <span class="add-day"></span>            <!-- +1天标记 -->
                    <p class="to-time">10:25</p>
                    <p class="to-place">白云T1</p>
                  </div>
                </div>
                <div class="company-info">
                  <span class="company1">国航CA8327 空客321(中)</span>
                </div>
              </div>
              <div class="price1">
                <p class="price-info"><span class="price-icon"></span><span>420</span></p>
              </div>
            </li>

        Args:
            page:   Playwright 页面对象（已导航到移动端搜索页）。
            params: 搜索参数。

        Returns:
            解析得到的 FlightPrice 列表，DOM 未加载或无航班时返回空列表。
        """
        import re as _re

        results: List[FlightPrice] = []

        # 等待航班列表渲染完成（React 会在数据就绪后填充 li.list-row.item）
        try:
            await page.wait_for_selector(
                "ul.list-content li.list-row.item", timeout=20000
            )
        except Exception as e:
            logger.debug(f"[移动端 DOM] 等待航班 li 超时: {e}")
            return results

        # 额外给一点时间让 list 完全渲染（有时 React 会增量补齐）
        await asyncio.sleep(1.5)

        try:
            rows = await page.query_selector_all("ul.list-content li.list-row.item")
        except Exception as e:
            logger.warning(f"[移动端 DOM] 枚举航班行失败: {e}")
            return results

        logger.info(f"[移动端 DOM] 发现 {len(rows)} 个航班行，开始解析")

        for row in rows:
            try:
                # ── 起飞/到达时间 + 机场 ───────────────────────────────────
                dep_time_el = await row.query_selector(".from-info .from-time")
                arr_time_el = await row.query_selector(".to-info .to-time")
                dep_place_el = await row.query_selector(".from-info .from-place")
                arr_place_el = await row.query_selector(".to-info .to-place")

                dep_time = (await dep_time_el.inner_text()).strip() if dep_time_el else ""
                arr_time = (await arr_time_el.inner_text()).strip() if arr_time_el else ""
                dep_place = (await dep_place_el.inner_text()).strip() if dep_place_el else ""
                arr_place = (await arr_place_el.inner_text()).strip() if arr_place_el else ""

                if not dep_time or not arr_time:
                    continue

                # ── 中转城市（空=直飞） ───────────────────────────────────
                stop_el = await row.query_selector(".stop-city")
                stop_text = (await stop_el.inner_text()).strip() if stop_el else ""
                if stop_text:
                    # 只抓直飞，跳过中转（中转机票通常是两段组合，不适合追价）
                    continue

                # ── 航司 + 航班号 + 机型 ───────────────────────────────────
                company_el = await row.query_selector(".company-info .company1")
                if not company_el:
                    continue
                company_text = (await company_el.inner_text()).strip()
                # 典型格式: "国航CA8327 空客321(中)"；代号共享格式:
                # "东航MU5307\n南航CZ8519" — 取第一行（实际承运）
                company_first = company_text.split("\n")[0].strip()

                # 提取航班号（2字母+3~4数字）
                m = _re.search(r"([A-Z0-9]{2})(\d{3,4})", company_first)
                if not m:
                    continue
                flight_no = m.group(0)
                airline = company_first[: m.start()].strip() or "未知航空"

                # ── 价格：.price1 .price-info 里的最后一个 <span> ─────────
                price_spans = await row.query_selector_all(".price1 .price-info span")
                price: Optional[Decimal] = None
                for sp in price_spans:
                    txt = (await sp.inner_text()).strip()
                    if not txt:
                        continue
                    digits = "".join(c for c in txt if c.isdigit() or c == ".")
                    if digits:
                        try:
                            price = Decimal(digits)
                        except Exception:
                            continue
                if price is None or price <= 0:
                    continue

                # ── 跨日判断：.add-day 非空即 +N天 ────────────────────────
                add_day_el = await row.query_selector(".add-day")
                add_day_text = (await add_day_el.inner_text()).strip() if add_day_el else ""
                arrival_date = params.departure_date
                if add_day_text:
                    # 文本形如 "+1天"、"+2天"；也兜底处理未给数字的情况
                    dm = _re.search(r"\+(\d+)", add_day_text)
                    extra_days = int(dm.group(1)) if dm else 1
                    arrival_date = QunarScraper._compute_arrival_date(
                        params.departure_date, dep_time, arr_time
                    )
                    # 若基于时刻计算的结果跨日数小于 add-day 标注，强制用标注值
                    from datetime import timedelta as _td
                    expected = params.departure_date + _td(days=extra_days)
                    if arrival_date < expected:
                        arrival_date = expected
                else:
                    arrival_date = QunarScraper._compute_arrival_date(
                        params.departure_date, dep_time, arr_time
                    )

                flight_info = FlightInfo(
                    flight_no=flight_no,
                    airline=airline,
                    departure_city=params.departure_city,
                    arrival_city=params.arrival_city,
                    departure_time=dep_time[:5],
                    arrival_time=arr_time[:5],
                    departure_date=params.departure_date,
                    direction=FlightDirection.DEPARTURE,
                    departure_airport=dep_place or None,
                    arrival_airport=arr_place or None,
                    arrival_date=arrival_date,
                )
                results.append(FlightPrice(
                    flight_info=flight_info,
                    price=price,
                    currency="CNY",
                    seat_class="经济舱",
                    available_seats=None,
                    scraped_at=datetime.now(timezone.utc),
                    source="qunar",
                ))
            except Exception as e:
                logger.debug(f"[移动端 DOM] 单行解析异常: {e}")
                continue

        return results

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
        # 国际线使用 "flights"；国内线可能使用不同 key（innerFlights / list /
        # results）。按优先级遍历并选中首个非空列表。
        flight_list: List = []
        matched_key: Optional[str] = None
        for _fl_key in ("flights", "innerFlights", "list", "results"):
            v = inner.get(_fl_key)
            if isinstance(v, list) and v:
                flight_list = v
                matched_key = _fl_key
                break

        if not flight_list:
            # 再兜底：任何 value 是 list 且元素是 dict 的顶层字段，取最大的一个
            best_key, best_list = None, []
            for k, v in inner.items():
                if isinstance(v, list) and v and isinstance(v[0], dict) and len(v) > len(best_list):
                    best_key, best_list = k, v
            if best_list:
                flight_list = best_list
                matched_key = f"{best_key}(fallback)"

        if not flight_list:
            logger.warning(
                f"[移动端] 航班列表为空，inner keys={list(inner.keys())[:15]}"
            )
            return results

        logger.info(
            f"[移动端] 找到 {len(flight_list)} 条原始记录（key={matched_key}）"
        )

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
                    arrival_date=QunarScraper._compute_arrival_date(
                        params.departure_date, dep_time, arr_time
                    ),
                )
                results.append(FlightPrice(
                    flight_info=flight_info,
                    price=price,
                    currency="CNY",
                    seat_class="经济舱",
                    available_seats=None,
                    scraped_at=datetime.now(timezone.utc),
                    source="qunar",
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
    def _compute_arrival_date(
        dep_date: date,
        dep_time: Optional[str],
        arr_time: Optional[str],
        arr_date_str: Optional[str] = None,
    ) -> Optional[date]:
        """推算或解析到达日期。

        优先使用 API 提供的 arrDate 字符串；无 arrDate 时通过时间差估算：
        若到达时刻（HH:MM）早于起飞时刻（HH:MM），则到达日期 = 出发日期 + 1 天。

        Args:
            dep_date:    出发日期。
            dep_time:    起飞时刻 "HH:MM"，可为 None。
            arr_time:    到达时刻 "HH:MM"，可为 None。
            arr_date_str: API 提供的到达日期字符串 "YYYY-MM-DD"，可为 None。

        Returns:
            到达日期，无法判断时返回 None。
        """
        from datetime import timedelta

        # 优先：API 直接提供到达日期
        if arr_date_str:
            try:
                return date.fromisoformat(arr_date_str)
            except ValueError:
                pass

        # 降级：通过 HH:MM 字符串比较估算（只能判断是否跨日，+1 精度）
        if dep_time and arr_time and len(dep_time) >= 5 and len(arr_time) >= 5:
            if arr_time[:5] < dep_time[:5]:
                return dep_date + timedelta(days=1)

        return None

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

        dep_time = first.get("depTime")
        arr_time = last.get("arrTime")
        arr_date_str = last.get("arrDate")  # wwwsearch API 提供到达日期
        arr_date = QunarScraper._compute_arrival_date(dep_date, dep_time, arr_time, arr_date_str)

        return FlightInfo(
            flight_no=flight_no,
            airline=airline,
            departure_city=first.get("depCityName", ""),
            arrival_city=last.get("arrCityName", ""),
            departure_date=dep_date,
            departure_time=dep_time,
            arrival_time=arr_time,
            direction=direction,
            departure_airport=first.get("depAirportName"),
            arrival_airport=last.get("arrAirportName"),
            departure_airport_code=first.get("depAirportCode"),
            arrival_airport_code=last.get("arrAirportCode"),
            arrival_date=arr_date,
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

    async def _click_price_sort(self, page: Page) -> bool:
        """尝试点击价格升序（低价优先）排序按钮。

        按优先级依次尝试常见选择器，任一成功即返回 True；
        若所有选择器均未命中，记录警告并返回 False（调用方继续执行不受影响）。

        Args:
            page: 当前 Playwright Page 实例。

        Returns:
            True 表示点击成功，False 表示未找到排序按钮。
        """
        # 优先级从高到低：先精确文字匹配，再类名匹配
        selectors = [
            "text=价格升序",
            "text=低价优先",
            "text=价格从低到高",
            "[class*='sort']:has-text('价格')",
            ".sort-price",
            ".J-sort-price",
            "[data-sort='price']",
            "li:has-text('价格')",
        ]
        for sel in selectors:
            try:
                elem = page.locator(sel).first
                if await elem.is_visible(timeout=2000):
                    await elem.click()
                    logger.info("[往返] 已点击价格排序按钮（选择器: %s）", sel)
                    return True
            except Exception:
                continue
        logger.warning("[往返] 未找到价格排序按钮，将对采集结果自行排序")
        return False

    async def _search_inter_roundtrip(self, params: SearchParams) -> List[FlightPrice]:
        """通过 interroundtrip_compare.htm 页面采集国际往返航班价格。

        导航到 ``interroundtrip_compare.htm``（使用 fromCity/toCity/fromDate/toDate
        参数格式），广泛捕获所有 qunar.com JSON API 响应，解析为含
        ``return_flight_info`` 和往返总价的 ``FlightPrice`` 列表。

        与旧版不同，此实现不仅限于拦截 ``wwwsearch`` 接口——
        ``interroundtrip_compare.htm`` 可能调用不同的后端端点，广泛捕获可确保
        兼容东南亚等 ``wwwsearch`` 接口不返回数据的航线。

        Args:
            params: 搜索参数，必须包含 return_date。

        Returns:
            往返组合 FlightPrice 列表，失败时返回空列表。
        """
        import json as _j

        page: Optional[Page] = None
        # 收集所有来自 qunar.com 的 JSON 响应，用于尝试多种解析策略
        captured_responses: List[Dict] = []
        scraped_at = datetime.now(timezone.utc)

        try:
            page = await self._context.new_page()

            # ── Session 热身：国际往返 API 同样需要 session cookie ─────────
            logger.info("[往返] Session 热身...")
            try:
                await page.goto(
                    "https://www.qunar.com/",
                    wait_until="domcontentloaded",
                    timeout=self.timeout,
                )
                await asyncio.sleep(random.uniform(1, 3))
                logger.info("[往返] Session 热身完成")
            except Exception as _warm_exc:
                logger.warning("[往返] Session 热身失败（%s），继续执行", _warm_exc)
            # ──────────────────────────────────────────────────────────────

            url = self._build_interroundtrip_compare_url(params)
            logger.info("[往返] 导航到 interroundtrip_compare: %s", url)

            # ── 广泛捕获所有 qunar.com JSON 响应 ──────────────────────────
            # interroundtrip_compare.htm 可能调用与 roundtrip_list_inter.htm
            # 不同的后端 API，因此不限定特定端点，保存所有含 JSON 的响应。
            async def _on_response(response) -> None:
                try:
                    if "qunar.com" not in response.url:
                        return
                    if not response.ok:
                        return
                    ct = response.headers.get("content-type", "")
                    if "json" not in ct:
                        return
                    body = await response.body()
                    data = _j.loads(body.decode("utf-8", errors="replace"))
                    logger.debug("[往返] 捕获 API: %s", response.url)
                    captured_responses.append({"url": response.url, "data": data})
                except Exception as exc:
                    logger.debug("[往返] 响应捕获异常: %s", exc)

            page.on("response", _on_response)
            # ──────────────────────────────────────────────────────────────

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=self.timeout)
            except Exception as exc:
                logger.debug("[往返] goto 异常（忽略）: %s", exc)

            # ── 等待初始数据，然后点击价格升序排序 ───────────────────────────
            # 等待约 5s 让页面完成初始 API 请求，然后点击价格升序按钮；
            # 如按钮触发新 API 请求则再等 10s 捕获排序后的数据，
            # 如排序为纯客户端行为则等待无害，后续自行按价格排序。
            await asyncio.sleep(5)
            await self._click_price_sort(page)
            await asyncio.sleep(10)
            # ─────────────────────────────────────────────────────────────────

            # 若此时仍无数据，继续轮询最多 15s
            for _ in range(30):
                await asyncio.sleep(0.5)
                if any(
                    bool((r["data"].get("result") or {}).get("flightPrices"))
                    for r in captured_responses
                    if isinstance(r.get("data"), dict)
                ):
                    break
            else:
                logger.warning(
                    "[往返] 未收到有效 flightPrices 数据（共捕获 %d 条响应）",
                    len(captured_responses),
                )
                if captured_responses:
                    logger.info(
                        "[往返] 已捕获 API URLs: %s",
                        [r["url"] for r in captured_responses[:10]],
                    )

        except Exception as exc:
            logger.error("[往返] 采集异常: %s", exc, exc_info=True)
        finally:
            if page:
                try:
                    await page.close()
                except Exception:
                    pass

        # ── 解析：优先使用最后一条含 flightPrices 的响应（排序后的最新数据）──
        best_result: Optional[Dict] = None
        for resp in reversed(captured_responses):
            data = resp.get("data") or {}
            result = data.get("result") or {}
            if result.get("flightPrices"):
                logger.info("[往返] 找到 flightPrices 数据来源: %s", resp["url"])
                best_result = result
                break

        if not best_result:
            return []

        prices = self._parse_inter_roundtrip_result(best_result, scraped_at)
        # 按往返总价升序排列，确保低价组合排前（无论页面排序是否生效）
        prices.sort(key=lambda fp: fp.price)
        logger.info("[往返] 返回 %d 条往返组合（已按价格升序）", len(prices))
        return prices

    async def _search_inter_roundtrip_fallback(
        self, params: SearchParams
    ) -> List[FlightPrice]:
        """分程搜索：把往返拆成两次单程搜索，上游合并去回程配对。

        适用场景：
        - 国际往返：``interroundtrip_compare.htm`` 无数据时降级
        - 国内往返：没有专用组合接口，分程是唯一路径

        搜索分两次：
        - 去程：departure_city→arrival_city，departure_date，方向 DEPARTURE
        - 回程：arrival_city→departure_city，return_date，方向 RETURN

        返回的记录由上游 ``_combine_roundtrip_prices()`` 负责配对。

        Args:
            params: 搜索参数，必须包含 return_date。

        Returns:
            去程 + 回程单程 FlightPrice 列表（方向已正确标记）。
        """
        outbound_params = SearchParams(
            departure_city=params.departure_city,
            arrival_city=params.arrival_city,
            departure_date=params.departure_date,
            return_date=None,  # 单程
        )
        return_params = SearchParams(
            departure_city=params.arrival_city,   # 交换：回程出发 = 目的地
            arrival_city=params.departure_city,   # 交换：回程到达 = 出发地
            departure_date=params.return_date,    # 回程日期作为出发日
            return_date=None,
        )

        logger.info(
            "[分程往返] 搜去程 %s→%s %s",
            outbound_params.departure_city,
            outbound_params.arrival_city,
            outbound_params.departure_date,
        )
        outbound_prices = await self.search_flights(outbound_params)

        logger.info(
            "[分程往返] 搜回程 %s→%s %s",
            return_params.departure_city,
            return_params.arrival_city,
            return_params.departure_date,
        )
        return_prices = await self.search_flights(return_params)

        # 将回程记录的方向改为 RETURN，以便 _combine_roundtrip_prices 正确配对
        for fp in return_prices:
            fp.flight_info = FlightInfo(
                flight_no=fp.flight_info.flight_no,
                airline=fp.flight_info.airline,
                departure_city=fp.flight_info.departure_city,
                arrival_city=fp.flight_info.arrival_city,
                departure_time=fp.flight_info.departure_time,
                arrival_time=fp.flight_info.arrival_time,
                departure_date=fp.flight_info.departure_date,
                direction=FlightDirection.RETURN,
                departure_airport=fp.flight_info.departure_airport,
                arrival_airport=fp.flight_info.arrival_airport,
                departure_airport_code=fp.flight_info.departure_airport_code,
                arrival_airport_code=fp.flight_info.arrival_airport_code,
                arrival_date=fp.flight_info.arrival_date,
            )

        logger.info(
            "[分程往返] 去程 %d 条，回程 %d 条",
            len(outbound_prices), len(return_prices),
        )
        return outbound_prices + return_prices

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

            # ── 滚动加载：持续滚动直到采集到目标数量 ──────────────────────────
            target = self.max_results
            MAX_SCROLLS, NO_NEW_THRESHOLD = 8, 2
            no_new_count = 0
            for _ in range(MAX_SCROLLS):
                if len(flight_elements) >= target:
                    break
                prev = len(flight_elements)
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(random.uniform(1.0, 2.0))
                flight_elements = await page.query_selector_all(".b-airfly")
                if len(flight_elements) <= prev:
                    no_new_count += 1
                    if no_new_count >= NO_NEW_THRESHOLD:
                        break
                else:
                    no_new_count = 0
            flight_elements = flight_elements[:target]
            # ──────────────────────────────────────────────────────────────────

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
                arrival_date=self._compute_arrival_date(dep_date, dep_time, arr_time),
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
                            # 航班详情存在 binfo 子对象中：
                            # - 直飞：binfo (单个 dict)
                            # - 中转/连程：binfo1 + binfo2 (两段分开存储)
                            binfo: Dict = record.get("binfo") or {}
                            binfo1: Dict = record.get("binfo1") or {}
                            binfo2: Dict = record.get("binfo2") or {}
                            # 首段（出发信息）优先 binfo，其次 binfo1
                            seg1: Dict = binfo if binfo else binfo1
                            # 末段（到达信息）优先 binfo，其次 binfo2（连程末段）
                            seg_last: Dict = binfo if binfo else (binfo2 if binfo2 else binfo1)

                            # Price — minPrice 在顶层
                            price_raw = (
                                record.get("minPrice")
                                or record.get("price")
                                or record.get("lowestPrice")
                                or record.get("cheapestPrice")
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

                            # Flight number — seg1.airCode
                            flight_no = str(
                                seg1.get("airCode")
                                or record.get("flightNo")
                                or record.get("flight_no")
                                or record.get("fn")
                                or record.get("flightNumber")
                                or "UNKNOWN"
                            ).strip()

                            # Airline name — seg1.fullName
                            airline = str(
                                seg1.get("fullName")
                                or seg1.get("mainCarrierShortName")
                                or record.get("airlineName")
                                or record.get("airline_name")
                                or record.get("airline")
                                or record.get("carrier")
                                or "未知航空"
                            ).strip()

                            # Departure time — seg1.depTime
                            dep_time = str(
                                seg1.get("depTime")
                                or record.get("takeoffTime")
                                or record.get("departureTime")
                                or record.get("startTime")
                                or "00:00"
                            )[:5]

                            # Arrival time — seg_last.arrTime（连程取末段到达）
                            arr_time = str(
                                seg_last.get("arrTime")
                                or record.get("landTime")
                                or record.get("arrivalTime")
                                or record.get("endTime")
                                or "00:00"
                            )[:5]

                            # Arrival date (cross-day flights)
                            arr_date_str = (
                                seg_last.get("arrDate")
                                or seg_last.get("date")
                            )

                            flight_info = FlightInfo(
                                flight_no=flight_no,
                                airline=airline,
                                departure_city=params.departure_city,
                                arrival_city=params.arrival_city,
                                departure_time=dep_time,
                                arrival_time=arr_time,
                                departure_date=params.departure_date,
                                direction=FlightDirection.DEPARTURE,
                                arrival_date=self._compute_arrival_date(
                                    params.departure_date, dep_time, arr_time,
                                    arr_date_str=arr_date_str,
                                ),
                            )
                            results.append(FlightPrice(
                                flight_info=flight_info,
                                price=price,
                                currency="CNY",
                                seat_class="经济舱",
                                available_seats=None,
                                scraped_at=datetime.now(timezone.utc),
                                source="qunar",
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
                            arrival_date=self._compute_arrival_date(
                                params.departure_date, dep_time[:5], arr_time[:5]
                            ),
                        )
                        results.append(FlightPrice(
                            flight_info=flight_info,
                            price=price,
                            currency="CNY",
                            seat_class="经济舱",
                            available_seats=None,
                            scraped_at=datetime.now(timezone.utc),
                            source="qunar",
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