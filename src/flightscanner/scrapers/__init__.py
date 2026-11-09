"""Scraper implementations for flight data collection."""

from .ctrip_scraper import CtripScraper
from .fliggy_scraper import FliggyScraper
from .qunar_scraper import QunarScraper
from .registry import ScraperRegistry
from .tongcheng_scraper import TongchengScraper

__all__ = [
    "CtripScraper",
    "FliggyScraper",
    "QunarScraper",
    "TongchengScraper",
    "ScraperRegistry",
]
