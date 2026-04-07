"""Main Streamlit application for FlightScanner.

Modern flat-design dashboard with inline route cards and a modal dialog
for adding new monitoring routes.
"""

import asyncio
import logging
import sys
from datetime import date, timedelta
from decimal import Decimal
from typing import List, Optional

import streamlit as st

from flightscanner.core.services import RouteService
from flightscanner.interfaces import FlightPrice, SearchParams
from flightscanner.utils.city_codes import (
    ALL_CITIES_LIST,
    DOMESTIC_AIRPORT_MAP,
    is_international_route,
)
from ui.utils.db import get_session, get_session_local
from ui.components.overview import render_overview_cards, render_route_list
from ui.components.cookie_manager import render_cookie_manager_dialog


# ── 日志配置：将 flightscanner 包的所有日志输出到终端 ────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
    force=True,  # 覆盖 Streamlit 内部可能已设置的 root handler
)
# 爬虫模块调试时可临时改为 DEBUG
logging.getLogger("flightscanner").setLevel(logging.INFO)


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
    c5, c6, c7 = st.columns(3)
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
    with c7:
        max_results_per_route = st.slider(
            "每平台采集上限",
            min_value=5, max_value=100, step=5, value=20,
            key="dlg_max_results",
            help="每次采集最多保留的航班条数（5~100）",
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
                        max_results=max_results_per_route,
                    )
                    st.session_state["new_route_id"] = route.id
                    st.rerun()
                finally:
                    session.close()
            except ValueError as exc:
                st.error(str(exc))
            except Exception as exc:
                st.error(f"添加失败：{exc}")


# ── Pinned-flight dialog helpers ───────────────────────────────────────────────

def _clear_pf_dlg_state() -> None:
    """清除精准航班弹窗相关的所有 session_state 键。"""
    for key in list(st.session_state.keys()):
        if key.startswith("_pf_"):
            del st.session_state[key]


def _search_flights_for_pinned(
    origin: str,
    destination: str,
    dep_date: date,
) -> List[FlightPrice]:
    """在弹窗内执行同步航班搜索，返回 FlightPrice 列表。

    使用 max_results=100 确保能覆盖大部分航班，方便用户从列表中选择目标航班。

    Args:
        origin: 出发城市。
        destination: 目的地城市。
        dep_date: 出发日期。

    Returns:
        搜索到的 FlightPrice 列表（按价格升序）。
    """
    from flightscanner.scheduler.price_monitor import PriceMonitorScheduler
    from flightscanner.utils.config import settings

    monitor = PriceMonitorScheduler(headless=settings.scraper_headless, enable_notifications=False)
    for scraper in monitor.scrapers:
        if hasattr(scraper, "max_results"):
            scraper.max_results = 100

    async def _run() -> List[FlightPrice]:
        try:
            params = SearchParams(
                departure_city=origin,
                arrival_city=destination,
                departure_date=dep_date,
                return_date=None,
            )
            return await monitor._scrape_all_platforms(params)
        finally:
            for scraper in monitor.scrapers:
                await scraper.close()

    return asyncio.run(_run())


def _format_flight_option(flight_no: str, fp: "FlightPrice") -> str:
    """Build a human-readable selectbox option string for a flight.

    Format:  {flight_no}  {airline}  {dep_time}→{arr_time}[+N]  ¥{price:.0f}{stops}  [{source}]

    Examples:
        CA4509  国航  08:30→11:00  ¥480  [qunar]
        CA953/MU5185  国航  07:00→15:30+1  ¥380  经停1次  [ctrip]
    """
    fi = fp.flight_info
    arr_time = fi.arrival_time or ""

    # 跨日标记（+1, +2…）
    overnight = ""
    if fi.arrival_date and fi.departure_date and fi.arrival_date > fi.departure_date:
        delta = (fi.arrival_date - fi.departure_date).days
        overnight = f"+{delta}"

    # 中转标记
    stops_str = ""
    stop_count = flight_no.count("/")
    if stop_count > 0:
        stops_str = f"  经停{stop_count}次"

    return (
        f"{flight_no}  {fi.airline}  {fi.departure_time}→{arr_time}{overnight}"
        f"  ¥{float(fp.price):.0f}{stops_str}  [{fp.source}]"
    )


@st.dialog("🎯 精准航班监控", width="large")
def _show_add_pinned_flight_dialog(session_factory) -> None:
    """多步弹窗：添加精准航班号监控路线。

    Step 0 — 填写城市/日期，选择「搜索选择」或「手动输入」模式。
    Step 1 — 搜索模式显示航班列表供选择；手动模式直接输入航班号。
             两种模式均支持选择舱位。
    Step 2 — 设置目标价格和采集间隔，确认添加。

    Args:
        session_factory: SQLAlchemy SessionLocal factory。
    """
    today = date.today()
    max_date = today + timedelta(days=365)
    step = st.session_state.get("_pf_step", 0)

    # ── STEP 0: Cities + dates + mode ─────────────────────────────────────
    if step == 0:
        st.markdown("##### 第 1 步：填写路线与选择方式")

        c1, c2 = st.columns(2)
        with c1:
            origin = st.selectbox(
                "出发城市", [""] + ALL_CITIES_LIST, index=0, key="_pf_origin_sel",
                help="输入城市名可快速筛选",
            )
        with c2:
            dest = st.selectbox(
                "目的地", [""] + ALL_CITIES_LIST, index=0, key="_pf_dest_sel",
                help="输入城市名可快速筛选",
            )

        c3, c4 = st.columns(2)
        with c3:
            dep_date = st.date_input(
                "出发日期", min_value=today, max_value=max_date, key="_pf_dep_date_input",
            )
        with c4:
            ret_date = st.date_input(
                "返程日期（留空则为单程）", value=None,
                min_value=dep_date + timedelta(days=1) if dep_date else today,
                max_value=max_date, key="_pf_ret_date_input",
            )

        st.markdown("**航班选择方式**")
        mode_label = st.radio(
            "航班选择方式",
            ["🔍 搜索选择（推荐）", "✏️ 手动输入航班号"],
            key="_pf_mode_radio",
            horizontal=True,
            label_visibility="collapsed",
        )

        if origin and dest:
            trip = "往返" if ret_date else "单程"
            flag = "🌐 国际航班" if is_international_route(origin, dest) else "🏠 国内航班"
            st.caption(f"{flag}　·　{trip}")

        st.divider()
        _, col_cancel, col_next = st.columns([4, 2, 2])
        with col_cancel:
            if st.button("取消", use_container_width=True, key="pf_cancel0"):
                _clear_pf_dlg_state()
                st.rerun()
        with col_next:
            is_search = "搜索" in mode_label
            btn_label = "搜索航班" if is_search else "下一步"
            if st.button(btn_label, type="primary", use_container_width=True, key="pf_next0"):
                if not origin or not dest:
                    st.error("请填写出发城市和目的地。")
                    return
                if origin == dest:
                    st.error("出发地和目的地不能相同。")
                    return

                st.session_state["_pf_origin"] = origin
                st.session_state["_pf_dest"] = dest
                st.session_state["_pf_dep_date"] = dep_date
                st.session_state["_pf_ret_date"] = ret_date
                st.session_state["_pf_trip_type"] = "roundtrip" if ret_date else "oneway"
                st.session_state["_pf_mode"] = "search" if is_search else "manual"

                if is_search:
                    with st.spinner(f"正在搜索 {origin} → {dest} 的航班…"):
                        try:
                            results = _search_flights_for_pinned(origin, dest, dep_date)
                            st.session_state["_pf_search_results"] = results
                            if ret_date:
                                ret_results = _search_flights_for_pinned(dest, origin, ret_date)
                                st.session_state["_pf_ret_search_results"] = ret_results
                        except Exception as exc:
                            st.error(f"搜索失败：{exc}")
                            return

                st.session_state["_pf_step"] = 1
                st.rerun(scope="fragment")

    # ── STEP 1: Flight selection or manual input ───────────────────────────
    elif step == 1:
        origin = st.session_state.get("_pf_origin", "")
        dest = st.session_state.get("_pf_dest", "")
        dep_date = st.session_state.get("_pf_dep_date")
        ret_date = st.session_state.get("_pf_ret_date")
        trip_type = st.session_state.get("_pf_trip_type", "oneway")
        mode = st.session_state.get("_pf_mode", "manual")

        trip_label = "（往返）" if trip_type == "roundtrip" else "（单程）"
        st.markdown(f"##### 第 2 步：选择/输入航班号  {origin} → {dest} {trip_label}")

        if mode == "search":
            results: List[FlightPrice] = st.session_state.get("_pf_search_results", [])
            if not results:
                st.warning("未搜索到航班结果，请直接输入航班号。")
                # Fall through to manual input below
                mode = "manual"
            else:
                # 跨平台去重：同一航班号保留最低价（不限来源）
                seen: dict = {}
                for fp in results:
                    no = fp.flight_info.flight_no
                    if no not in seen or fp.price < seen[no].price:
                        seen[no] = fp
                options = [
                    _format_flight_option(no, fp)
                    for no, fp in sorted(seen.items(), key=lambda x: x[1].price)
                ]

                st.markdown(f"**去程航班** — {dep_date}")
                out_sel = st.selectbox("选择去程航班", options, key="_pf_out_sel")
                if out_sel:
                    out_no = out_sel.split()[0]
                    st.session_state["_pf_out_no"] = out_no
                    fp_ref = seen.get(out_no)
                    if fp_ref:
                        st.session_state["_pf_out_dep_time"] = fp_ref.flight_info.departure_time

                if trip_type == "roundtrip":
                    ret_results: List[FlightPrice] = st.session_state.get("_pf_ret_search_results", [])
                    ret_seen: dict = {}
                    for fp in ret_results:
                        no = fp.flight_info.flight_no
                        if no not in ret_seen or fp.price < ret_seen[no].price:
                            ret_seen[no] = fp

                    if ret_seen:
                        ret_options = [
                            _format_flight_option(no, fp)
                            for no, fp in sorted(ret_seen.items(), key=lambda x: x[1].price)
                        ]
                        st.markdown(f"**回程航班** — {ret_date}")
                        in_sel = st.selectbox("选择回程航班", ret_options, key="_pf_in_sel")
                        if in_sel:
                            in_no = in_sel.split()[0]
                            st.session_state["_pf_in_no"] = in_no
                            fp_ref = ret_seen.get(in_no)
                            if fp_ref:
                                st.session_state["_pf_in_dep_time"] = fp_ref.flight_info.departure_time
                    else:
                        st.warning("未找到回程航班，请手动输入回程航班号。")
                        in_manual = st.text_input(
                            "回程航班号（如 CA954）", key="_pf_in_no_fallback",
                            value=st.session_state.get("_pf_in_no", ""),
                        )
                        if in_manual:
                            st.session_state["_pf_in_no"] = in_manual.upper().strip()

        if mode == "manual":
            out_manual = st.text_input(
                "去程航班号（如 CA953）",
                key="_pf_out_no_manual",
                value=st.session_state.get("_pf_out_no", ""),
                help="英文大写字母 + 数字，如 CA953、MU5185",
            )
            if out_manual:
                st.session_state["_pf_out_no"] = out_manual.upper().strip()

            if trip_type == "roundtrip":
                in_manual = st.text_input(
                    "回程航班号（如 CA954）",
                    key="_pf_in_no_manual",
                    value=st.session_state.get("_pf_in_no", ""),
                )
                if in_manual:
                    st.session_state["_pf_in_no"] = in_manual.upper().strip()

        # Cabin class (shown for both modes)
        st.markdown("**舱位筛选**（可选）")
        cabin_opts = ["不限（任意舱位）", "经济舱", "商务舱", "头等舱"]
        cabin_sel = st.selectbox("舱位", cabin_opts, key="_pf_cabin_sel")
        st.session_state["_pf_seat_class"] = None if cabin_sel == "不限（任意舱位）" else cabin_sel

        st.divider()
        col_back, _, col_next = st.columns([1, 3, 1])
        with col_back:
            if st.button("← 返回", use_container_width=True, key="pf_back1"):
                st.session_state["_pf_step"] = 0
                st.rerun(scope="fragment")
        with col_next:
            if st.button("下一步 →", type="primary", use_container_width=True, key="pf_next1"):
                if not st.session_state.get("_pf_out_no"):
                    st.error("请输入或选择去程航班号。")
                    return
                st.session_state["_pf_step"] = 2
                st.rerun(scope="fragment")

    # ── STEP 2: Target price + interval ───────────────────────────────────
    elif step == 2:
        origin = st.session_state.get("_pf_origin", "")
        dest = st.session_state.get("_pf_dest", "")
        dep_date = st.session_state.get("_pf_dep_date")
        ret_date = st.session_state.get("_pf_ret_date")
        trip_type = st.session_state.get("_pf_trip_type", "oneway")
        out_no = st.session_state.get("_pf_out_no", "")
        in_no = st.session_state.get("_pf_in_no", "")
        seat_class = st.session_state.get("_pf_seat_class")
        out_dep_time = st.session_state.get("_pf_out_dep_time")
        in_dep_time = st.session_state.get("_pf_in_dep_time")

        st.markdown("##### 第 3 步：设置目标价格")
        st.markdown(f"**{origin} → {dest}**  ·  {dep_date}"
                    + (f" ↔ {ret_date}" if trip_type == "roundtrip" else ""))
        out_time_hint = f"  起飞参考 {out_dep_time}" if out_dep_time else ""
        in_time_hint = (f"  回程 {in_no}  起飞参考 {in_dep_time}" if in_dep_time
                        else (f"  回程 {in_no}" if in_no else ""))
        st.caption(
            f"🎯 去程 **{out_no}**{out_time_hint}"
            + in_time_hint
            + (f"　舱位：{seat_class}" if seat_class else "")
        )
        st.divider()

        c1, c2, c3 = st.columns(3)
        with c1:
            target_price = st.number_input(
                "目标价格 (¥)", min_value=100, max_value=50000,
                value=800, step=50, key="_pf_target_price",
            )
        with c2:
            scrape_interval = st.select_slider(
                "采集间隔（小时）",
                options=[1, 2, 3, 4, 6, 8, 12, 24], value=6,
                key="_pf_interval",
            )
        with c3:
            max_results_val = st.slider(
                "采集上限",
                min_value=20, max_value=200, step=20, value=100,
                key="_pf_max_results",
                help="每次搜索最多获取的结果数（越高找到目标航班的概率越大）",
            )

        col_back, _, col_submit = st.columns([1, 3, 1])
        with col_back:
            if st.button("← 返回", use_container_width=True, key="pf_back2"):
                st.session_state["_pf_step"] = 1
                st.rerun(scope="fragment")
        with col_submit:
            if st.button("开始精准监控", type="primary", use_container_width=True, key="pf_submit"):
                try:
                    session = session_factory()
                    try:
                        svc = RouteService(session)
                        route = svc.add_route(
                            origin=origin,
                            destination=dest,
                            target_date=dep_date,
                            target_price=Decimal(str(target_price)),
                            scrape_interval=scrape_interval,
                            return_date=ret_date,
                            trip_type=trip_type,
                            max_results=max_results_val,
                            monitoring_mode="flight",
                            outbound_flight_no=out_no or None,
                            inbound_flight_no=in_no or None,
                            pinned_seat_class=seat_class,
                            outbound_dep_time_ref=out_dep_time or None,
                            inbound_dep_time_ref=in_dep_time or None,
                        )
                        st.session_state["new_route_id"] = route.id
                        _clear_pf_dlg_state()
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
        .stApp {
            background  : #f3f4f8;
            font-family : -apple-system, 'Helvetica Neue', 'Inter', system-ui, sans-serif;
        }
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
            border-radius: 14px;
            padding      : 1.1rem 1.4rem 1rem;
            border       : 1px solid #eaedf2;
            box-shadow   : 0 1px 3px rgba(0,0,0,0.05);
            position     : relative;
            overflow     : hidden;
            transition   : box-shadow 0.2s ease, transform 0.2s ease;
        }
        .fs-stat-card:hover {
            box-shadow: 0 4px 14px rgba(0,0,0,0.08);
            transform : translateY(-2px);
        }
        .fs-stat-card::before {
            content      : '';
            position     : absolute;
            top: 0; left: 0; right: 0; bottom: auto;
            height       : 3px;
            width        : auto;
            border-radius: 14px 14px 0 0;
        }
        .fs-stat-card.blue::before   { background: linear-gradient(90deg,#60a5fa,#3d7ff5); }
        .fs-stat-card.green::before  { background: linear-gradient(90deg,#34d399,#12b76a); }
        .fs-stat-card.amber::before  { background: linear-gradient(90deg,#fcd34d,#f59e0b); }
        .fs-stat-card.purple::before { background: linear-gradient(90deg,#a78bfa,#8b5cf6); }
        .fs-stat-value {
            font-size     : 2.1rem;
            font-weight   : 800;
            color         : #18191c;
            line-height   : 1;
            letter-spacing: -0.03em;
            margin-bottom : 0.3rem;
            margin-top    : 0.4rem;
        }
        .fs-stat-label {
            font-size     : 0.70rem;
            font-weight   : 700;
            color         : #9ca5b4;
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
            color         : #9ca5b4;
            text-transform: uppercase;
            letter-spacing: 0.10em;
            white-space   : nowrap;
        }
        .fs-section-line  { flex:1; height:1px; background:#eaedf2; }
        .fs-section-count { font-size:0.72rem; color:#9ca5b4; white-space:nowrap; }

        /* ── Route cards (expanders) ────────────────────────────────── */
        [data-testid="stExpander"] {
            background   : white;
            border-radius: 14px;
            border       : 1px solid #eaedf2 !important;
            box-shadow   : 0 1px 4px rgba(0,0,0,0.05);
            margin-bottom: 0.625rem;
            overflow     : hidden;
            transition   : box-shadow 0.2s ease;
        }
        [data-testid="stExpander"]:hover {
            box-shadow: 0 4px 14px rgba(0,0,0,0.08);
        }
        [data-testid="stExpander"] > details > summary {
            padding    : 0.9rem 1.4rem;
            font-size  : 0.875rem;
            font-weight: 500;
            color      : #18191c;
        }
        [data-testid="stExpander"] > details > summary:hover {
            background: #f8f9fb;
        }
        [data-testid="stExpander"] > details[open] > summary {
            background   : #f8f9fb;
            border-bottom: 1px solid #eaedf2;
        }

        /* ── Metadata chips ─────────────────────────────────────────── */
        .fs-meta-row {
            display  : flex;
            flex-wrap: wrap;
            gap      : 5px;
            margin   : 0.3rem 0 0.5rem;
        }
        .fs-chip {
            background   : #f4f5f7;
            color        : #6e7788;
            border-radius: 6px;
            padding      : 2px 8px;
            font-size    : 0.73rem;
            font-weight  : 500;
        }
        .fs-chip-accent {
            background   : #eff4ff;
            color        : #3d7ff5;
            border-radius: 6px;
            padding      : 2px 8px;
            font-size    : 0.73rem;
            font-weight  : 600;
        }

        /* ── Platform price row ─────────────────────────────────────── */
        .fs-price-row {
            display    : flex;
            gap        : 2rem;
            margin     : 0.6rem 0;
            align-items: flex-end;
        }
        .fs-price-source  { display: flex; flex-direction: column; gap: 2px; }
        .fs-source-label {
            font-size     : 0.66rem;
            font-weight   : 700;
            color         : #9ca5b4;
            text-transform: uppercase;
            letter-spacing: 0.07em;
        }
        .fs-price-val {
            font-size     : 1.6rem;
            font-weight   : 800;
            color         : #18191c;
            letter-spacing: -0.03em;
            line-height   : 1;
        }
        .fs-price-delta-down { font-size:0.72rem; font-weight:600; color:#12b76a; }
        .fs-price-delta-up   { font-size:0.72rem; font-weight:600; color:#ef4444; }

        /* ── Section subtitle ───────────────────────────────────────── */
        .fs-section-subtitle {
            font-size     : 0.72rem;
            font-weight   : 700;
            color         : #9ca5b4;
            text-transform: uppercase;
            letter-spacing: 0.10em;
            margin        : 1rem 0 0.5rem;
        }

        /* ── Price progress bar ─────────────────────────────────────── */
        .fs-price-bar-wrap {
            background   : #edf0f4;
            border-radius: 99px;
            height       : 5px;
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
            color          : #9ca5b4;
            margin-top     : 0.25rem;
        }

        /* ── Metric tiles (inside expanders) ────────────────────────── */
        [data-testid="stMetric"] {
            background   : #f8f9fb;
            border-radius: 12px;
            padding      : 0.75rem 1rem;
            border       : 1px solid #eaedf2;
        }
        [data-testid="stMetricValue"] > div {
            font-size  : 1.5rem !important;
            font-weight: 800    !important;
            color      : #18191c !important;
        }
        [data-testid="stMetricLabel"] > div {
            font-size     : 0.70rem  !important;
            font-weight   : 700      !important;
            color         : #9ca5b4  !important;
            text-transform: uppercase !important;
            letter-spacing: 0.06em   !important;
        }

        /* ── Primary button ─────────────────────────────────────────── */
        button[data-testid="baseButton-primary"] {
            background    : linear-gradient(135deg,#3d7ff5,#2563eb) !important;
            border        : none    !important;
            border-radius : 10px    !important;
            font-weight   : 600     !important;
            letter-spacing: 0.01em  !important;
            box-shadow    : 0 2px 8px rgba(37,99,235,0.22) !important;
            transition    : all 0.2s !important;
        }
        button[data-testid="baseButton-primary"]:hover {
            background: linear-gradient(135deg,#2563eb,#1d4ed8) !important;
            box-shadow: 0 4px 18px rgba(37,99,235,0.36) !important;
            transform : translateY(-1px) !important;
        }

        /* ── Secondary / default buttons ────────────────────────────── */
        button[data-testid="baseButton-secondary"] {
            background   : white            !important;
            border       : 1px solid #eaedf2 !important;
            border-radius: 8px              !important;
            color        : #6e7788          !important;
            font-size    : 0.85rem          !important;
            transition   : all 0.15s        !important;
        }
        button[data-testid="baseButton-secondary"]:hover {
            background  : #f8f9fb !important;
            border-color: #9ca5b4 !important;
        }

        /* ── Divider ─────────────────────────────────────────────────── */
        hr { border-color: #eaedf2 !important; margin: 1.25rem 0 !important; }

        /* ── Caption ─────────────────────────────────────────────────── */
        [data-testid="stCaptionContainer"] p {
            color    : #9ca5b4 !important;
            font-size: 0.78rem !important;
        }

        /* ── Alert boxes ─────────────────────────────────────────────── */
        [data-testid="stAlert"] { border-radius: 12px !important; }

        /* ── History entry card (主页底部入口) ──────────────────────── */
        .fs-history-entry {
            background   : white;
            border-radius: 14px;
            border       : 1px solid #eaedf2;
            box-shadow   : 0 1px 3px rgba(0,0,0,0.05);
            padding      : 1rem 1.4rem;
            display      : flex;
            align-items  : center;
            gap          : 0.75rem;
            margin-top   : 0.5rem;
            transition   : box-shadow 0.2s ease;
        }
        .fs-history-entry:hover { box-shadow: 0 4px 14px rgba(0,0,0,0.08); }

        /* ── History page header strip (历史页顶部) ─────────────────── */
        .fs-history-header {
            background      : white;
            border-radius   : 14px;
            border          : 1px solid #eaedf2;
            box-shadow      : 0 1px 3px rgba(0,0,0,0.05);
            padding         : 1.1rem 1.4rem;
            display         : flex;
            align-items     : center;
            justify-content : space-between;
            margin-bottom   : 0.75rem;
        }
        .fs-history-header-title {
            font-size     : 1.1rem;
            font-weight   : 800;
            color         : #18191c;
            letter-spacing: -0.02em;
        }
        .fs-history-header-sub {
            font-size : 0.78rem;
            color     : #9ca5b4;
            margin-top: 0.15rem;
        }
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

    # ── 主页 header ─────────────────────────────────────────────────────
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
    _, hd_btn_add, hd_btn_pinned, hd_btn_cookie = st.columns([6, 2, 2, 1])
    with hd_btn_add:
        if st.button("＋ 添加监控", type="primary", use_container_width=True):
            _show_add_route_dialog(get_session_local())
    with hd_btn_pinned:
        if st.button("🎯 精准监控", use_container_width=True,
                     help="按航班号精准追踪指定航班的价格动态"):
            _show_add_pinned_flight_dialog(get_session_local())
    with hd_btn_cookie:
        if st.button("🔑", help="Cookie 管理", use_container_width=True):
            render_cookie_manager_dialog()

    # ── Dashboard data ─────────────────────────────────────────────────
    with get_session() as session:
        svc = RouteService(session)
        routes = svc.get_all_routes()

        today = date.today()
        upcoming_routes   = [r for r in routes if r.target_date >= today]
        historical_routes = [r for r in routes if r.target_date <  today]

        # Drain any pending immediate-scrape triggers（遍历全量，历史页也能触发）
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

        # Flash message from previous scrape
        if "_flash" in st.session_state:
            level, msg = st.session_state.pop("_flash")
            if level == "success":
                st.success(msg)
            else:
                st.error(msg)

        # ── 统计卡片（全量路线）────────────────────────────────────────
        render_overview_cards(routes)

        # ── 路线标签页 ─────────────────────────────────────────────────
        n_hist = len(historical_routes)
        tab_label_hist = f"历史记录（{n_hist}）" if n_hist else "历史记录"
        tab_current, tab_history = st.tabs(["当前监控", tab_label_hist])

        with tab_current:
            render_route_list(upcoming_routes, svc)

        with tab_history:
            render_route_list(historical_routes, svc)


if __name__ == "__main__":
    main()
