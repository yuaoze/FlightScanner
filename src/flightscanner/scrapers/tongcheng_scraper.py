"""同程旅行（ly.com）机票价格采集器。

同程的航班列表页会请求 ``/flights/api/getflightlist``。本实现优先解析该
结构化响应，接口未返回有效数据时再解析页面中的 ``.flight-item`` 卡片。

国内航线使用 ``/flights/itinerary``，港澳台及国际航线使用官方 ``/iflight``
搜索页和 ``/miflightapi/ts/list`` 响应。两个站点的数据结构与反爬策略完全不同，
不能把国际航线送进国内入口后把空结果当作“暂无航班”。

国际往返使用官方 RT 首阶段列表，再以该列表项请求 ``searchDetail``，从
``flights[0]`` / ``flights[1]`` 还原真实去回程组合；价格采用成人含税总价，
不会用两个独立单程最低价拼成一个实际上不可售的“往返价”。国内往返仍采用
两次官方单程搜索，由调度器在相同来源、币种和舱等内生成参考组合。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Iterable, Sequence
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from bs4 import BeautifulSoup, Tag
from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)
from playwright.async_api import (
    TimeoutError as PlaywrightTimeoutError,
)

from flightscanner.interfaces import (
    AntiCrawlerDetectedError,
    FlightDirection,
    FlightInfo,
    FlightPrice,
    FlightScraper,
    NetworkTimeoutError,
    ParseError,
    SearchParams,
)
from flightscanner.utils.city_codes import get_city_code, is_international_route

logger = logging.getLogger(__name__)


_FLIGHT_NO_RE = re.compile(r"(?<![A-Z0-9])([A-Z0-9]{2,3}\s*[-]?\s*\d{3,4})(?!\d)", re.I)
_TIME_RE = re.compile(r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?!\d)")
_PRICE_RE = re.compile(r"(?:¥|￥|CNY|RMB)?\s*([0-9]+(?:\.[0-9]{1,2})?)", re.I)


class TongchengScraper(FlightScraper):
    """同程旅行机票采集器（结构化 API 优先，DOM 降级）。"""

    DEFAULT_COOKIES_FILE = "tongcheng_cookies.json"
    SOURCE = "tongcheng"
    BASE_URL = "https://www.ly.com/flights/itinerary/oneway"
    API_PATH = "/flights/api/getflightlist"
    IFLIGHT_BASE_URL = "https://www.ly.com/iflight/book1.html"
    IFLIGHT_API_PATH = "/miflightapi/ts/list"
    _CHROME_VERSION = "127.0.6533.120"
    _CHROME_USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        f"Chrome/{_CHROME_VERSION} Safari/537.36"
    )

    # 同程页面目前对北京使用 PEK 作为城市入口，而项目通用映射为 BJS。
    # 保留一个平台级别别名，其他城市继续使用共享 IATA 映射。
    _CITY_CODE_OVERRIDES = {"北京": "PEK"}
    _IFLIGHT_CITY_NAME_OVERRIDES = {
        "香港": "中国香港",
        "澳门": "中国澳门",
        "台北": "中国台北",
    }

    _CABIN_CODE_MAP = {
        "F": "头等舱",
        "A": "头等舱",
        "P": "头等舱",
        "C": "商务舱",
        "D": "商务舱",
        "I": "商务舱",
        "J": "商务舱",
        "W": "超级经济舱",
        "S": "超级经济舱",
        "Y": "经济舱",
    }

    def __init__(
        self,
        headless: bool = True,
        timeout: int = 30000,
        max_retries: int = 3,
        max_results: int = 20,
        cookies: list[dict[str, Any]] | None = None,
        cookies_file: str | None = None,
    ) -> None:
        self.headless = headless
        self.timeout = timeout
        self.max_retries = max_retries
        self.max_results = max_results

        self._cookies_path: str | None = None
        self._cookies_mtime: float | None = None
        if cookies is not None:
            self.cookies = list(cookies)
        else:
            self._cookies_path = cookies_file or self.DEFAULT_COOKIES_FILE
            self.cookies = self.load_cookies_from_file(self._cookies_path)
            self._cookies_mtime = self._get_cookies_mtime()

        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None

    # ------------------------------------------------------------------
    # Cookie 与浏览器生命周期

    def _get_cookies_mtime(self) -> float | None:
        if not self._cookies_path:
            return None
        try:
            return Path(self._cookies_path).stat().st_mtime
        except OSError:
            return None

    @staticmethod
    def load_cookies_from_file(path: str) -> list[dict[str, Any]]:
        """读取 Cookie Editor JSON 或原始 ``name=value; ...`` 字符串。"""
        cookie_file = Path(path)
        if not cookie_file.exists():
            return []
        try:
            content = cookie_file.read_text(encoding="utf-8").strip()
        except OSError as exc:
            logger.warning("同程 Cookie 文件读取失败：%s", exc)
            return []
        if not content:
            return []

        if content.startswith("["):
            try:
                raw = json.loads(content)
            except (TypeError, ValueError) as exc:
                logger.warning("同程 Cookie JSON 解析失败：%s", exc)
                return []
            cookies: list[dict[str, Any]] = []
            if not isinstance(raw, list):
                return []
            for item in raw:
                if not isinstance(item, dict) or not item.get("name") or "value" not in item:
                    continue
                cookie: dict[str, Any] = {
                    "name": str(item["name"]),
                    "value": str(item["value"]),
                    "domain": item.get("domain") or ".ly.com",
                    "path": item.get("path") or "/",
                }
                for key in ("expires", "httpOnly", "secure", "sameSite"):
                    if key in item:
                        cookie[key] = item[key]
                cookies.append(cookie)
            return cookies

        if content.lower().startswith("cookie:"):
            content = content[7:].strip()
        pairs: list[tuple[str, str]] = []
        for part in content.split(";"):
            name, separator, value = part.strip().partition("=")
            if separator and name.strip():
                pairs.append((name.strip(), value.strip()))
        return [
            {"name": name, "value": value, "domain": domain, "path": "/"}
            for domain in (".ly.com", "www.ly.com")
            for name, value in pairs
        ]

    async def reload_cookies_if_changed(self) -> bool:
        """热重载 Cookie；文件被删除时也会清空旧登录态。"""
        if not self._cookies_path:
            return False
        current_mtime = self._get_cookies_mtime()
        if current_mtime == self._cookies_mtime:
            return False
        # 首次运行且文件一直不存在，不需要反复重建 context。
        if current_mtime is None and self._cookies_mtime is None:
            return False

        self.cookies = (
            self.load_cookies_from_file(self._cookies_path) if current_mtime is not None else []
        )
        self._cookies_mtime = current_mtime
        if self._context is not None:
            try:
                await self._context.close()
            except Exception:
                logger.exception("[同程] 关闭旧 browser context 失败（已忽略）")
            self._context = None
        logger.info("[同程] Cookie 已热重载，共 %d 条", len(self.cookies))
        return True

    async def clear_cookies(self) -> None:
        """供 Cookie 管理 API 立即清除运行中 context 的登录态。"""
        self.cookies = []
        self._cookies_mtime = self._get_cookies_mtime()
        if self._context is not None:
            try:
                await self._context.close()
            finally:
                self._context = None

    async def _create_context(self) -> BrowserContext:
        assert self._browser is not None
        context = await self._browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=self._CHROME_USER_AGENT,
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
        )
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', "
            "{get: () => undefined, configurable: true});"
        )
        if self.cookies:
            # Loader accepts Cookie Editor's superset; Playwright ignores no fields at
            # runtime, while its TypedDict is intentionally narrower than dict[str, Any].
            await context.add_cookies(self.cookies)  # type: ignore[arg-type]
        return context

    async def _configure_iflight_client_hints(self, page: Page) -> None:
        """让 UA 字符串与 Chromium Client Hints 保持一致。

        国际站会校验 ``sec-ch-ua``；仅设置 ``user_agent`` 时 Playwright 仍可能
        暴露 ``HeadlessChrome``，站方随后把合法搜索响应替换为空对象。这里通过
        Chromium 官方 CDP 接口设置同一版本的 UA metadata，不生成或伪造站方
        签名，页面仍自行完成全部请求与风控流程。
        """
        session = await self._context.new_cdp_session(page) if self._context else None
        if session is None:
            raise ParseError("同程国际机票浏览器 CDP session 初始化失败")
        await session.send(
            "Network.setUserAgentOverride",
            {
                "userAgent": self._CHROME_USER_AGENT,
                "acceptLanguage": "zh-CN,zh;q=0.9,en;q=0.8",
                "platform": "Win32",
                "userAgentMetadata": {
                    "brands": [
                        {"brand": "Not/A)Brand", "version": "8"},
                        {"brand": "Chromium", "version": "127"},
                        {"brand": "Google Chrome", "version": "127"},
                    ],
                    "fullVersionList": [
                        {"brand": "Not/A)Brand", "version": "8.0.0.0"},
                        {"brand": "Chromium", "version": self._CHROME_VERSION},
                        {"brand": "Google Chrome", "version": self._CHROME_VERSION},
                    ],
                    "fullVersion": self._CHROME_VERSION,
                    "platform": "Windows",
                    "platformVersion": "10.0.0",
                    "architecture": "x86",
                    "model": "",
                    "mobile": False,
                    "bitness": "64",
                    "wow64": False,
                },
            },
        )

    async def _ensure_browser(self) -> None:
        if self._browser is None:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=self.headless,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ],
            )
        if self._context is None:
            self._context = await self._create_context()

    async def close(self) -> None:
        """幂等释放浏览器资源。"""
        try:
            if self._context is not None:
                await self._context.close()
            if self._browser is not None:
                await self._browser.close()
            if self._playwright is not None:
                await self._playwright.stop()
        except Exception as exc:
            logger.warning("关闭同程浏览器资源失败：%s", exc)
        finally:
            self._context = None
            self._browser = None
            self._playwright = None

    # ------------------------------------------------------------------
    # 搜索编排

    @classmethod
    def _city_code(cls, city: str) -> str:
        code = cls._CITY_CODE_OVERRIDES.get(city) or get_city_code(city)
        if not code and re.fullmatch(r"[A-Za-z]{3}", city.strip()):
            code = city.strip().upper()
        if not code:
            raise ValueError(f"同程暂不支持未知城市：{city}")
        return code.upper()

    def _build_search_url(self, params: SearchParams) -> str:
        dep = self._city_code(params.departure_city)
        arr = self._city_code(params.arrival_city)
        query = urlencode({"date": params.departure_date.isoformat()})
        return f"{self.BASE_URL}/{dep}-{arr}?{query}"

    def _build_iflight_search_url(self, params: SearchParams) -> str:
        """构造同程官方国际机票单程搜索 URL。

        国际站的 ``para`` 是页面自身使用的七段契约。固定查询一个成人、经济舱，
        让列表的含税最低价与 ``FlightPrice.seat_class`` 保持同一口径；往返由调用方
        以两次单程搜索完成。
        """
        dep = self._city_code(params.departure_city)
        arr = self._city_code(params.arrival_city)
        departure_city = self._IFLIGHT_CITY_NAME_OVERRIDES.get(
            params.departure_city, params.departure_city
        )
        arrival_city = self._IFLIGHT_CITY_NAME_OVERRIDES.get(
            params.arrival_city, params.arrival_city
        )
        is_roundtrip = params.return_date is not None
        para = "*".join(
            (
                dep,
                arr,
                params.departure_date.isoformat(),
                params.return_date.isoformat() if params.return_date else "",
                "RT" if is_roundtrip else "OW",
                "1_0_0",
                "Y",
            )
        )
        query = urlencode(
            {
                "advanced": "false",
                "arriveAirportCode": "",
                "departAirportCode": "",
                "para": para,
                "departureCity": departure_city,
                "departureCityIsInter": str(
                    int(is_international_route(params.departure_city, params.departure_city))
                ),
                "arrivalCity": arrival_city,
                "arrivalCityIsInter": str(
                    int(is_international_route(params.arrival_city, params.arrival_city))
                ),
            }
        )
        return f"{self.IFLIGHT_BASE_URL}?{query}"

    @staticmethod
    def _validate_params(params: SearchParams) -> None:
        if params.return_date and params.return_date < params.departure_date:
            raise ValueError("回程日期不能早于去程日期")

    async def search_flights(self, params: SearchParams) -> list[FlightPrice]:
        """搜索同程价格；往返搜索返回两组带方向的单程价格。"""
        self._validate_params(params)
        # 在启动浏览器前验证城市，避免无效输入产生昂贵的浏览器请求。
        self._city_code(params.departure_city)
        self._city_code(params.arrival_city)
        await self.reload_cookies_if_changed()

        if params.return_date is None:
            return await self._search_with_retries(params, FlightDirection.DEPARTURE)

        if is_international_route(params.departure_city, params.arrival_city):
            # 国际 RT 的 tp 是整套含税价，必须用详情中的两组航段还原组合；
            # 不能拆成两个 OW 后相加。
            return await self._search_with_retries(params, FlightDirection.DEPARTURE)

        outbound = SearchParams(
            departure_city=params.departure_city,
            arrival_city=params.arrival_city,
            departure_date=params.departure_date,
        )
        inbound = SearchParams(
            departure_city=params.arrival_city,
            arrival_city=params.departure_city,
            departure_date=params.return_date,
        )
        outbound_prices = await self._search_with_retries(outbound, FlightDirection.DEPARTURE)
        inbound_prices = await self._search_with_retries(inbound, FlightDirection.RETURN)
        return outbound_prices + inbound_prices

    async def _search_with_retries(
        self,
        params: SearchParams,
        direction: FlightDirection,
    ) -> list[FlightPrice]:
        """对网络/解析瞬态错误做有上限的退避重试。"""
        retries = max(0, self.max_retries)
        for attempt in range(retries + 1):
            try:
                await self._ensure_browser()
                return await self._search_oneway(params, direction)
            except AntiCrawlerDetectedError:
                # 验证页面需要人工处理，立即重试只会放大风控。
                raise
            except (NetworkTimeoutError, ParseError):
                if attempt >= retries:
                    raise
                delay = min(5.0, float(2**attempt))
                logger.warning(
                    "同程搜索失败，将在 %.1f 秒后重试（%d/%d）",
                    delay,
                    attempt + 1,
                    retries,
                )
                await asyncio.sleep(delay)
            except Exception as exc:
                error = ParseError(f"同程浏览器启动失败：{exc}")
                if attempt >= retries:
                    raise error from exc
                delay = min(5.0, float(2**attempt))
                logger.warning(
                    "同程浏览器启动失败，将在 %.1f 秒后重试（%d/%d）：%s",
                    delay,
                    attempt + 1,
                    retries,
                    exc,
                )
                await asyncio.sleep(delay)
        return []

    async def _search_oneway(
        self,
        params: SearchParams,
        direction: FlightDirection,
    ) -> list[FlightPrice]:
        if is_international_route(params.departure_city, params.arrival_city):
            return await self._search_iflight_oneway(params, direction)
        return await self._search_domestic_oneway(params, direction)

    async def _search_domestic_oneway(
        self,
        params: SearchParams,
        direction: FlightDirection,
    ) -> list[FlightPrice]:
        if self._context is None:
            raise ParseError("同程浏览器 context 未初始化")

        page: Page | None = None
        captured: list[dict[str, Any]] = []
        response_received = asyncio.Event()

        async def capture_response(response: Any) -> None:
            if self.API_PATH not in response.url:
                return
            try:
                payload = await response.json()
            except Exception as exc:
                logger.debug("同程航班接口响应无法解析：%s", exc)
                response_received.set()
                return
            captured.append({"url": response.url, "data": payload})
            response_received.set()

        try:
            page = await self._context.new_page()
            page.on("response", capture_response)
            url = self._build_search_url(params)
            logger.info(
                "同程搜索：%s → %s，%s（%s）",
                params.departure_city,
                params.arrival_city,
                params.departure_date,
                direction.value,
            )
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=self.timeout)
            except (PlaywrightTimeoutError, asyncio.TimeoutError) as exc:
                raise NetworkTimeoutError(f"同程页面加载超时：{exc}") from exc

            # API 通常会在首屏几秒内返回。超时后仍继续 DOM 降级，不把“接口无数据”
            # 误报成网络超时。
            try:
                await asyncio.wait_for(
                    response_received.wait(),
                    timeout=max(1.0, min(8.0, self.timeout / 2000)),
                )
            except asyncio.TimeoutError:
                pass

            prices = self._parse_api_responses(captured, params, direction)
            if not prices:
                if await self._is_blocked(page):
                    raise AntiCrawlerDetectedError("同程反爬验证已触发")
                try:
                    await page.wait_for_selector(
                        ".flight-item, [class*='flight-item']",
                        timeout=max(1000, self.timeout // 3),
                    )
                except Exception:
                    pass
                prices = self._parse_dom_html(await page.content(), params, direction)
            return self._normalise_results(prices)
        except (NetworkTimeoutError, AntiCrawlerDetectedError):
            raise
        except Exception as exc:
            logger.error("同程搜索失败：%s", exc, exc_info=True)
            raise ParseError(f"同程数据解析失败：{exc}") from exc
        finally:
            if page is not None:
                await page.close()

    async def _search_iflight_oneway(
        self,
        params: SearchParams,
        direction: FlightDirection,
    ) -> list[FlightPrice]:
        """搜索港澳台/国际单程并区分无票、协议漂移与风控拦截。"""
        if self._context is None:
            raise ParseError("同程浏览器 context 未初始化")

        page: Page | None = None
        captured: list[dict[str, Any]] = []
        response_received = asyncio.Event()
        response_finished = asyncio.Event()
        anti_crawler_detected = asyncio.Event()

        def detect_antispider_request(request: Any) -> None:
            if "/antispider_v2/as/capshow" in request.url.lower():
                anti_crawler_detected.set()

        async def capture_response(response: Any) -> None:
            if "/miflightapi/ts/searchdetail" in response.url.lower():
                if response.status in {403, 406, 418, 429}:
                    anti_crawler_detected.set()
                    return
                try:
                    detail_text = (await response.text()).lower()
                    if "/antispider_v2/as/capshow" in detail_text:
                        anti_crawler_detected.set()
                except Exception:
                    pass
                return
            if self.IFLIGHT_API_PATH not in response.url:
                return
            entry: dict[str, Any] = {
                "url": response.url,
                "status": response.status,
                "data": None,
            }
            try:
                text = await response.text()
                lowered = text.strip().lower()
                if "/antispider_v2/as/capshow" in lowered:
                    entry["blocked"] = True
                    anti_crawler_detected.set()
                # 国际页会先发一个仅返回数字的防护探针；它不是航班响应。
                elif text.strip().startswith(("{", "[")):
                    entry["data"] = json.loads(text)
                elif re.fullmatch(r"\d{1,8}", text.strip()):
                    entry["probe"] = True
                else:
                    entry["malformed"] = True
            except Exception as exc:
                entry["parse_error"] = type(exc).__name__
            captured.append(entry)

            if not entry.get("probe"):
                response_received.set()
                payload = self._unwrap_iflight_payload(entry.get("data"))
                if (
                    response.status >= 400
                    or self._is_malformed_iflight_payload(payload)
                    or (isinstance(payload, dict) and bool(payload.get("done")))
                ):
                    response_finished.set()

        try:
            page = await self._context.new_page()
            await self._configure_iflight_client_hints(page)
            page.on("response", capture_response)
            page.on("request", detect_antispider_request)
            url = self._build_iflight_search_url(params)
            logger.info(
                "同程国际搜索：%s → %s，%s（%s）",
                params.departure_city,
                params.arrival_city,
                params.departure_date,
                direction.value,
            )
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=self.timeout)
            except (PlaywrightTimeoutError, asyncio.TimeoutError) as exc:
                raise NetworkTimeoutError(f"同程国际机票页面加载超时：{exc}") from exc

            wait_seconds = max(2.0, min(15.0, self.timeout / 1000))
            try:
                await asyncio.wait_for(response_finished.wait(), timeout=wait_seconds)
            except asyncio.TimeoutError:
                # 某些成功请求分批返回但没有及时标记 done；继续读取页面 store。
                pass

            store_rows = await self._read_iflight_store(page)
            if store_rows:
                prices = self._parse_iflight_rows(store_rows, params, direction)
            else:
                prices = self._parse_iflight_responses(captured, params, direction)
            if prices:
                if params.return_date is not None:
                    source_rows = store_rows or self._latest_iflight_rows(captured)
                    combined = await self._build_iflight_roundtrip_results(
                        page,
                        source_rows,
                        params,
                    )
                    if not combined:
                        if anti_crawler_detected.is_set():
                            raise AntiCrawlerDetectedError(
                                "同程国际往返详情接口触发风控，请稍后重试"
                            )
                        raise ParseError(
                            "同程国际往返列表已返回，但未能取得可审计的去回程详情"
                        )
                    return self._normalise_results(combined)
                return self._normalise_results(prices)

            valid_empty = self._has_valid_empty_iflight_result(captured)
            blocked = anti_crawler_detected.is_set() or any(
                bool(entry.get("blocked"))
                or bool(entry.get("malformed"))
                or
                int(entry.get("status") or 0) in {403, 406, 418, 429}
                or self._is_malformed_iflight_payload(
                    self._unwrap_iflight_payload(entry.get("data"))
                )
                for entry in captured
                if not entry.get("probe")
            )
            if blocked or await self._is_blocked(page):
                raise AntiCrawlerDetectedError(
                    "同程国际机票接口触发风控（Cookie 登录正常也可能发生）；"
                    "请稍后重试或刷新同程 Cookie"
                )
            if await self._is_iflight_login_required(page):
                raise ParseError("同程登录态已失效，请在设置页重新扫码刷新 Cookie")
            if valid_empty:
                return []
            if not response_received.is_set():
                raise ParseError("同程国际机票列表接口未触发，页面协议可能已变化")
            raise NetworkTimeoutError("同程国际机票列表未在限定时间内返回完整结果")
        except (NetworkTimeoutError, AntiCrawlerDetectedError, ParseError):
            raise
        except Exception as exc:
            logger.error("同程国际机票搜索失败：%s", exc, exc_info=True)
            raise ParseError(f"同程国际机票数据解析失败：{exc}") from exc
        finally:
            if page is not None:
                await page.close()

    @staticmethod
    async def _read_iflight_store(page: Page) -> list[dict[str, Any]]:
        """从官方 Vue store 读取已渲染列表，作为网络响应解析的安全降级。"""
        try:
            rows = await page.evaluate(
                """() => {
                    const copyList = (store) => {
                        const list = store && store.state && store.state.book1 &&
                            store.state.book1.queryList;
                        return Array.isArray(list) ? JSON.parse(JSON.stringify(list)) : null;
                    };
                    const directStore = window.__APP__ && window.__APP__.config &&
                        window.__APP__.config.globalProperties &&
                        window.__APP__.config.globalProperties.$store;
                    const direct = copyList(directStore);
                    if (direct) return direct;
                    let node = document.querySelector('.flight-list') ||
                        document.querySelector('#app');
                    let component = node && node.__vueParentComponent;
                    while (component) {
                        const found = copyList(component.proxy && component.proxy.$store);
                        if (found) return found;
                        component = component.parent;
                    }
                    const app = window.__APP__;
                    const provides = app && app._context && app._context.provides;
                    if (provides) {
                        for (const key of Reflect.ownKeys(provides)) {
                            const found = copyList(provides[key]);
                            if (found) return found;
                        }
                    }
                    return [];
                }"""
            )
        except Exception:
            return []
        return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []

    async def _is_blocked(self, page: Page) -> bool:
        try:
            title = (await page.title()).lower()
            if any(token in title for token in ("验证码", "安全验证", "访问受限", "captcha")):
                return True
            nodes = await page.query_selector_all(
                ".captcha, #captcha, [class*='captcha'], [class*='verify-code'], "
                "[class*='slider-verify']"
            )
            # 同程正常航班页会预置一批隐藏 captcha 组件；只有实际可见的验证
            # 控件才表示请求被拦截。
            for node in nodes:
                try:
                    if await node.is_visible():
                        return True
                except Exception:
                    continue
            return False
        except Exception:
            return False

    @staticmethod
    async def _is_iflight_login_required(page: Page) -> bool:
        try:
            if "passport.ly.com" in page.url.lower():
                return True
            nodes = await page.query_selector_all(".no-login-box")
            for node in nodes:
                if await node.is_visible():
                    return True
            body_text = await page.locator("body").inner_text()
            return "登录查询更低价格" in body_text
        except Exception:
            return False

    def _normalise_results(self, prices: Sequence[FlightPrice]) -> list[FlightPrice]:
        """同航班/方向/舱等只保留最低公开价格，然后按价格截断。"""
        best: dict[tuple[Any, ...], FlightPrice] = {}
        for item in prices:
            key = (
                item.flight_info.flight_no,
                item.flight_info.direction,
                item.flight_info.departure_date,
                item.flight_info.departure_time,
                item.seat_class,
                item.currency,
            )
            old = best.get(key)
            if old is None or item.price < old.price:
                best[key] = item
        return sorted(best.values(), key=lambda item: item.price)[: self.max_results]

    # ------------------------------------------------------------------
    # 国际机票 API 解析

    @classmethod
    def _unwrap_iflight_payload(cls, data: Any) -> dict[str, Any] | None:
        """兼容网络层 ``{code, data}`` 包装与页面已解包的 TS 响应。"""
        current = data
        for _ in range(3):
            if not isinstance(current, dict):
                return None
            if any(key in current for key in ("result", "done", "res", "tid")):
                return current
            nested = current.get("data")
            if not isinstance(nested, dict):
                return current
            current = nested
        return current if isinstance(current, dict) else None

    @staticmethod
    def _is_malformed_iflight_payload(payload: dict[str, Any] | None) -> bool:
        """识别站方防护层返回的空对象，而不误判合法的 ``res=[]``。"""
        if payload is None:
            return False
        if not payload:
            return True
        if "result" in payload and payload.get("result") in (False, 0, "0", None):
            return True
        return False

    @classmethod
    def _has_valid_empty_iflight_result(cls, responses: Iterable[dict[str, Any]]) -> bool:
        for entry in responses:
            if entry.get("probe") or int(entry.get("status") or 0) >= 400:
                continue
            payload = cls._unwrap_iflight_payload(entry.get("data"))
            if not isinstance(payload, dict):
                continue
            rows = payload.get("res")
            if (
                payload.get("result") not in (False, 0, "0", None)
                and bool(payload.get("done"))
                and isinstance(rows, list)
                and not rows
            ):
                return True
        return False

    def _parse_iflight_responses(
        self,
        responses: Iterable[dict[str, Any]],
        params: SearchParams,
        direction: FlightDirection = FlightDirection.DEPARTURE,
    ) -> list[FlightPrice]:
        latest_rows = self._latest_iflight_rows(responses)
        return self._parse_iflight_rows(latest_rows, params, direction)

    def _latest_iflight_rows(
        self,
        responses: Iterable[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """还原 list 轮询的最新完整快照，并解析 ``pc=1`` 占位行。"""
        latest_rows: list[dict[str, Any]] = []
        for entry in responses:
            if not isinstance(entry, dict) or entry.get("probe"):
                continue
            if int(entry.get("status") or 0) >= 400:
                continue
            payload = self._unwrap_iflight_payload(entry.get("data", entry))
            if not isinstance(payload, dict):
                continue
            if payload.get("result") in (False, 0, "0", None):
                continue
            rows = payload.get("res")
            if not isinstance(rows, list) or not rows:
                continue

            previous_by_key = {
                str(row.get("flightKey")): row
                for row in latest_rows
                if row.get("flightKey")
            }
            snapshot: list[dict[str, Any]] = []
            for raw in rows:
                if not isinstance(raw, dict):
                    continue
                row = raw
                if raw.get("pc") == 1 and raw.get("flightKey"):
                    key = str(raw["flightKey"])
                    row = previous_by_key.get(key, raw)
                    if row is raw:
                        row = next(
                            (
                                old
                                for old_key, old in previous_by_key.items()
                                if key in old_key or old_key in key
                            ),
                            raw,
                        )
                snapshot.append(row)
            # list 的 res 是当前快照而非增量；只保留最新一轮，避免早期瞬时
            # 低价与最终可售快照跨轮竞价。
            if snapshot:
                latest_rows = snapshot
        return latest_rows

    async def _build_iflight_roundtrip_results(
        self,
        page: Page,
        rows: Iterable[dict[str, Any]],
        params: SearchParams,
    ) -> list[FlightPrice]:
        """通过官方 ``searchDetail`` 将 RT 列表项还原为真实去回程组合。"""
        candidates = [
            row
            for row in rows
            if isinstance(row, dict)
            and row.get("og")
            and self._to_positive_decimal(row.get("tp")) is not None
        ]
        candidates.sort(key=lambda row: self._to_positive_decimal(row.get("tp")) or Decimal("Infinity"))
        # 每条详情都会触发一次官方接口；限制为最低五个候选，兼顾可比样本与站方负载。
        candidates = candidates[: min(max(1, self.max_results), 5)]

        combined: list[FlightPrice] = []
        for row in candidates:
            try:
                detail = await asyncio.wait_for(
                    page.evaluate(
                        """async ({og}) => {
                            const app = window.__APP__;
                            const store = (app && app.config &&
                                app.config.globalProperties &&
                                app.config.globalProperties.$store) ||
                                (app && app._instance && app._instance.proxy &&
                                app._instance.proxy.$store);
                            if (!store || !store.state || !store.state.book1) return null;
                            const item = (store.state.book1.queryList || [])
                                .find((candidate) => candidate && candidate.og === og);
                            if (!item) return null;
                            return await store.dispatch('book1/searchDetailData', item);
                        }""",
                        {"og": row["og"]},
                    ),
                    timeout=max(5.0, min(20.0, self.timeout / 1000)),
                )
            except (asyncio.TimeoutError, PlaywrightTimeoutError, Exception) as exc:
                logger.debug("同程国际往返详情获取失败：%s", type(exc).__name__)
                continue
            parsed = self._parse_iflight_roundtrip_detail(detail, row, params)
            if parsed is not None:
                combined.append(parsed)
        return combined

    def _parse_iflight_roundtrip_detail(
        self,
        data: Any,
        summary: dict[str, Any],
        params: SearchParams,
    ) -> FlightPrice | None:
        """解析 ``searchDetail`` 的双航段与成人最终含税总价。"""
        detail = data
        for _ in range(3):
            if not isinstance(detail, dict):
                return None
            if isinstance(detail.get("flights"), list):
                break
            nested = detail.get("data")
            if not isinstance(nested, dict):
                return None
            detail = nested

        flights = detail.get("flights")
        if not isinstance(flights, list) or len(flights) < 2:
            return None
        outbound_segments = flights[0] if isinstance(flights[0], list) else []
        return_segments = flights[1] if isinstance(flights[1], list) else []
        outbound = self._parse_iflight_detail_leg(
            outbound_segments,
            params.departure_city,
            params.arrival_city,
            params.departure_date,
            FlightDirection.DEPARTURE,
        )
        inbound = self._parse_iflight_detail_leg(
            return_segments,
            params.arrival_city,
            params.departure_city,
            params.return_date,
            FlightDirection.RETURN,
        )
        if outbound is None or inbound is None:
            return None

        product_prices: list[Decimal] = []
        products = detail.get("products")
        if isinstance(products, list):
            for product in products:
                if not isinstance(product, dict):
                    continue
                if product.get("memberOnly") or product.get("couponOnly"):
                    continue
                price_infos = product.get("priceInfos")
                adult = price_infos.get("ADT") if isinstance(price_infos, dict) else None
                if not isinstance(adult, dict):
                    continue
                if adult.get("memberOnly") or adult.get("couponOnly"):
                    continue
                price = self._to_positive_decimal(adult.get("saleTotalPrice"))
                if price is not None:
                    product_prices.append(price)
        price = min(product_prices) if product_prices else self._to_positive_decimal(summary.get("tp"))
        if price is None:
            return None

        return FlightPrice(
            flight_info=outbound,
            return_flight_info=inbound,
            price=price,
            currency=self._normalise_currency(
                summary.get("currency") or summary.get("currencyCode")
            ),
            seat_class=self._normalise_cabin(summary.get("cb") or "Y"),
            available_seats=self._extract_available_seats(summary),
            scraped_at=datetime.now(timezone.utc),
            source=self.SOURCE,
        )

    def _parse_iflight_detail_leg(
        self,
        segments: Any,
        departure_city: str,
        arrival_city: str,
        fallback_date: date | None,
        direction: FlightDirection,
    ) -> FlightInfo | None:
        rows = [row for row in segments if isinstance(row, dict)] if isinstance(
            segments, list
        ) else []
        if not rows or fallback_date is None:
            return None
        first, last = rows[0], rows[-1]
        flight_numbers = [
            flight_no
            for row in rows
            if (flight_no := self._extract_flight_no(
                row.get("flightNumber") or row.get("flightNo") or ""
            ))
        ]
        if not flight_numbers:
            return None
        airlines: list[str] = []
        for row in rows:
            airline = str(
                row.get("airCompanyName")
                or row.get("airlineName")
                or row.get("carrierName")
                or row.get("aircode")
                or ""
            ).strip()
            if airline and airline not in airlines:
                airlines.append(airline)

        departure_dt = self._parse_datetime(first.get("departureDate"))
        arrival_dt = self._parse_datetime(last.get("arrivalDate"))
        departure_time = self._extract_time(
            departure_dt or first.get("departureTime") or first.get("departureDate")
        )
        arrival_time = self._extract_time(
            arrival_dt or last.get("arrivalTime") or last.get("arrivalDate")
        )
        if departure_time is None or arrival_time is None:
            return None
        departure_date = departure_dt.date() if departure_dt else fallback_date
        arrival_date = self._arrival_date(
            departure_date,
            departure_time,
            arrival_time,
            arrival_dt.date() if arrival_dt else None,
        )

        dep_code = self._clean_iata(first.get("departureCode"))
        arr_code = self._clean_iata(last.get("arrivalCode"))
        return FlightInfo(
            flight_no="+".join(flight_numbers),
            airline="/".join(airlines) or "未知航空公司",
            departure_city=departure_city,
            arrival_city=arrival_city,
            departure_time=departure_time,
            arrival_time=arrival_time,
            departure_date=departure_date,
            direction=direction,
            departure_airport=self._airport_with_terminal(
                first.get("departureAirportName") or dep_code,
                first.get("departureTerminal"),
            ),
            arrival_airport=self._airport_with_terminal(
                last.get("arrivalAirportName") or arr_code,
                last.get("arrivalTerminal"),
            ),
            departure_airport_code=dep_code,
            arrival_airport_code=arr_code,
            arrival_date=arrival_date,
        )

    def _parse_iflight_rows(
        self,
        rows: Iterable[Any],
        params: SearchParams,
        direction: FlightDirection,
    ) -> list[FlightPrice]:
        prices: list[FlightPrice] = []
        for row in rows:
            try:
                parsed = self._parse_iflight_row(row, params, direction)
            except Exception as exc:
                logger.debug("同程国际单条航班解析失败：%s", exc)
                continue
            if parsed is not None:
                prices.append(parsed)
        return prices

    def _parse_iflight_row(
        self,
        row: Any,
        params: SearchParams,
        direction: FlightDirection,
    ) -> FlightPrice | None:
        """解析国际站列表摘要；只采用成人含税总价 ``tp``。"""
        if not isinstance(row, dict) or self._is_sold_out(row):
            return None

        segments = row.get("acs")
        segment_rows = [item for item in segments if isinstance(item, dict)] if isinstance(
            segments, list
        ) else []
        flight_numbers: list[str] = []
        airline_names: list[str] = []
        for segment in segment_rows:
            flight_no = self._extract_flight_no(
                segment.get("an")
                or segment.get("flightNumber")
                or segment.get("flightNo")
                or ""
            )
            if flight_no:
                flight_numbers.append(flight_no)
            airline = str(
                segment.get("acn")
                or segment.get("airCompanyName")
                or segment.get("airlineName")
                or ""
            ).strip()
            if airline and airline not in airline_names:
                airline_names.append(airline)

        if not flight_numbers:
            fallback_no = self._extract_flight_no(
                row.get("flightNo") or row.get("flightNumber") or row.get("fno") or ""
            )
            if fallback_no:
                flight_numbers.append(fallback_no)
        if not flight_numbers:
            return None

        # tp 是国际站页面默认展示和排序使用的成人含税价；sp 是未税票面价，
        # 不能与国内含税报价混用。仅在明确给出 totalPrice 时兼容新版字段。
        price = self._to_positive_decimal(
            row.get("tp") or row.get("totalPrice") or row.get("taxIncludedPrice")
        )
        if price is None:
            return None

        dep_dates = row.get("fdate")
        arr_dates = row.get("adate")
        dep_times = row.get("ftime")
        arr_times = row.get("atime")
        dep_time = self._extract_time(
            row.get("dt")
            or (dep_times[0] if isinstance(dep_times, list) and dep_times else None)
            or row.get("departureTime")
        )
        arr_time = self._extract_time(
            row.get("at")
            or (arr_times[-1] if isinstance(arr_times, list) and arr_times else None)
            or row.get("arrivalTime")
        )
        if dep_time is None or arr_time is None:
            return None

        departure_date = self._parse_date_value(
            dep_dates[0] if isinstance(dep_dates, list) and dep_dates else dep_dates
        ) or params.departure_date
        explicit_arrival_date = self._parse_date_value(
            arr_dates[-1] if isinstance(arr_dates, list) and arr_dates else arr_dates
        )

        dep_nodes = row.get("dants")
        arr_nodes = row.get("aants")
        dep_node = (
            dep_nodes[0]
            if isinstance(dep_nodes, list) and dep_nodes and isinstance(dep_nodes[0], dict)
            else {}
        )
        arr_node = (
            arr_nodes[-1]
            if isinstance(arr_nodes, list) and arr_nodes and isinstance(arr_nodes[-1], dict)
            else {}
        )
        dep_airport = self._airport_with_terminal(
            dep_node.get("an") or dep_node.get("airportName"),
            dep_node.get("at") or dep_node.get("dat") or dep_node.get("terminal"),
        )
        arr_airport = self._airport_with_terminal(
            arr_node.get("an") or arr_node.get("airportName"),
            arr_node.get("at") or arr_node.get("aat") or arr_node.get("terminal"),
        )

        info = FlightInfo(
            flight_no="+".join(flight_numbers),
            airline="/".join(airline_names) or "未知航空公司",
            departure_city=params.departure_city,
            arrival_city=params.arrival_city,
            departure_time=dep_time,
            arrival_time=arr_time,
            departure_date=departure_date,
            direction=direction,
            departure_airport=dep_airport,
            arrival_airport=arr_airport,
            departure_airport_code=self._clean_iata(
                dep_node.get("ac") or dep_node.get("airportCode")
            ),
            arrival_airport_code=self._clean_iata(
                arr_node.get("ac") or arr_node.get("airportCode")
            ),
            arrival_date=self._arrival_date(
                departure_date,
                dep_time,
                arr_time,
                explicit_arrival_date,
            ),
        )
        return FlightPrice(
            flight_info=info,
            price=price,
            currency=self._normalise_currency(
                row.get("currency") or row.get("currencyCode")
            ),
            # 国际搜索 URL 固定 cabin=Y，因此无字段时也可确定为经济舱。
            seat_class=self._normalise_cabin(row.get("cabin") or "Y"),
            available_seats=self._extract_available_seats(row),
            scraped_at=datetime.now(timezone.utc),
            source=self.SOURCE,
        )

    # ------------------------------------------------------------------
    # API 解析

    def _parse_api_responses(
        self,
        responses: Iterable[dict[str, Any]],
        params: SearchParams,
        direction: FlightDirection = FlightDirection.DEPARTURE,
    ) -> list[FlightPrice]:
        results: list[FlightPrice] = []
        for entry in responses:
            if not isinstance(entry, dict):
                continue
            url = str(entry.get("url", ""))
            if url and self.API_PATH not in url:
                continue
            payload = entry.get("data", entry)
            parsed = self._parse_api_response(payload, params, direction)
            if parsed:
                results.extend(parsed)
        return results

    def _parse_api_response(
        self,
        data: Any,
        params: SearchParams,
        direction: FlightDirection = FlightDirection.DEPARTURE,
    ) -> list[FlightPrice]:
        """解析同程 ``getflightlist`` 的真实响应结构。"""
        if not isinstance(data, dict):
            return []
        res_code = data.get("resCode")
        if res_code not in (None, 0, "0"):
            return []
        body = data.get("body", data.get("data", data))
        if not isinstance(body, dict):
            return []
        rows = (
            body.get("FlightInfoSimpleList")
            or body.get("flightInfoSimpleList")
            or body.get("flightList")
            or body.get("flights")
        )
        if not isinstance(rows, list):
            return []

        prices: list[FlightPrice] = []
        for row in rows:
            try:
                parsed = self._parse_api_row(row, params, direction)
            except Exception as exc:
                logger.debug("同程单条航班解析失败：%s", exc)
                continue
            if parsed is not None:
                prices.append(parsed)
        return prices

    def _parse_api_row(
        self,
        row: Any,
        params: SearchParams,
        direction: FlightDirection,
    ) -> FlightPrice | None:
        if not isinstance(row, dict) or self._is_sold_out(row):
            return None

        flight_no = self._extract_flight_no(
            row.get("flightNo") or row.get("flightNO") or row.get("flightNumber") or ""
        )
        if not flight_no:
            return None

        candidate = self._select_public_price(row)
        if candidate is None:
            return None
        price, cabin_hint = candidate

        dep_dt = self._parse_datetime(
            row.get("flyOffTime") or row.get("departureDateTime") or row.get("departureTime")
        )
        arr_dt = self._parse_datetime(row.get("arrivalTime") or row.get("arrivalDateTime"))
        dep_time = self._extract_time(dep_dt or row.get("flyOffTime"))
        arr_time = self._extract_time(arr_dt or row.get("arrivalTime"))
        if dep_time is None or arr_time is None:
            return None

        arrival_date = self._arrival_date(
            params.departure_date, dep_time, arr_time, arr_dt.date() if arr_dt else None
        )
        dep_airport = self._airport_with_terminal(
            row.get("originAirportShortName") or row.get("originAirportName"),
            row.get("boardPoint") or row.get("departureTerminal"),
        )
        arr_airport = self._airport_with_terminal(
            row.get("arriveAirportShortName") or row.get("arrivalAirportName"),
            row.get("atsn") or row.get("arrivalTerminal"),
        )
        airline = str(
            row.get("airCompanyName")
            or row.get("airlineName")
            or row.get("carrierName")
            or "未知航空公司"
        ).strip()

        info = FlightInfo(
            flight_no=flight_no,
            airline=airline or "未知航空公司",
            departure_city=params.departure_city,
            arrival_city=params.arrival_city,
            departure_time=dep_time,
            arrival_time=arr_time,
            departure_date=params.departure_date,
            direction=direction,
            departure_airport=dep_airport,
            arrival_airport=arr_airport,
            departure_airport_code=self._clean_iata(row.get("originAirportCode")),
            arrival_airport_code=self._clean_iata(row.get("arriveAirportCode")),
            arrival_date=arrival_date,
        )
        return FlightPrice(
            flight_info=info,
            price=price,
            currency=self._normalise_currency(row.get("currency")),
            seat_class=self._normalise_cabin(cabin_hint),
            available_seats=self._extract_available_seats(row),
            scraped_at=datetime.now(timezone.utc),
            source=self.SOURCE,
        )

    @classmethod
    def _select_public_price(cls, row: dict[str, Any]) -> tuple[Decimal, str] | None:
        """只选择公开可买价格，不采用会员/领券/新人专享字段。"""
        candidates: list[tuple[Decimal, str]] = []
        field_groups = (
            ("lcp", "lcn", "lcd"),
            ("lecp", "lecn", "lecd"),
            ("flightPrice", "lcn", "lcd"),
            ("salePrice", "cabinCode", "cabinName"),
        )
        for price_field, code_field, desc_field in field_groups:
            price = cls._to_positive_decimal(row.get(price_field))
            if price is None:
                continue
            hint = str(row.get(desc_field) or row.get(code_field) or "Y")
            candidates.append((price, hint))

        # 某些响应仅给 productPrices；它作为无公开主价时的兼容降级，不与
        # lcp/lecp 同时竞价，防止误取活动产品价。
        if not candidates:
            products = row.get("productPrices")
            if isinstance(products, dict):
                for raw in products.values():
                    price = cls._to_positive_decimal(raw)
                    if price is not None:
                        candidates.append((price, str(row.get("lcd") or row.get("lcn") or "Y")))
            elif isinstance(products, list):
                for product in products:
                    if not isinstance(product, dict):
                        continue
                    if product.get("memberOnly") or product.get("couponOnly"):
                        continue
                    price = cls._to_positive_decimal(
                        product.get("price") or product.get("salePrice")
                    )
                    if price is not None:
                        candidates.append(
                            (price, str(product.get("cabinName") or product.get("cabin") or "Y"))
                        )

        return min(candidates, key=lambda value: value[0]) if candidates else None

    @staticmethod
    def _to_positive_decimal(value: Any) -> Decimal | None:
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, str):
            match = _PRICE_RE.search(value.replace(",", ""))
            if not match:
                return None
            value = match.group(1)
        try:
            result = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            return None
        return result if result.is_finite() and result > 0 else None

    @staticmethod
    def _is_sold_out(row: dict[str, Any]) -> bool:
        if row.get("isSoldOut") is True or row.get("soldOut") is True:
            return True
        status = " ".join(
            str(row.get(key, "")) for key in ("status", "saleStatus", "tips", "buttonText")
        )
        return any(token in status for token in ("售罄", "无票", "不可订", "已售完"))

    @staticmethod
    def _extract_available_seats(row: dict[str, Any]) -> int | None:
        # ``availableTickets`` 在同程真实响应中常为 0，但航班仍公开可售，不能
        # 把它当余票数。只消费语义明确的字段。
        for key in ("seatCount", "leftTickets", "remainSeats", "remainingSeats"):
            value = row.get(key)
            if isinstance(value, int) and value > 0:
                return value
            if isinstance(value, str):
                match = re.search(r"(?:仅剩|剩余)?\s*(\d+)\s*(?:张|席|个)?", value)
                if match and int(match.group(1)) > 0:
                    return int(match.group(1))
        # 国际站 rt<9 时页面明确展示“余 N 张”；9 表示模糊上限，不落成精确值。
        remaining = row.get("rt")
        if isinstance(remaining, int) and 0 < remaining < 9:
            return remaining
        return None

    # ------------------------------------------------------------------
    # DOM 降级解析

    def _parse_dom_html(
        self,
        html: str,
        params: SearchParams,
        direction: FlightDirection = FlightDirection.DEPARTURE,
    ) -> list[FlightPrice]:
        if not html:
            return []
        soup = BeautifulSoup(html, "html.parser")
        prices: list[FlightPrice] = []
        for card in soup.select(".flight-item, [class*='flight-item']"):
            if not isinstance(card, Tag) or self._dom_card_unavailable(card):
                continue
            try:
                parsed = self._parse_dom_card(card, params, direction)
            except Exception as exc:
                logger.debug("同程 DOM 航班卡片解析失败：%s", exc)
                continue
            if parsed is not None:
                prices.append(parsed)
        return prices

    @staticmethod
    def _dom_card_unavailable(card: Tag) -> bool:
        classes = " ".join(card.get("class", []))
        style = str(card.get("style", "")).replace(" ", "").lower()
        text = card.get_text(" ", strip=True)
        return (
            "skeleton" in classes.lower()
            or "template" in classes.lower()
            or "display:none" in style
            or "visibility:hidden" in style
            or any(token in text for token in ("售罄", "已售完", "暂无报价", "不可订"))
        )

    def _parse_dom_card(
        self,
        card: Tag,
        params: SearchParams,
        direction: FlightDirection,
    ) -> FlightPrice | None:
        name_node = card.select_one(".flight-item-name, [class*='flight-name'], [class*='airline']")
        name_text = (
            name_node.get_text(" ", strip=True) if name_node else card.get_text(" ", strip=True)
        )
        flight_no = self._extract_flight_no(name_text)
        if not flight_no:
            return None
        airline = re.sub(re.escape(flight_no), "", name_text, flags=re.I).strip(" ｜|-/")
        airline = airline or "未知航空公司"

        dep_node = card.select_one(".f-startTime, [class*='startTime'], [class*='depart-time']")
        arr_node = card.select_one(".f-endTime, [class*='endTime'], [class*='arrive-time']")
        dep_text = dep_node.get_text(" ", strip=True) if dep_node else ""
        arr_text = arr_node.get_text(" ", strip=True) if arr_node else ""
        dep_time = self._extract_time(dep_text)
        arr_time = self._extract_time(arr_text)
        if dep_time is None or arr_time is None:
            return None

        price_node = card.select_one(
            ".head-prices strong em, .head-prices strong, [class*='price'] strong, [class*='price']"
        )
        price = self._to_positive_decimal(
            price_node.get_text(" ", strip=True) if price_node else None
        )
        if price is None:
            return None
        cabin_node = card.select_one(".head-prices i, [class*='cabin']")
        cabin_hint = cabin_node.get_text(" ", strip=True) if cabin_node else "经济舱"

        dep_airport = self._dom_airport(dep_node)
        arr_airport = self._dom_airport(arr_node)
        info = FlightInfo(
            flight_no=flight_no,
            airline=airline,
            departure_city=params.departure_city,
            arrival_city=params.arrival_city,
            departure_time=dep_time,
            arrival_time=arr_time,
            departure_date=params.departure_date,
            direction=direction,
            departure_airport=dep_airport,
            arrival_airport=arr_airport,
            arrival_date=self._arrival_date(params.departure_date, dep_time, arr_time),
        )
        return FlightPrice(
            flight_info=info,
            price=price,
            currency="CNY",
            seat_class=self._normalise_cabin(cabin_hint),
            available_seats=self._seats_from_text(card.get_text(" ", strip=True)),
            scraped_at=datetime.now(timezone.utc),
            source=self.SOURCE,
        )

    @staticmethod
    def _dom_airport(node: Tag | None) -> str | None:
        if node is None:
            return None
        airport = node.select_one("em, [class*='airport']")
        text = airport.get_text(" ", strip=True) if airport else ""
        return text or None

    @staticmethod
    def _seats_from_text(text: str) -> int | None:
        match = re.search(r"仅剩\s*(\d+)\s*张", text)
        return int(match.group(1)) if match else None

    # ------------------------------------------------------------------
    # 小型标准化工具

    @staticmethod
    def _extract_flight_no(value: Any) -> str | None:
        match = _FLIGHT_NO_RE.search(str(value).upper())
        if not match:
            return None
        return re.sub(r"[\s-]", "", match.group(1)).upper()

    @staticmethod
    def _extract_time(value: Any) -> str | None:
        if isinstance(value, datetime):
            return value.strftime("%H:%M")
        match = _TIME_RE.search(str(value or ""))
        if not match:
            return None
        return f"{int(match.group(1)):02d}:{match.group(2)}"

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        if isinstance(value, datetime):
            return value
        if not value:
            return None
        text = str(value).strip().replace("T", " ")
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                return datetime.strptime(text[:19], fmt)
            except ValueError:
                continue
        return None

    @staticmethod
    def _parse_date_value(value: Any) -> date | None:
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        text = str(value or "").strip()
        if not text:
            return None
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None

    @staticmethod
    def _arrival_date(
        departure_date: date,
        departure_time: str,
        arrival_time: str,
        explicit_date: date | None = None,
    ) -> date:
        if explicit_date is not None and explicit_date >= departure_date:
            return explicit_date
        return departure_date + (
            timedelta(days=1) if arrival_time < departure_time else timedelta()
        )

    @classmethod
    def _normalise_cabin(cls, value: Any) -> str:
        text = str(value or "").strip()
        if "头等" in text:
            return "头等舱"
        if "公务" in text or "商务" in text:
            return "商务舱"
        if "超级经济" in text or "高端经济" in text:
            return "超级经济舱"
        if "经济" in text:
            return "经济舱"
        code_match = re.search(r"[A-Z]", text.upper())
        return cls._CABIN_CODE_MAP.get(code_match.group(0), "经济舱") if code_match else "经济舱"

    @staticmethod
    def _normalise_currency(value: Any) -> str:
        currency = str(value or "CNY").strip().upper()
        if currency in {"RMB", "¥", "￥"}:
            return "CNY"
        return currency if re.fullmatch(r"[A-Z]{3}", currency) else "CNY"

    @staticmethod
    def _clean_iata(value: Any) -> str | None:
        code = str(value or "").strip().upper()
        return code if re.fullmatch(r"[A-Z]{3}", code) else None

    @staticmethod
    def _airport_with_terminal(airport: Any, terminal: Any) -> str | None:
        airport_text = str(airport or "").strip()
        terminal_text = str(terminal or "").strip()
        if not airport_text:
            return terminal_text or None
        if terminal_text and terminal_text not in airport_text:
            return f"{airport_text}{terminal_text}"
        return airport_text
