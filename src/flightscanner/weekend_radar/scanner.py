"""周末低价雷达核心扫描引擎。

提供 WeekendDeal 数据类、WeekendRadarScanner 扫描器以及周末日期工具函数。
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Tuple

from flightscanner.interfaces import FlightInfo, FlightPrice, FlightScraper, SearchParams
from flightscanner.weekend_radar.destinations import ALL_DESTINATIONS, INTERNATIONAL_DESTINATIONS

logger = logging.getLogger(__name__)

# 上海出发的出发地配置（去哪儿接受城市名称）
_ORIGIN = "上海"
# 去程最早出发时间（周五下班后）
_OUTBOUND_FROM = "19:00"
# 去程到达截止时间：周六凌晨 02:00 前必须落地
_OUTBOUND_ARR_CUTOFF = "02:00"
# 回程时间窗口（周日晚间返程）
_RETURN_FROM = "18:00"
_RETURN_TO = "23:59"
# 回程到达截止时间：周一凌晨 02:00 前必须落地
_RETURN_ARR_CUTOFF = "02:00"


@dataclass
class WeekendDeal:
    """周末推荐往返组合。"""

    destination: str
    outbound_flight: FlightInfo
    return_flight: FlightInfo
    total_price: Decimal
    currency: str = "CNY"
    source: str = "qunar"
    historical_avg: Optional[Decimal] = None
    beat_pct: Optional[int] = None          # 击败历史均价的百分比
    ai_brief: Optional[Dict[str, Any]] = None


# ── 工具函数 ───────────────────────────────────────────────────────────────────

def get_upcoming_weekends(n: int = 8) -> List[Tuple[date, date]]:
    """返回未来 n 个周末的 (周五日期, 周日日期) 列表。

    若今天已是周六或周日，则从下周五开始计算。

    Args:
        n: 返回的周末数量。

    Returns:
        List of (friday_date, sunday_date) tuples.
    """
    today = date.today()
    weekday = today.weekday()  # 0=周一 … 6=周日

    # 计算距下个周五的天数
    # 若今天是周五(4)且还早，也算本周；否则取下周五
    if weekday <= 4:
        # 周一到周五：本周五可用（若今天 < 周五）或本周五（若今天就是周五）
        days_to_friday = (4 - weekday) % 7
    else:
        # 周六(5) / 周日(6)：下周五
        days_to_friday = (4 - weekday) % 7

    weekends: List[Tuple[date, date]] = []
    for i in range(n):
        friday = today + timedelta(days=days_to_friday + i * 7)
        sunday = friday + timedelta(days=2)
        weekends.append((friday, sunday))

    return weekends


def get_weekend_label(friday: date, sunday: date) -> str:
    """返回人类可读的周末标签。

    例如：'本周末 (4/11-4/13)' 或 '下下周末 (4/25-4/27)'

    Args:
        friday: 周五日期。
        sunday: 周日日期。

    Returns:
        Formatted label string.
    """
    today = date.today()
    days_away = (friday - today).days

    if days_away <= 6:
        prefix = "本周末"
    elif days_away <= 13:
        prefix = "下周末"
    elif days_away <= 20:
        prefix = "下下周末"
    else:
        prefix = f"第{days_away // 7 + 1}周末"

    return f"{prefix} ({friday.month}/{friday.day}-{sunday.month}/{sunday.day})"


def _is_direct_flight(flight_no: str) -> bool:
    """判断是否直飞（无中转）。航班号含 '/' 或 '+' 表示联程。"""
    return "/" not in flight_no and "+" not in flight_no


def _parse_hhmm(time_str: str) -> int:
    """将 'HH:MM' 字符串转换为分钟数，解析失败返回 0。"""
    try:
        h, m = map(int, time_str.split(":"))
        return h * 60 + m
    except (ValueError, AttributeError):
        return 0


def _matches_time_filter(
    dep_time: str,
    time_from: Optional[str] = None,
    time_to: Optional[str] = None,
) -> bool:
    """检查出发时间是否在指定时间窗口内。"""
    t = _parse_hhmm(dep_time)
    if time_from and t < _parse_hhmm(time_from):
        return False
    if time_to and t > _parse_hhmm(time_to):
        return False
    return True


def _arrival_before_cutoff(dep_time: str, arr_time: str, cutoff: str) -> bool:
    """检查航班到达时间是否在截止时刻之前。

    由于 FlightInfo 只存 HH:MM 不含日期，通过出发与到达的大小关系判断是否跨午夜：
    - arr >= dep：当日到达（周五/周日），出发日本身早于次日截止 → 直接放行
    - arr < dep：跨午夜到次日（周六/周一），需满足 arr <= cutoff

    前提：出发时间窗口均在傍晚（>=18:00），因此 arr < dep 等价于次日落地。
    到达时间缺失时放行（不因数据不完整而误过滤）。

    Args:
        dep_time: 出发时间，格式 'HH:MM'。
        arr_time: 到达时间，格式 'HH:MM'。
        cutoff:   截止时间，格式 'HH:MM'（如 '02:00'）。

    Returns:
        True 表示到达时间合规，False 表示超过截止时间。
    """
    if not arr_time:
        return True  # 到达时间缺失，不强行过滤
    dep_mins = _parse_hhmm(dep_time)
    arr_mins = _parse_hhmm(arr_time)
    if arr_mins >= dep_mins:
        # 当日到达，出发日 < 次日截止，直接放行
        return True
    # 跨午夜到次日落地，检查是否在截止时刻之前
    return arr_mins <= _parse_hhmm(cutoff)


def _compute_beat_pct(current_price: Decimal, historical_avg: Decimal) -> int:
    """当前价格击败历史均价的百分比（越高越便宜）。

    如果当前价格 >= 历史均价，返回 0（未击败）。
    """
    if current_price >= historical_avg:
        return 0
    return int((1 - float(current_price) / float(historical_avg)) * 100)


# ── 扫描器主类 ─────────────────────────────────────────────────────────────────

class WeekendRadarScanner:
    """周末低价雷达核心扫描引擎。

    封装对目的地池的批量 SearchParams 构造、调用现有 scraper、
    直飞过滤、往返价格合并等逻辑。
    """

    def __init__(self, scrapers: List[FlightScraper]) -> None:
        self.scrapers = scrapers

    async def scan_weekend(
        self,
        outbound_date: date,
        return_date: date,
        destinations: Optional[List[str]] = None,
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> List["WeekendDeal"]:
        """扫描指定周末的所有目的地，返回最优往返组合列表，按总价升序排列。

        Args:
            outbound_date: 周五出发日期。
            return_date: 周日返程日期。
            destinations: 目的地列表，None 表示全量目的地池。
            progress_callback: 可选进度回调，传入当前目的地名称。

        Returns:
            List of WeekendDeal sorted by total_price ascending.
        """
        dest_list = destinations if destinations is not None else ALL_DESTINATIONS

        results: List[WeekendDeal] = []
        for dest in dest_list:
            if progress_callback:
                progress_callback(dest)
            try:
                deal = await self._scan_one_destination(dest, outbound_date, return_date)
                if deal is not None:
                    results.append(deal)
            except Exception:
                logger.warning("扫描目的地 %s 失败，跳过", dest, exc_info=True)
            # 避免频繁请求
            await asyncio.sleep(0.5)

        results.sort(key=lambda d: d.total_price)
        return results

    async def _scan_one_destination(
        self,
        destination: str,
        outbound_date: date,
        return_date: date,
    ) -> Optional[WeekendDeal]:
        """扫描单一目的地，返回最优往返组合（None 表示无符合条件结果）。

        时间过滤规则：
        - 去程：dep_time >= 19:00（周五下班后直奔机场）
                arr_time < dep_time 时（跨午夜），到达必须在周六 02:00 前
        - 回程：dep_time 在 18:00-23:59（周日返程）
                arr_time < dep_time 时（跨午夜），到达必须在周一 02:00 前
        - 仅保留直飞（flight_no 不含 '/' 或 '+'）

        Args:
            destination: 目的地城市名。
            outbound_date: 周五出发日期。
            return_date: 周日返程日期。

        Returns:
            Best WeekendDeal or None if no suitable flights found.
        """
        if not self.scrapers:
            return None

        scraper = self.scrapers[0]

        # ── 搜索去程（单程，仅用于获取单腿价格和航班信息）─────────────────────
        try:
            outbound_params = SearchParams(
                departure_city=_ORIGIN,
                arrival_city=destination,
                departure_date=outbound_date,
            )
            outbound_prices: List[FlightPrice] = await scraper.search_flights(outbound_params)
        except Exception:
            logger.warning("去程搜索失败：%s → %s", _ORIGIN, destination, exc_info=True)
            return None

        # ── 搜索回程（单程）──────────────────────────────────────────────────
        try:
            return_params = SearchParams(
                departure_city=destination,
                arrival_city=_ORIGIN,
                departure_date=return_date,
            )
            return_prices: List[FlightPrice] = await scraper.search_flights(return_params)
        except Exception:
            logger.warning("回程搜索失败：%s → %s", destination, _ORIGIN, exc_info=True)
            return None

        # ── 去程过滤：直飞 + 周五19:00后 + 周六02:00前到达 + 城市校验 ────────────
        valid_outbound = [
            fp for fp in outbound_prices
            if _is_direct_flight(fp.flight_info.flight_no)
            and _matches_time_filter(fp.flight_info.departure_time, time_from=_OUTBOUND_FROM)
            and _arrival_before_cutoff(
                fp.flight_info.departure_time,
                fp.flight_info.arrival_time,
                _OUTBOUND_ARR_CUTOFF,
            )
            and (not fp.flight_info.departure_city or _ORIGIN in fp.flight_info.departure_city)
            and (not fp.flight_info.arrival_city or destination in fp.flight_info.arrival_city)
        ]

        # ── 回程过滤：直飞 + 周日18:00-23:59 + 周一02:00前到达 + 城市校验 ────────
        valid_return = [
            fp for fp in return_prices
            if _is_direct_flight(fp.flight_info.flight_no)
            and _matches_time_filter(
                fp.flight_info.departure_time,
                time_from=_RETURN_FROM,
                time_to=_RETURN_TO,
            )
            and _arrival_before_cutoff(
                fp.flight_info.departure_time,
                fp.flight_info.arrival_time,
                _RETURN_ARR_CUTOFF,
            )
            and (not fp.flight_info.departure_city or destination in fp.flight_info.departure_city)
            and (not fp.flight_info.arrival_city or _ORIGIN in fp.flight_info.arrival_city)
        ]

        if not valid_outbound or not valid_return:
            logger.debug(
                "目的地 %s 无符合条件航班（去程 %d 条，回程 %d 条）",
                destination, len(valid_outbound), len(valid_return),
            )
            return None

        # ── 选取最优（最低价）组合 ─────────────────────────────────────────────
        best_out = min(valid_outbound, key=lambda fp: fp.price)
        best_ret = min(valid_return, key=lambda fp: fp.price)
        total = best_out.price + best_ret.price

        logger.info(
            "目的地 %s：¥%s（去 ¥%s + 回 ¥%s）",
            destination, total, best_out.price, best_ret.price,
        )

        return WeekendDeal(
            destination=destination,
            outbound_flight=best_out.flight_info,
            return_flight=best_ret.flight_info,
            total_price=total,
            currency=best_out.currency,
            source=best_out.source,
        )
