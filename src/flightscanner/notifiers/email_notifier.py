"""Email notification implementation.

This module provides email notification functionality using SMTP
for sending price alerts to users.
"""

import json
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional

import smtplib

from flightscanner.interfaces import FlightPrice, Notifier, PriceTrend
from flightscanner.utils.config import Settings

logger = logging.getLogger(__name__)


class EmailNotifier(Notifier):
    """Email-based notifier for price alerts.

    This notifier sends price alert emails using SMTP configuration.

    Attributes:
        settings: Application settings containing SMTP configuration.
    """

    def __init__(self, settings: Settings):
        """Initialize the email notifier.

        Args:
            settings: Application settings with SMTP configuration.
        """
        self.settings = settings
        self.smtp_host = settings.smtp_host
        self.smtp_port = settings.smtp_port
        self.smtp_user = settings.smtp_user
        self.smtp_password = settings.smtp_password

    async def send_alert(
        self, flight_price: FlightPrice, trend: PriceTrend, message: str
    ) -> bool:
        """Send a price alert email.

        Args:
            flight_price: Flight price information.
            trend: Price trend analysis.
            message: Alert message to send.

        Returns:
            True if notification was sent successfully.

        Raises:
            ValueError: If SMTP is not configured.
            Exception: If email sending fails.
        """
        # Check SMTP configuration
        if not all([self.smtp_host, self.smtp_user, self.smtp_password]):
            raise ValueError(
                "SMTP not configured. Please set SMTP_HOST, SMTP_USER, and SMTP_PASSWORD."
            )

        try:
            # Create email message
            msg = MIMEMultipart("alternative")
            msg["From"] = self.smtp_user
            msg["To"] = self.smtp_user  # Send to self
            msg["Subject"] = self._build_subject(flight_price, trend)

            # Create plain text and HTML versions
            text_body = self._build_text_body(flight_price, trend, message)
            html_body = self._build_html_body(flight_price, trend, message)

            msg.attach(MIMEText(text_body, "plain", "utf-8"))
            msg.attach(MIMEText(html_body, "html", "utf-8"))

            # Send email
            logger.info(f"Sending email alert to {self.smtp_user}")

            with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                server.starttls()
                server.login(self.smtp_user, self.smtp_password)
                server.send_message(msg)

            logger.info("Email alert sent successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to send email alert: {e}")
            raise

    def _build_subject(self, flight_price: FlightPrice, trend: PriceTrend) -> str:
        """Build email subject line.

        Args:
            flight_price: Flight price information.
            trend: Price trend analysis.

        Returns:
            Email subject string.
        """
        direction_emoji = {"down": "📉", "up": "📈", "stable": "➡️"}
        emoji = direction_emoji.get(trend.direction, "")

        return (
            f"{emoji} FlightScanner 价格提醒: "
            f"{flight_price.flight_info.departure_city} → "
            f"{flight_price.flight_info.arrival_city} "
            f"¥{flight_price.price}"
        )

    def _build_text_body(
        self, flight_price: FlightPrice, trend: PriceTrend, message: str
    ) -> str:
        """Build plain text email body.

        Args:
            flight_price: Flight price information.
            trend: Price trend analysis.
            message: Alert message (JSON NotifyContext or plain text).

        Returns:
            Plain text email body.
        """
        flight = flight_price.flight_info
        ctx = self._parse_message(message)

        # 买点增强信息（仅在解析成功时展示）
        extra = ""
        if ctx.get("avg_30d", 0) > 0:
            reason_labels = {
                "target_hit": "已达目标价 🎯",
                "near_30d_low": "接近30天最低价 📉",
                "below_avg": "显著低于均价 💡",
                "rebound_warning": "价格反弹预警 ⚠️",
                "departure_approaching": "出发临近提醒 🔔",
                "trend_down": "趋势加速下降 📊",
            }
            reason_label = reason_labels.get(ctx.get("trigger_reason", ""), ctx.get("trigger_reason", ""))
            extra = (
                f"\n买点分析:\n"
                f"  30天均价: ¥{ctx['avg_30d']:.0f}\n"
                f"  30天最低: ¥{ctx['min_30d']:.0f}\n"
                f"  低于均价: {abs(ctx.get('pct_vs_avg', 0)):.1f}%\n"
                f"  触发原因: {reason_label}\n"
                f"  买点建议: {ctx.get('recommendation', '')}\n"
            )
            trigger = ctx.get("trigger_reason", "")
            if trigger == "departure_approaching" and ctx.get("days_until_departure") is not None:
                extra += f"  ⏰ 距出发仅剩 {ctx['days_until_departure']} 天\n"
            elif trigger == "rebound_warning" and ctx.get("rebound_pct"):
                extra += f"  ⚠️ 从近期低点 ¥{ctx.get('recent_low', 0):.0f} 反弹 {ctx['rebound_pct']:.1f}%\n"
            elif trigger == "trend_down" and ctx.get("trend_batches"):
                extra += f"  📊 连续 {ctx['trend_batches']} 次采集价格持续下降\n"
            if ctx.get("ai_reason"):
                extra += f"  🤖 AI: {ctx['ai_reason']}\n"

        body = f"""
FlightScanner 价格提醒
{'=' * 50}

航班信息:
  航班号: {flight.flight_no}
  航空公司: {flight.airline}
  航线: {flight.departure_city} → {flight.arrival_city}
  日期: {flight.departure_date}
  时间: {flight.departure_time} - {flight.arrival_time}

价格信息:
  当前价格: ¥{flight_price.price}
  舱位等级: {flight_price.seat_class}
  数据来源: {flight_price.source}
{extra}
趋势分析:
  趋势方向: {trend.direction}
  置信度: {trend.confidence:.0%}
  建议: {trend.recommendation}

{'=' * 50}

此邮件由 FlightScanner 自动发送。
"""
        return body

    def _build_html_body(
        self, flight_price: FlightPrice, trend: PriceTrend, message: str
    ) -> str:
        """Build HTML email body.

        Args:
            flight_price: Flight price information.
            trend: Price trend analysis.
            message: Alert message (JSON NotifyContext or plain text).

        Returns:
            HTML email body.
        """
        flight = flight_price.flight_info
        ctx = self._parse_message(message)

        # Color coding for trend direction
        trend_colors = {"down": "#28a745", "up": "#dc3545", "stable": "#ffc107"}
        trend_color = trend_colors.get(trend.direction, "#6c757d")

        # 买点增强 HTML 块（仅在解析成功时展示）
        extra_html = ""
        if ctx.get("avg_30d", 0) > 0:
            reason_labels = {
                "target_hit": "已达目标价 🎯",
                "near_30d_low": "接近30天最低价 📉",
                "below_avg": "显著低于均价 💡",
                "rebound_warning": "价格反弹预警 ⚠️",
                "departure_approaching": "出发临近提醒 🔔",
                "trend_down": "趋势加速下降 📊",
            }
            reason_label = reason_labels.get(ctx.get("trigger_reason", ""), ctx.get("trigger_reason", ""))

            # 场景化附加段落
            scenario_html = ""
            trigger = ctx.get("trigger_reason", "")
            if trigger == "departure_approaching" and ctx.get("days_until_departure") is not None:
                scenario_html = f'<p style="color:#dc3545;">⏰ 距出发仅剩 <strong>{ctx["days_until_departure"]}</strong> 天，建议尽快购买！</p>'
            elif trigger == "rebound_warning" and ctx.get("rebound_pct"):
                scenario_html = f'<p style="color:#dc3545;">⚠️ 从近期低点 ¥{ctx.get("recent_low", 0):.0f} 反弹 {ctx["rebound_pct"]:.1f}%，购买窗口可能正在关闭</p>'
            elif trigger == "trend_down" and ctx.get("trend_batches"):
                scenario_html = f'<p style="color:#17a2b8;">📊 连续 {ctx["trend_batches"]} 次采集价格持续下降，良好买点正在形成</p>'

            ai_html = ""
            if ctx.get("ai_reason"):
                ai_html = f'<p>🤖 <strong>AI 分析：</strong>{ctx["ai_reason"]}</p>'
                if ctx.get("ai_prediction_7d"):
                    ai_html += f'<p>📈 未来7天：{ctx["ai_prediction_7d"]}</p>'

            extra_html = f"""
            <div class="flight-info">
                <h2>买点分析</h2>
                <table style="width:100%; border-collapse:collapse;">
                    <tr>
                        <td style="padding:4px 8px;"><strong>30天均价</strong></td>
                        <td style="padding:4px 8px;">¥{ctx['avg_30d']:.0f}</td>
                        <td style="padding:4px 8px;"><strong>30天最低</strong></td>
                        <td style="padding:4px 8px;">¥{ctx['min_30d']:.0f}</td>
                    </tr>
                    <tr>
                        <td style="padding:4px 8px;"><strong>低于均价</strong></td>
                        <td style="padding:4px 8px; color:#28a745;">{abs(ctx.get('pct_vs_avg', 0)):.1f}%</td>
                        <td style="padding:4px 8px;"><strong>低于目标价</strong></td>
                        <td style="padding:4px 8px; color:#28a745;">{abs(ctx.get('pct_vs_target', 0)):.1f}%</td>
                    </tr>
                </table>
                <p><strong>触发原因：</strong>{reason_label}</p>
                <p><strong>买点建议：</strong><span style="color:#28a745; font-weight:bold;">{ctx.get('recommendation', '')}</span></p>
                {scenario_html}
                {ai_html}
            </div>"""

        html = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <style>
        body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                   color: white; padding: 20px; border-radius: 10px 10px 0 0; }}
        .content {{ background: #f9f9f9; padding: 20px; border: 1px solid #ddd; }}
        .flight-info {{ background: white; padding: 15px; margin: 10px 0;
                        border-radius: 5px; border-left: 4px solid #667eea; }}
        .price {{ font-size: 24px; font-weight: bold; color: #667eea; }}
        .trend {{ padding: 10px; border-radius: 5px; margin: 10px 0; }}
        .trend-down {{ background: #d4edda; color: #155724; }}
        .trend-up {{ background: #f8d7da; color: #721c24; }}
        .trend-stable {{ background: #fff3cd; color: #856404; }}
        .footer {{ text-align: center; color: #6c757d; padding: 20px; font-size: 12px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>✈️ FlightScanner 价格提醒</h1>
        </div>

        <div class="content">
            <div class="flight-info">
                <h2>航班信息</h2>
                <p><strong>航班号:</strong> {flight.flight_no}</p>
                <p><strong>航空公司:</strong> {flight.airline}</p>
                <p><strong>航线:</strong> {flight.departure_city} → {flight.arrival_city}</p>
                <p><strong>日期:</strong> {flight.departure_date}</p>
                <p><strong>时间:</strong> {flight.departure_time} - {flight.arrival_time}</p>
            </div>

            <div class="flight-info">
                <h2>价格信息</h2>
                <p class="price">¥{flight_price.price}</p>
                <p><strong>舱位等级:</strong> {flight_price.seat_class}</p>
                <p><strong>数据来源:</strong> {flight_price.source}</p>
            </div>
            {extra_html}
            <div class="trend trend-{trend.direction}" style="border-left: 4px solid {trend_color};">
                <h3>趋势分析</h3>
                <p><strong>趋势方向:</strong> {trend.direction.upper()}</p>
                <p><strong>置信度:</strong> {trend.confidence:.0%}</p>
                <p><strong>建议:</strong> {trend.recommendation}</p>
            </div>
        </div>

        <div class="footer">
            <p>此邮件由 FlightScanner 自动发送</p>
            <p>© 2024 FlightScanner. All rights reserved.</p>
        </div>
    </div>
</body>
</html>
"""
        return html

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
