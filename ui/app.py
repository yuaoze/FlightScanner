"""Main Streamlit application for FlightScanner.

Modern flat-design dashboard with inline route cards and a modal dialog
for adding new monitoring routes.
"""

import asyncio
from datetime import date, timedelta
from decimal import Decimal

import streamlit as st

from flightscanner.core.services import RouteService
from flightscanner.utils.city_codes import (
    ALL_CITIES_LIST,
    DOMESTIC_AIRPORT_MAP,
    is_international_route,
)
from ui.utils.db import get_session, get_session_local
from ui.components.overview import render_overview_cards, render_route_list


# ── Background scheduler singleton ────────────────────────────────────────────

@st.cache_resource
def _get_monitor():
    """Create and start the background scheduler (once per process)."""
    from flightscanner.scheduler.price_monitor import PriceMonitorScheduler
    from flightscanner.utils.config import settings
    m = PriceMonitorScheduler(
        headless=settings.scraper_headless,
        enable_notifications=True,
    )
    m.start()
    return m


# ── Immediate scrape helper ────────────────────────────────────────────────────

def trigger_immediate_scrape(route_id: int) -> None:
    """Trigger an on-demand price scrape for the given route.

    采集结果通过 ``st.session_state["_flash"]`` 传递到下一轮渲染，
    调用方负责在采集后执行 ``st.rerun()`` 刷新页面数据。

    Args:
        route_id: ID of the route to scrape.
    """
    try:
        from flightscanner.scheduler.price_monitor import PriceMonitorScheduler
        from flightscanner.utils.config import settings

        monitor = PriceMonitorScheduler(
            headless=settings.scraper_headless,
            enable_notifications=False,
        )
        with get_session() as session:
            route_service = RouteService(session)
            route = route_service.get_route_by_id(route_id)
            if not route:
                st.session_state["_flash"] = ("error", "路线不存在")
                return

            async def _run():
                try:
                    await monitor.scrape_route(route)
                finally:
                    for scraper in monitor.scrapers:
                        await scraper.close()

            with st.spinner(f"正在采集 {route.origin} → {route.destination} 的价格…"):
                asyncio.run(_run())

        st.session_state["_flash"] = (
            "success",
            f"{route.origin} → {route.destination} 采集完成，数据已更新。",
        )
    except Exception as exc:
        st.session_state["_flash"] = ("error", f"采集失败：{exc}")


# ── Add-route dialog ───────────────────────────────────────────────────────────

@st.dialog("添加监控路线", width="large")
def _show_add_route_dialog(session_factory) -> None:
    """Modal dialog for adding a new route to monitor.

    Displays origin/destination city selectboxes with airport sub-selection,
    departure date, optional return date, target price, scrape interval, and
    optional departure/arrival time-window filters.

    Args:
        session_factory: SQLAlchemy SessionLocal factory.
    """
    today = date.today()
    max_date = today + timedelta(days=365)

    # ── City inputs ────────────────────────────────────────────────────
    c1, c2 = st.columns(2)
    with c1:
        origin = st.selectbox(
            "出发城市",
            options=[""] + ALL_CITIES_LIST,
            index=0,
            key="dlg_origin",
            help="输入城市名可快速筛选",
        )
    with c2:
        destination = st.selectbox(
            "目的地",
            options=[""] + ALL_CITIES_LIST,
            index=0,
            key="dlg_destination",
            help="输入城市名可快速筛选",
        )

    # ── Airport sub-selection（仅多机场城市显示）──────────────────────
    dep_airport_code: str | None = None
    arr_airport_code: str | None = None

    dep_airports = DOMESTIC_AIRPORT_MAP.get(origin, []) if origin else []
    arr_airports = DOMESTIC_AIRPORT_MAP.get(destination, []) if destination else []

    if dep_airports or arr_airports:
        ap1, ap2 = st.columns(2)
        with ap1:
            if len(dep_airports) > 1:
                dep_options = ["不限（任意机场）"] + [
                    f"{a['name']}（{a['code']}）" for a in dep_airports
                ]
                dep_sel = st.selectbox(
                    "出发机场",
                    options=dep_options,
                    index=0,
                    key="dlg_dep_airport",
                    help="选择指定机场后，仅监控该机场出发的航班",
                )
                if dep_sel != "不限（任意机场）":
                    # 提取括号内的 IATA 代码
                    dep_airport_code = dep_sel.split("（")[-1].rstrip("）")
            elif dep_airports:
                a = dep_airports[0]
                st.caption(f"出发机场：{a['name']}（{a['code']}）")
                dep_airport_code = a["code"]

        with ap2:
            if len(arr_airports) > 1:
                arr_options = ["不限（任意机场）"] + [
                    f"{a['name']}（{a['code']}）" for a in arr_airports
                ]
                arr_sel = st.selectbox(
                    "到达机场",
                    options=arr_options,
                    index=0,
                    key="dlg_arr_airport",
                    help="选择指定机场后，仅监控到达该机场的航班",
                )
                if arr_sel != "不限（任意机场）":
                    arr_airport_code = arr_sel.split("（")[-1].rstrip("）")
            elif arr_airports:
                a = arr_airports[0]
                st.caption(f"到达机场：{a['name']}（{a['code']}）")
                arr_airport_code = a["code"]

    # ── Date inputs ────────────────────────────────────────────────────
    c3, c4 = st.columns(2)
    with c3:
        departure_date = st.date_input(
            "出发日期",
            min_value=today,
            max_value=max_date,
            key="dlg_dep_date",
        )
    with c4:
        return_date = st.date_input(
            "返程日期（留空则为单程）",
            value=None,
            min_value=departure_date + timedelta(days=1) if departure_date else today,
            max_value=max_date,
            key="dlg_ret_date",
        )

    # ── Price + interval ───────────────────────────────────────────────
    c5, c6 = st.columns(2)
    with c5:
        target_price = st.number_input(
            "目标价格 (¥)",
            min_value=100,
            max_value=50000,
            value=800,
            step=50,
            key="dlg_price",
        )
    with c6:
        scrape_interval = st.select_slider(
            "采集间隔（小时）",
            options=[1, 2, 3, 4, 6, 8, 12, 24],
            value=6,
            key="dlg_interval",
        )

    # ── Time-window filters ────────────────────────────────────────────
    with st.expander("⏰ 时间段过滤（可选）", expanded=False):
        st.caption("仅当航班起飞/降落时间在设定范围内时，才记录价格并触发提醒。")
        tw1, tw2 = st.columns(2)
        with tw1:
            dep_time_range = st.slider(
                "起飞时间段",
                min_value=0,
                max_value=23,
                value=(0, 23),
                step=1,
                format="%d:00",
                key="dlg_dep_time",
                help="仅保留在此起飞时间段内的航班",
            )
        with tw2:
            arr_time_range = st.slider(
                "降落时间段",
                min_value=0,
                max_value=23,
                value=(0, 23),
                step=1,
                format="%d:00",
                key="dlg_arr_time",
                help="仅保留在此降落时间段内的航班",
            )

    # 将小时整数转为 HH:MM 字符串（None = 无限制）
    dep_time_from = f"{dep_time_range[0]:02d}:00" if dep_time_range[0] > 0 else None
    dep_time_to   = f"{dep_time_range[1]:02d}:59" if dep_time_range[1] < 23 else None
    arr_time_from = f"{arr_time_range[0]:02d}:00" if arr_time_range[0] > 0 else None
    arr_time_to   = f"{arr_time_range[1]:02d}:59" if arr_time_range[1] < 23 else None

    # ── Auto-detect hint ───────────────────────────────────────────────
    if origin and destination:
        is_intl = is_international_route(origin, destination)
        trip = "往返" if return_date else "单程"
        flag = "🌐 国际航班" if is_intl else "🏠 国内航班"
        hints = [f"{flag}　·　{trip}"]
        if dep_airport_code:
            hints.append(f"出发：{dep_airport_code}")
        if arr_airport_code:
            hints.append(f"到达：{arr_airport_code}")
        if dep_time_from or dep_time_to:
            hints.append(
                f"起飞 {dep_time_from or '00:00'}–{dep_time_to or '23:59'}"
            )
        if arr_time_from or arr_time_to:
            hints.append(
                f"降落 {arr_time_from or '00:00'}–{arr_time_to or '23:59'}"
            )
        st.caption("　·　".join(hints))
    else:
        st.caption("填写城市名后自动识别国际/国内航班")

    st.divider()

    # ── Action buttons ─────────────────────────────────────────────────
    _, btn_l, btn_r = st.columns([4, 2, 2])
    with btn_l:
        if st.button("取消", use_container_width=True):
            st.rerun()
    with btn_r:
        if st.button("开始监控", type="primary", use_container_width=True):
            # Validate
            if not origin or not destination:
                st.error("请填写出发城市和目的地。")
                return
            if origin == destination:
                st.error("出发地和目的地不能相同。")
                return

            trip_type = "roundtrip" if return_date else "oneway"

            try:
                session = session_factory()
                try:
                    svc = RouteService(session)
                    route = svc.add_route(
                        origin=origin,
                        destination=destination,
                        target_date=departure_date,
                        target_price=Decimal(str(target_price)),
                        scrape_interval=scrape_interval,
                        return_date=return_date,
                        trip_type=trip_type,
                        dep_airport_code=dep_airport_code,
                        arr_airport_code=arr_airport_code,
                        dep_time_from=dep_time_from,
                        dep_time_to=dep_time_to,
                        arr_time_from=arr_time_from,
                        arr_time_to=arr_time_to,
                    )
                    st.session_state["new_route_id"] = route.id
                    st.rerun()
                finally:
                    session.close()
            except ValueError as exc:
                st.error(str(exc))
            except Exception as exc:
                st.error(f"添加失败：{exc}")


# ── CSS injection ──────────────────────────────────────────────────────────────

def _inject_css() -> None:
    """Inject global CSS for the premium dashboard design."""
    st.markdown(
        """
        <style>
        /* ── Global ────────────────────────────────────────────────── */
        .stApp { background: #eef2f7; }
        #MainMenu, footer { visibility: hidden; }
        .stDeployButton,
        [data-testid="stToolbar"],
        [data-testid="stDecoration"],
        [data-testid="stStatusWidget"] { display: none !important; }

        /* Hide sidebar & its toggle */
        [data-testid="stSidebar"]        { display: none !important; }
        [data-testid="collapsedControl"] { display: none !important; }

        /* Content area */
        .block-container {
            padding-top   : 0          !important;
            padding-bottom: 3rem       !important;
            max-width     : 1200px     !important;
        }
        /* Hide Streamlit top-right action buttons */
        header[data-testid="stHeader"] { background: transparent !important; }
        header[data-testid="stHeader"] button { display: none !important; }

        /* ── Page header banner ─────────────────────────────────────── */
        .fs-header {
            background   : linear-gradient(135deg, #0f172a 0%, #1e3a5f 55%, #0c4a6e 100%);
            border-radius: 0 0 24px 24px;
            padding      : 1.75rem 2.5rem 1.5rem;
            margin       : 0 -2rem 2rem;
            display      : flex;
            align-items  : center;
            justify-content: space-between;
        }
        .fs-header-left { display: flex; flex-direction: column; gap: 0.2rem; }
        .fs-header-title {
            font-size  : 1.75rem;
            font-weight: 900;
            color      : #f8fafc;
            letter-spacing: -0.03em;
            line-height: 1;
        }
        .fs-header-sub {
            font-size  : 0.83rem;
            color      : rgba(148,163,184,0.9);
            letter-spacing: 0.01em;
        }

        /* ── Stat grid ──────────────────────────────────────────────── */
        .fs-stat-grid {
            display              : grid;
            grid-template-columns: repeat(4, 1fr);
            gap                  : 1rem;
            margin-bottom        : 0.25rem;
        }
        .fs-stat-card {
            background   : white;
            border-radius: 18px;
            padding      : 1.5rem 1.75rem 1.25rem;
            border       : 1px solid #e8edf2;
            box-shadow   : 0 2px 10px rgba(15,23,42,0.05);
            position     : relative;
            overflow     : hidden;
            transition   : box-shadow 0.2s ease, transform 0.2s ease;
        }
        .fs-stat-card:hover {
            box-shadow: 0 6px 20px rgba(15,23,42,0.10);
            transform : translateY(-3px);
        }
        .fs-stat-card::before {
            content      : '';
            position     : absolute;
            left: 0; top: 0; bottom: 0;
            width        : 5px;
            border-radius: 18px 0 0 18px;
        }
        .fs-stat-card.blue::before   { background: linear-gradient(180deg,#60a5fa,#3b82f6); }
        .fs-stat-card.green::before  { background: linear-gradient(180deg,#34d399,#10b981); }
        .fs-stat-card.amber::before  { background: linear-gradient(180deg,#fcd34d,#f59e0b); }
        .fs-stat-card.purple::before { background: linear-gradient(180deg,#a78bfa,#8b5cf6); }
        .fs-stat-icon  { font-size:1.5rem; display:block; margin-bottom:0.5rem; line-height:1; }
        .fs-stat-value {
            font-size     : 2.5rem;
            font-weight   : 900;
            color         : #0f172a;
            line-height   : 1;
            letter-spacing: -0.04em;
            margin-bottom : 0.3rem;
        }
        .fs-stat-label {
            font-size     : 0.70rem;
            font-weight   : 700;
            color         : #94a3b8;
            text-transform: uppercase;
            letter-spacing: 0.08em;
        }

        /* ── Section header ─────────────────────────────────────────── */
        .fs-section-header {
            display    : flex;
            align-items: center;
            gap        : 12px;
            margin     : 1.75rem 0 1rem;
        }
        .fs-section-title {
            font-size     : 0.68rem;
            font-weight   : 700;
            color         : #94a3b8;
            text-transform: uppercase;
            letter-spacing: 0.10em;
            white-space   : nowrap;
        }
        .fs-section-line  { flex:1; height:1px; background:#e2e8f0; }
        .fs-section-count { font-size:0.72rem; color:#94a3b8; white-space:nowrap; }

        /* ── Route cards (expanders) ────────────────────────────────── */
        [data-testid="stExpander"] {
            background   : white;
            border-radius: 18px;
            border       : 1px solid #e8edf2 !important;
            box-shadow   : 0 2px 10px rgba(15,23,42,0.05);
            margin-bottom: 0.75rem;
            overflow     : hidden;
            transition   : box-shadow 0.2s ease;
        }
        [data-testid="stExpander"]:hover {
            box-shadow: 0 5px 20px rgba(15,23,42,0.09);
        }
        [data-testid="stExpander"] > details > summary {
            padding    : 1.05rem 1.5rem;
            font-size  : 0.875rem;
            font-weight: 500;
            color      : #1e293b;
        }
        [data-testid="stExpander"] > details > summary:hover {
            background: #fafcff;
        }
        [data-testid="stExpander"] > details[open] > summary {
            background   : #fafcff;
            border-bottom: 1px solid #f1f5f9;
        }

        /* ── Price progress bar ─────────────────────────────────────── */
        .fs-price-bar-wrap {
            background   : #f1f5f9;
            border-radius: 99px;
            height       : 6px;
            overflow     : hidden;
            margin       : 0.35rem 0 0;
        }
        .fs-price-bar-fill {
            height       : 100%;
            border-radius: 99px;
            transition   : width 0.4s ease;
        }
        .fs-price-meta {
            display        : flex;
            justify-content: space-between;
            font-size      : 0.72rem;
            color          : #94a3b8;
            margin-top     : 0.25rem;
        }

        /* ── Metric tiles (inside expanders) ────────────────────────── */
        [data-testid="stMetric"] {
            background   : #f8fafc;
            border-radius: 14px;
            padding      : 1rem 1.25rem;
            border       : 1px solid #e8edf2;
        }
        [data-testid="stMetricValue"] > div {
            font-size  : 1.75rem !important;
            font-weight: 800     !important;
            color      : #0f172a !important;
        }
        [data-testid="stMetricLabel"] > div {
            font-size     : 0.70rem  !important;
            font-weight   : 700      !important;
            color         : #94a3b8  !important;
            text-transform: uppercase !important;
            letter-spacing: 0.06em   !important;
        }

        /* ── Primary button ─────────────────────────────────────────── */
        button[data-testid="baseButton-primary"] {
            background    : linear-gradient(135deg,#3b82f6,#2563eb) !important;
            border        : none    !important;
            border-radius : 12px    !important;
            font-weight   : 600     !important;
            letter-spacing: 0.01em  !important;
            box-shadow    : 0 2px 10px rgba(37,99,235,0.25) !important;
            transition    : all 0.2s !important;
        }
        button[data-testid="baseButton-primary"]:hover {
            background: linear-gradient(135deg,#2563eb,#1d4ed8) !important;
            box-shadow: 0 4px 18px rgba(37,99,235,0.40) !important;
            transform : translateY(-1px) !important;
        }

        /* ── Secondary / default buttons ────────────────────────────── */
        button[data-testid="baseButton-secondary"] {
            background   : white            !important;
            border       : 1px solid #e2e8f0 !important;
            border-radius: 9px              !important;
            color        : #475569          !important;
            font-size    : 0.85rem          !important;
            transition   : all 0.15s        !important;
        }
        button[data-testid="baseButton-secondary"]:hover {
            background  : #f8fafc !important;
            border-color: #94a3b8 !important;
        }

        /* ── Divider ─────────────────────────────────────────────────── */
        hr { border-color: #e2e8f0 !important; margin: 1.25rem 0 !important; }

        /* ── Caption ─────────────────────────────────────────────────── */
        [data-testid="stCaptionContainer"] p {
            color    : #94a3b8 !important;
            font-size: 0.78rem !important;
        }

        /* ── Alert boxes ─────────────────────────────────────────────── */
        [data-testid="stAlert"] { border-radius: 12px !important; }
        </style>
        """,
        unsafe_allow_html=True,
    )


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    """Entry point for the FlightScanner Streamlit dashboard."""
    st.set_page_config(
        page_title="FlightScanner",
        page_icon="✈️",
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    _inject_css()
    _get_monitor()

    # ── Register newly added route with background scheduler ───────────
    if "new_route_id" in st.session_state:
        new_route_id: int = st.session_state.pop("new_route_id")
        with get_session() as session:
            svc = RouteService(session)
            new_route = svc.get_route_by_id(new_route_id)
            if new_route:
                session.expunge(new_route)
                _get_monitor().register_new_route(new_route)

    # ── Page header ────────────────────────────────────────────────────
    st.markdown(
        """
        <div class="fs-header">
            <div class="fs-header-left">
                <div class="fs-header-title">✈️ FlightScanner</div>
                <div class="fs-header-sub">实时航班价格监控 · 智能低价提醒</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    _, hd_btn = st.columns([8, 2])
    with hd_btn:
        if st.button("＋ 添加监控", type="primary", use_container_width=True):
            _show_add_route_dialog(get_session_local())

    # ── Flash message from previous scrape ─────────────────────────────
    if "_flash" in st.session_state:
        level, msg = st.session_state.pop("_flash")
        if level == "success":
            st.success(msg)
        else:
            st.error(msg)

    # ── Dashboard data ─────────────────────────────────────────────────
    with get_session() as session:
        svc = RouteService(session)
        routes = svc.get_all_routes()

        # Drain any pending immediate-scrape triggers
        scrape_ran = False
        for route in routes:
            key = f"trigger_scrape_{route.id}"
            if st.session_state.get(key, False):
                st.session_state[key] = False
                trigger_immediate_scrape(route.id)
                scrape_ran = True

        # 采集完成后触发刷新，让下一轮渲染从数据库取最新数据
        if scrape_ran:
            st.rerun()

        render_overview_cards(routes)
        render_route_list(routes, svc)


if __name__ == "__main__":
    main()
