"""Charts component for visualizing price trends.

This module provides visualization components for price history data.
"""

import streamlit as st
import pandas as pd
import altair as alt
from decimal import Decimal
from typing import List
from datetime import datetime

from flightscanner.interfaces import FlightPrice


def render_price_trend_chart(
    price_history: List[FlightPrice],
    target_price: Decimal,
    route_name: str = "Route",
):
    """Render a price trend chart using Altair.

    Args:
        price_history: List of historical flight prices.
        target_price: Target price threshold to display on chart.
        route_name: Name of the route for the chart title.
    """
    if not price_history:
        st.info("该路线暂无价格历史数据。")
        return

    # Convert to pandas DataFrame for Altair
    data = []
    for price in price_history:
        data.append({
            "date": price.scraped_at,
            "price": float(price.price),
            "flight": f"{price.flight_info.flight_no} ({price.flight_info.airline})",
            "departure": price.flight_info.departure_time,
            "arrival": price.flight_info.arrival_time,
        })

    df = pd.DataFrame(data)

    # Sort by date
    df = df.sort_values("date")

    # Create base chart
    base = alt.Chart(df).encode(
        x=alt.X("date:T", title="日期"),
        tooltip=[
            alt.Tooltip("date:T", title="日期", format="%Y-%m-%d %H:%M"),
            alt.Tooltip("price:Q", title="价格 (¥)", format=".0f"),
            alt.Tooltip("flight:N", title="航班"),
            alt.Tooltip("departure:N", title="出发"),
            alt.Tooltip("arrival:N", title="到达"),
        ]
    )

    # Price line
    line = base.mark_line(point=True, color="#1f77b4").encode(
        y=alt.Y("price:Q", title="价格 (¥)"),
    )

    # Target price reference line
    target_line = alt.Chart(pd.DataFrame({
        "target": [float(target_price)]
    })).mark_rule(color="red", strokeDash=[5, 5]).encode(
        y="target:Q",
    )

    # Combine charts
    chart = (line + target_line).properties(
        title=f"价格趋势：{route_name}",
        width=800,
        height=400,
    )

    st.altair_chart(chart, use_container_width=True)

    # Statistics
    render_price_statistics(df, target_price)


def render_price_statistics(df: pd.DataFrame, target_price: Decimal):
    """Render price statistics summary.

    Args:
        df: DataFrame with price history data.
        target_price: Target price threshold.
    """
    st.markdown("#### 价格统计")

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        avg_price = df["price"].mean()
        st.metric(
            label="平均价格",
            value=f"¥{avg_price:.0f}",
        )

    with col2:
        min_price = df["price"].min()
        min_date = df.loc[df["price"].idxmin(), "date"]
        st.metric(
            label="最低价格",
            value=f"¥{min_price:.0f}",
            delta=f"{min_date.strftime('%m/%d')}",
        )

    with col3:
        max_price = df["price"].max()
        max_date = df.loc[df["price"].idxmax(), "date"]
        st.metric(
            label="最高价格",
            value=f"¥{max_price:.0f}",
            delta=f"{max_date.strftime('%m/%d')}",
        )

    with col4:
        latest_price = df.iloc[-1]["price"]
        diff = latest_price - float(target_price)
        st.metric(
            label="当前价格",
            value=f"¥{latest_price:.0f}",
            delta=f"{'↓' if diff < 0 else '↑'}¥{abs(diff):.0f} 相比目标",
        )

    # Price history table
    st.markdown("#### 最近价格检查")

    # Show last 10 entries
    display_df = df.tail(10).copy()
    display_df["date"] = display_df["date"].dt.strftime("%Y-%m-%d %H:%M")
    display_df = display_df.rename(columns={
        "date": "抓取时间",
        "price": "价格 (¥)",
        "flight": "航班",
        "departure": "出发",
        "arrival": "到达",
    })

    st.dataframe(
        display_df,
        use_container_width=True,
        hide_index=True,
    )