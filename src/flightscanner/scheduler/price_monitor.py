"""Background scheduler for automated price monitoring.

This module provides a scheduler that automatically scrapes prices for active routes
and sends alerts when prices drop below target thresholds.
"""

import asyncio
import json
import logging
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from statistics import median
from typing import Dict, List, Optional, Set, Tuple

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from flightscanner.models.database import Route
from flightscanner.core.services import RouteService
from flightscanner.scrapers import ScraperRegistry
from flightscanner.analyzers import RuleBasedAnalyzer
from flightscanner.analyzers.rule_based_analyzer import _batch_min_prices
from flightscanner.notifiers import build_notifiers
from flightscanner.interfaces import FlightDirection, FlightPrice, FlightScraper, Notifier, PriceTrend, SearchParams
from flightscanner.models.database import init_db
from flightscanner.utils.config import settings

logger = logging.getLogger(__name__)


def _time_diff_minutes(t1: str, t2: str) -> int:
    """两个 HH:MM 字符串的时间差（绝对值，分钟）。"""
    def _to_min(t: str) -> int:
        try:
            h, m = map(int, t.split(":"))
            return h * 60 + m
        except (ValueError, AttributeError):
            return 0
    return abs(_to_min(t1) - _to_min(t2))


@dataclass
class NotifyContext:
    """通知上下文，包含触发价格提醒所需的完整信息。"""
    route_id: int
    origin: str
    destination: str
    target_date: date
    target_price: Decimal
    current_price: Decimal
    avg_30d: float           # 30天均价
    min_30d: float           # 30天最低价
    max_30d: float           # 30天最高价
    price_count: int         # 历史记录数
    trigger_reason: str      # "target_hit" | "near_30d_low" | "below_avg"
    recommendation: str      # "立即购买" | "建议购买" | "可以考虑"
    pct_vs_avg: float        # 当前价相比均价的百分比差（负=低于均价）
    pct_vs_target: float     # 当前价相比目标价的百分比差（负=低于目标）
    source: str
    flight_no: str
    airline: str
    departure_time: str
    arrival_time: str


class PriceMonitorScheduler:
    """定时路线价格监控调度器。

    管理多条路线的定时采集任务，支持多平台并行采集与多渠道告警推送。

    Attributes:
        headless: 浏览器是否无头运行。
        scrapers: 已启用的爬虫实例列表（支持同时运行多个平台）。
        analyzer: 价格趋势分析器。
        notifiers: 已启用的通知器实例列表。
        scheduler: APScheduler 异步调度器。
    """

    def __init__(
        self,
        headless: bool = True,
        enable_notifications: bool = False,
    ):
        """初始化监控调度器。

        根据 ``settings.scraper_type`` 配置（支持逗号分隔多平台）
        通过 ScraperRegistry 批量构建爬虫实例，支持多源并行采集。

        Args:
            headless: 是否无头模式，默认 True。
            enable_notifications: 是否启用价格告警推送，默认 False。
        """
        self.headless = headless

        # ── 解析爬虫平台列表 ──────────────────────────────────────────────
        platforms = [p.strip() for p in settings.scraper_type.split(",") if p.strip()]

        # QunarScraper 需要额外传入 Cookie
        qunar_cookies = None
        if "qunar" in platforms and settings.qunar_cookies:
            try:
                qunar_cookies = json.loads(settings.qunar_cookies)
                logger.info("已从配置加载 Qunar Cookie")
            except json.JSONDecodeError as exc:
                logger.warning("Qunar Cookie 解析失败：%s", exc)

        # CtripScraper 也支持通过环境变量传入 Cookie
        ctrip_cookies = None
        if "ctrip" in platforms and settings.ctrip_cookies:
            try:
                ctrip_cookies = json.loads(settings.ctrip_cookies)
                logger.info("已从配置加载 Ctrip Cookie")
            except json.JSONDecodeError as exc:
                logger.warning("Ctrip Cookie 解析失败：%s", exc)

        # 按平台逐一构建（QunarScraper / CtripScraper 需特殊参数）
        self.scrapers: List[FlightScraper] = []
        for platform in platforms:
            if platform == "qunar":
                scraper = ScraperRegistry.get(
                    "qunar",
                    headless=headless,
                    cookies=qunar_cookies,
                    max_results=20,
                )
            elif platform == "ctrip":
                scraper = ScraperRegistry.get(
                    "ctrip",
                    headless=headless,
                    cookies=ctrip_cookies,  # None 时自动尝试 ctrip_cookies.json
                )
            else:
                scraper = ScraperRegistry.get(platform, headless=headless)
            self.scrapers.append(scraper)
            logger.info("已初始化爬虫：%s（headless=%s）", platform, headless)

        if not self.scrapers:
            logger.warning("未配置任何爬虫平台，采集任务将无法执行")

        self.analyzer = RuleBasedAnalyzer()

        # ── 通知渠道 ──────────────────────────────────────────────────────
        self.notifiers: List[Notifier] = build_notifiers(settings, enable_notifications)

        self.scheduler = AsyncIOScheduler()
        self._engine, self._SessionLocal = init_db(settings.database_url)

        logger.info(
            "PriceMonitorScheduler 已初始化（平台=%s，headless=%s，通知=%s）",
            platforms, headless, enable_notifications,
        )

    async def scrape_route(self, route: Route) -> None:
        """对单条路线执行多平台并行采集。

        并发调用所有已启用的爬虫，合并去重后存库，并在价格达标时触发告警。
        若路线配置了机场代码或时间段过滤，采集结果在存库前自动过滤。

        Args:
            route: 需要采集的路线对象。
        """
        logger.info(
            "开始采集路线 %s：%s → %s（%s）",
            route.id, route.origin, route.destination, route.target_date,
        )

        try:
            # ── 精准航班号监控模式 ─────────────────────────────────────────
            if getattr(route, "monitoring_mode", "route") == "flight":
                await self._scrape_pinned_flights(route)
                return

            trip_type = getattr(route, "trip_type", "oneway")

            params = SearchParams(
                departure_city=route.origin,
                arrival_city=route.destination,
                departure_date=route.target_date,
                return_date=route.return_date if trip_type == "roundtrip" else None,
            )
            for scraper in self.scrapers:
                if hasattr(scraper, "max_results"):
                    scraper.max_results = getattr(route, "max_results", 20)
            flight_prices = await self._scrape_all_platforms(params)

            if not flight_prices:
                logger.warning(
                    "路线 %s（%s → %s）所有平台均未采集到数据",
                    route.id, route.origin, route.destination,
                )
                return

            logger.info("路线 %s 共采集到 %d 条价格（合并去重后）", route.id, len(flight_prices))

            # ── 生成本次采集的批次 ID ──────────────────────────────────────
            # 格式: "route_{route_id}_{timestamp}_{hash}"
            # 用于标记这一次采集的所有记录，确保同一批次的数据被统一处理
            import hashlib
            from datetime import datetime
            from decimal import Decimal

            batch_timestamp = datetime.now(timezone.utc).isoformat()
            # 计算哈希值：使用路由 ID、来源平台、采集时间戳
            hash_input = f"{route.id}_{''.join(fp.source for fp in flight_prices)}_{batch_timestamp}"
            batch_hash = hashlib.md5(hash_input.encode()).hexdigest()[:8]
            batch_id = f"route_{route.id}_{batch_timestamp}_{batch_hash}"

            # 为所有 FlightPrice 对象标记 batch_id
            for fp in flight_prices:
                fp.batch_id = batch_id

            # ── 往返程配对：将去程/回程单独记录合并为含往返总价的组合记录 ──────
            # 对已含 return_flight_info 的记录（如携程、Qunar 国际往返）直接透传；
            # 对 Qunar 国内往返 DOM 解析返回的 DEPARTURE/RETURN 分离记录则进行配对。
            if trip_type == "roundtrip":
                flight_prices = self._combine_roundtrip_prices(flight_prices)

            # ── 按路线配置的机场/时间段过滤 ─────────────────────────────
            flight_prices = self._apply_route_filters(route, flight_prices)
            if not flight_prices:
                logger.info(
                    "路线 %s 采集结果全部被机场/时间段过滤器过滤（0 条匹配），跳过存库",
                    route.id,
                )
                return
            logger.info("路线 %s 过滤后剩余 %d 条价格", route.id, len(flight_prices))

            # ── 存库 ─────────────────────────────────────────────────────
            session = self._SessionLocal()
            try:
                route_service = RouteService(session)
                for fp in flight_prices:
                    route_service.save_price_for_route(route.id, fp)
                logger.info("路线 %s 已保存 %d 条价格记录（批次 ID: %s）", route.id, len(flight_prices), batch_id)

                # ── 价格告警判断 ───────────────────────────────────────────
                # 1. 获取30天历史并计算统计数据
                history = route_service.get_route_price_history(route.id, days=30)
                stats = self._compute_price_stats(history)
                price_count = int(stats.get("batch_count", len(history)))

                # 2. 本次采集最低价记录
                best_fp = min(flight_prices, key=lambda fp: fp.price)

                # 3. 判断触发条件 + 防骚扰冷却
                should_notify, reason = self._should_notify(
                    route, best_fp.price, stats, price_count
                )
                if should_notify and not self._is_cooldown_active(route, best_fp.price):
                    if self.notifiers and history:
                        trend = self.analyzer.predict_trend(history, route.target_date)
                        ctx = self._build_notify_context(
                            route, best_fp, stats, reason, price_count
                        )
                        message_json = self._build_alert_message_data(ctx)
                        await self._send_alert(best_fp, trend, message_json)
                    # 记录本次通知时间和价格（即使无通知渠道也记录，防止反复触发判断）
                    self._update_route_notification_state(route.id, best_fp.price)
                elif should_notify:
                    logger.info(
                        "路线 %s 冷却中（上次通知未超 %d 小时且价格未再降 5%%），跳过通知",
                        route.id, settings.notify_cooldown_hours,
                    )

                # ── G1：记录预测（每路线每 12 小时最多一次）────────────────────
                await self._maybe_log_prediction(session, route, history)
            finally:
                session.close()

        except Exception as exc:
            logger.error("路线 %s 采集失败：%s", route.id, exc, exc_info=True)

    @staticmethod
    def _hhmm_to_minutes(hhmm: str) -> int:
        """将 'HH:MM' 格式时间字符串转换为午夜起的分钟数。"""
        try:
            h, m = map(int, hhmm.split(":"))
            return h * 60 + m
        except (ValueError, AttributeError):
            return 0

    def _apply_route_filters(
        self,
        route: Route,
        prices: List[FlightPrice],
    ) -> List[FlightPrice]:
        """按路线配置的机场代码和时间段过滤航班价格列表。

        过滤规则：
        - 机场代码：若路线设置了 dep_airport_code/arr_airport_code，则仅保留
          flight_info.departure_airport_code/arrival_airport_code 匹配的记录。
          若航班无机场代码数据（爬虫未采集到），则放行（宁可放行不可漏掉）。
        - 时间段：若路线设置了 dep_time_from/dep_time_to，则仅保留
          起飞时间在该时间段内的航班；arr_time_from/arr_time_to 同理。

        Args:
            route: 路线配置（含过滤字段）。
            prices: 待过滤的 FlightPrice 列表。

        Returns:
            过滤后的 FlightPrice 列表。
        """
        dep_airport = getattr(route, "dep_airport_code", None)
        arr_airport = getattr(route, "arr_airport_code", None)
        dep_from    = getattr(route, "dep_time_from", None)
        dep_to      = getattr(route, "dep_time_to", None)
        arr_from    = getattr(route, "arr_time_from", None)
        arr_to      = getattr(route, "arr_time_to", None)

        # 没有任何过滤条件，直接返回
        if not any([dep_airport, arr_airport, dep_from, dep_to, arr_from, arr_to]):
            return prices

        dep_from_min = self._hhmm_to_minutes(dep_from) if dep_from else None
        dep_to_min   = self._hhmm_to_minutes(dep_to)   if dep_to   else None
        arr_from_min = self._hhmm_to_minutes(arr_from) if arr_from else None
        arr_to_min   = self._hhmm_to_minutes(arr_to)   if arr_to   else None

        result: List[FlightPrice] = []
        for fp in prices:
            fi = fp.flight_info

            # ── 机场过滤（无机场信息的航班放行） ─────────────────────────
            if dep_airport:
                fp_dep = fi.departure_airport_code
                if fp_dep and fp_dep != dep_airport:
                    continue
            if arr_airport:
                fp_arr = fi.arrival_airport_code
                if fp_arr and fp_arr != arr_airport:
                    continue

            # ── 时间段过滤 ────────────────────────────────────────────────
            if dep_from_min is not None or dep_to_min is not None:
                dep_min = self._hhmm_to_minutes(fi.departure_time or "00:00")
                if dep_from_min is not None and dep_min < dep_from_min:
                    continue
                if dep_to_min is not None and dep_min > dep_to_min:
                    continue

            if arr_from_min is not None or arr_to_min is not None:
                arr_min = self._hhmm_to_minutes(fi.arrival_time or "00:00")
                if arr_from_min is not None and arr_min < arr_from_min:
                    continue
                if arr_to_min is not None and arr_min > arr_to_min:
                    continue

            result.append(fp)

        return result

    async def _scrape_pinned_flights(self, route: Route) -> None:
        """精准航班号监控：搜索指定航班，记录价格并更新航班状态。

        对单程：仅搜索去程方向，匹配 outbound_flight_no。
        对往返：分两次单程搜索，分别匹配去程/回程航班，存库价格为两段之和。

        Args:
            route: 精准监控路线（monitoring_mode == 'flight'）。
        """
        trip_type = getattr(route, "trip_type", "oneway")
        outbound_no = getattr(route, "outbound_flight_no", None)
        inbound_no = getattr(route, "inbound_flight_no", None)
        seat_class = getattr(route, "pinned_seat_class", None)

        if not outbound_no:
            logger.warning("路线 %s 精准模式未设置 outbound_flight_no，跳过", route.id)
            return

        # ── 去程搜索（max_results 调高，确保能找到目标航班） ────────────────
        for scraper in self.scrapers:
            if hasattr(scraper, "max_results"):
                scraper.max_results = 100

        out_params = SearchParams(
            departure_city=route.origin,
            arrival_city=route.destination,
            departure_date=route.target_date,
            return_date=None,
        )
        out_prices = await self._scrape_all_platforms(out_params)
        outbound_fp, out_status = self._match_pinned_flight(out_prices, outbound_no, seat_class)

        # ── 往返程：回程搜索 ──────────────────────────────────────────────
        inbound_fp: Optional[FlightPrice] = None
        in_status = "available"
        if trip_type == "roundtrip" and inbound_no and route.return_date:
            in_params = SearchParams(
                departure_city=route.destination,
                arrival_city=route.origin,
                departure_date=route.return_date,
                return_date=None,
            )
            in_prices = await self._scrape_all_platforms(in_params)
            inbound_fp, in_status = self._match_pinned_flight(in_prices, inbound_no, seat_class)

        # ── 确定综合状态并写库 ────────────────────────────────────────────
        status = self._determine_flight_status(
            route, outbound_fp, inbound_fp, out_status, in_status, trip_type, inbound_no
        )

        session = self._SessionLocal()
        try:
            route_service = RouteService(session)
            route_service.update_flight_status(route.id, status)
        finally:
            session.close()

        if status in ("not_found", "sold_out"):
            logger.info(
                "路线 %s 精准航班 %s 状态：%s，跳过存库",
                route.id, outbound_no, status,
            )
            return

        # ── 组合价格并存库 ────────────────────────────────────────────────
        if trip_type == "roundtrip" and outbound_fp and inbound_fp:
            total_price = outbound_fp.price + inbound_fp.price
            prices_to_save: List[FlightPrice] = [
                FlightPrice(
                    flight_info=outbound_fp.flight_info,
                    price=total_price,
                    currency=outbound_fp.currency,
                    seat_class=outbound_fp.seat_class,
                    available_seats=outbound_fp.available_seats,
                    scraped_at=outbound_fp.scraped_at,
                    source=outbound_fp.source,
                    return_flight_info=inbound_fp.flight_info,
                )
            ]
        elif outbound_fp:
            prices_to_save = [outbound_fp]
        else:
            return

        import hashlib
        batch_timestamp = datetime.now(timezone.utc).isoformat()
        batch_hash = hashlib.md5(
            f"{route.id}_pinned_{batch_timestamp}".encode()
        ).hexdigest()[:8]
        batch_id = f"route_{route.id}_{batch_timestamp}_{batch_hash}"
        for fp in prices_to_save:
            fp.batch_id = batch_id

        session = self._SessionLocal()
        try:
            route_service = RouteService(session)
            for fp in prices_to_save:
                route_service.save_price_for_route(route.id, fp)
            logger.info(
                "路线 %s 精准航班 %s 已存库（批次 %s）", route.id, outbound_no, batch_id
            )

            # ── 价格告警（与普通模式相同逻辑） ────────────────────────────
            history = route_service.get_route_price_history(route.id, days=30)
            stats = self._compute_price_stats(history)
            price_count = int(stats.get("batch_count", len(history)))
            best_fp = min(prices_to_save, key=lambda fp: fp.price)
            should_notify, reason = self._should_notify(
                route, best_fp.price, stats, price_count
            )
            if should_notify and not self._is_cooldown_active(route, best_fp.price):
                if self.notifiers and history:
                    trend = self.analyzer.predict_trend(history, route.target_date)
                    ctx = self._build_notify_context(route, best_fp, stats, reason, price_count)
                    message_json = self._build_alert_message_data(ctx)
                    await self._send_alert(best_fp, trend, message_json)
                self._update_route_notification_state(route.id, best_fp.price)
        finally:
            session.close()

    @staticmethod
    def _match_pinned_flight(
        prices: List[FlightPrice],
        target_flight_no: str,
        seat_class_filter: Optional[str],
    ) -> Tuple[Optional[FlightPrice], str]:
        """从采集结果中匹配目标航班号和舱位，返回 (best_match, status)。

        Status 取值：
        - 'available'  — 找到航班且有余票
        - 'sold_out'   — 找到航班但 available_seats == 0（明确售罄）
        - 'not_found'  — 未在结果中找到目标航班号

        Args:
            prices: 采集到的 FlightPrice 列表。
            target_flight_no: 目标航班号（不区分大小写）。
            seat_class_filter: 指定舱位筛选，None 表示不限。

        Returns:
            (匹配到的最低价 FlightPrice 或 None, 状态字符串)
        """
        target = target_flight_no.upper().strip()
        all_matching = [
            fp for fp in prices
            if fp.flight_info.flight_no.upper().strip() == target
            and (not seat_class_filter or fp.seat_class == seat_class_filter)
        ]

        if not all_matching:
            return None, "not_found"

        available = [fp for fp in all_matching if fp.available_seats != 0]
        if not available:
            return None, "sold_out"

        return min(available, key=lambda fp: fp.price), "available"

    @staticmethod
    def _determine_flight_status(
        route: Route,
        outbound_fp: Optional[FlightPrice],
        inbound_fp: Optional[FlightPrice],
        out_status: str,
        in_status: str,
        trip_type: str,
        inbound_flight_no: Optional[str],
    ) -> str:
        """综合去程/回程匹配状态，返回路线最终航班状态。

        优先级：sold_out > not_found > schedule_changed > available。

        Returns:
            'available' | 'sold_out' | 'not_found' | 'schedule_changed'
        """
        if out_status == "sold_out":
            return "sold_out"
        if out_status == "not_found":
            return "not_found"

        # 检查去程时刻是否变动（与参考时刻偏差 > 60 分钟）
        outbound_dep_time_ref = getattr(route, "outbound_dep_time_ref", None)
        if outbound_fp and outbound_dep_time_ref:
            actual = outbound_fp.flight_info.departure_time
            if actual and _time_diff_minutes(outbound_dep_time_ref, actual) > 60:
                return "schedule_changed"

        # 往返程：检查回程
        if trip_type == "roundtrip" and inbound_flight_no:
            if in_status == "sold_out":
                return "sold_out"
            if in_status == "not_found":
                return "not_found"
            inbound_dep_time_ref = getattr(route, "inbound_dep_time_ref", None)
            if inbound_fp and inbound_dep_time_ref:
                actual = inbound_fp.flight_info.departure_time
                if actual and _time_diff_minutes(inbound_dep_time_ref, actual) > 60:
                    return "schedule_changed"

        return "available"

    async def _scrape_all_platforms(
        self, params: SearchParams
    ) -> List[FlightPrice]:
        """并发调用所有爬虫并合并去重结果。

        将 ``params``（含 ``return_date``）原样透传给各爬虫实例。对支持往返程
        的爬虫（如 QunarScraper 对国际往返走专用接口），由爬虫内部路由到对应
        实现，直接返回含 ``return_flight_info`` 的组合记录；对不支持往返的爬虫
        则返回单程结果。

        若某个平台采集出错，记录日志后跳过，不影响其他平台结果。

        Args:
            params: 搜索参数，return_date 非空时各爬虫自行处理往返逻辑。

        Returns:
            合并去重后的 FlightPrice 列表，按价格升序排列。
        """
        return await self._scrape_oneway(params)

    async def _scrape_oneway(
        self, params: SearchParams
    ) -> List[FlightPrice]:
        """并发调用所有爬虫，合并去重搜索结果。

        每个平台的原始结果按价格升序截取前 ``_per_platform_limit`` 条后再合并，
        避免低质量/高价结果堆积入库。

        Args:
            params: 搜索参数（含 return_date 时各爬虫自行处理往返逻辑）。

        Returns:
            合并去重后的 FlightPrice 列表，按价格升序排列。
        """
        if not self.scrapers:
            return []

        tasks = [s.search_flights(params) for s in self.scrapers]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_prices: List[FlightPrice] = []
        for scraper, result in zip(self.scrapers, results):
            platform = type(scraper).__name__
            if isinstance(result, Exception):
                logger.error("%s 采集失败：%s", platform, result)
            elif isinstance(result, list):
                # ── 每平台仅保留最低的前 N 条 ────────────────────────────────
                limit = getattr(scraper, "max_results", 20)
                top = sorted(result, key=lambda fp: fp.price)[:limit]
                logger.info(
                    "%s 采集到 %d 条结果，保留最低 %d 条",
                    platform, len(result), len(top),
                )
                all_prices.extend(top)

        # ── 去重：同平台同航班同舱位保留最低价，不同平台数据独立保留 ────────────
        return self._deduplicate(all_prices)

    @staticmethod
    def _deduplicate(prices: List[FlightPrice]) -> List[FlightPrice]:
        """合并去重：相同 (flight_no, seat_class, source) 组合仅保留价格最低的记录。

        不同平台（source）的数据独立保留，便于比价和来源溯源。
        同一平台同一航班的重复采集结果则保留最低价。

        Args:
            prices: 原始价格列表（可能来自多个平台）。

        Returns:
            去重后按价格升序排列的列表。
        """
        best: Dict[Tuple[str, str, str], FlightPrice] = {}
        for fp in prices:
            key = (fp.flight_info.flight_no, fp.seat_class, fp.source)
            if key not in best or fp.price < best[key].price:
                best[key] = fp
        return sorted(best.values(), key=lambda fp: fp.price)

    @staticmethod
    def _combine_roundtrip_prices(prices: List[FlightPrice]) -> List[FlightPrice]:
        """将去程/回程价格记录配对为往返组合记录。

        两种来源均能正确处理：
        1. 爬虫已返回含 return_flight_info 的组合记录（携程 batchSearch、Qunar 国际往返 API）
           → 直接纳入结果集。
        2. 爬虫返回单独的 DEPARTURE + RETURN 记录（Qunar 国际往返降级搜索）
           → 按 FlightDirection 分组，每个去程选同来源最便宜的回程配对，
           生成 return_flight_info 已填充、price 为两段之和的记录。

        两类结果合并后一起返回，不会因存在组合记录而丢弃单程待配对记录。

        Args:
            prices: 爬虫返回的原始价格列表，可能混合已组合记录与单程记录。

        Returns:
            组合后的往返价格列表，按价格升序排列。
            若完全无法配对（既无组合记录也无去/回程对），则原样返回 prices。
        """
        # 爬虫已返回组合往返记录（如携程 batchSearch、Qunar 国际往返 API）
        combined_existing = [fp for fp in prices if fp.return_flight_info is not None]

        # 仅含单程记录（如 Qunar 国际往返降级搜索）：配对去程 + 回程
        single_leg = [fp for fp in prices if fp.return_flight_info is None]
        outbound = [fp for fp in single_leg if fp.flight_info.direction == FlightDirection.DEPARTURE]
        returns = [fp for fp in single_leg if fp.flight_info.direction == FlightDirection.RETURN]

        newly_combined: List[FlightPrice] = []
        if outbound and returns:
            # 为每个去程找同来源最便宜的回程进行配对；若无同来源则跨来源取最低价
            cheapest_return_by_source: Dict[str, FlightPrice] = {}
            for fp in returns:
                src = fp.source
                if src not in cheapest_return_by_source or fp.price < cheapest_return_by_source[src].price:
                    cheapest_return_by_source[src] = fp
            cheapest_return_global = min(returns, key=lambda fp: fp.price)

            for out_fp in outbound:
                ret_fp = cheapest_return_by_source.get(out_fp.source, cheapest_return_global)
                seats = None
                if out_fp.available_seats is not None and ret_fp.available_seats is not None:
                    seats = min(out_fp.available_seats, ret_fp.available_seats)
                elif out_fp.available_seats is not None:
                    seats = out_fp.available_seats
                newly_combined.append(
                    FlightPrice(
                        flight_info=out_fp.flight_info,
                        price=out_fp.price + ret_fp.price,
                        currency=out_fp.currency,
                        seat_class=out_fp.seat_class,
                        available_seats=seats,
                        scraped_at=out_fp.scraped_at,
                        source=out_fp.source,
                        return_flight_info=ret_fp.flight_info,
                    )
                )
        elif single_leg:
            logger.warning("往返程配对失败：去程 %d 条，回程 %d 条", len(outbound), len(returns))

        all_combined = combined_existing + newly_combined
        if not all_combined:
            # 完全无法配对时原样返回，避免丢失数据
            return prices
        return sorted(all_combined, key=lambda fp: fp.price)

    async def _send_alert(
        self,
        flight_price: FlightPrice,
        trend: PriceTrend,
        message: str,
    ) -> None:
        """并行调用所有通知渠道发送价格提醒。

        Args:
            flight_price: 触发通知的航班价格记录（最低价）。
            trend: 价格趋势分析结果。
            message: 已序列化为 JSON 字符串的 NotifyContext 数据。
        """
        try:
            results = await asyncio.gather(
                *[n.send_alert(flight_price, trend, message) for n in self.notifiers],
                return_exceptions=True,
            )
            for notifier, result in zip(self.notifiers, results):
                if isinstance(result, Exception):
                    logger.error(
                        "%s 推送失败：%s", type(notifier).__name__, result
                    )
            logger.info(
                "价格提醒已通过 %d 个渠道发送（航班 %s）",
                len(self.notifiers), flight_price.flight_info.flight_no,
            )
        except Exception as exc:
            logger.error("发送价格提醒时出错：%s", exc, exc_info=True)

    # ── 通知触发逻辑辅助方法 ────────────────────────────────────────────────

    @staticmethod
    def _compute_price_stats(history: List[FlightPrice]) -> Dict[str, float]:
        """从价格历史中计算30天统计数据。

        Args:
            history: 价格历史记录列表。

        Returns:
            包含 avg_30d、min_30d、max_30d、batch_count 的字典；历史为空时全部返回 0.0。
            avg_30d 为各采集批次最低价的中位数，对偶发性特价票具有更强的鲁棒性。
        """
        if not history:
            return {"avg_30d": 0.0, "min_30d": 0.0, "max_30d": 0.0, "batch_count": 0.0}
        prices = [float(fp.price) for fp in history]
        batch_mins = _batch_min_prices(history)
        return {
            "avg_30d": median(batch_mins),
            "min_30d": min(prices),
            "max_30d": max(prices),
            "batch_count": float(len(batch_mins)),
        }

    @staticmethod
    def _should_notify(
        route: Route,
        current_price: Decimal,
        stats: Dict[str, float],
        price_count: int,
    ) -> Tuple[bool, str]:
        """判断是否应该触发价格通知。

        满足以下任一条件即触发（优先级由高到低）：
          1. target_hit:   current_price <= route.target_price
          2. near_30d_low: current_price <= min_30d * 1.05（接近30天最低，误差5%内）
          3. below_avg:    current_price < avg_30d * (1 - threshold/100)
                           且 price_count >= 7（数据量充足）

        Args:
            route: 路线配置对象（含 target_price）。
            current_price: 本次采集最低价。
            stats: 30天统计字典（avg_30d, min_30d, max_30d）。
            price_count: 历史价格记录数量。

        Returns:
            (should_notify, trigger_reason) 元组。
        """
        # 条件 1：达到目标价
        if current_price <= route.target_price:
            return True, "target_hit"

        avg_30d = stats["avg_30d"]
        min_30d = stats["min_30d"]

        # 条件 2：接近30天最低价（在5%误差内）
        if min_30d > 0 and float(current_price) <= min_30d * 1.05:
            return True, "near_30d_low"

        # 条件 3：显著低于30天均价
        threshold = settings.notify_below_avg_threshold
        if (
            avg_30d > 0
            and price_count >= 7
            and float(current_price) < avg_30d * (1 - threshold / 100)
        ):
            return True, "below_avg"

        return False, ""

    def _is_cooldown_active(self, route: Route, current_price: Decimal) -> bool:
        """检查通知防骚扰冷却是否仍在生效。

        在冷却小时数内，若价格未再下降 ≥5%，则静默。

        Args:
            route: 路线配置对象（含 last_notified_at 和 last_notified_price）。
            current_price: 当前最低价格。

        Returns:
            True 表示冷却中（应静默），False 表示可以通知。
        """
        last_notified_at = getattr(route, "last_notified_at", None)
        if not last_notified_at:
            return False

        # 确保时区感知
        if last_notified_at.tzinfo is None:
            last_notified_at = last_notified_at.replace(tzinfo=timezone.utc)

        hours_since = (
            datetime.now(timezone.utc) - last_notified_at
        ).total_seconds() / 3600

        if hours_since >= settings.notify_cooldown_hours:
            return False

        # 冷却期内：若价格又降了 ≥5%，打破冷却
        last_notified_price = getattr(route, "last_notified_price", None)
        if last_notified_price is not None and float(last_notified_price) > 0:
            drop_pct = (
                float(last_notified_price) - float(current_price)
            ) / float(last_notified_price)
            if drop_pct >= 0.05:
                return False

        return True

    def _build_notify_context(
        self,
        route: Route,
        best_fp: FlightPrice,
        stats: Dict[str, float],
        reason: str,
        price_count: int,
    ) -> NotifyContext:
        """构建通知上下文对象。

        Args:
            route: 路线配置对象。
            best_fp: 本次采集的最低价航班记录。
            stats: 30天统计字典（avg_30d, min_30d, max_30d）。
            reason: 触发原因字符串。
            price_count: 历史价格记录数量。

        Returns:
            填充完整的 NotifyContext 实例。
        """
        current_price = float(best_fp.price)
        target_price = float(route.target_price)
        avg_30d = stats["avg_30d"]

        pct_vs_avg = (
            (current_price - avg_30d) / avg_30d * 100 if avg_30d > 0 else 0.0
        )
        pct_vs_target = (
            (current_price - target_price) / target_price * 100
            if target_price > 0
            else 0.0
        )

        recommendation_map = {
            "target_hit": "立即购买",
            "near_30d_low": "建议购买",
            "below_avg": "可以考虑",
        }

        fi = best_fp.flight_info
        return NotifyContext(
            route_id=route.id,
            origin=route.origin,
            destination=route.destination,
            target_date=route.target_date,
            target_price=Decimal(str(route.target_price)),
            current_price=best_fp.price,
            avg_30d=avg_30d,
            min_30d=stats["min_30d"],
            max_30d=stats["max_30d"],
            price_count=price_count,
            trigger_reason=reason,
            recommendation=recommendation_map.get(reason, "–"),
            pct_vs_avg=pct_vs_avg,
            pct_vs_target=pct_vs_target,
            source=best_fp.source,
            flight_no=fi.flight_no,
            airline=fi.airline,
            departure_time=fi.departure_time,
            arrival_time=fi.arrival_time,
        )

    @staticmethod
    def _build_alert_message_data(ctx: NotifyContext) -> str:
        """将 NotifyContext 序列化为 JSON 字符串，作为 message 参数传入通知器。

        Args:
            ctx: 通知上下文对象。

        Returns:
            JSON 格式的消息字符串。
        """
        return json.dumps(
            {
                "route": f"{ctx.origin} → {ctx.destination}",
                "target_date": str(ctx.target_date),
                "current_price": float(ctx.current_price),
                "target_price": float(ctx.target_price),
                "avg_30d": ctx.avg_30d,
                "min_30d": ctx.min_30d,
                "trigger_reason": ctx.trigger_reason,
                "recommendation": ctx.recommendation,
                "pct_vs_avg": ctx.pct_vs_avg,
                "pct_vs_target": ctx.pct_vs_target,
                "flight_no": ctx.flight_no,
                "airline": ctx.airline,
                "departure_time": ctx.departure_time,
                "arrival_time": ctx.arrival_time,
                "source": ctx.source,
            },
            ensure_ascii=False,
        )

    def _update_route_notification_state(
        self, route_id: int, price: Decimal
    ) -> None:
        """更新路线的上次通知时间和价格。

        Args:
            route_id: 路线 ID。
            price: 本次通知时的价格。
        """
        session = self._SessionLocal()
        try:
            route_service = RouteService(session)
            route_service.update_notification_state(
                route_id, datetime.now(timezone.utc), price
            )
        finally:
            session.close()

    async def scrape_active_routes(self) -> None:
        """Scrape all active routes.

        This is the main scheduled job that runs periodically.
        """
        logger.info("Starting scheduled scrape of active routes")

        session = self._SessionLocal()
        try:
            route_service = RouteService(session)
            active_routes = route_service.get_active_routes()

            if not active_routes:
                logger.info("No active routes to scrape")
                return

            logger.info(f"Found {len(active_routes)} active routes to scrape")

            # Scrape each route (with delay to avoid rate limiting)
            for i, route in enumerate(active_routes):
                await self.scrape_route(route)

                # Add delay between routes (except for last one)
                if i < len(active_routes) - 1:
                    logger.info("Waiting 60 seconds before next route...")
                    await asyncio.sleep(60)

            logger.info("Completed scraping all active routes")

        finally:
            session.close()

    def schedule_route(self, route: Route, next_run_time: Optional[datetime] = None) -> None:
        """Schedule a specific route for periodic scraping.

        Only the route ID is captured in the closure; the job fetches a fresh
        Route object from the DB each time it fires to avoid DetachedInstanceError.

        Args:
            route:         Route to schedule.
            next_run_time: Override for the first fire time.  ``None`` lets
                           APScheduler default to ``now + interval``.  Pass the
                           calculated ``latest_scraped_at + interval`` so that
                           routes which were scraped before the scheduler started
                           fire at the correct future time instead of waiting a
                           full extra interval.
        """
        job_id = f"scrape_route_{route.id}"
        route_id = route.id  # capture only the ID, not the stale ORM object

        async def scrape_this_route() -> None:
            session = self._SessionLocal()
            try:
                route_service = RouteService(session)
                fresh_route = route_service.get_route_by_id(route_id)
                if fresh_route and fresh_route.is_active:
                    await self.scrape_route(fresh_route)
                else:
                    logger.info("路线 %s 不存在或已停用，跳过本次采集", route_id)
            finally:
                session.close()

        if self.scheduler.get_job(job_id):
            self.scheduler.remove_job(job_id)

        # ── 只在明确提供 next_run_time 时才传入，否则让 APScheduler 用触发器默认值 ──
        job_kwargs: Dict = dict(
            trigger=IntervalTrigger(hours=route.scrape_interval),
            id=job_id,
            name=f"Scrape {route.origin} → {route.destination}",
            replace_existing=True,
        )
        if next_run_time is not None:
            job_kwargs["next_run_time"] = next_run_time

        self.scheduler.add_job(scrape_this_route, **job_kwargs)

        logger.info(
            "已调度路线 %s（%s → %s），间隔 %d 小时，首次触发：%s",
            route.id, route.origin, route.destination, route.scrape_interval,
            next_run_time.strftime("%H:%M UTC") if next_run_time else "默认",
        )

    def register_new_route(self, route: Route) -> None:
        """注册新路线：立即采集一次，并按设定间隔定期采集。

        在 Streamlit 主线程（同步）中调用安全：
        - schedule_route() 使用 APScheduler 的线程安全 add_job()；
        - 立即采集通过 run_coroutine_threadsafe() 提交到后台事件循环，不阻塞主线程。

        Args:
            route: 刚添加的路线对象（属性须已加载，session 可已关闭）。
        """
        # ── 注册定期采集任务 ──────────────────────────────────────────────
        self.schedule_route(route)

        # ── 立即提交一次采集到后台事件循环 ────────────────────────────────
        loop: Optional[asyncio.AbstractEventLoop] = getattr(self, "_loop", None)
        if loop and loop.is_running():
            asyncio.run_coroutine_threadsafe(self.scrape_route(route), loop)
            logger.info("路线 %s（%s → %s）已提交立即采集任务", route.id, route.origin, route.destination)
        else:
            logger.warning(
                "后台事件循环未就绪，路线 %s 将在第一次定时触发时采集", route.id
            )

    def reschedule_all_routes(self) -> None:
        """重新调度所有活跃路线，确保首次触发时间与上次采集时间对齐。

        使用 ``get_all_routes()`` 获取含 ``latest_scraped_at`` 的完整路线信息，
        按 ``latest_scraped_at + scrape_interval`` 计算每条路线的首次触发时间，
        避免 APScheduler 重新注册后将触发时间重置为 ``now + interval``。

        逾期路线（应触发时间已过）由 ``_startup_catchup()`` 立即补采，
        此处将其下一次触发时间顺延一个完整间隔，避免双重采集。
        """
        session = self._SessionLocal()
        try:
            route_service = RouteService(session)
            all_routes = route_service.get_all_routes()

            # 清除旧调度任务
            for job in self.scheduler.get_jobs():
                if job.id.startswith("scrape_route_"):
                    self.scheduler.remove_job(job.id)

            now = datetime.now(timezone.utc)
            count = 0
            for r in all_routes:
                if not r.is_active:
                    continue

                if r.latest_scraped_at:
                    last = r.latest_scraped_at
                    if last.tzinfo is None:
                        last = last.replace(tzinfo=timezone.utc)
                    next_run = last + timedelta(hours=r.scrape_interval)
                    if next_run <= now:
                        # 已逾期：_startup_catchup() 会立即处理，
                        # 这里顺延一个间隔避免重复采集
                        next_run = now + timedelta(hours=r.scrape_interval)
                else:
                    # 从未采集：_startup_catchup() 立即处理，
                    # 定时器从现在起算一个完整间隔后首次触发
                    next_run = now + timedelta(hours=r.scrape_interval)

                self.schedule_route(r, next_run_time=next_run)
                count += 1

            logger.info("已重新调度 %d 条活跃路线", count)

        finally:
            session.close()

    async def _startup_catchup(self) -> None:
        """启动时检查逾期路线并立即补采。

        若当前时间距上次采集时间已超过该路线的采集间隔，则视为逾期，
        在调度器启动后立即执行一次采集，无需等待下一个定时触发点。
        从未采集过的路线（latest_scraped_at 为 None）同样视为逾期。
        """
        now = datetime.now(timezone.utc)
        session = self._SessionLocal()
        overdue_ids: Set[int] = set()
        to_scrape: List[Route] = []
        try:
            route_service = RouteService(session)
            all_routes = route_service.get_all_routes()
            for r in all_routes:
                if not r.is_active:
                    continue
                if r.latest_scraped_at is None:
                    overdue_ids.add(r.id)
                    continue
                last = r.latest_scraped_at
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                if (now - last) >= timedelta(hours=r.scrape_interval):
                    overdue_ids.add(r.id)

            if not overdue_ids:
                logger.info("启动检查：所有路线均在采集间隔内，无需补采")
                return

            logger.info("启动检查：发现 %d 条逾期路线，开始补采", len(overdue_ids))

            # 获取 Route ORM 对象，expunge 后脱离 session 但属性仍可访问
            active_routes = route_service.get_active_routes()
            for r in active_routes:
                if r.id in overdue_ids:
                    session.expunge(r)
                    to_scrape.append(r)
        finally:
            session.close()

        for i, route in enumerate(to_scrape):
            await self.scrape_route(route)
            if i < len(to_scrape) - 1:
                logger.info("逾期补采：等待 60 秒后继续下一条路线…")
                await asyncio.sleep(60)

        logger.info("逾期补采完成，共采集 %d 条路线", len(to_scrape))

    def start(self) -> None:
        """在独立后台线程中启动调度器，并在启动时补采逾期路线。

        为避免与 Streamlit 的同步主线程冲突，调度器运行在专属后台线程中，
        该线程持有自己的 asyncio 事件循环，APScheduler 的所有协程任务均在
        该循环内执行，与 Playwright 浏览器实例的生命周期完全匹配。

        **重要**：``run_forever()`` 必须在任何情况下都被调用，否则调度器的
        事件循环将静默退出，所有定时采集任务永久失效。``_startup()`` 中的
        非关键步骤（路线调度、逾期补采）使用独立 try/except 隔离，不影响
        主循环启动。
        """
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        def _thread_main() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop

            async def _startup() -> None:
                # AsyncIOScheduler.start() 内部调用 asyncio.get_running_loop()，
                # 必须在事件循环运行时（即 run_until_complete / run_forever 内部）调用。
                self.scheduler.start()
                logger.info("APScheduler 已在后台线程启动")

                # ── 每日进化任务：G2 回测 + G3 RCA（UTC 03:00）────────────────
                try:
                    from apscheduler.triggers.cron import CronTrigger
                    self.scheduler.add_job(
                        self._run_daily_evolution,
                        CronTrigger(hour=3, minute=0, timezone="UTC"),
                        id="daily_evolution",
                        replace_existing=True,
                    )
                    logger.info("每日进化任务已注册（UTC 03:00）")
                except Exception:
                    logger.exception("每日进化任务注册失败，G2/G3 将不会自动运行")

                # ── 调度所有活跃路线（非关键：失败不阻断主循环） ──────────────
                try:
                    self.reschedule_all_routes()
                except Exception:
                    logger.exception("reschedule_all_routes 异常，定时任务将在下次重启后恢复")

                # ── 启动时补采逾期路线（非关键：失败不阻断主循环） ────────────
                try:
                    await self._startup_catchup()
                except Exception:
                    logger.exception("_startup_catchup 异常，逾期路线将在定时触发时补采")

                logger.info("后台调度器就绪，进入事件循环")

            # ── run_forever() 必须无条件执行 ─────────────────────────────────
            # 若 _startup() 抛出异常（如数据库连接失败），此处捕获并记录，
            # 随后继续进入 run_forever()，确保调度器事件循环保持运行。
            try:
                loop.run_until_complete(_startup())
            except Exception:
                logger.exception(
                    "调度器启动阶段出现异常，事件循环仍将继续运行（已调度的任务不受影响）"
                )
            loop.run_forever()

        self._thread = threading.Thread(
            target=_thread_main,
            daemon=True,
            name="FlightScannerScheduler",
        )
        self._thread.start()
        logger.info("后台调度线程已启动")

    async def _maybe_log_prediction(
        self,
        session: "Session",
        route: Route,
        price_history: List[FlightPrice],
    ) -> None:
        """G1：若满足条件则记录一次预测（每路线 12 小时冷却）。

        Args:
            session: 当前数据库会话。
            route: 路线对象。
            price_history: 最新价格历史列表。
        """
        from flightscanner.analyzers.evolution_engine import log_prediction  # lazy import
        from ui.components.ai_brief import _should_auto_trigger  # 复用已有逻辑

        try:
            # 12h 冷却：距上次预测不足 12 小时则跳过
            route_service = RouteService(session)
            last_pred = route_service.get_last_prediction_time(route.id)
            if last_pred is not None:
                last_pred_aware = last_pred
                if hasattr(last_pred, "tzinfo") and last_pred.tzinfo is None:
                    last_pred_aware = last_pred.replace(tzinfo=timezone.utc)
                elapsed = (datetime.now(timezone.utc) - last_pred_aware).total_seconds()
                if elapsed < 43200:  # 12 小时
                    return

            # 仅在满足 auto-trigger 条件时记录
            should, _ = _should_auto_trigger(price_history)
            if not should:
                return

            from flightscanner.analyzers.deepseek_analyzer import generate_brief_with_fallback
            brief = generate_brief_with_fallback(
                price_history=price_history,
                target_date=route.target_date,
                route_label=f"{route.origin} → {route.destination}",
                api_key=settings.deepseek_api_key or None,
                base_url=settings.deepseek_base_url,
                model=settings.deepseek_model,
            )
            current_price = float(min(fp.price for fp in price_history))
            days_until = (route.target_date - date.today()).days
            log_prediction(session, route.id, brief, current_price, days_until)
            logger.info("[G1] 预测已记录 route_id=%d action=%s", route.id, brief.get("action"))
        except Exception as exc:
            logger.warning("[G1] 预测记录失败 route_id=%d：%s", route.id, exc)

    async def _run_daily_evolution(self) -> None:
        """G2 回测 + G3 RCA，每天 UTC 03:00 运行。"""
        from flightscanner.analyzers.evolution_engine import run_backtesting, run_rca

        logger.info("[Evolution] G2 回测开始")
        try:
            n2 = await run_backtesting(self._SessionLocal)
            logger.info("[Evolution] G2 完成，处理 %d 条", n2)
        except Exception as exc:
            logger.error("[Evolution] G2 回测失败：%s", exc, exc_info=True)
            return

        if settings.deepseek_api_key:
            logger.info("[Evolution] G3 RCA 开始")
            try:
                n3 = await run_rca(
                    self._SessionLocal,
                    api_key=settings.deepseek_api_key,
                    base_url=settings.deepseek_base_url,
                    model=settings.deepseek_model,
                )
                logger.info("[Evolution] G3 完成，处理 %d 条", n3)
            except Exception as exc:
                logger.error("[Evolution] G3 RCA 失败：%s", exc, exc_info=True)

    def stop(self) -> None:
        """停止调度器并清理所有爬虫资源。"""
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            logger.info("调度器已停止")

        loop: Optional[asyncio.AbstractEventLoop] = getattr(self, "_loop", None)
        if loop and loop.is_running():
            async def _close_scrapers() -> None:
                for scraper in self.scrapers:
                    await scraper.close()

            asyncio.run_coroutine_threadsafe(_close_scrapers(), loop)
            loop.call_soon_threadsafe(loop.stop)
            logger.info("爬虫资源清理已提交至后台线程")