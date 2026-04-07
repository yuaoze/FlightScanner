"""Models package for FlightScanner."""

from .database import Base, Flight, PriceHistory, Route, AIPredictionLog, init_db

__all__ = ["Base", "Flight", "PriceHistory", "Route", "AIPredictionLog", "init_db"]
