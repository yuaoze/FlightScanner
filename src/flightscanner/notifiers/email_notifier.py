"""Email notification implementation.

This module provides email notification functionality using SMTP
for sending price alerts to users.
"""

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
            message: Alert message.

        Returns:
            Plain text email body.
        """
        flight = flight_price.flight_info

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

趋势分析:
  趋势方向: {trend.direction}
  置信度: {trend.confidence:.0%}
  建议: {trend.recommendation}

{'=' * 50}
{message}

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
            message: Alert message.

        Returns:
            HTML email body.
        """
        flight = flight_price.flight_info

        # Color coding for trend direction
        trend_colors = {"down": "#28a745", "up": "#dc3545", "stable": "#ffc107"}
        trend_color = trend_colors.get(trend.direction, "#6c757d")

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

            <div class="trend trend-{trend.direction}" style="border-left: 4px solid {trend_color};">
                <h3>趋势分析</h3>
                <p><strong>趋势方向:</strong> {trend.direction.upper()}</p>
                <p><strong>置信度:</strong> {trend.confidence:.0%}</p>
                <p><strong>建议:</strong> {trend.recommendation}</p>
            </div>

            <div class="flight-info">
                <p>{message}</p>
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
