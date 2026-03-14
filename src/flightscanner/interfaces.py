"""Abstract base classes defining the core interfaces for FlightScanner.

This module defines the contracts that all concrete implementations must follow,
enabling loose coupling and easy testing through dependency injection.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, date
from decimal import Decimal
from enum import Enum
from typing import Optional, List


class FlightDirection(str, Enum):
    """Flight direction type."""
    DEPARTURE = "departure"
    RETURN = "return"


@dataclass
class FlightInfo:
    """Data class representing a flight's basic information.

    Attributes:
        flight_no: Flight number (e.g., "CA1234")
        airline: Airline name (e.g., "中国国航")
        departure_city: Departure city name
        arrival_city: Arrival city name
        departure_time: Scheduled departure time
        arrival_time: Scheduled arrival time
        departure_date: Flight date
        direction: Whether this is departure or return flight
        departure_airport: Full name of departure airport (optional)
        arrival_airport: Full name of arrival airport (optional)
        departure_airport_code: IATA code of departure airport (optional)
        arrival_airport_code: IATA code of arrival airport (optional)
    """
    flight_no: str
    airline: str
    departure_city: str
    arrival_city: str
    departure_time: str
    arrival_time: str
    departure_date: date
    direction: FlightDirection
    departure_airport: Optional[str] = None       # 出发机场全称
    arrival_airport: Optional[str] = None         # 到达机场全称
    departure_airport_code: Optional[str] = None  # IATA 代码
    arrival_airport_code: Optional[str] = None    # IATA 代码


@dataclass
class FlightPrice:
    """Data class representing a flight price snapshot.

    For one-way flights ``return_flight_info`` is ``None`` and ``price`` is
    the single-leg fare.  For round-trip combined records ``return_flight_info``
    holds the return-leg details and ``price`` is the combined total fare.

    Attributes:
        flight_info: Outbound (or single-leg) flight information.
        price: Fare — single-leg for one-way, combined total for round-trip.
        currency: Currency code (e.g., "CNY").
        seat_class: Seat class (e.g., "经济舱", "商务舱").
        available_seats: Number of available seats (if available).
        scraped_at: Timestamp when this price was scraped.
        source: Data source platform (e.g., "ctrip").
        return_flight_info: Return-leg flight info (round-trip only, else None).
    """
    flight_info: FlightInfo
    price: Decimal
    currency: str
    seat_class: str
    available_seats: Optional[int]
    scraped_at: datetime
    source: str
    return_flight_info: Optional[FlightInfo] = None  # 往返程回程航班信息


@dataclass
class SearchParams:
    """Parameters for flight search.

    Attributes:
        departure_city: Departure city name
        arrival_city: Arrival city name
        departure_date: Departure date
        return_date: Return date for round-trip (optional)
    """
    departure_city: str
    arrival_city: str
    departure_date: date
    return_date: Optional[date] = None


@dataclass
class PriceTrend:
    """Analysis result of price trend.

    Attributes:
        direction: Trend direction ("up", "down", "stable")
        confidence: Confidence score (0.0 to 1.0)
        recommendation: Human-readable recommendation
        predicted_lowest_price: Predicted lowest price in near future
        best_booking_time: Suggested best time to book
    """
    direction: str
    confidence: float
    recommendation: str
    predicted_lowest_price: Optional[Decimal]
    best_booking_time: Optional[datetime]


class ScraperError(Exception):
    """Base exception for scraper errors."""
    pass


class NetworkTimeoutError(ScraperError):
    """Raised when network request times out."""
    pass


class ParseError(ScraperError):
    """Raised when page parsing fails."""
    pass


class AntiCrawlerDetectedError(ScraperError):
    """Raised when anti-crawler mechanism is detected."""
    pass


class FlightScraper(ABC):
    """Abstract base class for flight data scrapers.

    All scraper implementations must inherit from this class and implement
    the search_flights method.
    """

    @abstractmethod
    async def search_flights(self, params: SearchParams) -> List[FlightPrice]:
        """Search for flights based on the given parameters.

        Args:
            params: Search parameters including cities and dates.

        Returns:
            List of flight prices found.

        Raises:
            NetworkTimeoutError: When network request times out.
            ParseError: When page parsing fails.
            AntiCrawlerDetectedError: When anti-crawler mechanism blocks access.
        """
        pass

    @abstractmethod
    async def close(self) -> None:
        """Clean up resources (browser, connections, etc.)."""
        pass


class DataRepository(ABC):
    """Abstract base class for data persistence operations.

    All repository implementations must inherit from this class.
    """

    @abstractmethod
    def save_price(self, flight_price: FlightPrice) -> int:
        """Save a price snapshot to the database.

        Args:
            flight_price: Flight price data to save.

        Returns:
            The ID of the saved record.

        Raises:
            DatabaseError: When save operation fails.
        """
        pass

    @abstractmethod
    def get_history(
        self,
        departure_city: str,
        arrival_city: str,
        days: int = 30
    ) -> List[FlightPrice]:
        """Get historical price data for a route.

        Args:
            departure_city: Departure city name.
            arrival_city: Arrival city name.
            days: Number of days to look back.

        Returns:
            List of historical flight prices.
        """
        pass

    @abstractmethod
    def get_latest_prices(
        self,
        departure_city: str,
        arrival_city: str,
        departure_date: date
    ) -> List[FlightPrice]:
        """Get the latest prices for a specific route and date.

        Args:
            departure_city: Departure city name.
            arrival_city: Arrival city name.
            departure_date: Departure date.

        Returns:
            List of latest flight prices.
        """
        pass


class PriceAnalyzer(ABC):
    """Abstract base class for price analysis.

    All analyzer implementations must inherit from this class.
    """

    @abstractmethod
    def predict_trend(
        self,
        historical_prices: List[FlightPrice],
        target_date: date
    ) -> PriceTrend:
        """Analyze historical prices and predict future trend.

        Args:
            historical_prices: Historical price data.
            target_date: Target departure date to analyze for.

        Returns:
            Price trend analysis result.
        """
        pass

    @abstractmethod
    def should_alert(
        self,
        current_price: Decimal,
        trend: PriceTrend,
        threshold: Decimal
    ) -> bool:
        """Determine if an alert should be sent.

        Args:
            current_price: Current flight price.
            trend: Analyzed price trend.
            threshold: User-defined price threshold.

        Returns:
            True if an alert should be sent.
        """
        pass


class Notifier(ABC):
    """Abstract base class for notification delivery.

    All notifier implementations must inherit from this class.
    """

    @abstractmethod
    async def send_alert(
        self,
        flight_price: FlightPrice,
        trend: PriceTrend,
        message: str
    ) -> bool:
        """Send a price alert notification.

        Args:
            flight_price: Flight price information.
            trend: Price trend analysis.
            message: Alert message to send.

        Returns:
            True if notification was sent successfully.

        Raises:
            NotificationError: When notification fails to send.
        """
        pass
