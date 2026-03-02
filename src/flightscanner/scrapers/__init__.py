"""Scraper implementations for flight data collection."""

from .ctrip_scraper import CtripScraper
from .qunar_scraper import QunarScraper

__all__ = ["CtripScraper", "QunarScraper"]
