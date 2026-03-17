"""Charts component for visualizing price trends.

This module provides two view modes:
- 简洁视图: line chart, min price per scrape session
- 高级视图: price-range bar chart with departure/arrival time filter panel
"""

import streamlit as st
import pandas as pd
import altair as alt
from decimal import Decimal
from datetime import datetime, timezone, time as dtime
from typing import List
from zoneinfo import ZoneInfo

from flightscanner.interfaces import FlightPrice

# ── Constants ─────────────────────────────────────────────────────────────────

_SOURCE_LABELS: dict[str, str] = {
    "qunar": "去哪儿",
    "ctrip": "携程",
}

_BEIJING = ZoneInfo("Asia/Shanghai")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _source_label(source: str) -> str:
    """Return human-readable platform name."""
    return _SOURCE_LABELS.get(source.lower(), source)


def _to_beijing(dt: datetime) -> datetime:
    """Convert UTC (or naive-UTC) datetime to Beijing time; strip tzinfo for Altair."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_BEIJING).replace(tzinfo=None)


def _parse_hhmm(time_str: str) -> int:
    """Parse 'HH:MM' string to minutes from midnight."""
    try:
        h, m = map(int, time_str.split(":"))
        return h * 60 + m
    except (ValueError, AttributeError):
        return 0


def _build_dataframe(price_history: List[FlightPrice]) -> pd.DataFrame:
    """Flatten price history to a DataFrame with all display fields.

    For round-trip records (return_flight_info is not None) the return leg's
    flight number, airline, departure and arrival times are included as
    separate ``ret_*`` columns so the records table can render both legs.
    """
    rows = []
    for fp in price_history:
        ret = fp.return_flight_info
        fi = fp.flight_info
        # 机场代码优先取 IATA code，无 code 时降级为机场全名或占位符
        dep_airport = fi.departure_airport_code or fi.departure_airport or ""
        arr_airport = fi.arrival_airport_code or fi.arrival_airport or ""
        rows.append({
            "date": _to_beijing(fp.scraped_at),
            "price": float(fp.price),
            "source": fp.source,
            "source_label": _source_label(fp.source),
            "flight": f"{fi.flight_no} ({fi.airline})",
            "dep_time": fi.departure_time or "00:00",
            "arr_time": fi.arrival_time or "00:00",
            "dep_minutes": _parse_hhmm(fi.departure_time or "00:00"),
            "arr_minutes": _parse_hhmm(fi.arrival_time or "00:00"),
            "dep_airport": dep_airport,
            "arr_airport": arr_airport,
            "seat_class": fp.seat_class,
            "direction": fi.direction.value,
            # 回程字段（单程时为空字符串）
            "ret_flight": f"{ret.flight_no} ({ret.airline})" if ret else "",
            "ret_dep_time": ret.departure_time if ret else "",
            "ret_arr_time": ret.arrival_time if ret else "",
        })
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def _agg_by_session(df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate prices by 1-hour bucket × source: compute min and max price."""
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df["bucket"] = df["date"].dt.floor("1h")
    agg = (
        df.groupby(["bucket", "source_label"])
        .agg(min_price=("price", "min"), max_price=("price", "max"), count=("price", "count"))
        .reset_index()
        .rename(columns={"bucket": "date"})
    )
    return agg[agg["count"] > 0].reset_index(drop=True)


def _color_enc(df_agg: pd.DataFrame) -> alt.Color:
    multi = df_agg["source_label"].nunique() > 1
    return alt.Color(
        "source_label:N",
        title="来源平台",
        legend=alt.Legend() if multi else None,
    )


def _target_rule(target_price: Decimal) -> alt.Chart:
    """Draw a horizontal dashed red line representing the target price."""
    return (
        alt.Chart(pd.DataFrame({"t": [float(target_price)]}))
        .mark_rule(color="red", strokeDash=[5, 5], opacity=0.7)
        .encode(y=alt.Y("t:Q"))
    )


# ── Main entry ────────────────────────────────────────────────────────────────

def render_price_trend_chart(
    price_history: List[FlightPrice],
    target_price: Decimal,
    route_name: str = "Route",
    is_roundtrip: bool = False,
    route_id: int = 0,
) -> None:
    """Render price trend chart with Simple and Advanced view tabs.

    Args:
        price_history: Historical price records for this route.
        target_price: Alert threshold shown as a dashed red line.
        route_name: Human-readable route label used in chart titles.
        is_roundtrip: Whether this is a round-trip route; controls the
            column layout of the records table in the Advanced view.
        route_id: Unique route ID used to de-duplicate Streamlit widget keys.
    """
    if not price_history:
        st.info("该路线暂无价格历史数据。")
        return

    df = _build_dataframe(price_history)
    if df.empty:
        return

    tab_simple, tab_adv = st.tabs(["📈 简洁视图", "📊 高级视图"])

    with tab_simple:
        _render_simple_view(df, target_price, route_name)

    with tab_adv:
        _render_advanced_view(df, target_price, route_name, is_roundtrip, key_suffix=str(route_id))


# ── Simple view ───────────────────────────────────────────────────────────────

def _render_simple_view(df: pd.DataFrame, target_price: Decimal, route_name: str) -> None:
    """Line chart: lowest price per scrape session per source platform."""
    df_agg = _agg_by_session(df)
    if df_agg.empty:
        st.info("暂无数据")
        return

    color = _color_enc(df_agg)

    line = (
        alt.Chart(df_agg)
        .mark_line(point=True, opacity=0.7)
        .encode(
            x=alt.X("date:T", title="采集时间"),
            y=alt.Y("min_price:Q", title="最低价 (¥)", scale=alt.Scale(zero=False)),
            color=color,
            tooltip=[
                alt.Tooltip("date:T", title="采集时间", format="%Y-%m-%d %H:%M"),
                alt.Tooltip("min_price:Q", title="最低价 (¥)", format=".0f"),
                alt.Tooltip("source_label:N", title="来源平台"),
                alt.Tooltip("count:Q", title="航班数量"),
            ],
        )
    )

    chart = (line + _target_rule(target_price)).properties(
        title=f"价格趋势：{route_name}",
        height=360,
    )
    st.altair_chart(chart, width="stretch")
    _render_simple_stats(df_agg, target_price)


def _render_simple_stats(df_agg: pd.DataFrame, target_price: Decimal) -> None:
    st.markdown("#### 价格统计")
    col1, col2, col3 = st.columns(3)

    all_min = df_agg["min_price"].min()
    all_min_date = df_agg.loc[df_agg["min_price"].idxmin(), "date"]
    diff = all_min - float(target_price)

    with col1:
        st.metric("平均最低价", f"¥{df_agg['min_price'].mean():.0f}")
    with col2:
        st.metric(
            "历史最低",
            f"¥{all_min:.0f}",
            delta=all_min_date.strftime("%m/%d %H:%M"),
        )
    with col3:
        st.metric(
            "最低价 vs 目标",
            f"¥{all_min:.0f}",
            delta=f"{'↓' if diff < 0 else '↑'}¥{abs(diff):.0f}",
            delta_color="inverse",
        )


# ── Advanced view ─────────────────────────────────────────────────────────────

def _render_advanced_view(
    df: pd.DataFrame,
    target_price: Decimal,
    route_name: str,
    is_roundtrip: bool = False,
    key_suffix: str = "",
) -> None:
    """Price-range bar chart with departure/arrival time filter panel."""

    # ── Filter panel ────────────────────────────────────────────────────────
    # 动态取机场选项（排除空字符串）
    all_dep_airports = sorted(a for a in df["dep_airport"].unique() if a)
    all_arr_airports = sorted(a for a in df["arr_airport"].unique() if a)

    with st.expander("⚙️ 视图设置", expanded=True):
        # 机场过滤（仅当数据中有机场信息时显示）
        if all_dep_airports or all_arr_airports:
            a_col1, a_col2 = st.columns(2)
            with a_col1:
                sel_dep_airports = st.multiselect(
                    "出发机场",
                    options=all_dep_airports,
                    default=all_dep_airports,
                    key=f"dep_airport_filter_{key_suffix}",
                    help="只保留从所选机场出发的航班",
                )
            with a_col2:
                sel_arr_airports = st.multiselect(
                    "到达机场",
                    options=all_arr_airports,
                    default=all_arr_airports,
                    key=f"arr_airport_filter_{key_suffix}",
                    help="只保留到达所选机场的航班",
                )
        else:
            sel_dep_airports = []
            sel_arr_airports = []

        # 时间段过滤
        f_col1, f_col2 = st.columns(2)
        with f_col1:
            dep_range = st.slider(
                "起飞时间范围",
                min_value=dtime(0, 0),
                max_value=dtime(23, 59),
                value=(dtime(0, 0), dtime(23, 59)),
                key=f"dep_filter_{key_suffix}",
                help="保留起飞时间在此范围内的航班",
            )
        with f_col2:
            arr_range = st.slider(
                "到达时间范围",
                min_value=dtime(0, 0),
                max_value=dtime(23, 59),
                value=(dtime(0, 0), dtime(23, 59)),
                key=f"arr_filter_{key_suffix}",
                help="保留到达时间在此范围内的航班",
            )

    # ── Apply filters ────────────────────────────────────────────────────────
    dep_min = dep_range[0].hour * 60 + dep_range[0].minute
    dep_max = dep_range[1].hour * 60 + dep_range[1].minute
    arr_min = arr_range[0].hour * 60 + arr_range[0].minute
    arr_max = arr_range[1].hour * 60 + arr_range[1].minute

    df_f = df[
        (df["dep_minutes"] >= dep_min) & (df["dep_minutes"] <= dep_max) &
        (df["arr_minutes"] >= arr_min) & (df["arr_minutes"] <= arr_max)
    ]

    # 机场过滤（multiselect 为空时视为"全选"）
    if sel_dep_airports:
        df_f = df_f[df_f["dep_airport"].isin(sel_dep_airports) | (df_f["dep_airport"] == "")]
    if sel_arr_airports:
        df_f = df_f[df_f["arr_airport"].isin(sel_arr_airports) | (df_f["arr_airport"] == "")]

    flight_count = df_f["flight"].nunique()
    # 构建 caption
    caption_parts = [f"过滤后：{flight_count} 个航班，共 {len(df_f)} 条记录"]
    caption_parts.append(
        f"起飞 {dep_range[0].strftime('%H:%M')}–{dep_range[1].strftime('%H:%M')}"
    )
    caption_parts.append(
        f"到达 {arr_range[0].strftime('%H:%M')}–{arr_range[1].strftime('%H:%M')}"
    )
    if sel_dep_airports and len(sel_dep_airports) < len(all_dep_airports):
        caption_parts.append(f"出发机场：{'/'.join(sel_dep_airports)}")
    if sel_arr_airports and len(sel_arr_airports) < len(all_arr_airports):
        caption_parts.append(f"到达机场：{'/'.join(sel_arr_airports)}")
    st.caption("　|　".join(caption_parts))

    if df_f.empty:
        st.warning("当前过滤条件无匹配航班，请调整时间范围。")
        return

    # ── Aggregate ────────────────────────────────────────────────────────────
    df_agg = _agg_by_session(df_f)
    color = _color_enc(df_agg)

    tooltip = [
        alt.Tooltip("date:T", title="采集时间", format="%Y-%m-%d %H:%M"),
        alt.Tooltip("min_price:Q", title="最低价 (¥)", format=".0f"),
        alt.Tooltip("max_price:Q", title="最高价 (¥)", format=".0f"),
        alt.Tooltip("source_label:N", title="来源平台"),
        alt.Tooltip("count:Q", title="航班数量"),
    ]

    # Vertical range rule (min → max)
    rule = (
        alt.Chart(df_agg)
        .mark_rule(strokeWidth=3)
        .encode(
            x=alt.X("date:T", title="采集时间"),
            y=alt.Y("min_price:Q", title="价格 (¥)", scale=alt.Scale(zero=False)),
            y2=alt.Y2("max_price:Q"),
            color=color,
            tooltip=tooltip,
        )
    )

    # Tick caps at min and max
    base_tick = alt.Chart(df_agg).mark_tick(size=14, thickness=2).encode(
        x="date:T",
        color=color,
    )
    tick_low = base_tick.encode(y=alt.Y("min_price:Q", scale=alt.Scale(zero=False)))
    tick_high = base_tick.encode(y=alt.Y("max_price:Q", scale=alt.Scale(zero=False)))

    chart = (rule + tick_low + tick_high + _target_rule(target_price)).properties(
        title=f"价格区间：{route_name}",
        height=360,
    )
    st.altair_chart(chart, width="stretch")
    _render_advanced_stats(df_agg, df_f, target_price, is_roundtrip)


def _render_advanced_stats(
    df_agg: pd.DataFrame,
    df_f: pd.DataFrame,
    target_price: Decimal,
    is_roundtrip: bool = False,
) -> None:
    st.markdown("#### 价格统计（过滤后）")
    col1, col2, col3, col4 = st.columns(4)

    global_min = df_agg["min_price"].min()
    global_max = df_agg["max_price"].max()
    latest_row = df_agg.loc[df_agg["date"].idxmax()]
    diff = global_min - float(target_price)

    with col1:
        st.metric("历史最低价", f"¥{global_min:.0f}")
    with col2:
        st.metric("历史最高价", f"¥{global_max:.0f}")
    with col3:
        lo, hi = latest_row["min_price"], latest_row["max_price"]
        label = f"¥{lo:.0f}" if lo == hi else f"¥{lo:.0f} ~ ¥{hi:.0f}"
        st.metric("最近一次采集区间", label)
    with col4:
        st.metric(
            "最低价 vs 目标",
            f"¥{global_min:.0f}",
            delta=f"{'↓' if diff < 0 else '↑'}¥{abs(diff):.0f}",
            delta_color="inverse",
        )

    # Recent records table
    st.markdown("#### 最近价格记录")
    display_df = df_f.sort_values("date", ascending=False).head(10).copy()
    display_df["date"] = display_df["date"].dt.strftime("%Y-%m-%d %H:%M")

    # 判断数据中是否有机场信息（决定是否展示机场列）
    has_airports = display_df["dep_airport"].any() or display_df["arr_airport"].any()

    if is_roundtrip:
        display_df = display_df.rename(columns={
            "date": "采集时间",
            "price": "往返总价 (¥)",
            "source_label": "来源平台",
            "flight": "去程航班",
            "dep_time": "去程起飞",
            "arr_time": "去程到达",
            "dep_airport": "出发机场",
            "arr_airport": "到达机场",
            "ret_flight": "回程航班",
            "ret_dep_time": "回程起飞",
            "ret_arr_time": "回程到达",
            "seat_class": "舱位",
        })
        cols = ["采集时间", "来源平台", "往返总价 (¥)", "去程航班", "去程起飞", "去程到达"]
        if has_airports:
            cols += ["出发机场", "到达机场"]
        cols += ["回程航班", "回程起飞", "回程到达", "舱位"]
        st.dataframe(display_df[cols], width="stretch", hide_index=True)
    else:
        display_df = display_df.rename(columns={
            "date": "采集时间",
            "price": "价格 (¥)",
            "source_label": "来源平台",
            "flight": "航班",
            "dep_time": "起飞",
            "arr_time": "到达",
            "dep_airport": "出发机场",
            "arr_airport": "到达机场",
            "seat_class": "舱位",
            "direction": "方向",
        })
        cols = ["采集时间", "来源平台", "价格 (¥)", "航班", "起飞", "到达"]
        if has_airports:
            cols += ["出发机场", "到达机场"]
        cols += ["舱位", "方向"]
        st.dataframe(display_df[cols], width="stretch", hide_index=True)
