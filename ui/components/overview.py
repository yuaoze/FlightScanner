"""Overview component for displaying route metrics and list.

This module provides components for displaying summary cards and route details.
"""

import streamlit as st
from datetime import date
from decimal import Decimal
from typing import List

from flightscanner.core.services import RouteService, RouteWithLatestPrice


def render_overview_cards(routes: List[RouteWithLatestPrice]):
    """Render metric cards showing route statistics.

    Args:
        routes: List of routes with price information.
    """
    # Calculate metrics
    total_routes = len(routes)
    active_routes = sum(1 for r in routes if r.is_active)
    upcoming_routes = sum(
        1 for r in routes if r.is_active and r.target_date >= date.today()
    )

    # Routes below target price
    below_target = sum(
        1 for r in routes
        if r.latest_price and r.latest_price <= r.target_price
    )

    # Render cards in columns
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric(
            label="总路线数",
            value=total_routes,
            help="正在监控的路线总数",
        )

    with col2:
        st.metric(
            label="活跃路线",
            value=active_routes,
            help="当前正在监控的路线数",
        )

    with col3:
        st.metric(
            label="达到目标",
            value=below_target,
            help="当前价格等于或低于目标价格的路线数",
        )

    with col4:
        st.metric(
            label="即将出行",
            value=upcoming_routes,
            help="未来日期的活跃路线数",
        )


def render_route_list(routes: List[RouteWithLatestPrice], route_service: RouteService):
    """Render the list of monitored routes with actions.

    Args:
        routes: List of routes with price information.
        route_service: Route service for performing actions.
    """
    if not routes:
        st.info(
            "暂无监控路线。请在侧边栏添加一条路线开始监控。"
        )
        return

    st.markdown("### 监控路线列表")

    # Display routes in a table-like format
    for route in routes:
        with st.container():
            # Route header row
            col1, col2, col3, col4, col5 = st.columns([2, 1.5, 1.5, 1, 1.5])

            with col1:
                # Route name and status badge
                status_emoji = "✅" if route.is_active else "⏸️"
                price_status = ""
                if route.latest_price:
                    if route.latest_price <= route.target_price:
                        price_status = "🎯"
                    else:
                        diff = route.latest_price - route.target_price
                        price_status = f"↑¥{diff:.0f}"

                st.markdown(
                    f"**{status_emoji} {route.origin} → {route.destination}** {price_status}"
                )

            with col2:
                st.text(f"日期：{route.target_date}")

            with col3:
                if route.latest_price:
                    st.text(f"最新：¥{route.latest_price:.0f}")
                else:
                    st.text("最新：暂无")

            with col4:
                st.text(f"目标：¥{route.target_price:.0f}")

            with col5:
                # Action buttons
                action_col1, action_col2, action_col3 = st.columns(3)

                with action_col1:
                    # Toggle status button
                    if st.button(
                        "⏸️" if route.is_active else "▶️",
                        key=f"toggle_{route.id}",
                        help="暂停" if route.is_active else "激活",
                    ):
                        route_service.toggle_route_status(route.id)
                        st.rerun()

                with action_col2:
                    # Debug: Immediate scrape button
                    if st.button(
                        "🔄",
                        key=f"scrape_{route.id}",
                        help="立即采集价格",
                    ):
                        # Store the route ID to trigger scraping
                        st.session_state[f"trigger_scrape_{route.id}"] = True
                        st.rerun()

                with action_col3:
                    # Delete button
                    if st.button(
                        "🗑️",
                        key=f"delete_{route.id}",
                        help="删除路线",
                    ):
                        route_service.delete_route(route.id)
                        st.rerun()

            # Route details with interval setting
            detail_col1, detail_col2, detail_col3 = st.columns([2, 2, 2])
            with detail_col1:
                st.caption(
                    f"创建时间：{route.created_at.strftime('%Y-%m-%d %H:%M')} | "
                    f"价格记录：{route.price_count}条"
                )
            with detail_col2:
                if route.latest_scraped_at:
                    st.caption(
                        f"最后检查：{route.latest_scraped_at.strftime('%Y-%m-%d %H:%M')}"
                    )
            with detail_col3:
                # Interval setting with expander
                with st.popover(f"⚙️ 采集间隔：{route.scrape_interval}小时", use_container_width=False):
                    new_interval = st.select_slider(
                        "设置采集间隔",
                        options=[1, 2, 3, 4, 6, 8, 12, 24],
                        value=route.scrape_interval,
                        key=f"interval_{route.id}",
                    )
                    if st.button("更新", key=f"update_interval_{route.id}"):
                        route_service.update_route_interval(route.id, new_interval)
                        st.success(f"采集间隔已更新为 {new_interval} 小时")
                        st.rerun()

            st.markdown("---")