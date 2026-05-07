"""Unit tests for the Weekend Radar module."""

import asyncio
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from flightscanner.weekend_radar.destinations import (
    HSR_EXCLUSION_FROM_SHANGHAI,
    ALL_DESTINATIONS,
    INTERNATIONAL_DESTINATIONS,
)
from flightscanner.weekend_radar.scanner import (
    WeekendDeal,
    WeekendRadarScanner,
    get_upcoming_weekends,
    get_weekend_label,
    _is_direct_flight,
    _arrival_before_cutoff,
)
from flightscanner.weekend_radar.brief_generator import (
    generate_weekend_brief,
    _enforce_actual_price,
)
from flightscanner.interfaces import FlightDirection, FlightInfo, FlightPrice, SearchParams


# ── 高铁排除逻辑 ──────────────────────────────────────────────────────────────

class TestHsrExclusionLogic:
    def test_shanghai_nearby_excluded(self):
        """上海周边高铁城市应被排除出目的地池。"""
        for city in ["杭州", "南京", "苏州", "合肥"]:
            assert city not in ALL_DESTINATIONS, f"{city} 应被高铁排除"

    def test_suitable_destinations_included(self):
        """合适的远途城市应保留在目的地池。"""
        for city in ["三亚", "成都", "重庆", "哈尔滨"]:
            assert city in ALL_DESTINATIONS, f"{city} 应在目的地池中"

    def test_international_not_excluded(self):
        """国际目的地不应被高铁排除。"""
        for city in INTERNATIONAL_DESTINATIONS:
            assert city in ALL_DESTINATIONS, f"国际目的地 {city} 应保留"


# ── 周末日期工具函数 ───────────────────────────────────────────────────────────

class TestGetUpcomingWeekends:
    def test_returns_n_weekends(self):
        weekends = get_upcoming_weekends(4)
        assert len(weekends) == 4

    def test_each_pair_is_friday_sunday(self):
        weekends = get_upcoming_weekends(4)
        for friday, sunday in weekends:
            # 周五 weekday() == 4，周日 weekday() == 6
            assert friday.weekday() == 4, f"{friday} 应为周五"
            assert sunday.weekday() == 6, f"{sunday} 应为周日"

    def test_sunday_is_two_days_after_friday(self):
        weekends = get_upcoming_weekends(2)
        for friday, sunday in weekends:
            assert (sunday - friday).days == 2

    def test_weekends_are_consecutive(self):
        weekends = get_upcoming_weekends(3)
        for i in range(1, len(weekends)):
            prev_friday, _ = weekends[i - 1]
            curr_friday, _ = weekends[i]
            assert (curr_friday - prev_friday).days == 7, "连续周末应相差7天"


class TestGetWeekendLabel:
    def test_label_contains_dates(self):
        friday = date(2026, 4, 10)
        sunday = date(2026, 4, 12)
        label = get_weekend_label(friday, sunday)
        assert "4/10" in label or "4/12" in label


# ── 直飞过滤逻辑 ──────────────────────────────────────────────────────────────

class TestDirectFlightFilter:
    def test_direct_flight_passes(self):
        assert _is_direct_flight("CA1234") is True
        assert _is_direct_flight("MU5678") is True

    def test_codeshare_slash_filtered(self):
        assert _is_direct_flight("CA1234/MU5678") is False

    def test_plus_filtered(self):
        assert _is_direct_flight("CA1234+MU5678") is False


# ── 到达时间截止过滤 ────────────────────────────────────────────────────────────

class TestArrivalBeforeCutoff:
    """_arrival_before_cutoff 的边界条件覆盖。

    前提：出发时间均 >= 18:00，因此 arr < dep 等价于跨午夜次日到达。
    截止时间统一使用 '02:00'（周六/周一凌晨 2 点）。
    """

    CUTOFF = "02:00"

    # ── 当日到达（arr >= dep），无论到达多晚都放行 ────────────────────────────
    def test_same_day_arrival_passes(self):
        """周五 19:00 出发，当晚 21:30 到达 → 周五落地，放行。"""
        assert _arrival_before_cutoff("19:00", "21:30", self.CUTOFF) is True

    def test_same_day_late_arrival_passes(self):
        """周五 22:00 出发，当晚 23:50 到达 → 周五落地，放行。"""
        assert _arrival_before_cutoff("22:00", "23:50", self.CUTOFF) is True

    # ── 跨午夜到达，在截止前 ─────────────────────────────────────────────────
    def test_cross_midnight_before_cutoff_passes(self):
        """周五 23:00 出发，次日 01:30 到达 → 周六 01:30 < 02:00，放行。"""
        assert _arrival_before_cutoff("23:00", "01:30", self.CUTOFF) is True

    def test_cross_midnight_exactly_at_cutoff_passes(self):
        """周五 22:30 出发，次日 02:00 到达 → 恰好等于截止时间，放行。"""
        assert _arrival_before_cutoff("22:30", "02:00", self.CUTOFF) is True

    # ── 跨午夜到达，超过截止 ─────────────────────────────────────────────────
    def test_cross_midnight_after_cutoff_blocked(self):
        """周五 21:00 出发，次日 03:00 到达 → 周六 03:00 > 02:00，过滤。"""
        assert _arrival_before_cutoff("21:00", "03:00", self.CUTOFF) is False

    def test_cross_midnight_just_over_cutoff_blocked(self):
        """周五 22:30 出发，次日 02:01 到达 → 周六 02:01 > 02:00，过滤。"""
        assert _arrival_before_cutoff("22:30", "02:01", self.CUTOFF) is False

    def test_long_haul_blocked(self):
        """周五 19:00 出发 6 小时长途，次日 01:00（实际 6 小时后=01:00）→ 01:00 < 02:00，放行。"""
        assert _arrival_before_cutoff("19:00", "01:00", self.CUTOFF) is True

    def test_very_late_arrival_blocked(self):
        """周日 23:30 出发，次日 06:00 到达 → 周一 06:00 > 02:00，过滤。"""
        assert _arrival_before_cutoff("23:30", "06:00", self.CUTOFF) is False

    # ── 边界：到达时间缺失 ──────────────────────────────────────────────────
    def test_empty_arrival_time_passes(self):
        """到达时间为空字符串时，不因数据缺失误过滤。"""
        assert _arrival_before_cutoff("22:00", "", self.CUTOFF) is True


# ── AI 文案生成降级 ────────────────────────────────────────────────────────────

class TestBriefGeneratorFallback:
    """API key 缺失时应使用规则引擎降级。"""

    def _make_flight_info(self, dep_city: str, arr_city: str, dep_time: str) -> FlightInfo:
        return FlightInfo(
            flight_no="CA1234",
            airline="中国国航",
            departure_city=dep_city,
            arrival_city=arr_city,
            departure_time=dep_time,
            arrival_time="22:00",
            departure_date=date(2026, 4, 10),
            direction=FlightDirection.DEPARTURE,
        )

    def test_fallback_returns_dict_without_api_key(self):
        outbound = self._make_flight_info("上海", "三亚", "19:30")
        ret = self._make_flight_info("三亚", "上海", "18:00")

        result = asyncio.run(
            generate_weekend_brief(
                destination="三亚",
                outbound_info=outbound,
                return_info=ret,
                total_price=Decimal("1200"),
                historical_avg=Decimal("1500"),
                is_international=False,
                api_key=None,
            )
        )
        assert isinstance(result, dict)
        assert "headline" in result
        assert "body" in result
        assert "tags" in result

    def test_fallback_no_visa_note_for_domestic(self):
        outbound = self._make_flight_info("上海", "三亚", "19:30")
        ret = self._make_flight_info("三亚", "上海", "18:00")

        result = asyncio.run(
            generate_weekend_brief(
                destination="三亚",
                outbound_info=outbound,
                return_info=ret,
                total_price=Decimal("1200"),
                historical_avg=None,
                is_international=False,
                api_key=None,
            )
        )
        assert result.get("visa_note") is None

    def test_fallback_includes_visa_note_for_international(self):
        outbound = self._make_flight_info("上海", "香港", "20:00")
        ret = self._make_flight_info("香港", "上海", "19:00")

        result = asyncio.run(
            generate_weekend_brief(
                destination="香港",
                outbound_info=outbound,
                return_info=ret,
                total_price=Decimal("900"),
                historical_avg=None,
                is_international=True,
                api_key=None,
            )
        )
        assert result.get("visa_note") is not None


class TestEnforceActualPrice:
    """_enforce_actual_price 应将标题中任何 ¥NNN 替换为实际价格。"""

    def test_replaces_fabricated_price(self):
        brief = {"headline": "¥599 武汉周末说走就走！", "body": "文案", "tags": []}
        fixed = _enforce_actual_price(brief, Decimal("783"))
        assert "¥783" in fixed["headline"]
        assert "¥599" not in fixed["headline"]

    def test_replaces_formatted_price_with_comma(self):
        brief = {"headline": "¥1,200 东京周末游！", "body": "文案", "tags": []}
        fixed = _enforce_actual_price(brief, Decimal("1500"))
        assert "¥1,500" in fixed["headline"]
        assert "¥1,200" not in fixed["headline"]

    def test_no_price_in_headline_unchanged(self):
        brief = {"headline": "武汉过早+夜游黄鹤楼！", "body": "文案", "tags": []}
        fixed = _enforce_actual_price(brief, Decimal("783"))
        assert fixed["headline"] == "武汉过早+夜游黄鹤楼！"

    def test_correct_price_passes_through(self):
        brief = {"headline": "¥783 武汉周末！", "body": "文案", "tags": []}
        fixed = _enforce_actual_price(brief, Decimal("783"))
        assert fixed["headline"] == "¥783 武汉周末！"
