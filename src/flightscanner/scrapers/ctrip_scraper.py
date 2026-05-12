"""携程（Ctrip）机票爬虫实现。

采用双策略架构：
1. API 拦截（主路径）：监听携程 XHR 响应，解析 JSON 格式的航班数据；
2. DOM 解析（备用路径）：当 API 未返回有效数据时，解析页面 DOM 元素。

反爬对抗措施：
- 注入 JS 隐藏 navigator.webdriver 等自动化特征；
- 设置真实 User-Agent 与中文地区信息；
- 随机延迟避免频率过高被封。
"""

import asyncio
import logging
import random
import re
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from playwright.async_api import async_playwright, Browser, BrowserContext, Page

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
from flightscanner.utils.city_codes import CITY_CODE_MAP, get_city_code
from flightscanner.utils.config import settings

logger = logging.getLogger(__name__)

# 舱位代码 → 中文描述
# batchSearch API 可能返回 "@Y-Y" 等组合代码（取第一段字母前缀匹配）
CABIN_TYPE_MAP: Dict[str, str] = {
    "Y": "经济舱",
    "S": "超级经济舱",
    "C": "商务舱",
    "F": "头等舱",
    "W": "超级经济舱",
    "@Y-Y": "经济舱",
    "@C-C": "商务舱",
}


class CtripScraper(FlightScraper):
    """携程机票爬虫（Playwright 浏览器自动化）。

    主策略：通过 Playwright response 监听拦截携程 XHR/Fetch 接口的 JSON 响应，
    提取结构化航班数据。当 API 未返回有效数据时，降级为 DOM 解析。

    注入已登录 Cookie 可显著提升 API 拦截成功率：携程在登录态下才会返回完整
    的结构化航班列表（`itinerary/api/products` 等接口），未登录态下这些接口
    通常返回空数据，只能回退到 DOM 解析。

    Attributes:
        headless: 是否以无头模式运行浏览器。
        timeout: 页面加载超时（毫秒）。
        max_retries: 最大重试次数。
        cookies: 已注入的 Playwright 格式 Cookie 列表。
    """

    #: 默认 Cookie 文件路径，放在项目根目录即可自动加载
    DEFAULT_COOKIES_FILE = "ctrip_cookies.json"

    def __init__(
        self,
        headless: bool = True,
        timeout: int = 30000,
        max_retries: int = 3,
        max_results: int = 20,
        cookies: Optional[List[Dict]] = None,
        cookies_file: Optional[str] = None,
    ):
        """初始化爬虫。

        Args:
            headless: 是否无头模式，默认 True。
            timeout: 页面超时毫秒数，默认 30000。
            max_retries: 最大重试次数，默认 3。
            max_results: 每次采集最多保留的航班条数（按价格升序截取），默认 20。
            cookies: 已解析的 Playwright 格式 Cookie 列表（优先级最高）。
            cookies_file: Cookie 文件路径；为 None 时自动尝试
                ``ctrip_cookies.json``。
        """
        self.headless = headless
        self.timeout = timeout
        self.max_retries = max_retries
        self.max_results = max_results

        # Cookie 加载优先级：
        #   1. 显式传入的 cookies 列表
        #   2. cookies_file 指定的文件
        #   3. 工作目录下的 ctrip_cookies.json（若存在）
        # _cookies_path / _cookies_mtime 用于 reload_cookies_if_changed 热重载。
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
                logger.info("从 %s 加载了 %d 条携程 Cookie", path, len(self.cookies))

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

        与 QunarScraper.reload_cookies_if_changed 同语义 —— 详见该处注释。
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
            "[携程] 检测到 cookie 文件已更新，已重新加载 %d 条；下次采集将使用新 cookie",
            len(new_cookies),
        )

        if self._context is not None:
            try:
                await self._context.close()
            except Exception:
                logger.exception("[携程] 关闭旧 browser context 失败（已忽略）")
            self._context = None
        return True

    @staticmethod
    def load_cookies_from_file(path: str) -> List[Dict]:
        """从文件加载携程 Cookie，支持两种格式。

        **格式一：JSON 数组（推荐，来自 Cookie Editor 等浏览器扩展导出）**

        .. code-block:: json

            [
              {"name": "GUID", "value": "xxx", "domain": ".ctrip.com", "path": "/"},
              {"name": "ibu_uid", "value": "yyy", "domain": ".ctrip.com", "path": "/"}
            ]

        **格式二：原始 Cookie 字符串（从 Chrome DevTools → Network → 请求头复制）**

        .. code-block:: text

            GUID=xxx; ibu_uid=yyy; _bfa=zzz

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
                        "domain": c.get("domain", ".ctrip.com"),
                        "path": c.get("path", "/"),
                    }
                    for opt in ("expires", "httpOnly", "secure", "sameSite"):
                        if opt in c:
                            cookie[opt] = c[opt]
                    result.append(cookie)
                return result
            except Exception as e:
                logger.warning("携程 Cookie JSON 解析失败: %s", e)
                return []

        # ── 格式二：原始 Cookie 字符串 ────────────────────────────────────
        if content.lower().startswith("cookie:"):
            content = content[7:].strip()

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
        # 注册到所有携程相关域名，确保 flights.ctrip.com 等子域均能收到 Cookie
        for domain in (".ctrip.com", "flights.ctrip.com", "www.ctrip.com", ".trip.com"):
            for name, value in pairs:
                cookies.append({
                    "name": name,
                    "value": value,
                    "domain": domain,
                    "path": "/",
                })
        return cookies

    # ── 浏览器生命周期 ───────────────────────────────────────────────────────

    async def _ensure_browser(self) -> None:
        """按需初始化 Playwright 浏览器（仅首次调用时执行）。"""
        if self._browser:
            return

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=self.headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-web-security",
            ],
        )
        self._context = await self._browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
        )
        # 注入反爬 JS：对每个新页面隐藏 webdriver 特征
        await self._context.add_init_script(
            """
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined,
                configurable: true,
            });
            // 清除 CDP 注入的 cdc_ 属性
            const _windowKeys = Object.keys(window);
            for (const key of _windowKeys) {
                if (key.startsWith('cdc_')) {
                    delete window[key];
                }
            }
            // 模拟真实 Chrome 对象
            window.chrome = {
                runtime: {},
                loadTimes: function() { return {}; },
                csi: function() { return {}; },
            };
            """
        )
        # 注入 Cookie（若已加载）— 必须在首次导航前注入才能生效
        if self.cookies:
            await self._context.add_cookies(self.cookies)
            logger.info("已注入 %d 条携程 Cookie", len(self.cookies))

        logger.debug("携程爬虫浏览器已初始化（headless=%s，Cookie=%d 条）",
                     self.headless, len(self.cookies))

    async def close(self) -> None:
        """释放浏览器资源。"""
        try:
            if self._context:
                await self._context.close()
            if self._browser:
                await self._browser.close()
            if self._playwright:
                await self._playwright.stop()
        except Exception as exc:
            logger.error("关闭浏览器时出错：%s", exc)
        finally:
            self._context = None
            self._browser = None
            self._playwright = None

    # ── 主入口 ───────────────────────────────────────────────────────────────

    async def search_flights(self, params: SearchParams) -> List[FlightPrice]:
        """在携程搜索指定路线的机票价格。

        Args:
            params: 搜索参数（城市、日期等）。

        Returns:
            航班价格列表；若无结果则返回空列表。

        Raises:
            NetworkTimeoutError: 页面加载超时。
            AntiCrawlerDetectedError: 检测到反爬机制（CAPTCHA 等）。
            ParseError: 数据解析失败。
        """
        # 文件 mtime 变了就重读 cookie 并关闭旧 context；下次 _ensure_browser 重建。
        await self.reload_cookies_if_changed()
        await self._ensure_browser()

        page: Optional[Page] = None
        # 捕获格式：{"url": ..., "data": ...}，方便调试时定位具体接口
        captured: List[Dict[str, Any]] = []

        try:
            page = await self._context.new_page()

            # 使用 page.route() 拦截 API 请求，避免 inspector cache 逐出大响应
            # （batchSearch 接口响应体约 11MB，page.on("response") 无法可靠读取）
            import json as _json_mod

            async def _api_route(route) -> None:
                """拦截携程 API 请求，读取并缓存响应体，再原样转发给浏览器。"""
                try:
                    response = await route.fetch()
                    body_bytes = await response.body()
                    body = body_bytes.decode("utf-8", errors="replace")
                    stripped = body.lstrip()
                    if (stripped.startswith("{") or stripped.startswith("[")) and len(body) > 200:
                        try:
                            data = _json_mod.loads(body)
                            if isinstance(data, dict):
                                url = route.request.url
                                captured.append({"url": url, "data": data})
                                logger.debug(
                                    "携程 API 已捕获：%s (%d bytes)",
                                    url.split("?")[0], len(body),
                                )
                        except Exception:
                            pass
                    await route.fulfill(response=response, body=body_bytes)
                except Exception:
                    # 若 fetch/fulfill 失败，让请求正常通过
                    try:
                        await route.continue_()
                    except Exception:
                        pass

            # 只拦截携程 JSON API 路径（不拦截 JS/CSS/图片，避免性能损失）
            for pattern in (
                "**/international/search/api/**",
                "**/restapi/soa2/**",
                "**/online/process**",
                "**/itinerary/api/**",
            ):
                await page.route(pattern, _api_route)

            url = self._build_search_url(params)
            logger.info("携程搜索：%s → %s，%s，URL: %s",
                        params.departure_city, params.arrival_city,
                        params.departure_date, url)

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=self.timeout)
            except asyncio.TimeoutError as exc:
                raise NetworkTimeoutError(f"携程页面加载超时：{exc}") from exc

            # Cookie 注入时携程需要更多时间执行 JS 并发起认证 API 请求
            wait_secs = random.uniform(7, 10) if self.cookies else random.uniform(5, 8)
            logger.debug("等待 %.1f 秒让页面 API 请求完成…", wait_secs)
            await asyncio.sleep(wait_secs)

            if await self._is_blocked(page):
                raise AntiCrawlerDetectedError("携程反爬机制已触发（CAPTCHA / 验证页面）")

            # ── 优先：解析捕获的 API 响应 ────────────────────────────────
            flight_prices = self._parse_api_responses(captured, params)
            if flight_prices:
                flight_prices.sort(key=lambda fp: fp.price)
                total_api = len(flight_prices)
                flight_prices = flight_prices[: self.max_results]
                logger.info(
                    "API 解析得到 %d 条，按价格升序保留最低 %d 条",
                    total_api, len(flight_prices),
                )

            # ── 备用：DOM 解析 ───────────────────────────────────────────
            if not flight_prices:
                logger.info(
                    "API 拦截无数据（共捕获 %d 条 JSON 响应），切换到 DOM 解析",
                    len(captured),
                )
                flight_prices = await self._parse_flights_from_dom(page, params)

            if not flight_prices:
                import json as _json
                debug_path = "ctrip_debug_api_responses.json"
                with open(debug_path, "w", encoding="utf-8") as f:
                    _json.dump(captured, f, ensure_ascii=False, indent=2, default=str)
                try:
                    await page.screenshot(path="ctrip_debug_no_flights.png", timeout=5000)
                except Exception:
                    pass
                logger.warning("携程未获取到航班（API + DOM 均无数据）")

            logger.info("携程共获取 %d 条航班价格", len(flight_prices))
            return flight_prices

        except (NetworkTimeoutError, AntiCrawlerDetectedError):
            raise
        except Exception as exc:
            logger.error("携程搜索出错：%s", exc, exc_info=True)
            raise ParseError(f"携程数据解析失败：{exc}") from exc
        finally:
            if page:
                await page.close()

    # ── URL 构建 ─────────────────────────────────────────────────────────────

    def _build_search_url(self, params: SearchParams) -> str:
        """构建携程单程搜索 URL。

        URL 格式：
            https://flights.ctrip.com/online/list/oneway-{dep}-{arr}
                ?depdate={date}&cabin=Y_S_C_F&adult=1&child=0&infant=0

        Args:
            params: 搜索参数。

        Returns:
            完整的携程搜索 URL。
        """
        dep_code = (get_city_code(params.departure_city) or params.departure_city[:3]).lower()
        arr_code = (get_city_code(params.arrival_city) or params.arrival_city[:3]).lower()
        dep_date = params.departure_date.strftime("%Y-%m-%d")

        if params.return_date:
            ret_date = params.return_date.strftime("%Y-%m-%d")
            return (
                f"https://flights.ctrip.com/online/list/round-{dep_code}-{arr_code}"
                f"?depdate={dep_date}_{ret_date}&cabin=y_s_c_f&adult=1&child=0&infant=0"
            )

        return (
            f"https://flights.ctrip.com/online/list/oneway-{dep_code}-{arr_code}"
            f"?depdate={dep_date}&cabin=y_s_c_f&adult=1&child=0&infant=0"
        )

    # ── 反爬检测 ─────────────────────────────────────────────────────────────

    async def _is_blocked(self, page: Page) -> bool:
        """判断页面是否被反爬机制拦截。

        Args:
            page: Playwright Page 对象。

        Returns:
            True 表示被拦截。
        """
        try:
            title = await page.title()
            if any(kw in title for kw in ("验证", "验证码", "blocked", "Blocked")):
                return True
            captcha = await page.query_selector(
                ".captcha, .verify-code, #captcha, "
                "[class*='captcha'], [class*='verify']"
            )
            return captcha is not None
        except Exception:
            return False

    # ── API 响应解析 ─────────────────────────────────────────────────────────

    def _parse_api_responses(
        self, responses: List[Dict[str, Any]], params: SearchParams
    ) -> List[FlightPrice]:
        """遍历捕获的 API 响应，尝试解析出航班价格列表。

        同时兼容携程多个版本 API 的响应结构：
        - `data.flightItineraryList`（国内航班主接口）
        - `data.flights`（部分旧版接口）
        - `flightList`（扁平格式）

        Args:
            responses: 捕获的响应列表，每项格式为 ``{"url": str, "data": dict}``。
            params: 原始搜索参数（用于补全 FlightInfo 字段）。

        Returns:
            解析出的 FlightPrice 列表。
        """
        for entry in responses:
            # 兼容旧格式（直接是 dict）和新格式（{"url": ..., "data": ...}）
            if isinstance(entry, dict) and "data" in entry and "url" in entry:
                data = entry["data"]
                url = entry["url"]
            else:
                data = entry
                url = "(unknown)"
            result = self._try_parse_response(data, params)
            if result:
                logger.info("携程 API 解析成功：%s，得到 %d 条航班", url, len(result))
                return result
        return []

    def _try_parse_response(
        self, data: Dict[str, Any], params: SearchParams
    ) -> List[FlightPrice]:
        """尝试从单个 API 响应中解析航班列表。

        Args:
            data: API 响应 JSON dict。
            params: 搜索参数。

        Returns:
            解析成功返回 FlightPrice 列表，失败返回空列表。
        """
        if not isinstance(data, dict):
            return []

        # 寻找航班列表字段（兼容多种 key 名）
        inner = data.get("data") or data
        if not isinstance(inner, dict):
            return []

        flight_list = (
            inner.get("flightItineraryList")
            or inner.get("flightList")
            or inner.get("flights")
        )
        if not flight_list or not isinstance(flight_list, list):
            return []

        logger.debug("携程 API：找到 %d 条航班记录", len(flight_list))
        prices: List[FlightPrice] = []
        for itinerary in flight_list:
            try:
                parsed = self._parse_itinerary(itinerary, params)
                prices.extend(parsed)
            except Exception as exc:
                logger.debug("解析航班行程失败：%s", exc)
        return prices

    def _parse_itinerary(
        self, itinerary: Dict[str, Any], params: SearchParams
    ) -> List[FlightPrice]:
        """解析单条航班行程（一个行程可能包含多个价格档位）。

        Args:
            itinerary: API 返回的单条行程字典。
            params: 搜索参数。

        Returns:
            该行程对应的 FlightPrice 列表（按舱位分）。
        """
        prices: List[FlightPrice] = []

        # ── 提取航班信息 ─────────────────────────────────────────────────────
        # 兼容嵌套格式（flightSegments[0].flightList[0]）和扁平格式
        segment = None
        seg_airline: Optional[str] = None  # 航段级航空公司（batchSearch seg0.airlineName）
        segments = itinerary.get("flightSegments")
        if segments and isinstance(segments, list) and segments:
            seg0 = segments[0]
            seg_airline = seg0.get("airlineName")  # 保存航段级别的航空公司名称作为备用
            fl = seg0.get("flightList")
            if fl and isinstance(fl, list) and fl:
                segment = fl[0]
        if segment is None:
            segment = itinerary  # 扁平格式直接作为 segment

        flight_no = (
            segment.get("flightNo")
            or segment.get("flight_no")
            or segment.get("flightNumber")
            or "UNKNOWN"
        ).strip().upper()

        airline = (
            (segment.get("marketAirlineInfo") or {}).get("airlineName")
            or segment.get("marketAirlineName")   # batchSearch 字段名
            or segment.get("airlineName")
            or segment.get("airline")
            or seg_airline                         # 航段级别备用
            or "未知航空公司"
        ).strip()

        dep_dt_str = (
            segment.get("departureDateTime")       # batchSearch 字段名
            or segment.get("departureDate")
            or segment.get("depDate")
            or segment.get("departureTime")
            or ""
        )
        arr_dt_str = (
            segment.get("arrivalDateTime")         # batchSearch 字段名
            or segment.get("arrivalDate")
            or segment.get("arrDate")
            or segment.get("arrivalTime")
            or ""
        )
        dep_time = self._extract_time_str(dep_dt_str)
        arr_time = self._extract_time_str(arr_dt_str)

        # ── 提取机场信息 ──────────────────────────────────────────────────
        dep_airport_code = (
            segment.get("depAirportCode")
            or segment.get("departureAirportCode")
            or ""
        )
        arr_airport_code = (
            segment.get("arrAirportCode")
            or segment.get("arrivalAirportCode")
            or ""
        )
        dep_airport = segment.get("depAirportName") or segment.get("departureAirportName")
        arr_airport = segment.get("arrAirportName") or segment.get("arrivalAirportName")

        flight_info = FlightInfo(
            flight_no=flight_no,
            airline=airline,
            departure_city=params.departure_city,
            arrival_city=params.arrival_city,
            departure_time=dep_time,
            arrival_time=arr_time,
            departure_date=params.departure_date,
            direction=FlightDirection.DEPARTURE,
            departure_airport=dep_airport,
            arrival_airport=arr_airport,
            departure_airport_code=dep_airport_code or None,
            arrival_airport_code=arr_airport_code or None,
        )

        # ── 往返搜索：构建标记信息 ──────────────────────────────────
        # 携程往返搜索时 API 只返回去程航班信息，回程信息被合并到 adultPrice 中。
        # 无法从 API 提取真实的回程航班，但可以创建虚拟的占位符来标记这是已合并的往返记录，
        # 防止下游 _combine_roundtrip_prices() 误将其当作单程去程记录处理。
        return_flight_info: Optional[FlightInfo] = None
        if params.return_date is not None:
            # 创建虚拟的回程航班占位符
            # 这是 Ctrip API 的局限：返回合计价但不返回回程航班细节
            return_flight_info = FlightInfo(
                flight_no="VIRTUAL_RETURN",  # 虚拟占位
                airline="",  # 不可知
                departure_city=params.arrival_city,
                arrival_city=params.departure_city,
                departure_time="00:00",
                arrival_time="00:00",
                departure_date=params.return_date,
                direction=FlightDirection.RETURN,
            )

        # ── 提取最低价格 ─────────────────────────────────────────────────────
        # batchSearch 每个行程（itinerary）含多个价格档位（priceList），每档对应
        # 不同的退改签套餐；价格监控只关心最低价，因此每个行程只保留一条记录。
        # 往返搜索时 adultPrice = 两程合计；设置 return_flight_info 标记已合并。
        price_list = (
            itinerary.get("priceList")
            or itinerary.get("prices")
            or [itinerary]  # 扁平格式
        )
        if not isinstance(price_list, list):
            price_list = [itinerary]

        is_roundtrip = params.return_date is not None

        best_price: Optional[Decimal] = None
        best_cabin_code = "Y"
        best_seats: Optional[int] = None

        for price_entry in price_list:
            try:
                # price_is_total=True 表示 raw_price 已是含税总价，不应再加 adultTax
                price_is_total = False
                if is_roundtrip:
                    # 优先使用明确为"总价"的字段；这些字段通常已含税，不应再叠加 adultTax
                    for field in ("roundTripPrice", "twowayPrice", "totalAdultPrice", "roundPrice"):
                        v = price_entry.get(field)
                        if v:
                            raw_price = v
                            price_is_total = True
                            break
                    else:
                        # 降级为 adultPrice（基础票价，需叠加 adultTax）
                        raw_price = (
                            price_entry.get("adultPrice")
                            or price_entry.get("price")
                            or price_entry.get("salePrice")
                            or 0
                        )
                else:
                    raw_price = (
                        price_entry.get("adultPrice")
                        or price_entry.get("price")
                        or price_entry.get("salePrice")
                        or 0
                    )
                if not raw_price:
                    continue

                # ── 含税总价 ──────────────────────────────────────────────────
                # batchSearch 国际航班：adultPrice = 基础票价，adultTax = 机场税/燃油费；
                # 国内航班：adultTax = None，adultPrice 已含税。
                # 若 raw_price 来自 roundTripPrice 等"总价"字段则已含税，不再叠加；
                # 若降级为 adultPrice 则需相加才等于最终价格。
                if not price_is_total:
                    adult_tax = price_entry.get("adultTax") or 0
                    raw_price = raw_price + adult_tax

                seats_left = price_entry.get("seatsLeft")
                # 仅在 seatsLeft 明确为 0 时跳过（已售罄）；
                # batchSearch API 不返回 seatsLeft，None 表示未知，不应过滤
                if seats_left == 0:
                    continue

                candidate = Decimal(str(raw_price))
                if best_price is None or candidate < best_price:
                    best_price = candidate
                    best_cabin_code = str(
                        price_entry.get("cabin")
                        or price_entry.get("cabinType")
                        or "Y"
                    ).strip().upper()
                    best_seats = seats_left
            except Exception as exc:
                logger.debug("解析价格档位失败：%s", exc)

        if best_price is not None:
            seat_class = CABIN_TYPE_MAP.get(best_cabin_code, "经济舱")
            prices.append(FlightPrice(
                flight_info=flight_info,
                price=best_price,
                currency="CNY",
                seat_class=seat_class,
                available_seats=best_seats,
                scraped_at=datetime.now(timezone.utc),
                source="ctrip",
                return_flight_info=return_flight_info,
            ))

        return prices

    @staticmethod
    def _extract_time_str(dt_str: str) -> str:
        """从日期时间字符串中提取 HH:MM 格式的时间。

        Args:
            dt_str: 可能是 "2024-01-15 08:00:00"、"08:00" 等格式。

        Returns:
            HH:MM 格式字符串；无法提取时返回 "00:00"。
        """
        if not dt_str:
            return "00:00"
        # 匹配 HH:MM（不含秒）
        m = re.search(r"(\d{1,2}):(\d{2})", dt_str)
        if m:
            return f"{int(m.group(1)):02d}:{m.group(2)}"
        return "00:00"

    # ── DOM 备用解析 ─────────────────────────────────────────────────────────

    async def _parse_flights_from_dom(
        self, page: Page, params: SearchParams
    ) -> List[FlightPrice]:
        """从页面 DOM 解析航班列表（API 拦截失败时的备用路径）。

        Args:
            page: 当前 Playwright Page。
            params: 搜索参数。

        Returns:
            解析到的 FlightPrice 列表。
        """
        # 等待航班列表容器出现
        try:
            await page.wait_for_selector(
                ".flight-item, [class*='flight-item'], [class*='flightItem'],"
                " .flight-info, [class*='list-item']",
                timeout=self.timeout // 2,
            )
        except Exception:
            logger.warning("携程 DOM：等待航班列表超时，尝试直接解析")

        flight_elements = await page.query_selector_all(
            ".flight-item, [class*='flight-item'], [class*='flightItem']"
        )
        if not flight_elements:
            logger.warning("携程 DOM：未找到航班元素")
            return []

        logger.info("携程 DOM：找到 %d 个航班元素", len(flight_elements))

        # 保存第一个航班元素的 outerHTML，供调试时检查实际 DOM 结构
        try:
            first_html = await flight_elements[0].evaluate("el => el.outerHTML")
            with open("ctrip_debug_first_flight_elem.html", "w", encoding="utf-8") as f:
                f.write(first_html)
            logger.debug("已保存首个航班元素 HTML → ctrip_debug_first_flight_elem.html")
        except Exception as _e:
            logger.debug("保存调试 HTML 失败: %s", _e)

        prices: List[FlightPrice] = []
        for elem in flight_elements:
            fp = await self._parse_flight_element(elem, params)
            if fp:
                prices.append(fp)
        return prices

    async def _parse_flight_element(
        self, element, params: SearchParams
    ) -> Optional[FlightPrice]:
        """解析单个 DOM 航班元素。

        使用多个候选选择器，以兼容携程不同版本前端。

        Args:
            element: Playwright ElementHandle。
            params: 搜索参数。

        Returns:
            解析成功返回 FlightPrice，失败返回 None。
        """
        async def _text(selectors: str) -> str:
            """尝试多个选择器，返回第一个匹配元素的内部文本。"""
            for sel in selectors.split(","):
                sel = sel.strip()
                try:
                    el = await element.query_selector(sel)
                    if el:
                        return (await el.inner_text()).strip()
                except Exception:
                    pass
            return ""

        try:
            # 航班号：".plane-No" 文本形如 "EU6674 空客320(中)"，只取航班代码部分
            flight_no_raw = await _text(".plane-No") or ""
            _m = re.match(r"([A-Z0-9]{2,3}\d{3,4})", flight_no_raw.strip().upper())
            flight_no = (
                _m.group(1)
                if _m
                else (flight_no_raw.split()[0].upper() if flight_no_raw else "UNKNOWN")
            )

            airline = await _text(".airline-name span, .airline-name") or "未知航空公司"

            dep_time_raw = await _text(".depart-box .time") or ""
            arr_time_raw = await _text(".arrive-box .time") or ""
            dep_time = self._extract_time_str(dep_time_raw)
            arr_time = self._extract_time_str(arr_time_raw)

            price_text = await _text(".flight-price .price, .price")
            price_str = re.sub(r"[^\d.]", "", price_text)
            if not price_str:
                return None
            price = Decimal(price_str)

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
            return FlightPrice(
                flight_info=flight_info,
                price=price,
                currency="CNY",
                seat_class="经济舱",
                available_seats=None,
                scraped_at=datetime.now(timezone.utc),
                source="ctrip",
            )
        except Exception as exc:
            logger.debug("携程 DOM 元素解析失败：%s", exc)
            return None
