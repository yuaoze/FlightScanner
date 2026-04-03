"""Charts component for visualizing price trends.

This module provides a unified view with a price-range bar chart or line chart,
inline filter panel, and a records table including duration and stops columns.
"""

import streamlit as st
import pandas as pd
import altair as alt
from decimal import Decimal
from datetime import datetime, timezone, time as dtime
from typing import List, Optional
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


def _day_offset_marker(
    arr_time: Optional[str],
    dep_date: Optional[object],
    arrival_date: Optional[object],
    dep_time: Optional[str],
) -> str:
    """Return arr_time with a '+N' suffix showing how many days later arrival is.

    Uses the actual arrival_date when available for precise multi-day detection.
    Falls back to HH:MM string comparison (yields '+1' at most) when arrival_date
    is None.

    Args:
        arr_time:     Arrival time "HH:MM", or None.
        dep_date:     datetime.date of departure, or None.
        arrival_date: datetime.date of arrival, or None.
        dep_time:     Departure time "HH:MM", or None (used for fallback only).

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
    # Fallback: HH:MM comparison for +1 only
    if dep_time and arr_time < dep_time:
        return f"{arr_time} +1"
    return arr_time


def _build_dataframe(price_history: List[FlightPrice]) -> pd.DataFrame:
    """Flatten price history to a DataFrame with all display fields.

    Includes duration and stops columns in addition to the standard fields.
    For round-trip records (return_flight_info is not None) the return leg's
    flight number, airline, departure and arrival times are included as
    separate ``ret_*`` columns so the records table can render both legs.
    """
    rows = []
    for fp in price_history:
        ret = fp.return_flight_info
        fi = fp.flight_info
        dep_airport = fi.departure_airport_code or fi.departure_airport or ""
        arr_airport = fi.arrival_airport_code or fi.arrival_airport or ""
        dep_t = fi.departure_time or "00:00"
        arr_t = fi.arrival_time or "00:00"

        # ── 飞行时长计算 ─────────────────────────────────────────────────────
        # 使用实际 arrival_date 避免 +N 天误差
        day_delta = 0
        if fi.arrival_date and fi.departure_date:
            day_delta = (fi.arrival_date - fi.departure_date).days
        elif _parse_hhmm(arr_t) < _parse_hhmm(dep_t):
            day_delta = 1
        total_mins = _parse_hhmm(arr_t) - _parse_hhmm(dep_t) + day_delta * 1440
        if total_mins > 0:
            dur_h, dur_m = divmod(total_mins, 60)
            duration_str = f"{dur_h}h{dur_m:02d}m"
        else:
            duration_str = ""

        # ── 经停次数 ─────────────────────────────────────────────────────────
        # 去哪儿联程航班号格式 "CA1234/MU5678"
        stops_count = fi.flight_no.count("/")
        stops_str = "直飞" if stops_count == 0 else f"{stops_count}经停"

        rows.append({
            "date": _to_beijing(fp.scraped_at),
            "price": float(fp.price),
            "source": fp.source,
            "source_label": _source_label(fp.source),
            "batch_id": fp.batch_id or "",
            "flight": f"{fi.flight_no} ({fi.airline})",
            "dep_time": dep_t,
            "arr_time": _day_offset_marker(arr_t, fi.departure_date, fi.arrival_date, dep_t),
            "dep_minutes": _parse_hhmm(dep_t),
            "arr_minutes": _parse_hhmm(arr_t),
            "dep_airport": dep_airport,
            "arr_airport": arr_airport,
            "seat_class": fp.seat_class,
            "direction": fi.direction.value,
            "duration": duration_str,
            "stops": stops_str,
            # 回程字段（单程时为空字符串）
            "ret_flight": f"{ret.flight_no} ({ret.airline})" if ret else "",
            "ret_dep_time": ret.departure_time if ret else "",
            "ret_arr_time": _day_offset_marker(
                ret.arrival_time or "00:00",
                ret.departure_date,
                ret.arrival_date,
                ret.departure_time or "00:00",
            ) if ret else "",
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
        scale=alt.Scale(domain=["去哪儿", "携程"], range=["#3d7ff5", "#12b76a"]),
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
    """Render price trend chart with unified view (no tabs).

    Args:
        price_history: Historical price records for this route.
        target_price: Alert threshold shown as a dashed red line.
        route_name: Human-readable route label used in chart titles.
        is_roundtrip: Whether this is a round-trip route; controls the
            column layout of the records table.
        route_id: Unique route ID used to de-duplicate Streamlit widget keys.
    """
    if not price_history:
        st.info("该路线暂无价格历史数据。")
        return

    df = _build_dataframe(price_history)
    if df.empty:
        return

    _render_unified_view(df, target_price, route_name, is_roundtrip, key_suffix=str(route_id))


# ── Unified view ──────────────────────────────────────────────────────────────

def _render_unified_view(
    df: pd.DataFrame,
    target_price: Decimal,
    route_name: str,
    is_roundtrip: bool = False,
    key_suffix: str = "",
) -> None:
    """Unified price view: inline 3-column filter panel + chart + records table."""

    # ── Filter panel (3 columns inline) ─────────────────────────────────────
    all_dep_airports = sorted(a for a in df["dep_airport"].unique() if a)
    all_arr_airports = sorted(a for a in df["arr_airport"].unique() if a)

    f_col1, f_col2, f_col3 = st.columns([2, 2, 3])

    with f_col1:
        dep_range = st.slider(
            "起飞时间",
            min_value=dtime(0, 0),
            max_value=dtime(23, 59),
            value=(dtime(0, 0), dtime(23, 59)),
            key=f"dep_filter_{key_suffix}",
            help="保留起飞时间在此范围内的航班",
        )

    with f_col2:
        arr_range = st.slider(
            "到达时间",
            min_value=dtime(0, 0),
            max_value=dtime(23, 59),
            value=(dtime(0, 0), dtime(23, 59)),
            key=f"arr_filter_{key_suffix}",
            help="保留到达时间在此范围内的航班",
        )

    with f_col3:
        ap_col1, ap_col2 = st.columns(2)
        with ap_col1:
            sel_dep_airports = st.multiselect(
                "出发机场",
                options=all_dep_airports,
                default=all_dep_airports,
                key=f"dep_airport_filter_{key_suffix}",
                help="只保留从所选机场出发的航班",
            ) if all_dep_airports else []
        with ap_col2:
            sel_arr_airports = st.multiselect(
                "到达机场",
                options=all_arr_airports,
                default=all_arr_airports,
                key=f"arr_airport_filter_{key_suffix}",
                help="只保留到达所选机场的航班",
            ) if all_arr_airports else []

    # Chart type selector (compact horizontal radio inside filter panel)
    chart_type = st.radio(
        "图表类型",
        ["折线图", "价格区间"],
        horizontal=True,
        key=f"chart_type_{key_suffix}",
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

    if sel_dep_airports:
        df_f = df_f[df_f["dep_airport"].isin(sel_dep_airports) | (df_f["dep_airport"] == "")]
    if sel_arr_airports:
        df_f = df_f[df_f["arr_airport"].isin(sel_arr_airports) | (df_f["arr_airport"] == "")]

    flight_count = df_f["flight"].nunique()
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

    # ── Chart ────────────────────────────────────────────────────────────────
    df_agg = _agg_by_session(df_f)
    color = _color_enc(df_agg)

    if chart_type == "折线图":
        chart_layer = (
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
        title_str = f"价格趋势：{route_name}"
    else:
        tooltip = [
            alt.Tooltip("date:T", title="采集时间", format="%Y-%m-%d %H:%M"),
            alt.Tooltip("min_price:Q", title="最低价 (¥)", format=".0f"),
            alt.Tooltip("max_price:Q", title="最高价 (¥)", format=".0f"),
            alt.Tooltip("source_label:N", title="来源平台"),
            alt.Tooltip("count:Q", title="航班数量"),
        ]
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
        base_tick = alt.Chart(df_agg).mark_tick(size=14, thickness=2).encode(
            x="date:T",
            color=color,
        )
        tick_low = base_tick.encode(y=alt.Y("min_price:Q", scale=alt.Scale(zero=False)))
        tick_high = base_tick.encode(y=alt.Y("max_price:Q", scale=alt.Scale(zero=False)))
        chart_layer = rule + tick_low + tick_high
        title_str = f"价格区间：{route_name}"

    chart = (chart_layer + _target_rule(target_price)).properties(
        title=title_str,
        height=360,
    )
    st.altair_chart(chart, width="stretch")

    # ── Stats (4 core metrics) ───────────────────────────────────────────────
    st.markdown("#### 价格统计")
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

    # ── Records table ────────────────────────────────────────────────────────
    _render_compact_table(df_f, is_roundtrip)


def _render_compact_table(df_f: pd.DataFrame, is_roundtrip: bool = False) -> None:
    """Render the latest-batch records table with duration and stops columns."""
    st.markdown("#### 最近价格记录")

    # 找出每个平台最新的 batch_id，取那批次的所有记录
    latest_batch: dict[str, str] = {}
    for _, row in df_f.iterrows():
        bid = row["batch_id"]
        src = row["source"]
        if bid and (src not in latest_batch or bid > latest_batch[src]):
            latest_batch[src] = bid

    if latest_batch:
        mask = df_f.apply(
            lambda r: bool(r["batch_id"]) and r["batch_id"] == latest_batch.get(r["source"]),
            axis=1,
        )
        display_df = df_f[mask].copy()
    else:
        # 兜底：无 batch_id 时取每个平台最新 scraped_at 的记录
        latest_ts: dict[str, object] = {}
        for _, row in df_f.iterrows():
            src = row["source"]
            if src not in latest_ts or row["date"] > latest_ts[src]:
                latest_ts[src] = row["date"]
        display_df = df_f[
            df_f.apply(lambda r: r["date"] == latest_ts.get(r["source"]), axis=1)
        ].copy()

    display_df = display_df.sort_values("price", ascending=True).head(10).copy()
    display_df["date"] = display_df["date"].dt.strftime("%Y-%m-%d %H:%M")

    has_airports = display_df["dep_airport"].any() or display_df["arr_airport"].any()

    if is_roundtrip:
        display_df = display_df.rename(columns={
            "date": "采集时间",
            "price": "往返总价 (¥)",
            "source_label": "来源平台",
            "flight": "去程航班",
            "dep_time": "去程起飞",
            "arr_time": "去程到达",
            "duration": "去程时长",
            "dep_airport": "出发机场",
            "arr_airport": "到达机场",
            "ret_flight": "回程航班",
            "ret_dep_time": "回程起飞",
            "ret_arr_time": "回程到达",
            "seat_class": "舱位",
        })
        cols = ["采集时间", "来源平台", "往返总价 (¥)", "去程航班", "去程起飞", "去程到达", "去程时长"]
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
            "duration": "时长",
            "stops": "经停",
            "dep_airport": "出发机场",
            "arr_airport": "到达机场",
            "seat_class": "舱位",
        })
        cols = ["采集时间", "来源平台", "价格 (¥)", "航班", "起飞", "到达", "时长", "经停"]
        if has_airports:
            cols += ["出发机场", "到达机场"]
        cols += ["舱位"]
        st.dataframe(display_df[cols], width="stretch", hide_index=True)
