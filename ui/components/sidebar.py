"""Sidebar component for adding new routes to monitor.

This module provides a form for users to add new routes to the monitoring system.
"""

import streamlit as st
from datetime import date, timedelta
from decimal import Decimal

from flightscanner.core.services import RouteService


def render_sidebar(session_factory):
    """Render the sidebar with route addition form.

    Args:
        session_factory: SessionLocal factory for creating database sessions.
    """
    st.header("添加新路线")

    min_date = date.today()
    max_date = min_date + timedelta(days=365)

    # Origin / destination
    origin = st.text_input(
        "出发城市",
        placeholder="例如：北京",
        help="输入出发城市的中文名称",
        key="sidebar_origin",
    )
    destination = st.text_input(
        "到达城市",
        placeholder="例如：上海",
        help="输入到达城市的中文名称",
        key="sidebar_destination",
    )

    # Target date
    target_date = st.date_input(
        "目标旅行日期",
        min_value=min_date,
        max_value=max_date,
        help="选择计划出行的日期",
        key="sidebar_target_date",
    )

    # Trip type — 使用普通 radio（不在 st.form 内），切换时立即触发重渲染
    trip_type_label = st.radio(
        "行程类型",
        options=["单程", "往返"],
        horizontal=True,
        help="单程仅监控去程价格；往返同时监控去程和回程",
        key="sidebar_trip_type",
    )
    trip_type = "roundtrip" if trip_type_label == "往返" else "oneway"

    # 回程日期：仅"往返"时显示
    return_date = None
    if trip_type == "roundtrip":
        return_date = st.date_input(
            "回程日期",
            min_value=target_date + timedelta(days=1),
            max_value=max_date,
            help="选择回程日期（须晚于出发日期）",
            key="sidebar_return_date",
        )

    # International checkbox
    is_international = st.checkbox(
        "国际航班",
        value=False,
        help="勾选后请使用中文城市名，如「东京」、「首尔」（系统将自动识别国际路线）",
        key="sidebar_is_international",
    )

    # Target price
    target_price = st.number_input(
        "目标价格 (¥)",
        min_value=100,
        max_value=50000,
        value=800,
        step=50,
        help="设置您的目标价格，当价格低于此价格时会收到提醒",
        key="sidebar_target_price",
    )

    # Scrape interval
    scrape_interval = st.select_slider(
        "采集间隔（小时）",
        options=[1, 2, 3, 4, 6, 8, 12, 24],
        value=6,
        help="设置价格采集的时间间隔",
        key="sidebar_scrape_interval",
    )

    # Submit button
    if st.button("添加路线", type="primary", use_container_width=True):
        # Validation
        if not origin or not destination:
            st.error("请填写出发城市和到达城市。")
            return

        if origin == destination:
            st.error("出发城市和到达城市不能相同。")
            return

        if trip_type == "roundtrip" and return_date is None:
            st.error("往返程请选择回程日期。")
            return

        # Add route to database
        try:
            session = session_factory()
            try:
                service = RouteService(session)
                route = service.add_route(
                    origin=origin,
                    destination=destination,
                    target_date=target_date,
                    target_price=Decimal(str(target_price)),
                    scrape_interval=scrape_interval,
                    return_date=return_date,
                    trip_type=trip_type,
                    is_international=is_international,
                )
                # 通知 app.py 立即采集并注册定期任务
                st.session_state["new_route_id"] = route.id
                trip_label = "往返" if trip_type == "roundtrip" else "单程"
                st.success(
                    f"路线已添加：{origin} → {destination}，{trip_label}，"
                    f"日期：{target_date}，采集间隔：{scrape_interval}小时"
                )
                st.rerun()
            finally:
                session.close()
        except ValueError as e:
            st.error(str(e))
        except Exception as e:
            st.error(f"添加路线失败：{e}")

    # Instructions
    st.markdown("---")
    st.markdown(
        """
        ### 使用说明

        1. **添加路线**：填写上方表单开始监控航线
        2. **自动抓取**：系统按每条路线设置的间隔自动抓取价格
        3. **立即采集**：点击路线列表中的🔄按钮立即采集价格
        4. **调整间隔**：点击"⚙️ 采集间隔"调整监控频率
        5. **价格提醒**：当价格降至目标价格以下时会收到通知
        6. **趋势追踪**：在仪表板中查看价格历史趋势

        ### 小提示

        - 使用中文城市名称以获得更好的匹配效果
        - 根据当前价格设置合理的目标价格
        - 建议在出行前2-3周开始监控路线
        - 采集间隔越短，监控越及时，但对目标网站压力越大
        - 使用🔄按钮可以随时手动触发价格采集
        - 每条路线可以设置不同的采集间隔

        ### v1.0 新特性

        - ✨ 支持为每条路线单独设置采集间隔
        - ✨ 新增立即采集按钮（调试模式）
        - ✨ 无头模式下自动弹出二维码并等待登录完成
        - ✨ 登录后自动继续采集，无需手动重启
        """
    )
