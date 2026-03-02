"""Background scheduler for automated price monitoring.

This module provides a scheduler that automatically scrapes prices for active routes
and sends alerts when prices drop below target thresholds.
"""

import asyncio
import json
import logging
from datetime import datetime
from decimal import Decimal
from typing import List

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from flightscanner.models.database import Route
from flightscanner.core.services import RouteService
from flightscanner.scrapers import CtripScraper, QunarScraper
from flightscanner.analyzers import RuleBasedAnalyzer
from flightscanner.notifiers import EmailNotifier
from flightscanner.interfaces import SearchParams, FlightDirection
from flightscanner.models.database import init_db
from flightscanner.utils.config import settings

logger = logging.getLogger(__name__)


class PriceMonitorScheduler:
    """Scheduler for automated route price monitoring.

    This class manages the scheduled scraping of flight prices for active routes
    and triggers alerts when prices meet target thresholds.

    Attributes:
        headless: Whether to run browser in headless mode.
        scraper: Flight scraper instance.
        analyzer: Price analyzer instance.
        notifier: Notification sender instance.
        scheduler: APScheduler instance.
    """

    def __init__(
        self,
        headless: bool = True,
        enable_notifications: bool = False,
    ):
        """Initialize the price monitor scheduler.

        Args:
            headless: Whether to run browser in headless mode.
            enable_notifications: Whether to send email notifications.
        """
        self.headless = headless

        # Initialize scraper based on configuration
        scraper_type = settings.scraper_type.lower()

        # Load Qunar cookies if available
        qunar_cookies = None
        if settings.qunar_cookies:
            try:
                qunar_cookies = json.loads(settings.qunar_cookies)
                logger.info("Loaded Qunar cookies from configuration")
            except json.JSONDecodeError as e:
                logger.warning(f"Failed to parse Qunar cookies: {e}")

        if scraper_type == "qunar":
            self.scraper = QunarScraper(
                headless=headless,
                cookies=qunar_cookies,
            )
            logger.info(f"Initialized Qunar scraper (headless={headless}, cookies={bool(qunar_cookies)})")
        else:
            self.scraper = CtripScraper(headless=headless)
            logger.info(f"Initialized Ctrip scraper (headless={headless})")

        self.analyzer = RuleBasedAnalyzer()
        self.notifier = EmailNotifier() if enable_notifications else None
        self.scheduler = AsyncIOScheduler()
        self._engine, self._SessionLocal = init_db()

        logger.info(
            f"PriceMonitorScheduler initialized (scraper={scraper_type}, "
            f"headless={headless}, notifications={enable_notifications})"
        )

    async def scrape_route(self, route: Route) -> None:
        """Scrape prices for a single route.

        Args:
            route: Route to scrape prices for.
        """
        logger.info(
            f"Scraping route {route.id}: {route.origin} → {route.destination} "
            f"on {route.target_date}"
        )

        try:
            # Create search parameters
            params = SearchParams(
                departure_city=route.origin,
                arrival_city=route.destination,
                departure_date=route.target_date,
            )

            # Scrape flight prices
            flight_prices = await self.scraper.search_flights(params)

            if not flight_prices:
                logger.warning(
                    f"No flights found for route {route.id}: "
                    f"{route.origin} → {route.destination}"
                )
                return

            logger.info(
                f"Found {len(flight_prices)} flights for route {route.id}"
            )

            # Save prices to database
            session = self._SessionLocal()
            try:
                route_service = RouteService(session)

                for flight_price in flight_prices:
                    route_service.save_price_for_route(route.id, flight_price)

                logger.info(
                    f"Saved {len(flight_prices)} price records for route {route.id}"
                )

                # Check if any price is below target
                lowest_price = min(fp.price for fp in flight_prices)

                if lowest_price <= route.target_price:
                    logger.info(
                        f"Price alert! Route {route.id}: "
                        f"¥{lowest_price} ≤ target ¥{route.target_price}"
                    )

                    # Send notification if enabled
                    if self.notifier:
                        await self._send_alert(route, flight_prices, lowest_price)

            finally:
                session.close()

        except Exception as e:
            logger.error(
                f"Failed to scrape route {route.id}: {e}",
                exc_info=True,
            )

    async def _send_alert(
        self,
        route: Route,
        flight_prices: List,
        lowest_price: Decimal,
    ) -> None:
        """Send price alert notification.

        Args:
            route: Route that triggered the alert.
            flight_prices: List of flight prices found.
            lowest_price: Lowest price found.
        """
        try:
            # Find the cheapest flight
            cheapest = min(flight_prices, key=lambda fp: fp.price)

            # Analyze price trend (use all prices for this route)
            session = self._SessionLocal()
            try:
                route_service = RouteService(session)
                history = route_service.get_route_price_history(route.id, days=30)

                if history:
                    trend = self.analyzer.predict_trend(history, route.target_date)

                    message = (
                        f"Great news! Flight prices for {route.origin} → {route.destination} "
                        f"on {route.target_date} have dropped to ¥{lowest_price:.0f}, "
                        f"below your target of ¥{route.target_price:.0f}!\n\n"
                        f"Flight: {cheapest.flight_info.flight_no} ({cheapest.flight_info.airline})\n"
                        f"Departure: {cheapest.flight_info.departure_time}\n"
                        f"Arrival: {cheapest.flight_info.arrival_time}\n\n"
                        f"Price trend: {trend.direction} (confidence: {trend.confidence:.0%})\n"
                        f"Recommendation: {trend.recommendation}"
                    )

                    await self.notifier.send_alert(cheapest, trend, message)
                    logger.info(f"Alert sent for route {route.id}")

            finally:
                session.close()

        except Exception as e:
            logger.error(
                f"Failed to send alert for route {route.id}: {e}",
                exc_info=True,
            )

    async def scrape_active_routes(self) -> None:
        """Scrape all active routes.

        This is the main scheduled job that runs periodically.
        """
        logger.info("Starting scheduled scrape of active routes")

        session = self._SessionLocal()
        try:
            route_service = RouteService(session)
            active_routes = route_service.get_active_routes()

            if not active_routes:
                logger.info("No active routes to scrape")
                return

            logger.info(f"Found {len(active_routes)} active routes to scrape")

            # Scrape each route (with delay to avoid rate limiting)
            for i, route in enumerate(active_routes):
                await self.scrape_route(route)

                # Add delay between routes (except for last one)
                if i < len(active_routes) - 1:
                    logger.info("Waiting 60 seconds before next route...")
                    await asyncio.sleep(60)

            logger.info("Completed scraping all active routes")

        finally:
            session.close()

    def schedule_route(self, route: Route) -> None:
        """Schedule a specific route for periodic scraping.

        Args:
            route: Route to schedule.
        """
        job_id = f"scrape_route_{route.id}"

        # Create a wrapper function that scrapes this specific route
        async def scrape_this_route():
            await self.scrape_route(route)

        # Remove existing job if any
        if self.scheduler.get_job(job_id):
            self.scheduler.remove_job(job_id)

        # Add new job with route-specific interval
        self.scheduler.add_job(
            scrape_this_route,
            trigger=IntervalTrigger(hours=route.scrape_interval),
            id=job_id,
            name=f"Scrape {route.origin} → {route.destination}",
            replace_existing=True,
        )

        logger.info(
            f"Scheduled route {route.id} ({route.origin} → {route.destination}) "
            f"with {route.scrape_interval}-hour interval"
        )

    def reschedule_all_routes(self) -> None:
        """Reschedule all active routes based on their current intervals."""
        session = self._SessionLocal()
        try:
            route_service = RouteService(session)
            active_routes = route_service.get_active_routes()

            # Remove all existing route jobs
            for job in self.scheduler.get_jobs():
                if job.id.startswith("scrape_route_"):
                    self.scheduler.remove_job(job.id)

            # Schedule each active route
            for route in active_routes:
                self.schedule_route(route)

            logger.info(f"Rescheduled {len(active_routes)} active routes")

        finally:
            session.close()

    def start(self) -> None:
        """Start the scheduler.

        Schedules individual jobs for each active route based on their
        scrape_interval settings, and runs an immediate scrape on startup.
        """
        # Start the scheduler
        self.scheduler.start()
        logger.info("Scheduler started")

        # Schedule all active routes
        self.reschedule_all_routes()

        # Run immediate scrape for all active routes
        logger.info("Running initial scrape...")
        asyncio.create_task(self.scrape_active_routes())

    def stop(self) -> None:
        """Stop the scheduler and cleanup resources."""
        if self.scheduler.running:
            self.scheduler.shutdown()
            logger.info("Scheduler stopped")

        # Cleanup scraper resources
        asyncio.create_task(self.scraper.close())