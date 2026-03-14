"""WeCom (企业微信) group robot notification implementation.

This module provides a WeCom Webhook-based notifier for price alerts,
posting Markdown-formatted messages to a WeCom group robot.
"""

import json
import logging
from typing import Optional

import httpx

from flightscanner.interfaces import FlightPrice, Notifier, PriceTrend
from flightscanner.utils.config import settings

logger = logging.getLogger(__name__)


class WeComNotifier(Notifier):
    """WeCom group robot notifier for price alerts.

    Sends Markdown-formatted price alert messages to a WeCom group
    chat via the Webhook API.

    Attributes:
        webhook_url: WeCom group robot Webhook URL.
    """

    def __init__(self, webhook_url: Optional[str] = None):
        """Initialize the WeCom notifier.

        Args:
            webhook_url: WeCom Webhook URL. Defaults to settings.wecom_webhook_url.
        """
        self.webhook_url = webhook_url or settings.wecom_webhook_url

    async def send_alert(
        self,
        flight_price: FlightPrice,
        trend: PriceTrend,
        message: str,
    ) -> bool:
        """Send a price alert via WeCom group robot Webhook.

        Args:
            flight_price: Flight price information.
            trend: Price trend analysis result.
            message: Alert message text.

        Returns:
            True if the message was sent successfully.

        Raises:
            ValueError: If webhook_url is not configured.
            httpx.HTTPError: If the HTTP request fails.
        """
        if not self.webhook_url:
            raise ValueError(
                "WeCom webhook URL is not configured (WECOM_WEBHOOK_URL)"
            )

        content = self._build_message(flight_price, trend, message)
        payload = {
            "msgtype": "markdown",
            "markdown": {"content": content},
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(self.webhook_url, json=payload)
                response.raise_for_status()
                logger.info(
                    f"WeCom alert sent for flight {flight_price.flight_info.flight_no}"
                )
                return True
        except httpx.HTTPError as e:
            logger.error(f"Failed to send WeCom alert: {e}")
            raise

    def _build_message(
        self,
        flight_price: FlightPrice,
        trend: PriceTrend,
        message: str,
    ) -> str:
        """Build a Markdown-formatted WeCom message.

        Args:
            flight_price: Flight price information.
            trend: Price trend analysis result.
            message: Base alert message (JSON NotifyContext or plain text).

        Returns:
            Formatted Markdown message string.
        """
        direction_emoji = {"down": "📉", "up": "📈", "stable": "➡️"}.get(
            trend.direction, "➡️"
        )
        fi = flight_price.flight_info
        ctx = self._parse_message(message)

        # 买点增强信息（仅在解析成功时展示）
        extra = ""
        if ctx.get("avg_30d", 0) > 0:
            reason_labels = {
                "target_hit": "已达目标价 🎯",
                "near_30d_low": "接近30天最低价 📉",
                "below_avg": "显著低于均价 💡",
            }
            reason_label = reason_labels.get(
                ctx.get("trigger_reason", ""), ctx.get("trigger_reason", "")
            )
            extra = (
                f"\n**买点分析**\n"
                f"> **30天均价**：¥{ctx['avg_30d']:.0f}　**30天最低**：¥{ctx['min_30d']:.0f}\n"
                f"> **低于均价**：<font color=\"info\">{abs(ctx.get('pct_vs_avg', 0)):.1f}%</font>\n"
                f"> **触发原因**：{reason_label}\n"
                f"> **买点建议**：<font color=\"warning\">{ctx.get('recommendation', '')}</font>\n"
            )

        return (
            f"## ✈️ 机票价格提醒\n\n"
            f"> **航班**：{fi.flight_no} ({fi.airline})\n"
            f"> **航线**：{fi.departure_city} → {fi.arrival_city}\n"
            f"> **日期**：{fi.departure_date}\n"
            f"> **出发**：{fi.departure_time}　**到达**：{fi.arrival_time}\n"
            f"> **舱位**：{flight_price.seat_class}\n\n"
            f"**当前价格**：<font color=\"warning\">¥{flight_price.price:.0f}</font>\n\n"
            f"**趋势**：{direction_emoji} {trend.direction} "
            f"（置信度 {trend.confidence:.0%}）\n\n"
            f"**建议**：{trend.recommendation}\n"
            f"{extra}"
        )

    @staticmethod
    def _parse_message(message: str) -> dict:
        """将 JSON 消息字符串反序列化为字典。

        Args:
            message: JSON 格式的消息字符串（或普通文本）。

        Returns:
            包含通知上下文字段的字典；解析失败时返回空字典。
        """
        try:
            return json.loads(message)
        except Exception:
            return {}
