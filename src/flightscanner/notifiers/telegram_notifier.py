"""Telegram notification implementation.

This module provides a Telegram bot-based notifier for price alerts,
using the Telegram Bot API to send Markdown-formatted messages.
"""

import json
import logging
from typing import Optional

import httpx

from flightscanner.interfaces import FlightPrice, Notifier, PriceTrend
from flightscanner.utils.config import settings

logger = logging.getLogger(__name__)


class TelegramNotifier(Notifier):
    """Telegram bot notifier for price alerts.

    Sends price alert messages to a Telegram chat via the Bot API,
    using Markdown formatting for readability.

    Attributes:
        bot_token: Telegram bot token.
        chat_id: Target Telegram chat ID.
    """

    def __init__(
        self,
        bot_token: Optional[str] = None,
        chat_id: Optional[str] = None,
    ):
        """Initialize the Telegram notifier.

        Args:
            bot_token: Telegram bot token. Defaults to settings.telegram_bot_token.
            chat_id: Target chat ID. Defaults to settings.telegram_chat_id.
        """
        self.bot_token = bot_token or settings.telegram_bot_token
        self.chat_id = chat_id or settings.telegram_chat_id

    async def send_alert(
        self,
        flight_price: FlightPrice,
        trend: PriceTrend,
        message: str,
    ) -> bool:
        """Send a price alert via Telegram Bot API.

        Args:
            flight_price: Flight price information.
            trend: Price trend analysis result.
            message: Alert message text.

        Returns:
            True if the message was sent successfully.

        Raises:
            ValueError: If bot_token or chat_id is not configured.
            httpx.HTTPError: If the HTTP request fails.
        """
        if not self.bot_token:
            raise ValueError("Telegram bot token is not configured (TELEGRAM_BOT_TOKEN)")
        if not self.chat_id:
            raise ValueError("Telegram chat ID is not configured (TELEGRAM_CHAT_ID)")

        text = self._build_message(flight_price, trend, message)
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "Markdown",
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(url, json=payload)
                response.raise_for_status()
                logger.info(
                    f"Telegram alert sent for flight {flight_price.flight_info.flight_no}"
                )
                return True
        except httpx.HTTPError as e:
            logger.error(f"Failed to send Telegram alert: {e}")
            raise

    def _build_message(
        self,
        flight_price: FlightPrice,
        trend: PriceTrend,
        message: str,
    ) -> str:
        """Build a Markdown-formatted Telegram message.

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
                f"\n*买点分析*\n"
                f"30天均价：¥{ctx['avg_30d']:.0f}　最低：¥{ctx['min_30d']:.0f}\n"
                f"低于均价：{abs(ctx.get('pct_vs_avg', 0)):.1f}%\n"
                f"触发原因：{reason_label}\n"
                f"买点建议：*{ctx.get('recommendation', '')}*\n"
            )

        return (
            f"✈️ *机票价格提醒*\n\n"
            f"*航班*：{fi.flight_no} ({fi.airline})\n"
            f"*航线*：{fi.departure_city} → {fi.arrival_city}\n"
            f"*日期*：{fi.departure_date}\n"
            f"*出发*：{fi.departure_time}  *到达*：{fi.arrival_time}\n"
            f"*舱位*：{flight_price.seat_class}\n\n"
            f"*当前价格*：¥{flight_price.price:.0f}\n"
            f"*趋势*：{direction_emoji} {trend.direction} "
            f"（置信度 {trend.confidence:.0%}）\n\n"
            f"*建议*：{trend.recommendation}\n"
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
