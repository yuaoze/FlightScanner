"""被动读取飞猪官网单程报价；往返分别采集真实单程后由调度器配对。"""

import asyncio
import json
import logging
import re
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

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
from flightscanner.utils.city_codes import (
    DOMESTIC_AIRPORT_MAP,
    get_city_code,
    is_international_route,
)

logger = logging.getLogger(__name__)
_COOKIE_DOMAINS = {"fliggy.com", "taobao.com", "alitrip.com"}
_CITY_AIRPORTS = {
    get_city_code(city): {airport["code"] for airport in airports}
    for city, airports in DOMESTIC_AIRPORT_MAP.items()
}
_CITY_AIRPORTS.update(
    {
        "TYO": {"HND", "NRT"},
        "OSA": {"KIX", "ITM", "UKB"},
        "SEL": {"ICN", "GMP"},
        "BKK": {"BKK", "DMK"},
        "TPE": {"TPE", "TSA"},
        "JKT": {"CGK", "HLP"},
        "LON": {"LHR", "LGW", "LCY", "LTN", "STN", "SEN"},
        "PAR": {"CDG", "ORY", "BVA"},
        "ROM": {"FCO", "CIA"},
        "MIL": {"MXP", "LIN", "BGY"},
        "STO": {"ARN", "BMA", "NYO", "VST"},
        "IST": {"IST", "SAW"},
        "NYC": {"JFK", "LGA", "EWR"},
        "YTO": {"YYZ", "YTZ", "YHM"},
        "CHI": {"ORD", "MDW"},
        "HOU": {"IAH", "HOU"},
        "WAS": {"IAD", "DCA", "BWI"},
        "YMQ": {"YUL", "YMX"},
        "MOW": {"SVO", "DME", "VKO"},
    }
)


class FliggyScraper(FlightScraper):
    DEFAULT_COOKIES_FILE = "fliggy_cookies.json"

    def __init__(
        self,
        headless: bool = True,
        timeout: int = 30000,
        max_retries: int = 3,
        max_results: int = 20,
        cookies: list[dict] | None = None,
        cookies_file: str | None = None,
    ) -> None:
        self.headless = headless
        self.timeout = timeout
        self.max_retries = max_retries
        self.max_results = max_results
        self._cookies_path = (
            None if cookies is not None else Path(cookies_file or self.DEFAULT_COOKIES_FILE)
        )
        self.cookies = (
            self._normalize_cookies(cookies)
            if cookies is not None
            else self.load_cookies_from_file(str(self._cookies_path))
        )
        self._cookies_mtime = self._get_cookies_mtime()
        self._playwright = None
        self._browser = None
        self._context = None

    @staticmethod
    def _normalize_cookies(cookies: list[dict]) -> list[dict]:
        result = []
        for cookie in cookies:
            if (
                not isinstance(cookie, dict)
                or not isinstance(cookie.get("name"), str)
                or not isinstance(cookie.get("value"), str)
            ):
                continue
            domain = str(cookie.get("domain", ".fliggy.com")).lower()
            host = domain.lstrip(".")
            if not any(host == root or host.endswith("." + root) for root in _COOKIE_DOMAINS):
                continue
            normalized = {
                "name": cookie["name"],
                "value": cookie["value"],
                "domain": domain,
                "path": cookie.get("path", "/"),
            }
            for key in ("expires", "httpOnly", "secure", "sameSite"):
                if key in cookie:
                    normalized[key] = cookie[key]
            result.append(normalized)
        return result

    @staticmethod
    def load_cookies_from_file(path: str) -> list[dict]:
        file = Path(path)
        if not file.exists():
            return []
        try:
            text = file.read_text(encoding="utf-8").strip()
            if text.startswith("["):
                return FliggyScraper._normalize_cookies(json.loads(text))
            if text.lower().startswith("cookie:"):
                text = text[7:].strip()
            cookies = []
            for pair in text.split(";"):
                name, separator, value = pair.partition("=")
                if separator and name.strip():
                    cookies.append({"name": name.strip(), "value": value.strip()})
            return FliggyScraper._normalize_cookies(cookies)
        except (OSError, ValueError, TypeError):
            logger.warning("飞猪 Cookie 文件无法读取，请重新上传")
            return []

    def _get_cookies_mtime(self) -> int | None:
        if self._cookies_path is None:
            return None
        try:
            return self._cookies_path.stat().st_mtime_ns
        except FileNotFoundError:
            return None

    async def reload_cookies_if_changed(self) -> bool:
        if self._cookies_path is None:
            return False
        modified = self._get_cookies_mtime()
        if modified == self._cookies_mtime:
            return False
        self.cookies = self.load_cookies_from_file(str(self._cookies_path))
        self._cookies_mtime = modified
        if self._context is not None:
            await self._context.close()
            self._context = None
        return True

    async def _ensure_browser(self) -> None:
        try:
            if self._browser is None:
                self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch(headless=self.headless)
            if self._context is None:
                self._context = await self._browser.new_context(
                    locale="zh-CN",
                    timezone_id="Asia/Shanghai",
                    viewport={"width": 1440, "height": 900},
                )
                if self.cookies:
                    await self._context.add_cookies(self.cookies)
        except (Exception, asyncio.CancelledError):
            await self.close()
            raise

    async def close(self) -> None:
        timeout = min(5.0, self.timeout / 1000)
        try:
            for resource in (self._context, self._browser):
                if resource is not None:
                    try:
                        await asyncio.wait_for(resource.close(), timeout=timeout)
                    except Exception:
                        logger.warning("关闭飞猪浏览器资源失败", exc_info=True)
            if self._playwright is not None:
                await asyncio.wait_for(self._playwright.stop(), timeout=timeout)
        finally:
            self._context = self._browser = self._playwright = None

    @staticmethod
    def _build_search_url(params: SearchParams) -> str:
        if params.return_date is not None:
            raise ValueError("飞猪搜索入口只接受单程参数，往返须分程采集")
        departure = get_city_code(params.departure_city)
        arrival = get_city_code(params.arrival_city)
        if not departure or not arrival:
            raise ScraperError("飞猪无法识别城市代码，请使用已支持的城市名称")
        international = is_international_route(params.departure_city, params.arrival_city)
        base = (
            "https://sijipiao.fliggy.com/ie/flight_search_result.htm"
            if international
            else "https://sjipiao.fliggy.com/flight_search_result.htm"
        )
        return (
            base
            + "?"
            + urlencode(
                {
                    "tripType": 0,
                    "depCity": departure,
                    "arrCity": arrival,
                    "depDate": params.departure_date.isoformat(),
                    "depCityName": params.departure_city,
                    "arrCityName": params.arrival_city,
                }
            )
        )

    async def search_flights(self, params: SearchParams) -> list[FlightPrice]:
        try:
            await asyncio.wait_for(self.reload_cookies_if_changed(), timeout=self.timeout / 1000)
            await asyncio.wait_for(self._ensure_browser(), timeout=self.timeout / 1000)
        except asyncio.TimeoutError as exc:
            await self.close()
            raise NetworkTimeoutError("飞猪浏览器初始化超时") from exc
        outbound = await self._search_oneway(replace(params, return_date=None))
        if params.return_date is None or not outbound:
            return outbound
        inbound = await self._search_oneway(
            SearchParams(
                departure_city=params.arrival_city,
                arrival_city=params.departure_city,
                departure_date=params.return_date,
            )
        )
        if not inbound:
            return []
        return outbound + [
            replace(price, flight_info=replace(price.flight_info, direction=FlightDirection.RETURN))
            for price in inbound
        ]

    @staticmethod
    def _decode_response(text: str) -> dict:
        stripped = text.strip()
        if not stripped.startswith("{"):
            match = re.fullmatch(
                r"[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*\s*\((.*)\)\s*;?",
                stripped,
                flags=re.DOTALL,
            )
            if not match:
                raise ParseError("飞猪响应不是 JSON 或合法 JSONP")
            stripped = match.group(1)
        try:
            data = json.loads(stripped)
        except ValueError as exc:
            raise ParseError("飞猪响应 JSON 解析失败") from exc
        if not isinstance(data, dict):
            raise ParseError("飞猪响应结构不正确")
        return data

    @staticmethod
    async def _is_blocked(page) -> bool:
        if any("/_____tmd_____/" in frame.url or "/punish" in frame.url for frame in page.frames):
            return True
        return (
            await page.locator(
                "iframe[src*='punish'], iframe[src*='_____tmd_____'], .nc-container"
            ).count()
            > 0
        )

    async def _search_oneway(self, params: SearchParams) -> list[FlightPrice]:
        page = None
        deadline = asyncio.get_running_loop().time() + self.timeout / 1000
        try:
            page = await asyncio.wait_for(self._context.new_page(), timeout=self.timeout / 1000)
            return await asyncio.wait_for(
                self._collect_oneway(page, params, deadline),
                timeout=max(0, deadline - asyncio.get_running_loop().time()),
            )
        except (asyncio.TimeoutError, PlaywrightTimeoutError) as exc:
            raise NetworkTimeoutError("飞猪搜索超时，请检查登录状态或稍后重试") from exc
        finally:
            if page is not None:
                try:
                    await asyncio.wait_for(page.close(), timeout=min(5.0, self.timeout / 1000))
                except asyncio.CancelledError:
                    await self.close()
                    raise
                except Exception:
                    logger.warning("飞猪搜索页关闭失败，回收浏览器会话", exc_info=True)
                    await self.close()

    async def _collect_oneway(
        self, page, params: SearchParams, deadline: float
    ) -> list[FlightPrice]:
        international = is_international_route(params.departure_city, params.arrival_city)
        expected_host = "sijipiao.fliggy.com" if international else "sjipiao.fliggy.com"
        expected_path = (
            "/ie/flight_search_result_poller.do" if international else "/searchow/search.htm"
        )
        pending: list[asyncio.Task] = []

        async def capture(response) -> dict:
            if response.status in {401, 403, 429}:
                raise AntiCrawlerDetectedError("飞猪需要登录或验证，请在设置中扫码更新登录状态")
            if not response.ok:
                raise ScraperError(f"飞猪搜索接口 HTTP {response.status}")
            try:
                return self._decode_response(await response.text())
            except ScraperError:
                raise
            except Exception as exc:
                raise ParseError("飞猪搜索响应读取失败") from exc

        def on_response(response) -> None:
            parsed = urlsplit(response.url)
            if parsed.hostname == expected_host and parsed.path == expected_path:
                pending.append(asyncio.create_task(capture(response)))

        page.on("response", on_response)
        try:
            document = await page.goto(
                self._build_search_url(params),
                wait_until="domcontentloaded",
                timeout=self.timeout,
            )
            if document is not None and not document.ok:
                raise ScraperError(f"飞猪搜索页面 HTTP {document.status}")
            collected: dict[tuple, FlightPrice] = {}
            processed = 0
            finished = False
            while asyncio.get_running_loop().time() < deadline:
                if await self._is_blocked(page):
                    raise AntiCrawlerDetectedError(
                        "飞猪搜索触发滑块/人机验证，请在设置中使用淘宝 App 扫码登录；不会自动弹窗"
                    )
                if urlsplit(page.url).hostname == "login.taobao.com":
                    raise ScraperError("飞猪需要登录，请在设置中使用淘宝 App 扫码登录")
                while processed < len(pending) and pending[processed].done():
                    data = pending[processed].result()
                    processed += 1
                    prices, finished = self._parse_response(
                        data, params, international=international
                    )
                    for price in prices:
                        info = price.flight_info
                        key = (
                            info.flight_no,
                            info.departure_date,
                            info.departure_time,
                            info.arrival_date,
                            info.arrival_time,
                            info.departure_airport_code,
                            info.arrival_airport_code,
                        )
                        previous = collected.get(key)
                        if previous is None or price.price < previous.price:
                            collected[key] = price
                if finished and processed == len(pending):
                    return sorted(collected.values(), key=lambda item: item.price)
                await asyncio.sleep(0.25)
            raise NetworkTimeoutError("飞猪搜索未完成全部响应，无法确认本次采集结果")
        finally:
            page.remove_listener("response", on_response)
            for task in pending:
                if not task.done():
                    task.cancel()
            if pending:
                await asyncio.wait_for(
                    asyncio.gather(*pending, return_exceptions=True),
                    timeout=min(5.0, self.timeout / 1000),
                )

    @staticmethod
    def _amount(value: Any) -> Decimal:
        if value is None or isinstance(value, bool):
            raise ValueError("缺少金额")
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0:
            raise ValueError("金额无效")
        return amount

    @staticmethod
    def _flight_info(segments: list[dict], params: SearchParams) -> FlightInfo:
        if not segments:
            raise ValueError("缺少航段")
        numbers = [str(segment.get("flight_no", "")).strip().upper() for segment in segments]
        if any(not re.fullmatch(r"[A-Z0-9]{2}\d{1,4}[A-Z]?", number) for number in numbers):
            raise ValueError("航班号不完整")
        times = []
        domestic = not is_international_route(params.departure_city, params.arrival_city)
        for segment in segments:
            raw_times = [str(segment[field]) for field in ("departure", "arrival")]
            if any(
                not re.match(r"^\d{4}[-/]\d{2}[-/]\d{2}[T ]\d{2}:\d{2}", raw) for raw in raw_times
            ):
                raise ValueError("航段缺少明确的起降时刻")
            departure, arrival = (
                datetime.fromisoformat(raw.replace("/", "-")) for raw in raw_times
            )
            if domestic and arrival < departure:
                raise ValueError("国内航段到达时间早于出发时间")
            if not all(
                re.fullmatch(r"[A-Z]{3}", str(segment.get(field, "")))
                for field in ("dep_code", "arr_code")
            ):
                raise ValueError("航段缺少机场代码")
            times.append((departure, arrival))
        first, last = segments[0], segments[-1]
        for city, airport in (
            (params.departure_city, first["dep_code"]),
            (params.arrival_city, last["arr_code"]),
        ):
            city_code = get_city_code(city)
            if airport not in _CITY_AIRPORTS.get(city_code, {city_code}):
                raise ValueError("航段机场与搜索城市不符")
        for index in range(1, len(segments)):
            previous_airport = segments[index - 1]["arr_code"]
            next_airport = segments[index]["dep_code"]
            same_city = previous_airport == next_airport or any(
                {previous_airport, next_airport}.issubset(airports)
                for airports in _CITY_AIRPORTS.values()
            )
            if not same_city or times[index][0] < times[index - 1][1]:
                raise ValueError("中转航段地点或时间不连续")
        departure, arrival = times[0][0], times[-1][1]
        if departure.date() != params.departure_date:
            raise ValueError("航班日期与搜索日期不符")
        airlines = list(
            dict.fromkeys(str(segment.get("airline") or "未知航空公司") for segment in segments)
        )
        return FlightInfo(
            flight_no="+".join(numbers),
            airline="/".join(airlines),
            departure_city=params.departure_city,
            arrival_city=params.arrival_city,
            departure_date=params.departure_date,
            departure_time=departure.strftime("%H:%M"),
            arrival_time=arrival.strftime("%H:%M"),
            arrival_date=arrival.date(),
            direction=FlightDirection.DEPARTURE,
            departure_airport=first.get("dep_name"),
            arrival_airport=last.get("arr_name"),
            departure_airport_code=first.get("dep_code"),
            arrival_airport_code=last.get("arr_code"),
        )

    @staticmethod
    def _domestic_segment(item: dict, data: dict) -> dict:
        airports = data.get("airportMap", {})
        airlines = data.get("aircodeNameMap", {})
        return {
            "flight_no": item.get("flightNo"),
            "airline": airlines.get(item.get("airlineCode")),
            "departure": item.get("depTime"),
            "arrival": item.get("arrTime"),
            "dep_code": item.get("depAirport"),
            "arr_code": item.get("arrAirport"),
            "dep_name": airports.get(item.get("depAirport")),
            "arr_name": airports.get(item.get("arrAirport")),
        }

    def _parse_domestic_item(
        self, item: dict, data: dict, params: SearchParams
    ) -> list[FlightPrice]:
        results = []
        alternatives = item.get("transferFlight", []) if item.get("isTransfer") else [None]
        for second in alternatives:
            try:
                cabin = item.get("cabin") or {}
                segments = [self._domestic_segment(item, data)]
                if second is not None:
                    segments.append(self._domestic_segment(second, data))
                    total = second["totalInfo"]
                    fare = self._amount(total["lowestPrice"])
                    amount = fare + self._amount(total["buildOilPrice"])
                    seats = total.get("cabinNum")
                else:
                    fare = self._amount(cabin["price"])
                    amount = (
                        fare + self._amount(item["buildPrice"]) + self._amount(item["oilPrice"])
                    )
                    seats = cabin.get("cabinNum")
                available = int(seats) if seats is not None and str(seats).isdigit() else None
                if fare <= 0 or available == 0:
                    continue
                results.append(
                    FlightPrice(
                        flight_info=self._flight_info(segments, params),
                        price=amount,
                        currency="CNY",
                        seat_class=str(cabin.get("specialType") or "未标明"),
                        available_seats=available,
                        scraped_at=datetime.now(timezone.utc),
                        source="fliggy",
                    )
                )
            except (KeyError, TypeError, ValueError, InvalidOperation):
                logger.debug("跳过航段或含税价格不完整的飞猪国内报价")
        return results

    def _parse_international_item(self, item: dict, params: SearchParams) -> list[FlightPrice]:
        try:
            groups = item["flightInfo"]
            if not isinstance(groups, list) or len(groups) != 1:
                return []
            segments = [
                {
                    "flight_no": segment.get("marketingFlightNo"),
                    "airline": segment.get("marketingAirlineName"),
                    "departure": segment.get("depTimeStr"),
                    "arrival": segment.get("arrTimeStr"),
                    "dep_code": segment.get("depAirportCode"),
                    "arr_code": segment.get("arrAirportCode"),
                    "dep_name": segment.get("depAirportName"),
                    "arr_name": segment.get("arrAirportName"),
                }
                for segment in groups[0]["flightSegments"]
            ]
            self._amount(item["adultTax"])
            # 国际接口以分计价，官网 price 格式化函数除以 100；国内接口直接使用元。
            amount = self._amount(item["totalAdultPrice"]) / 100
            seats = item.get("quantity")
            available = int(seats) if seats is not None and str(seats).isdigit() else None
            if amount <= 0 or available == 0:
                return []
            return [
                FlightPrice(
                    flight_info=self._flight_info(segments, params),
                    price=amount,
                    currency="CNY",
                    seat_class="未标明",
                    available_seats=available,
                    scraped_at=datetime.now(timezone.utc),
                    source="fliggy",
                )
            ]
        except (KeyError, TypeError, ValueError, InvalidOperation):
            logger.debug("跳过航段或含税价格不完整的飞猪国际报价")
            return []

    def _parse_response(
        self, response: dict, params: SearchParams, *, international: bool
    ) -> tuple[list[FlightPrice], bool]:
        if params.return_date is not None:
            raise ParseError("飞猪单程解析拒绝往返报价，避免价格口径混用")
        status = response.get("status")
        if (
            status not in (None, 200)
            or (international and status != 200)
            or response.get("errorMsg")
        ):
            raise ScraperError(f"飞猪搜索接口未成功（状态 {status}），请检查登录或验证状态")
        data = response.get("data")
        if not isinstance(data, dict):
            raise ParseError("飞猪响应缺少航班数据对象")
        for field, city in (
            ("depCityCode", params.departure_city),
            ("arrCityCode", params.arrival_city),
        ):
            if data.get(field) and data[field] != get_city_code(city):
                raise ParseError("飞猪响应城市与搜索路线不一致")
        key = "flightItems" if international else "flight"
        items = data.get(key)
        if not isinstance(items, list):
            if international and data.get("isContinue"):
                return [], False
            raise ParseError("飞猪响应缺少航班列表，不能判定为无票")
        results = []
        for item in items:
            if not isinstance(item, dict):
                continue
            results.extend(
                self._parse_international_item(item, params)
                if international
                else self._parse_domestic_item(item, data, params)
            )
        if items and not results:
            raise ParseError("飞猪返回了航班，但没有可确认的完整含税报价")
        finished = not international or data.get("isContinue") in (False, 0)
        return results, finished
