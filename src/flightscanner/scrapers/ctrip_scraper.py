"""携程机票采集：被动读取官网响应及完整往返选择结果。"""

import asyncio
import logging
import os
import re
import secrets
import shutil
import sys
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

from playwright.async_api import (
    async_playwright,
    Browser,
    BrowserContext,
    Page,
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
    ScraperError,
    SearchParams,
)
from flightscanner.utils.city_codes import get_city_code

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
        self._display_process: Optional[asyncio.subprocess.Process] = None
        self._display_directory: Optional[TemporaryDirectory] = None

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

    async def _start_virtual_display(self) -> Dict[str, str]:
        search_path = os.pathsep.join((str(Path(sys.executable).parent), os.defpath, os.getenv("PATH", "")))
        xvfb = shutil.which("Xvfb", path=search_path)
        xauth = shutil.which("xauth", path=search_path)
        if not xvfb or not xauth:
            raise ScraperError("携程后台采集需要 Xvfb 和 xauth；缺少组件，不会打开桌面浏览器")
        self._display_directory = TemporaryDirectory(prefix="flightscanner-xvfb-")
        auth_file = str(Path(self._display_directory.name) / "Xauthority")
        token = secrets.token_hex(16)

        # Xvfb 启动时先读取认证密钥，再为自动分配的显示编号登记同一密钥。
        for display in (":0", None):
            if display is None:
                self._display_process = await asyncio.create_subprocess_exec(
                    xvfb, "-displayfd", "1", "-screen", "0", "1440x900x24",
                    "-nolisten", "tcp", "-auth", auth_file,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                number = await asyncio.wait_for(self._display_process.stdout.readline(), 10)
                if not number.strip().isdigit():
                    raise ScraperError("携程后台虚拟显示启动失败，不会打开桌面浏览器")
                display = f":{int(number)}"
            auth = await asyncio.create_subprocess_exec(
                xauth, "-f", auth_file, "source", "-",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await auth.communicate(f"add {display} . {token}\n".encode())
            if auth.returncode != 0:
                raise ScraperError("携程后台虚拟显示认证初始化失败")

        environment = os.environ.copy()
        environment.update(DISPLAY=display, XAUTHORITY=auth_file)
        environment.pop("WAYLAND_DISPLAY", None)
        return environment

    async def _ensure_browser(self) -> None:
        """按需初始化 Playwright 浏览器（仅首次调用时执行）。"""
        if not self._browser:
            try:
                environment = None
                virtual = self.headless and sys.platform.startswith("linux")
                if virtual:
                    environment = await self._start_virtual_display()
                self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch(
                    headless=False if virtual else self.headless,
                    env=environment,
                )
            except (Exception, asyncio.CancelledError):
                await self.close()
                raise
        if self._context is not None:
            return
        self._context = await self._browser.new_context(
            viewport={"width": 1440, "height": 900},
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
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
            if self._display_process is not None:
                if self._display_process.returncode is None:
                    self._display_process.terminate()
                await self._display_process.communicate()
                self._display_process = None
            if self._display_directory is not None:
                self._display_directory.cleanup()
                self._display_directory = None
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
        capture_tasks: List[asyncio.Task] = []

        async def capture_response(response) -> None:
            try:
                data = await response.json()
                if isinstance(data, dict):
                    captured.append({"url": response.url.split("?")[0], "data": data})
            except Exception as exc:
                logger.debug("携程响应读取失败，保留 DOM 解析路径：%s", exc)

        def on_response(response) -> None:
            parsed = urlsplit(response.url)
            if parsed.hostname != "flights.ctrip.com" or not response.ok:
                return
            if any(part in parsed.path for part in (
                "/international/search/api/", "/restapi/soa2/",
                "/online/process", "/itinerary/api/",
            )):
                capture_tasks.append(asyncio.create_task(capture_response(response)))

        try:
            page = await self._context.new_page()
            page.on("response", on_response)

            url = self._build_search_url(params)
            logger.info("携程搜索：%s → %s，%s，URL: %s",
                        params.departure_city, params.arrival_city,
                        params.departure_date, url)

            try:
                response = await page.goto(
                    url, wait_until="domcontentloaded", timeout=self.timeout
                )
            except (asyncio.TimeoutError, PlaywrightTimeoutError) as exc:
                raise NetworkTimeoutError(f"携程页面加载超时：{exc}") from exc
            if response is not None and response.status in {403, 432}:
                raise AntiCrawlerDetectedError(
                    f"携程拒绝浏览器访问（HTTP {response.status}）；"
                    "需要人工登录或验证，后台采集不会自动打开桌面窗口"
                )

            await self._wait_for_search_results(page)
            if params.return_date is not None:
                return await self._parse_roundtrip_from_dom(page, params)
            if capture_tasks:
                try:
                    await asyncio.wait_for(
                        asyncio.gather(*capture_tasks), timeout=self.timeout / 1000
                    )
                except asyncio.TimeoutError:
                    logger.warning("携程部分响应读取超时，继续解析已完成响应和页面数据")

            # ── 优先：解析捕获的 API 响应 ────────────────────────────────
            flight_prices = self._parse_api_responses(captured, params)
            if flight_prices:
                flight_prices.sort(key=lambda fp: fp.price)
                logger.info(
                    "API 解析得到 %d 条，交由调度器过滤后截断",
                    len(flight_prices),
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
                page.remove_listener("response", on_response)
                await page.close()
            for task in capture_tasks:
                if not task.done():
                    task.cancel()
            if capture_tasks:
                await asyncio.gather(*capture_tasks, return_exceptions=True)

    async def _wait_for_search_results(self, page: Page, return_selection: bool = False) -> None:
        try:
            await page.wait_for_function(
                """returnSelection => (
                    document.querySelector('.flight-item .plane-No')
                    && (!returnSelection || (
                        document.querySelector('.segment_tab_selected_flight')
                        && [...document.querySelectorAll('.flight-item .btn-book')]
                            .some(button => button.textContent.includes('订票'))
                    ))
                ) || document.body.innerText.includes('为保障您的安全访问')
                  || document.body.innerText.includes('依次点击图标验证')""",
                arg=return_selection,
                timeout=self.timeout,
            )
        except PlaywrightTimeoutError:
            logger.debug("携程等待航班列表超时")
        if await self._is_blocked(page):
            if self.headless:
                raise AntiCrawlerDetectedError(
                    "携程搜索需要人工验证，后台采集已停止；不会自动打开桌面窗口"
                )
            await self._wait_for_manual_verification(page)

    async def _wait_for_manual_verification(self, page: Page, timeout: float = 120) -> None:
        logger.warning("请在携程浏览器窗口完成验证，当前搜索会话将保留 %s 秒", timeout)
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline and not page.is_closed():
            if not await self._is_blocked(page) and await page.locator(".flight-item").count():
                return
            await asyncio.sleep(1)
        raise AntiCrawlerDetectedError("携程人工验证未完成，请重新采集并在浏览器中完成验证")

    async def _parse_roundtrip_from_dom(
        self, page: Page, params: SearchParams
    ) -> List[FlightPrice]:
        results: List[FlightPrice] = []
        outbound_count = await page.locator(".flight-item").count()
        return_params = SearchParams(
            departure_city=params.arrival_city,
            arrival_city=params.departure_city,
            departure_date=params.return_date,
        )
        for index in range(outbound_count):
            cards = page.locator(".flight-item")
            if index >= await cards.count():
                break
            card = cards.nth(index)
            outbound = await self._parse_flight_element(await card.element_handle(), params)
            if outbound is None:
                continue
            await card.get_by_text("选为去程", exact=False).click()
            await self._wait_for_search_results(page, return_selection=True)
            await page.locator(".segment_tab_selected_flight").wait_for(timeout=self.timeout)
            await page.locator(".flight-item .btn-book").filter(
                has_text="订票"
            ).first.wait_for(timeout=self.timeout)
            return_cards = page.locator(".flight-item")
            for return_index in range(await return_cards.count()):
                return_card = return_cards.nth(return_index)
                if "往返含税价" not in await return_card.inner_text():
                    continue
                inbound = await self._parse_flight_element(
                    await return_card.element_handle(), return_params
                )
                if inbound is None:
                    continue
                # 回程选择页标价已包含已选去程，不能再相加。
                results.append(replace(
                    inbound,
                    flight_info=outbound.flight_info,
                    return_flight_info=replace(
                        inbound.flight_info, direction=FlightDirection.RETURN
                    ),
                ))
            await page.locator(".segment_tab_selected_flight .research_link").click()
            await page.get_by_text("选为去程", exact=False).first.wait_for(timeout=self.timeout)
        results.sort(key=lambda fp: fp.price)
        logger.info("携程完成去回程选择，取得 %d 条完整往返报价", len(results))
        return results

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
            if captcha is not None:
                return True
            for text in ("为保障您的安全访问", "依次点击图标验证"):
                if await page.query_selector(f"text={text}") is not None:
                    return True
            return False
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
        if params.return_date is not None:
            return []
        prices: List[FlightPrice] = []

        # ── 提取航班信息 ─────────────────────────────────────────────────────
        # 嵌套格式的 flightSegments[0].flightList 是完整单程的有序航段。
        # 中转行程用首段出发、末段到达，并组合全部航班号。
        legs: List[Dict[str, Any]] = []
        segment_airline: Optional[str] = None
        segment_groups = itinerary.get("flightSegments")
        if isinstance(segment_groups, list) and segment_groups:
            first_group = segment_groups[0]
            if isinstance(first_group, dict):
                segment_airline = first_group.get("airlineName")
                raw_legs = first_group.get("flightList")
                if isinstance(raw_legs, list):
                    legs = [leg for leg in raw_legs if isinstance(leg, dict)]

        # 旧版接口会直接返回扁平航班对象。
        if not legs:
            legs = [itinerary]

        first_leg = legs[0]
        last_leg = legs[-1]

        flight_numbers: List[str] = []
        airline_names: List[str] = []
        for leg in legs:
            raw_flight_no = (
                leg.get("flightNo")
                or leg.get("flight_no")
                or leg.get("flightNumber")
            )
            if raw_flight_no:
                normalized_flight_no = str(raw_flight_no).strip().upper()
                if normalized_flight_no:
                    flight_numbers.append(normalized_flight_no)

            market_airline_info = leg.get("marketAirlineInfo")
            raw_airline = (
                market_airline_info.get("airlineName")
                if isinstance(market_airline_info, dict)
                else None
            )
            raw_airline = (
                raw_airline
                or leg.get("marketAirlineName")
                or leg.get("airlineName")
                or leg.get("airline")
            )
            if raw_airline:
                airline_name = str(raw_airline).strip()
                if airline_name and airline_name not in airline_names:
                    airline_names.append(airline_name)

        # 多航段只要丢失一个航班号，组合标识就不可靠，还可能与
        # 共享首段的其他转机方案在去重或入库时碰撞。
        if len(flight_numbers) != len(legs):
            logger.debug("携程行程航段号不完整，跳过：%s", flight_numbers)
            return []

        flight_no = "+".join(flight_numbers)
        if not airline_names and segment_airline:
            fallback_airline = str(segment_airline).strip()
            if fallback_airline:
                airline_names.append(fallback_airline)
        airline = "/".join(airline_names) or "未知航空公司"

        dep_dt_str = (
            first_leg.get("departureDateTime")       # batchSearch 字段名
            or first_leg.get("departureDate")
            or first_leg.get("depDate")
            or first_leg.get("departureTime")
            or ""
        )
        arr_dt_str = (
            last_leg.get("arrivalDateTime")         # batchSearch 字段名
            or last_leg.get("arrivalDate")
            or last_leg.get("arrDate")
            or last_leg.get("arrivalTime")
            or ""
        )
        dep_time = self._extract_time_str(dep_dt_str)
        arr_time = self._extract_time_str(arr_dt_str)
        arrival_date = self._extract_date_str(arr_dt_str)
        if arrival_date is not None and arrival_date < params.departure_date:
            # Some legacy/mock payloads carry a stale calendar date.  Keep it
            # unknown so the shared filter can fall back to the two clock times
            # instead of persisting a negative, misleading day offset.
            arrival_date = None

        # ── 提取机场信息 ──────────────────────────────────────────────────
        dep_airport_code = (
            first_leg.get("depAirportCode")
            or first_leg.get("departureAirportCode")
            or ""
        )
        arr_airport_code = (
            last_leg.get("arrAirportCode")
            or last_leg.get("arrivalAirportCode")
            or ""
        )
        dep_airport = first_leg.get("depAirportName") or first_leg.get(
            "departureAirportName"
        )
        arr_airport = last_leg.get("arrAirportName") or last_leg.get(
            "arrivalAirportName"
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
            departure_airport=dep_airport,
            arrival_airport=arr_airport,
            departure_airport_code=dep_airport_code or None,
            arrival_airport_code=arr_airport_code or None,
            arrival_date=arrival_date,
        )

        # ── 提取最低价格 ─────────────────────────────────────────────────────
        price_list = (
            itinerary.get("priceList")
            or itinerary.get("prices")
            or [itinerary]  # 扁平格式
        )
        if not isinstance(price_list, list):
            price_list = [itinerary]

        best_price: Optional[Decimal] = None
        best_cabin_code = "Y"
        best_seats: Optional[int] = None

        for price_entry in price_list:
            try:
                raw_price = (
                    price_entry.get("adultPrice")
                    or price_entry.get("price")
                    or price_entry.get("salePrice")
                    or 0
                )
                if not raw_price:
                    continue
                raw_price = Decimal(str(raw_price)) + Decimal(
                    str(price_entry.get("adultTax") or 0)
                )

                seats_left = price_entry.get("seatsLeft")
                # 仅在 seatsLeft 明确为 0 时跳过（已售罄）；
                # batchSearch API 不返回 seatsLeft，None 表示未知，不应过滤
                if seats_left == 0:
                    continue

                candidate = Decimal(str(raw_price))
                if not candidate.is_finite() or candidate <= 0:
                    continue
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

    @staticmethod
    def _extract_date_str(dt_str: str) -> Optional[date]:
        """Extract a calendar date from Ctrip's common datetime strings."""
        if not dt_str:
            return None
        match = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", str(dt_str))
        if not match:
            return None
        try:
            return date(*(int(part) for part in match.groups()))
        except ValueError:
            return None

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
            flight_numbers = []
            for plane in await element.query_selector_all(".plane-No"):
                raw = (await plane.inner_text()).strip().upper()
                match = re.match(r"([A-Z0-9]{2,3}\d{3,4})\b", raw)
                if not match:
                    return None
                flight_numbers.append(match.group(1))
            if not flight_numbers:
                return None
            transfer = re.search(r"转(\d+)次", await _text(".arrow-box"))
            if transfer and len(flight_numbers) != int(transfer.group(1)) + 1:
                return None
            if "中转" in await _text(".transfer-info-group") and len(flight_numbers) < 2:
                return None
            flight_no = "+".join(flight_numbers)

            airline = await _text(".airline-name span, .airline-name") or "未知航空公司"

            dep_time_raw = await _text(".depart-box .time") or ""
            arr_time_raw = await _text(".arrive-box .time") or ""
            if not dep_time_raw or not arr_time_raw:
                return None
            dep_time = self._extract_time_str(dep_time_raw)
            arr_time = self._extract_time_str(arr_time_raw)
            day_offset = re.search(r"([+-]\d+)\s*天", arr_time_raw)
            arrival_date = params.departure_date + timedelta(
                days=int(day_offset.group(1)) if day_offset else 0
            )

            price_text = await _text(".flight-price .price, .price")
            if "-" in price_text:
                return None
            price_match = re.search(r"(\d[\d,]*(?:\.\d+)?)", price_text)
            if not price_match:
                return None
            price = Decimal(price_match.group(1).replace(",", ""))
            if not price.is_finite() or price <= 0:
                return None

            flight_info = FlightInfo(
                flight_no=flight_no,
                airline=airline,
                departure_city=params.departure_city,
                arrival_city=params.arrival_city,
                departure_time=dep_time,
                arrival_time=arr_time,
                departure_date=params.departure_date,
                direction=FlightDirection.DEPARTURE,
                departure_airport=await _text(".depart-box .airport") or None,
                arrival_airport=await _text(".arrive-box .airport") or None,
                arrival_date=arrival_date,
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
