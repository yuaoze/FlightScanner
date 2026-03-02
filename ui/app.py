"""Main Streamlit application for FlightScanner.

This module provides the main entry point for the FlightScanner web dashboard.
"""

import streamlit as st
import asyncio

from ui.utils.db import get_session, get_session_local
from ui.components.sidebar import render_sidebar
from ui.components.overview import render_overview_cards, render_route_list
from ui.components.charts import render_price_trend_chart
from flightscanner.core.services import RouteService


def trigger_immediate_scrape(route_id: int):
    """Trigger immediate price scraping for a specific route.

    Args:
        route_id: The route ID to scrape.
    """
    try:
        # Import here to avoid circular dependency
        from flightscanner.scheduler.price_monitor import PriceMonitorScheduler
        from flightscanner.utils.config import settings

        # Create a temporary monitor instance
        monitor = PriceMonitorScheduler(
            headless=settings.scraper_headless,
            enable_notifications=False,
        )

        # Get the route
        with get_session() as session:
            route_service = RouteService(session)
            route = route_service.get_route_by_id(route_id)

            if route:
                # Run the scrape synchronously
                with st.spinner(f"正在采集 {route.origin} → {route.destination} 的价格..."):
                    asyncio.run(monitor.scrape_route(route))
                    asyncio.run(monitor.scraper.close())
                st.success(f"价格采集完成！")
            else:
                st.error("路线不存在")

    except Exception as e:
        st.error(f"价格采集失败：{e}")


def main():
    """Main function to run the FlightScanner dashboard."""
    # Page configuration
    st.set_page_config(
        page_title="航班价格监控",
        page_icon=":airplane:",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    # Sidebar: Add route form
    with st.sidebar:
        render_sidebar(get_session_local())

    # Main area
    st.title("航班价格监控")
    st.markdown("### 飞行价格监控仪表板")

    # Get routes data
    with get_session() as session:
        route_service = RouteService(session)
        routes = route_service.get_all_routes()

        # Check for immediate scrape triggers
        for route in routes:
            if st.session_state.get(f"trigger_scrape_{route.id}", False):
                # Clear the trigger
                st.session_state[f"trigger_scrape_{route.id}"] = False
                # Execute the scrape
                trigger_immediate_scrape(route.id)

        # Overview cards
        st.markdown("## 概览")
        render_overview_cards(routes)

        st.markdown("---")

        # Route list with actions
        render_route_list(routes, route_service)

        st.markdown("---")

        # Price trend chart
        if routes:
            st.markdown("## 价格趋势")

            # Route selector
            route_options = {
                f"{r.origin} → {r.destination} ({r.target_date})": r
                for r in routes
            }

            selected_route_name = st.selectbox(
                "选择要查看价格历史的路线：",
                options=list(route_options.keys()),
            )

            selected_route = route_options[selected_route_name]

            # Get price history for selected route
            if selected_route.price_count > 0:
                price_history = route_service.get_route_price_history(
                    selected_route.id,
                    days=30,
                )

                render_price_trend_chart(
                    price_history=price_history,
                    target_price=selected_route.target_price,
                    route_name=f"{selected_route.origin} → {selected_route.destination}",
                )
            else:
                st.info(
                    "该路线暂无价格历史数据。系统将根据设置的间隔自动抓取价格，或点击🔄按钮立即采集。"
                )


if __name__ == "__main__":
    main()