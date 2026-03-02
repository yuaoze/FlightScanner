"""Models package for FlightScanner."""

from .database import Base, Flight, PriceHistory, Route, init_db

__all__ = ["Base", "Flight", "PriceHistory", "Route", "init_db"]
