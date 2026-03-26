"""Overview component — flat-design route cards and stat metrics.

This module renders the summary stat cards and the scrollable list of
monitored route cards.  Each route card is an expander that shows a
concise summary when collapsed and reveals action controls, metadata,
and price trend charts when expanded.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

import streamlit as st

from flightscanner.core.services import RouteService, RouteWithLatestPrice
from flightscanner.interfaces import FlightPrice
from flightscanner.utils.city_codes import get_airport_name
from ui.components.charts import _source_label, _to_beijing, render_price_trend_chart
from ui.components.ai_brief import render_ai_brief


# ── Arrival-time formatter with overnight (+N) marker ─────────────────────────

def _fmt_arrival(
    dep_time: Optional[str],
    arr_time: Optional[str],
    dep_date: Optional[date] = None,
    arrival_date: Optional[date] = None,
) -> str:
    """Format arrival time, appending a +N day marker for cross-day flights.

    Uses the actual arrival_date when provided for precise multi-day detection
    (e.g. +2 for long-haul flights).  Falls back to HH:MM comparison (+1 max)
    when dates are unavailable.

    Args:
        dep_time:     Departure time string "HH:MM" or None.
        arr_time:     Arrival time string "HH:MM" or None.
        dep_date:     Departure date or None.
        arrival_date: Arrival date or None.

    Returns:
        Formatted string such as "06:30 +2" or "14:55".
    """
    if not arr_time:
        return ""
    if dep_date is not None and arrival_date is not None:
        try:
            delta = (arrival_date - dep_date).days
            if delta > 0:
                return f"{arr_time} +{delta}"
            return arr_time
        except Exception:
            pass
    if dep_time and arr_time < dep_time:
        return f"{arr_time} +1"
    return arr_time


# ── Helpers shared by top-10 table and per-platform summary ───────────────────

def _collect_latest_batch_records(
    price_history: List[FlightPrice],
) -> Tuple[Dict[str, str], List[FlightPrice]]:
    """Return the latest batch_id per source and all records from those batches.

    Args:
        price_history: Full 30-day price history for a route.

    Returns:
        Tuple of (latest_batch dict, list of FlightPrice from those batches).
        Falls back to timestamp-based grouping when batch_id is absent.
    """
    # Step 1: find latest batch_id per source
    latest_batch: Dict[str, str] = {}
    for fp in price_history:
        bid = fp.batch_id
        if bid is None:
            continue
        if fp.source not in latest_batch or bid > latest_batch[fp.source]:
            latest_batch[fp.source] = bid

    if latest_batch:
        records = [
            fp for fp in price_history
            if fp.batch_id is not None and fp.batch_id == latest_batch.get(fp.source)
        ]
        return latest_batch, records

    # Fallback: group by latest scraped_at per source
    latest_time: Dict[str, object] = {}
    for fp in price_history:
        if fp.source not in latest_time or fp.scraped_at > latest_time[fp.source]:
            latest_time[fp.source] = fp.scraped_at
    records = [
        fp for fp in price_history
        if fp.scraped_at == latest_time.get(fp.source)
    ]
    return {}, records


# ── Per-platform price summary (inside an expanded card) ──────────────────────

def _render_source_price_summary(
    price_history: List[FlightPrice],
    target_price: Decimal,
) -> None:
    """Render the latest minimum price per scraper platform as metric tiles.

    Args:
        price_history: Price records for this route (30-day window).
        target_price:  Alert threshold for delta colour-coding.
    """
    if not price_history:
        return

    _, batch_records = _collect_latest_batch_records(price_history)

    # Keep the minimum-price record per source from the latest batch
    latest: Dict[str, FlightPrice] = {}
    for fp in batch_records:
        src = fp.source
        if src not in latest or fp.price < latest[src].price:
            latest[src] = fp

    if not latest:
        return

    sources = sorted(latest.keys())
    cols = st.columns(len(sources))
    for col, src in zip(cols, sources):
        fp = latest[src]
        price_val = float(fp.price)
        diff = price_val - float(target_price)
        delta_str = f"{'↓' if diff < 0 else '↑'}¥{abs(diff):.0f} 相比目标"
        with col:
            st.metric(
                label=f"{_source_label(src)} 最新价",
                value=f"¥{price_val:.0f}",
                delta=delta_str,
                delta_color="inverse",
            )


# ── Top stat cards ─────────────────────────────────────────────────────────────

def render_overview_cards(routes: List[RouteWithLatestPrice]) -> None:
    """Render four summary stat cards at the top of the dashboard.

    Args:
        routes: All routes returned by RouteService.get_all_routes().
    """
    total     = len(routes)
    active    = sum(1 for r in routes if r.is_active)
    upcoming  = sum(1 for r in routes if r.is_active and r.target_date >= date.today())
    on_target = sum(1 for r in routes if r.latest_price and r.latest_price <= r.target_price)

    st.markdown(
        f"""
        <div class="fs-stat-grid">
            <div class="fs-stat-card blue">
                <span class="fs-stat-icon">📊</span>
                <div class="fs-stat-value">{total}</div>
                <div class="fs-stat-label">总路线数</div>
            </div>
            <div class="fs-stat-card green">
                <span class="fs-stat-icon">📡</span>
                <div class="fs-stat-value">{active}</div>
                <div class="fs-stat-label">活跃监控</div>
            </div>
            <div class="fs-stat-card amber">
                <span class="fs-stat-icon">🎯</span>
                <div class="fs-stat-value">{on_target}</div>
                <div class="fs-stat-label">达到目标价</div>
            </div>
            <div class="fs-stat-card purple">
                <span class="fs-stat-icon">📅</span>
                <div class="fs-stat-value">{upcoming}</div>
                <div class="fs-stat-label">即将出行</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ── Next-check countdown helper ────────────────────────────────────────────────

def _next_check_countdown(route: "RouteWithLatestPrice") -> str:
    """Return a short countdown string for the route's next scheduled check.

    Only meaningful for active routes.  Returns empty string for paused routes.

    Args:
        route: Route data including latest_scraped_at and scrape_interval.

    Returns:
        String like "⏱ 2h 15m 后检查", "⏱ 45m 后检查", "⏱ 即将检查", or
        "⏱ 等待首次采集".  Empty string when route is not active.
    """
    if not route.is_active:
        return ""
    if not route.latest_scraped_at:
        return "⏱ 等待首次采集"
    last = route.latest_scraped_at
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    delta_secs = (
        last + timedelta(hours=route.scrape_interval) - datetime.now(timezone.utc)
    ).total_seconds()
    if delta_secs <= 0:
        return "⏱ 即将检查"
    total_mins = int(delta_secs / 60)
    h, m = divmod(total_mins, 60)
    return f"⏱ {h}h {m:02d}m 后检查" if h > 0 else f"⏱ {m}m 后检查"


# ── Route list ─────────────────────────────────────────────────────────────────

def render_route_list(
    routes: List[RouteWithLatestPrice],
    route_service: RouteService,
) -> None:
    """Render the monitored routes as flat expandable cards.

    Args:
        routes:        Routes from RouteService.get_all_routes().
        route_service: Service instance for performing route actions.
    """
    # ── Section header ─────────────────────────────────────────────────
    active_count = sum(1 for r in routes if r.is_active)
    st.markdown(
        f"""
        <div class="fs-section-header">
            <span class="fs-section-title">监控路线</span>
            <div class="fs-section-line"></div>
            <span class="fs-section-count">{active_count} 活跃 / {len(routes)} 条</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if not routes:
        st.markdown(
            '<div style="text-align:center;padding:4rem 0;color:#94a3b8;">'
            '<div style="font-size:3rem;margin-bottom:1rem;">✈️</div>'
            '<div style="font-size:1rem;font-weight:600;color:#475569;margin-bottom:0.5rem;">暂无监控路线</div>'
            '<div style="font-size:0.875rem;">点击右上角「＋ 添加监控」开始添加</div>'
            "</div>",
            unsafe_allow_html=True,
        )
        return

    for route in routes:
        _render_route_card(route, route_service)


def _render_route_card(
    route: RouteWithLatestPrice,
    route_service: RouteService,
) -> None:
    """Render a single route as a flat expandable card.

    Args:
        route:         Route data with latest price information.
        route_service: Service for performing route actions.
    """
    # ── Build expander label ──────────────────────────────────────────
    status = "✅" if route.is_active else "⏸️"

    badges = ""
    if route.is_international:
        badges += "🌐 "
    if route.trip_type == "roundtrip":
        badges += "往返  "

    # Date display with days-until-travel countdown
    days_left = (route.target_date - date.today()).days
    if route.return_date:
        date_info = f"{route.target_date} ↔ {route.return_date}"
    else:
        date_info = str(route.target_date)
    if 0 < days_left <= 60:
        date_info += f"（{days_left}天后）"

    # Price display
    if route.latest_price:
        pv = float(route.latest_price)
        tv = float(route.target_price)
        if route.latest_price <= route.target_price:
            price_info = f"¥{pv:.0f}  🎯 已达目标 ¥{tv:.0f}"
        else:
            diff = pv - tv
            price_info = f"¥{pv:.0f}  /  目标 ¥{tv:.0f}  (+¥{diff:.0f})"
    else:
        price_info = f"暂无  /  目标 ¥{float(route.target_price):.0f}"

    label = (
        f"{status}  {badges}"
        f"**{route.origin} → {route.destination}**"
        f"  ·  {date_info}"
        f"  ·  {price_info}"
    )
    countdown = _next_check_countdown(route)
    if countdown:
        label += f"  ·  {countdown}"

    # ── Expandable card ───────────────────────────────────────────────
    with st.expander(label, expanded=False):

        # Action buttons
        b_toggle, b_scrape, b_delete, b_settings = st.columns([1, 1, 1, 5])

        with b_toggle:
            if st.button(
                "⏸️" if route.is_active else "▶️",
                key=f"toggle_{route.id}",
                help="暂停监控" if route.is_active else "恢复监控",
            ):
                route_service.toggle_route_status(route.id)
                st.rerun()

        with b_scrape:
            if st.button(
                "🔄",
                key=f"scrape_{route.id}",
                help="立即采集最新价格",
            ):
                st.session_state[f"trigger_scrape_{route.id}"] = True
                st.rerun()

        with b_delete:
            if st.button(
                "🗑️",
                key=f"delete_{route.id}",
                help="删除此路线",
            ):
                route_service.delete_route(route.id)
                st.rerun()

        with b_settings:
            with st.popover(
                f"⚙️ 间隔 {route.scrape_interval}h",
                use_container_width=False,
            ):
                new_interval = st.select_slider(
                    "采集间隔（小时）",
                    options=[1, 2, 3, 4, 6, 8, 12, 24],
                    value=route.scrape_interval,
                    key=f"interval_{route.id}",
                )
                if st.button("保存", key=f"save_interval_{route.id}"):
                    route_service.update_route_interval(route.id, new_interval)
                    # 重新调度所有路线，使新的采集间隔生效
                    from ui.app import _get_monitor
                    _get_monitor().reschedule_all_routes()
                    st.success(f"采集间隔已更新为 {new_interval} 小时")
                    st.rerun()

        # Metadata
        meta = [f"创建 {_to_beijing(route.created_at).strftime('%Y-%m-%d')}"]
        meta.append(f"{route.price_count} 条记录")
        if route.latest_scraped_at:
            meta.append(
                f"最后检查 {_to_beijing(route.latest_scraped_at).strftime('%m-%d %H:%M')}"
            )

        # ── 机场/时间段过滤标注 ────────────────────────────────────────────
        if route.dep_airport_code or route.arr_airport_code:
            airport_hint = []
            if route.dep_airport_code:
                airport_hint.append(
                    f"出发 {get_airport_name(route.dep_airport_code)}（{route.dep_airport_code}）"
                )
            if route.arr_airport_code:
                airport_hint.append(
                    f"到达 {get_airport_name(route.arr_airport_code)}（{route.arr_airport_code}）"
                )
            meta.append("  ".join(airport_hint))

        if route.dep_time_from or route.dep_time_to or route.arr_time_from or route.arr_time_to:
            time_hints = []
            if route.dep_time_from or route.dep_time_to:
                time_hints.append(
                    f"起飞 {route.dep_time_from or '00:00'}–{route.dep_time_to or '23:59'}"
                )
            if route.arr_time_from or route.arr_time_to:
                time_hints.append(
                    f"降落 {route.arr_time_from or '00:00'}–{route.arr_time_to or '23:59'}"
                )
            meta.append("  ".join(time_hints))

        # ── 下次采集倒计时 ──────────────────────────────────────────────
        if countdown:
            meta.append(countdown)

        st.caption("  ·  ".join(meta))

        # ── Price-to-target progress bar ───────────────────────────────
        if route.latest_price:
            pv = float(route.latest_price)
            tv = float(route.target_price)
            # fill = how far along toward the target (capped at 100%)
            # fill 100% means at or below target; lower fill = farther above
            if pv <= tv:
                fill_pct  = 100
                bar_color = "linear-gradient(90deg,#34d399,#10b981)"
                bar_label = f"✅ 已达目标价  低 ¥{tv - pv:.0f}"
            else:
                # when price = 2× target → fill = 0%; price = target → fill = 100%
                ratio     = max(0.0, min(1.0, tv / pv))
                fill_pct  = round(ratio * 100)
                diff_pct  = (pv - tv) / tv * 100
                if diff_pct <= 20:
                    bar_color = "linear-gradient(90deg,#fcd34d,#f59e0b)"
                else:
                    bar_color = "linear-gradient(90deg,#fca5a5,#ef4444)"
                bar_label = f"高于目标 {diff_pct:.1f}%  (¥{pv - tv:.0f})"

            st.markdown(
                f"""
                <div style="margin:0.6rem 0 0.25rem;">
                    <div style="display:flex;justify-content:space-between;
                                font-size:0.72rem;color:#94a3b8;margin-bottom:4px;">
                        <span>当前 ¥{pv:.0f}</span>
                        <span style="font-weight:600;color:#475569;">{bar_label}</span>
                        <span>目标 ¥{tv:.0f}</span>
                    </div>
                    <div class="fs-price-bar-wrap">
                        <div class="fs-price-bar-fill"
                             style="width:{fill_pct}%;background:{bar_color};">
                        </div>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        # Price charts
        st.divider()
        _render_price_section(route, route_service)


def _render_price_section(
    route: RouteWithLatestPrice,
    route_service: RouteService,
) -> None:
    """Render the price trend section inside an expanded route card.

    For round-trip routes, shows the combined total price trend and the latest
    outbound + return flight details.  Each stored FlightPrice record for a
    round-trip route already contains both legs (flight_info = outbound,
    return_flight_info = return leg, price = combined total).

    Args:
        route:         Route data.
        route_service: Service for loading price history.
    """
    if route.price_count == 0:
        st.markdown(
            '<div style="text-align:center;padding:1.5rem 0;color:#94a3b8;font-size:0.85rem;">'
            "暂无价格记录，点击 🔄 立即采集，或等待定时任务自动采集。"
            "</div>",
            unsafe_allow_html=True,
        )
        return

    history = route_service.get_route_price_history(route.id, days=30)

    if route.trip_type == "roundtrip":
        # ── 往返程：combined records (price = 往返总价) ─────────────────
        st.markdown(
            f"**往返总价** {route.origin} ↔ {route.destination}"
            f"　去程 {route.target_date}　回程 {route.return_date}"
        )
        _render_source_price_summary(history, route.target_price)
        render_price_trend_chart(
            history,
            route.target_price,
            f"{route.origin} ↔ {route.destination} 往返",
            is_roundtrip=True,
            route_id=route.id,
        )

        # Show latest combined flight detail (cheapest from latest batch)
        _, batch_records = _collect_latest_batch_records(history)
        if batch_records:
            best_fp = min(batch_records, key=lambda fp: fp.price)
            out = best_fp.flight_info
            ret = best_fp.return_flight_info
            if out and ret:
                out_arr = _fmt_arrival(out.departure_time, out.arrival_time, out.departure_date, out.arrival_date)
                ret_arr = _fmt_arrival(ret.departure_time, ret.arrival_time, ret.departure_date, ret.arrival_date)
                st.caption(
                    f"去程：{out.flight_no} {out.airline}  "
                    f"{out.departure_time}→{out_arr}  "
                    f"({out.departure_date})"
                    f"　｜　"
                    f"回程：{ret.flight_no} {ret.airline}  "
                    f"{ret.departure_time}→{ret_arr}  "
                    f"({ret.departure_date})"
                )
    else:
        _render_source_price_summary(history, route.target_price)
        render_price_trend_chart(
            history,
            route.target_price,
            f"{route.origin} → {route.destination}",
            route_id=route.id,
        )

    # ── AI 价格简报（按需生成，每日缓存）────────────────────────────────────
    st.divider()
    render_ai_brief(route, history)
