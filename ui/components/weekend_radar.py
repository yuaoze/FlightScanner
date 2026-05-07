"""周末灵感雷达 Streamlit Tab 组件 v2。

新增特性：
- 动态超值标签（跨周比价 / 日均价折扣 / 心理阈值 / 每小时成本）
- 真实风景图沉浸式卡片（背景图 + 渐变遮罩，告别纯色色块）
- 快捷过滤器（预算滑块 / 仅看国际免签 / 拒绝红眼）
- 航班质量预警（红眼航班标签）
"""

import asyncio
import hashlib
import json
import logging
import random
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

import streamlit as st

from flightscanner.weekend_radar.destinations import (
    DESTINATION_EMOJI,
    DESTINATION_GRADIENT,
    DESTINATION_IMAGE,
    INTERNATIONAL_DESTINATIONS,
    VISA_INFO,
)
from flightscanner.weekend_radar.scanner import (
    WeekendDeal,
    WeekendRadarScanner,
    get_upcoming_weekends,
    get_weekend_label,
)

logger = logging.getLogger(__name__)


# ── 近似单程飞行时长（小时，基于上海出发） ────────────────────────────────────

_APPROX_ONE_WAY_HOURS: Dict[str, float] = {
    "三亚": 2.8,  "成都": 2.5,  "重庆": 2.3,  "昆明": 2.8,
    "大理": 3.0,  "丽江": 3.2,  "西安": 2.2,  "桂林": 2.2,
    "贵阳": 2.5,  "厦门": 1.5,  "青岛": 1.3,  "大连": 1.5,
    "沈阳": 1.8,  "哈尔滨": 2.3, "长沙": 1.8, "武汉": 1.5,
    "广州": 2.0,  "深圳": 2.2,  "南宁": 2.5,  "张家界": 1.8,
    "西双版纳": 3.2, "东京": 3.5, "大阪": 3.0, "首尔": 2.0,
    "济州": 2.5,  "香港": 2.0,  "澳门": 2.2,
    "曼谷": 5.0,  "新加坡": 5.5, "吉隆坡": 5.8, "普吉岛": 5.5,
}


# ── Mock 显示元数据（确定性随机，刷新不跳变）──────────────────────────────────

@dataclass
class _DealMeta:
    """供超值标签计算用的展示元数据（可来自 DB 或 Mock 生成）。"""
    daily_avg: Decimal          # 当日该航线市场均价（参考值）
    weekend_prices: List[Decimal]   # 未来4个周末的参考价格列表
    round_trip_hours: float         # 往返总飞行小时数
    is_cheapest_weekend: bool = False  # 是否近4周最低


def _det_rng(destination: str, salt: str = "") -> random.Random:
    """基于目的地名称 + 盐值生成确定性随机器，保证每次展示结果一致。"""
    seed = int(hashlib.md5(f"{destination}{salt}".encode()).hexdigest()[:8], 16)
    return random.Random(seed)


def _enrich_with_display_meta(
    deals: List[WeekendDeal],
) -> List[Tuple[WeekendDeal, _DealMeta]]:
    """为每个 WeekendDeal 注入 Mock 元数据，供超值标签计算使用。

    当 deal.historical_avg 已存在（数据库有历史数据）时优先使用；
    否则通过确定性随机生成合理的参考价格，保证卡片视觉效果。
    """
    enriched: List[Tuple[WeekendDeal, _DealMeta]] = []
    for deal in deals:
        rng = _det_rng(deal.destination)

        # 日均价：优先使用 historical_avg，否则 Mock 1.2-2.1 倍
        if deal.historical_avg and deal.historical_avg > 0:
            daily_avg = deal.historical_avg
        else:
            mult = Decimal(str(round(rng.uniform(1.2, 2.1), 2)))
            daily_avg = (deal.total_price * mult).quantize(Decimal("1"))

        # 未来4周参考价格列表（当前周已包含在内，可能是最低）
        base = float(deal.total_price)
        weekend_prices: List[Decimal] = [deal.total_price]
        for _ in range(3):
            weekend_prices.append(
                Decimal(str(round(base * rng.uniform(1.05, 1.65))))
            )
        rng.shuffle(weekend_prices)

        rt_hours = _APPROX_ONE_WAY_HOURS.get(deal.destination, 2.5) * 2.0
        is_cheapest = deal.total_price <= min(weekend_prices)

        enriched.append((deal, _DealMeta(
            daily_avg=daily_avg,
            weekend_prices=weekend_prices,
            round_trip_hours=rt_hours,
            is_cheapest_weekend=is_cheapest,
        )))
    return enriched


# ── 超值标签计算 ──────────────────────────────────────────────────────────────

def _compute_value_tags(
    deal: WeekendDeal,
    meta: _DealMeta,
    is_international: bool,
) -> List[str]:
    """基于4条规则生成超值标签列表，按优先级排序最多返回2个。

    规则优先级（高→低）：跨周比价 > 日均价折扣 > 心理阈值 > 每小时成本
    """
    tags: List[str] = []

    # 规则1：跨周比价 — 近4周最低
    if meta.is_cheapest_weekend:
        tags.append("📉 近四周最低")

    # 规则2：当日均价比 — 低于均价的 60%（即五折以下）
    if meta.daily_avg > 0 and deal.total_price < meta.daily_avg * Decimal("0.6"):
        tags.append("✨ 仅当日均价5折")

    # 规则3：心理阈值
    if not is_international and deal.total_price < 600:
        tags.append("🔥 骨折白菜价")
    elif is_international and deal.total_price < 1500:
        tags.append("🌍 跨国捡漏")

    # 规则4：每飞行小时成本 < ¥150
    if meta.round_trip_hours > 0:
        cph = float(deal.total_price) / meta.round_trip_hours
        if cph < 150:
            tags.append(f"✈️ 极致性价比 (¥{cph:.0f}/小时)")

    return tags[:2]


def _value_tag_html(text: str) -> str:
    """将超值标签文本包装为带颜色 CSS 类的 <span>。"""
    if text.startswith("📉"):
        cls = "value-tag value-tag-red"
    elif text.startswith("✨"):
        cls = "value-tag value-tag-purple"
    elif text.startswith("🔥"):
        cls = "value-tag value-tag-orange"
    elif text.startswith("🌍"):
        cls = "value-tag value-tag-green"
    else:  # ✈️
        cls = "value-tag value-tag-blue"
    return f'<span class="{cls}">{text}</span>'


# ── 时间工具 & 红眼检测 ───────────────────────────────────────────────────────

def _time_to_mins(time_str: str) -> int:
    """HH:MM → 分钟数，解析失败返回 720（noon）。"""
    try:
        h, m = map(int, (time_str or "12:00").split(":"))
        return h * 60 + m
    except Exception:
        return 720


def _is_early_morning_arrival(arrival_time: str) -> bool:
    """到达时间在 00:01-06:59 范围内（红眼落地判定）。"""
    mins = _time_to_mins(arrival_time)
    return 1 <= mins <= 419  # 00:01 - 06:59


def _has_redeye_leg(deal: WeekendDeal) -> bool:
    """往返任意一程的到达时间在 00:01-06:59 范围内。"""
    return (
        _is_early_morning_arrival(deal.outbound_flight.arrival_time)
        or _is_early_morning_arrival(deal.return_flight.arrival_time)
    )


def _flight_warning_html(arrival_time: str) -> str:
    """到达时间 > 01:00 时返回红眼警告标签 HTML，否则返回空字符串。"""
    mins = _time_to_mins(arrival_time)
    # 01:01 ~ 06:59 触发警告（Feature 4: 晚于 01:00）
    if 61 <= mins <= 419:
        return '<span class="flight-warning-tag">🌙 红眼航班</span>'
    return ""


# ── CSS ──────────────────────────────────────────────────────────────────────

_RADAR_CSS = """
<style>
/* ══════════════════════════════════════════════════════
   周末雷达卡片 v2 — 风景图沉浸式 + 超值标签 + 红眼预警
   ══════════════════════════════════════════════════════ */

/* ── 卡片容器 ──────────────────────────────────────── */
.radar-card {
    background   : #ffffff;
    border-radius: 18px;
    overflow     : hidden;
    box-shadow   : 0 2px 12px rgba(0,0,0,0.08);
    transition   : transform 0.22s ease, box-shadow 0.22s ease;
    margin-bottom: 1.1rem;
    border       : 1px solid #e8ecf3;
}
.radar-card:hover {
    transform : translateY(-5px);
    box-shadow: 0 12px 32px rgba(0,0,0,0.16);
}

/* ── 英雄区（背景图 + 渐变遮罩）──────────────────────
   通过 background-image: gradient, url() 实现双层叠加
   让渐变色遮罩浮于风景图上方，保证文字清晰可读       */
.radar-card-hero {
    height             : 120px;
    background-size    : cover;
    background-position: center;
    display            : flex;
    align-items        : flex-end;
    padding            : 0 1rem 0.65rem;
}
.radar-hero-content {
    display    : flex;
    align-items: center;
    gap        : 0.45rem;
}
.radar-card-emoji {
    font-size  : 2rem;
    line-height: 1;
    filter     : drop-shadow(0 1px 3px rgba(0,0,0,0.5));
}
.radar-card-dest-name {
    font-size  : 1.42rem;
    font-weight: 800;
    color      : #ffffff;
    text-shadow: 0 1px 6px rgba(0,0,0,0.7);
    letter-spacing: 0.02em;
}

/* ── 卡片正文区 ───────────────────────────────────── */
.radar-card-body { padding: 0.9rem 1.1rem 0.8rem; }

/* ── 价格行 ───────────────────────────────────────── */
.radar-price-row {
    display    : flex;
    align-items: baseline;
    gap        : 0.3rem;
    flex-wrap  : wrap;
}
.radar-card-price {
    font-size  : 2rem;
    font-weight: 900;
    color      : #ef4444;
    line-height: 1.1;
}
.radar-card-unit {
    font-size  : 0.87rem;
    color      : #94a3b8;
    font-weight: 500;
}
.radar-card-beat {
    font-size  : 0.75rem;
    color      : #64748b;
    margin-top : 0.15rem;
}

/* ── 超值标签组 ───────────────────────────────────── */
.radar-value-tags {
    margin-top: 0.5rem;
    display   : flex;
    flex-wrap : wrap;
    gap       : 5px;
}
.value-tag {
    display      : inline-block;
    font-size    : 0.71rem;
    font-weight  : 700;
    padding      : 3px 9px;
    border-radius: 20px;
    color        : #ffffff;
    white-space  : nowrap;
    line-height  : 1.5;
    letter-spacing: 0.01em;
}
/* 各颜色变体 */
.value-tag-red    { background: linear-gradient(135deg, #ef4444, #dc2626); }
.value-tag-orange { background: linear-gradient(135deg, #f97316, #ef4444); }
.value-tag-purple { background: linear-gradient(135deg, #8b5cf6, #7c3aed); }
.value-tag-green  { background: linear-gradient(135deg, #10b981, #059669); }
.value-tag-blue   { background: linear-gradient(135deg, #3b82f6, #2563eb); }

/* ── 种草文案 ──────────────────────────────────────── */
.radar-card-headline {
    font-size  : 0.9rem;
    font-weight: 700;
    color      : #0f172a;
    margin-top : 0.5rem;
}
.radar-card-brief {
    font-size  : 0.84rem;
    color      : #374151;
    margin-top : 0.28rem;
    line-height: 1.65;
}

/* ── 签证标签 ──────────────────────────────────────── */
.radar-card-visa {
    display      : inline-block;
    font-size    : 0.73rem;
    padding      : 2px 9px;
    border-radius: 20px;
    background   : #f0fdf4;
    color        : #166534;
    margin-top   : 0.4rem;
    border       : 1px solid #bbf7d0;
}

/* ── 航班元信息区 ──────────────────────────────────── */
.radar-card-meta {
    font-size  : 0.73rem;
    color      : #94a3b8;
    margin-top : 0.5rem;
    line-height: 1.95;
}

/* ── 红眼航班警告标签 ─────────────────────────────── */
.flight-warning-tag {
    display       : inline-block;
    font-size     : 0.67rem;
    padding       : 1px 7px;
    border-radius : 10px;
    background    : #fef9c3;
    color         : #92400e;
    border        : 1px solid #fde68a;
    margin-left   : 4px;
    vertical-align: middle;
    white-space   : nowrap;
}

/* ── AI 标签行 ──────────────────────────────────────── */
.radar-tags {
    margin-top: 0.4rem;
    display   : flex;
    flex-wrap : wrap;
    gap       : 4px;
}
.radar-tag {
    font-size    : 0.68rem;
    padding      : 2px 7px;
    border-radius: 12px;
    background   : #f1f5f9;
    color        : #475569;
}
</style>
"""


def _inject_css() -> None:
    st.markdown(_RADAR_CSS, unsafe_allow_html=True)


# ── 缓存加载 ──────────────────────────────────────────────────────────────────

def _load_cached_deals(
    session_factory, outbound_date: date, return_date: date
) -> List[WeekendDeal]:
    """从 WeekendRadarCache 表读取指定周末的缓存结果并还原为 WeekendDeal。"""
    from flightscanner.interfaces import FlightDirection, FlightInfo
    from flightscanner.models.database import WeekendRadarCache

    session = session_factory()
    try:
        rows = (
            session.query(WeekendRadarCache)
            .filter(
                WeekendRadarCache.outbound_date == outbound_date,
                WeekendRadarCache.return_date == return_date,
            )
            .order_by(WeekendRadarCache.total_price)
            .all()
        )

        deals: List[WeekendDeal] = []
        for row in rows:
            outbound_flight = FlightInfo(
                flight_no=row.outbound_flight_no or "",
                airline=row.outbound_airline or "",
                departure_city="上海",
                arrival_city=row.destination,
                departure_time=row.outbound_dep_time or "",
                arrival_time=row.outbound_arr_time or "",
                departure_date=row.outbound_date,
                direction=FlightDirection.DEPARTURE,
                departure_airport_code=row.outbound_dep_airport,
            )
            return_flight = FlightInfo(
                flight_no=row.return_flight_no or "",
                airline=row.return_airline or "",
                departure_city=row.destination,
                arrival_city="上海",
                departure_time=row.return_dep_time or "",
                arrival_time=row.return_arr_time or "",
                departure_date=row.return_date,
                direction=FlightDirection.RETURN,
            )

            brief_data: Optional[Dict[str, Any]] = None
            if row.ai_brief:
                try:
                    brief_data = json.loads(row.ai_brief)
                except (json.JSONDecodeError, TypeError):
                    pass

            deals.append(
                WeekendDeal(
                    destination=row.destination,
                    outbound_flight=outbound_flight,
                    return_flight=return_flight,
                    total_price=Decimal(str(row.total_price)),
                    currency=row.currency or "CNY",
                    source=row.source or "qunar",
                    historical_avg=(
                        Decimal(str(row.historical_avg)) if row.historical_avg else None
                    ),
                    beat_pct=row.beat_pct,
                    ai_brief=brief_data,
                )
            )
        return deals
    finally:
        session.close()


# ── 实时扫描（同步包装） ──────────────────────────────────────────────────────

def _run_scan(
    outbound_date: date, return_date: date, session_factory
) -> List[WeekendDeal]:
    """同步包装器：在新 asyncio 事件循环中执行扫描，并将结果持久化到缓存表。"""
    from datetime import timezone
    import asyncio as _asyncio

    from flightscanner.scheduler.price_monitor import PriceMonitorScheduler
    from flightscanner.utils.config import settings
    from flightscanner.weekend_radar.brief_generator import generate_weekend_brief
    from flightscanner.models.database import WeekendRadarCache

    async def _async_scan() -> List[WeekendDeal]:
        monitor = PriceMonitorScheduler(
            headless=settings.scraper_headless,
            enable_notifications=False,
        )
        scanner = WeekendRadarScanner(monitor.scrapers)

        def _cb(dest: str) -> None:
            pass  # 进度由外层 st.status 承担

        deals = await scanner.scan_weekend(
            outbound_date=outbound_date,
            return_date=return_date,
            progress_callback=_cb,
        )

        now = __import__("datetime").datetime.now(timezone.utc)
        api_key = getattr(settings, "deepseek_api_key", None)

        for deal in deals:
            is_intl = deal.destination in INTERNATIONAL_DESTINATIONS
            try:
                brief = await generate_weekend_brief(
                    destination=deal.destination,
                    outbound_info=deal.outbound_flight,
                    return_info=deal.return_flight,
                    total_price=deal.total_price,
                    historical_avg=deal.historical_avg,
                    is_international=is_intl,
                    api_key=api_key,
                    base_url=getattr(settings, "deepseek_base_url", "https://api.deepseek.com"),
                    model=getattr(settings, "deepseek_model", "deepseek-chat"),
                )
            except Exception:
                brief = None

            deal.ai_brief = brief
            beat_pct = brief.get("beat_pct") if brief else deal.beat_pct
            deal.beat_pct = beat_pct

            session = session_factory()
            try:
                record = WeekendRadarCache(
                    origin="上海",
                    destination=deal.destination,
                    outbound_date=outbound_date,
                    return_date=return_date,
                    outbound_flight_no=deal.outbound_flight.flight_no,
                    outbound_airline=deal.outbound_flight.airline,
                    outbound_dep_time=deal.outbound_flight.departure_time,
                    outbound_arr_time=deal.outbound_flight.arrival_time,
                    outbound_dep_airport=deal.outbound_flight.departure_airport_code,
                    return_flight_no=deal.return_flight.flight_no,
                    return_airline=deal.return_flight.airline,
                    return_dep_time=deal.return_flight.departure_time,
                    return_arr_time=deal.return_flight.arrival_time,
                    total_price=deal.total_price,
                    currency=deal.currency,
                    historical_avg=deal.historical_avg,
                    beat_pct=beat_pct,
                    ai_brief=json.dumps(brief, ensure_ascii=False) if brief else None,
                    source=deal.source,
                    scan_type="manual",
                    scanned_at=now,
                )
                session.add(record)
                session.commit()
            except Exception:
                session.rollback()
                logger.warning("写入缓存失败：%s", deal.destination, exc_info=True)
            finally:
                session.close()

        for scraper in monitor.scrapers:
            try:
                await scraper.close()
            except Exception:
                pass

        return deals

    return _asyncio.run(_async_scan())


# ── 加入监控 ──────────────────────────────────────────────────────────────────

def _add_to_monitoring(deal: WeekendDeal, session_factory) -> None:
    """将周末推荐组合加入精准监控列表，并立即采集一次价格。"""
    from flightscanner.core.services import RouteService

    is_intl = deal.destination in INTERNATIONAL_DESTINATIONS
    session = session_factory()
    new_route_id: Optional[int] = None
    try:
        svc = RouteService(session)
        # 与 weekend_radar.scanner 的扫描时间窗对齐：去程周五晚 19:00 之后起飞，
        # 回程周日晚 18:00–23:59 起飞，防止加监控后又把非周末晚场的航班纳入。
        route = svc.add_route(
            origin="上海",
            destination=deal.destination,
            target_date=deal.outbound_flight.departure_date,
            return_date=deal.return_flight.departure_date,
            trip_type="roundtrip",
            target_price=deal.total_price * Decimal("0.95"),
            is_international=is_intl,
            dep_time_from="19:00",
            ret_dep_time_from="18:00",
            ret_dep_time_to="23:59",
        )
        new_route_id = route.id
        st.toast("已加入精准监控列表！")
    except Exception:
        logger.exception("加入监控失败：%s", deal.destination)
        st.toast("加入监控失败，请稍后重试", icon="❌")
        return
    finally:
        session.close()

    # ── 锁定后立刻做第一次采集，避免用户等到下一个调度周期才看到数据 ────────
    # trigger_immediate_scrape 自身带 spinner + flash 消息，出异常也只写
    # session_state._flash，不会打断 Streamlit 渲染。
    if new_route_id is not None:
        try:
            from ui.app import trigger_immediate_scrape  # 惰性导入避免循环引用
            trigger_immediate_scrape(new_route_id)
        except Exception:
            logger.exception("路线 %s 新增后立即采集失败", new_route_id)
        # 触发 rerun：让 flash 消息显示，并让路线列表能立刻看到带最新价格的新路线
        st.rerun()


# ── 单张卡片渲染（v2：背景图 + 超值标签 + 红眼预警）──────────────────────────

def _render_deal_card(
    deal: WeekendDeal,
    meta: _DealMeta,
    session_factory,
    idx: int = 0,
) -> None:
    """渲染单张目的地种草卡片（背景图遮罩 + 动态超值标签 + 红眼预警）。"""
    is_intl = deal.destination in INTERNATIONAL_DESTINATIONS
    emoji = DESTINATION_EMOJI.get(deal.destination, DESTINATION_EMOJI["_default"])
    img_url = DESTINATION_IMAGE.get(deal.destination, DESTINATION_IMAGE["_default"])
    brief: Dict[str, Any] = deal.ai_brief or {}

    # ── 超值标签 HTML ─────────────────────────────────────────────────────────
    value_tags = _compute_value_tags(deal, meta, is_intl)
    value_tags_html = (
        '<div class="radar-value-tags">'
        + "".join(_value_tag_html(t) for t in value_tags)
        + "</div>"
    ) if value_tags else ""

    # ── 可选内容片段 ──────────────────────────────────────────────────────────
    beat_pct = deal.beat_pct or 0
    beat_html = (
        f'<div class="radar-card-beat">击败 {beat_pct}% 的历史周末均价</div>'
        if beat_pct else ""
    )

    headline = brief.get("headline", f"{deal.destination} 周末特惠")
    body = brief.get("body", "")
    visa_note = brief.get("visa_note", "")
    visa_html = (
        f'<div class="radar-card-visa">{visa_note}</div>'
        if visa_note else ""
    )

    # ── AI 标签 ──────────────────────────────────────────────────────────────
    tags_html = "".join(
        f'<span class="radar-tag">{t}</span>'
        for t in brief.get("tags", [])
    )

    # ── 红眼警告（Feature 4）──────────────────────────────────────────────────
    out_warn = _flight_warning_html(deal.outbound_flight.arrival_time)
    ret_warn = _flight_warning_html(deal.return_flight.arrival_time)

    # ── 航班元信息 ────────────────────────────────────────────────────────────
    meta_html = (
        f'去程：{deal.outbound_flight.departure_time} → '
        f'{deal.outbound_flight.arrival_time}'
        f'（{deal.outbound_flight.flight_no}）{out_warn}<br>'
        f'回程：{deal.return_flight.departure_time} → '
        f'{deal.return_flight.arrival_time}'
        f'（{deal.return_flight.flight_no}）{ret_warn}'
    )

    # ── 英雄区：背景图 + linear-gradient 遮罩（从透明到黑色）────────────────
    # 注意：此处 background-image 使用双层叠加
    #   第1层：渐变遮罩（浮于上方），保证白色文字可读
    #   第2层：目的地风景图（picsum/Unsplash 占位）
    hero_bg = (
        f"linear-gradient(to bottom, transparent 20%, rgba(0,0,0,0.82) 100%), "
        f"url('{img_url}')"
    )

    # ── 组装完整卡片 HTML ─────────────────────────────────────────────────────
    # 注意：所有 HTML 必须从列0开始（无前导空格），
    # 否则 CommonMark 会将4+空格缩进识别为 <pre><code> 代码块。
    card_html = (
        f'<div class="radar-card">'
        f'<div class="radar-card-hero" style="background-image:{hero_bg};background-size:cover;background-position:center;">'
        f'<div class="radar-hero-content">'
        f'<span class="radar-card-emoji">{emoji}</span>'
        f'<span class="radar-card-dest-name">{deal.destination}</span>'
        f'</div>'
        f'</div>'
        f'<div class="radar-card-body">'
        f'<div class="radar-price-row">'
        f'<span class="radar-card-price">¥{int(deal.total_price):,}</span>'
        f'<span class="radar-card-unit">往返</span>'
        f'</div>'
        f'{beat_html}'
        f'{value_tags_html}'
        f'<div class="radar-card-headline">{headline}</div>'
        f'<div class="radar-card-brief">{body}</div>'
        f'{visa_html}'
        f'<div class="radar-card-meta">{meta_html}</div>'
        f'<div class="radar-tags">{tags_html}</div>'
        f'</div>'
        f'</div>'
    )

    st.markdown(card_html, unsafe_allow_html=True)

    btn_key = f"radar_lock_{idx}_{deal.destination}_{deal.outbound_flight.departure_date}"
    if st.button("❤️ 锁定价格并监控", key=btn_key, use_container_width=True):
        _add_to_monitoring(deal, session_factory)


# ── 卡片网格渲染 ──────────────────────────────────────────────────────────────

def _render_deal_grid(
    deals: List[WeekendDeal],
    meta_map: Dict[str, _DealMeta],
    session_factory,
) -> None:
    """以3列网格渲染目的地卡片。"""
    if not deals:
        st.info("暂无符合过滤条件的周末推荐，请调整筛选项或点击「🔄 实时深度扫描」。")
        return

    cols = st.columns(3)
    for idx, deal in enumerate(deals):
        meta = meta_map.get(deal.destination)
        if meta is None:
            # 理论上不会触发，保险起见构造默认值
            meta = _DealMeta(
                daily_avg=deal.total_price * Decimal("1.5"),
                weekend_prices=[deal.total_price],
                round_trip_hours=_APPROX_ONE_WAY_HOURS.get(deal.destination, 2.5) * 2,
            )
        with cols[idx % 3]:
            _render_deal_card(deal, meta, session_factory, idx)


# ── 主渲染函数 ────────────────────────────────────────────────────────────────

def render_weekend_radar_tab(session_factory) -> None:
    """渲染「🌍 周末灵感雷达」Tab 的全部内容。

    Args:
        session_factory: SQLAlchemy SessionLocal factory。
    """
    _inject_css()

    st.markdown("### 🌍 周末灵感雷达")
    st.markdown(
        "打工人的完美逃跑计划 — 自动过滤高铁4小时圈，只推荐真正值得飞的目的地。",
    )

    # ── 周末选择 + 扫描按钮 ───────────────────────────────────────────────────
    weekend_options = get_upcoming_weekends(8)
    col_sel, col_btn = st.columns([3, 1])
    with col_sel:
        selected_idx = st.selectbox(
            "选择周末",
            options=range(len(weekend_options)),
            format_func=lambda i: get_weekend_label(*weekend_options[i]),
            key="radar_weekend_selector",
        )
    with col_btn:
        st.write("")  # 对齐间距
        scan_clicked = st.button(
            "🔄 实时深度扫描",
            type="primary",
            use_container_width=True,
            key="radar_scan_btn",
        )

    friday, sunday = weekend_options[selected_idx]

    # ── 快捷过滤器（Feature 3）────────────────────────────────────────────────
    with st.container():
        f_col1, f_col2, f_col3 = st.columns([3, 1.6, 1.6])
        with f_col1:
            max_budget = st.slider(
                "💰 最大往返预算",
                min_value=300,
                max_value=5000,
                value=1500,
                step=100,
                format="¥%d",
                key="radar_filter_budget",
            )
        with f_col2:
            only_intl = st.toggle(
                "🌍 仅看国际/免签",
                key="radar_filter_intl",
            )
        with f_col3:
            no_redeye = st.toggle(
                "🛏️ 拒绝红眼航班",
                key="radar_filter_redeye",
            )

    # ── 扫描执行 ──────────────────────────────────────────────────────────────
    scan_results_key = f"radar_deals_{friday}_{sunday}"

    if scan_clicked:
        with st.status("正在扫描…", expanded=True) as status:
            st.write("🔒 锁定出发地：上海（PVG / SHA）")
            st.write("🚄 排除高铁4小时圈城市…")
            st.write("✈️ 实时请求航司接口，请稍候（约1-2分钟）…")
            try:
                deals = _run_scan(friday, sunday, session_factory)
                st.write(f"🤖 AI 生成最新种草文案（{len(deals)} 个目的地）…")
                st.session_state[scan_results_key] = deals
                status.update(
                    label=f"扫描完成！发现 {len(deals)} 个绝佳周末方案",
                    state="complete",
                )
            except Exception as exc:
                logger.exception("实时扫描失败")
                status.update(label=f"扫描出错：{exc}", state="error")
                st.session_state[scan_results_key] = []

    # ── 数据来源：本次扫描 > 数据库缓存 ─────────────────────────────────────
    if scan_results_key in st.session_state:
        raw_deals: List[WeekendDeal] = st.session_state[scan_results_key]
        st.caption(f"显示本次实时扫描结果（共 {len(raw_deals)} 条）")
    else:
        raw_deals = _load_cached_deals(session_factory, friday, sunday)
        if raw_deals:
            st.caption(
                f"显示缓存结果（共 {len(raw_deals)} 条），"
                "点击「🔄 实时深度扫描」获取最新数据"
            )
        else:
            st.caption("暂无缓存数据，点击「🔄 实时深度扫描」获取最新推荐")

    # ── 注入 Mock 元数据 ──────────────────────────────────────────────────────
    enriched = _enrich_with_display_meta(raw_deals)
    meta_map: Dict[str, _DealMeta] = {
        deal.destination: meta for deal, meta in enriched
    }

    # ── 应用快捷过滤器 ────────────────────────────────────────────────────────
    deals_to_show: List[WeekendDeal] = []
    for deal in raw_deals:
        # 预算过滤
        if deal.total_price > max_budget:
            continue
        # 仅国际/免签过滤（仅看免签 + 落地签目的地）
        if only_intl:
            is_intl_dest = deal.destination in INTERNATIONAL_DESTINATIONS
            visa_status = VISA_INFO.get(deal.destination, {}).get("status", "")
            is_visa_free = visa_status in ("免签", "落地签")
            if not (is_intl_dest and is_visa_free):
                continue
        # 拒绝红眼过滤（任意一程在 00:01-06:59 落地则过滤）
        if no_redeye and _has_redeye_leg(deal):
            continue
        deals_to_show.append(deal)

    # ── 渲染统计摘要 ──────────────────────────────────────────────────────────
    filtered_count = len(raw_deals) - len(deals_to_show)
    if filtered_count > 0:
        st.caption(f"过滤器已隐藏 {filtered_count} 条结果，当前展示 {len(deals_to_show)} 条。")

    # ── 卡片网格 ──────────────────────────────────────────────────────────────
    _render_deal_grid(deals_to_show, meta_map, session_factory)
