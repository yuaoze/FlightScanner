"""FlightScanner - Flight price monitoring and analysis system.

A modular, testable flight price monitoring system that scrapes flight prices,
stores historical data, analyzes trends using LLM, and sends price alerts.
"""

from .interfaces import (
    FlightDirection,
    FlightInfo,
    FlightPrice,
    SearchParams,
    PriceTrend,
    FlightScraper,
    DataRepository,
    PriceAnalyzer,
    Notifier,
    ScraperError,
    NetworkTimeoutError,
    ParseError,
    AntiCrawlerDetectedError,
)

__version__ = "0.1.0"

__all__ = [
    # Data classes
    "FlightDirection",
    "FlightInfo",
    "FlightPrice",
    "SearchParams",
    "PriceTrend",
    # Abstract base classes
    "FlightScraper",
    "DataRepository",
    "PriceAnalyzer",
    "Notifier",
    # Exceptions
    "ScraperError",
    "NetworkTimeoutError",
    "ParseError",
    "AntiCrawlerDetectedError",
]
