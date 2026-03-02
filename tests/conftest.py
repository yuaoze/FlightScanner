"""Pytest configuration for FlightScanner tests."""

import sys
from pathlib import Path

import pytest

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))


@pytest.fixture
def mock_flight_info():
    """Create a mock FlightInfo for testing."""
    from datetime import date, timedelta
    from flightscanner.interfaces import FlightInfo, FlightDirection

    return FlightInfo(
        flight_no="CA1234",
        airline="中国国航",
        departure_city="北京",
        arrival_city="上海",
        departure_time="08:00",
        arrival_time="10:30",
        departure_date=date.today() + timedelta(days=7),
        direction=FlightDirection.DEPARTURE,
    )


@pytest.fixture
def mock_flight_price(mock_flight_info):
    """Create a mock FlightPrice for testing."""
    from datetime import datetime, timezone
    from decimal import Decimal
    from flightscanner.interfaces import FlightPrice

    return FlightPrice(
        flight_info=mock_flight_info,
        price=Decimal("680.00"),
        currency="CNY",
        seat_class="经济舱",
        available_seats=15,
        scraped_at=datetime.now(timezone.utc),
        source="ctrip",
    )
