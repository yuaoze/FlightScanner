"""Unit tests for EmailNotifier."""

import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

import pytest

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from flightscanner.interfaces import FlightDirection, FlightInfo, FlightPrice, PriceTrend
from flightscanner.notifiers import EmailNotifier
from flightscanner.utils.config import Settings


@pytest.fixture
def mock_settings():
    """Create mock settings for testing."""
    settings = Mock(spec=Settings)
    settings.smtp_host = "smtp.example.com"
    settings.smtp_port = 587
    settings.smtp_user = "test@example.com"
    settings.smtp_password = "test_password"
    return settings


@pytest.fixture
def notifier(mock_settings):
    """Create an EmailNotifier instance."""
    return EmailNotifier(mock_settings)


@pytest.fixture
def sample_flight_price():
    """Create a sample flight price for testing."""
    flight_info = FlightInfo(
        flight_no="CA1234",
        airline="中国国航",
        departure_city="北京",
        arrival_city="上海",
        departure_time="08:00",
        arrival_time="10:30",
        departure_date=date.today() + timedelta(days=7),
        direction=FlightDirection.DEPARTURE,
    )

    return FlightPrice(
        flight_info=flight_info,
        price=Decimal("680.00"),
        currency="CNY",
        seat_class="经济舱",
        available_seats=15,
        scraped_at=datetime.now(timezone.utc),
        source="ctrip",
    )


@pytest.fixture
def sample_trend():
    """Create a sample price trend for testing."""
    return PriceTrend(
        direction="down",
        confidence=0.8,
        recommendation="价格呈下降趋势，建议继续观察。",
        predicted_lowest_price=Decimal("650.00"),
        best_booking_time=datetime.now(),
    )


class TestEmailNotifier:
    """Test cases for EmailNotifier."""

    def test_init_with_valid_settings(self, mock_settings):
        """Test EmailNotifier initialization with valid settings."""
        notifier = EmailNotifier(mock_settings)

        assert notifier.smtp_host == "smtp.example.com"
        assert notifier.smtp_port == 587
        assert notifier.smtp_user == "test@example.com"
        assert notifier.smtp_password == "test_password"

    @pytest.mark.asyncio
    async def test_send_alert_success(self, notifier: EmailNotifier, sample_flight_price: FlightPrice, sample_trend: PriceTrend):
        """Test successful email sending."""
        message = "Test alert message"

        with patch('smtplib.SMTP') as mock_smtp:
            # Configure mock SMTP server
            mock_server = MagicMock()
            mock_smtp.return_value.__enter__.return_value = mock_server

            # Send alert
            result = await notifier.send_alert(sample_flight_price, sample_trend, message)

            # Verify success
            assert result is True

            # Verify SMTP was called correctly
            mock_smtp.assert_called_once_with("smtp.example.com", 587)
            mock_server.starttls.assert_called_once()
            mock_server.login.assert_called_once_with("test@example.com", "test_password")
            mock_server.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_alert_without_smtp_config(self, sample_flight_price: FlightPrice, sample_trend: PriceTrend):
        """Test that send_alert raises error when SMTP is not configured."""
        # Create settings without SMTP config
        settings = Mock(spec=Settings)
        settings.smtp_host = None
        settings.smtp_port = 587
        settings.smtp_user = None
        settings.smtp_password = None

        notifier = EmailNotifier(settings)

        with pytest.raises(ValueError, match="SMTP not configured"):
            await notifier.send_alert(sample_flight_price, sample_trend, "Test message")

    @pytest.mark.asyncio
    async def test_send_alert_with_partial_smtp_config(self, sample_flight_price: FlightPrice, sample_trend: PriceTrend):
        """Test that send_alert raises error when SMTP is partially configured."""
        # Create settings with partial config
        settings = Mock(spec=Settings)
        settings.smtp_host = "smtp.example.com"
        settings.smtp_port = 587
        settings.smtp_user = "test@example.com"
        settings.smtp_password = None  # Missing password

        notifier = EmailNotifier(settings)

        with pytest.raises(ValueError, match="SMTP not configured"):
            await notifier.send_alert(sample_flight_price, sample_trend, "Test message")

    @pytest.mark.asyncio
    async def test_send_alert_handles_smtp_error(self, notifier: EmailNotifier, sample_flight_price: FlightPrice, sample_trend: PriceTrend):
        """Test that send_alert handles SMTP errors gracefully."""
        message = "Test alert message"

        with patch('smtplib.SMTP') as mock_smtp:
            # Simulate SMTP error
            mock_smtp.side_effect = Exception("SMTP connection failed")

            # Should raise the exception
            with pytest.raises(Exception, match="SMTP connection failed"):
                await notifier.send_alert(sample_flight_price, sample_trend, message)

    def test_build_subject_down_trend(self, notifier: EmailNotifier, sample_flight_price: FlightPrice):
        """Test subject line for down trend."""
        trend = PriceTrend(
            direction="down",
            confidence=0.8,
            recommendation="Test",
            predicted_lowest_price=None,
            best_booking_time=None,
        )

        subject = notifier._build_subject(sample_flight_price, trend)

        assert "📉" in subject
        assert "北京" in subject
        assert "上海" in subject
        assert "680" in subject

    def test_build_subject_up_trend(self, notifier: EmailNotifier, sample_flight_price: FlightPrice):
        """Test subject line for up trend."""
        trend = PriceTrend(
            direction="up",
            confidence=0.8,
            recommendation="Test",
            predicted_lowest_price=None,
            best_booking_time=None,
        )

        subject = notifier._build_subject(sample_flight_price, trend)

        assert "📈" in subject

    def test_build_subject_stable_trend(self, notifier: EmailNotifier, sample_flight_price: FlightPrice):
        """Test subject line for stable trend."""
        trend = PriceTrend(
            direction="stable",
            confidence=0.8,
            recommendation="Test",
            predicted_lowest_price=None,
            best_booking_time=None,
        )

        subject = notifier._build_subject(sample_flight_price, trend)

        assert "➡️" in subject

    def test_build_text_body(self, notifier: EmailNotifier, sample_flight_price: FlightPrice, sample_trend: PriceTrend):
        """Test plain text email body generation."""
        message = "Test message"
        body = notifier._build_text_body(sample_flight_price, sample_trend, message)

        assert "CA1234" in body
        assert "中国国航" in body
        assert "北京" in body
        assert "上海" in body
        assert "680" in body
        assert "down" in body
        assert "80%" in body
        assert message in body

    def test_build_html_body(self, notifier: EmailNotifier, sample_flight_price: FlightPrice, sample_trend: PriceTrend):
        """Test HTML email body generation."""
        message = "Test message"
        html = notifier._build_html_body(sample_flight_price, sample_trend, message)

        assert "<!DOCTYPE html>" in html
        assert "CA1234" in html
        assert "中国国航" in html
        assert "北京" in html
        assert "上海" in html
        assert "680" in html
        assert "down" in html
        assert message in html
        assert "<style>" in html  # Contains CSS
