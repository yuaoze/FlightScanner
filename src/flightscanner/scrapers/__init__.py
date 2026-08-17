"""Scraper implementations for flight data collection."""

from .ctrip_scraper import CtripScraper
from .qunar_scraper import QunarScraper
from .registry import ScraperRegistry
from .tongcheng_scraper import TongchengScraper

__all__ = [
    "CtripScraper",
    "QunarScraper",
    "TongchengScraper",
    "ScraperRegistry",
]
