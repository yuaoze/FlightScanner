#!/usr/bin/env python3
"""Main entry point for FlightScanner background scheduler.

This script starts the background price monitoring scheduler that
automatically scrapes prices for active routes every 6 hours.

Usage:
    python main.py [--no-headless] [--enable-notifications]

Options:
    --no-headless          Run browser in visible mode (for debugging)
    --enable-notifications Enable email notifications for price alerts
"""

import argparse
import asyncio
import logging
import signal
import sys

from flightscanner.scheduler import PriceMonitorScheduler

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("flightscanner.log"),
    ],
)

logger = logging.getLogger(__name__)


def signal_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    logger.info(f"Received signal {signum}, shutting down...")
    sys.exit(0)


async def main(headless: bool, enable_notifications: bool) -> None:
    """Run the price monitor scheduler.

    Args:
        headless: Whether to run browser in headless mode.
        enable_notifications: Whether to send email notifications.
    """
    logger.info("=" * 60)
    logger.info("FlightScanner Price Monitor v1.0")
    logger.info("=" * 60)
    logger.info(f"Headless mode: {headless}")
    logger.info(f"Notifications: {enable_notifications}")
    logger.info("=" * 60)

    # Create and start the scheduler
    monitor = PriceMonitorScheduler(
        headless=headless,
        enable_notifications=enable_notifications,
    )

    # Setup signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        # Start the scheduler
        monitor.start()
        logger.info("Scheduler started successfully")

        # Keep running until interrupted
        while True:
            await asyncio.sleep(1)

    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received, shutting down...")

    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)

    finally:
        # Cleanup
        monitor.stop()
        logger.info("FlightScanner scheduler stopped")


if __name__ == "__main__":
    # Parse command line arguments
    parser = argparse.ArgumentParser(
        description="FlightScanner Price Monitor - Background scheduler for automated flight price monitoring",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Run with default settings (headless, no notifications)
    python main.py

    # Run with visible browser for debugging
    python main.py --no-headless

    # Run with email notifications enabled
    python main.py --enable-notifications
        """,
    )

    parser.add_argument(
        "--no-headless",
        action="store_true",
        help="Run browser in visible mode (for debugging)",
    )

    parser.add_argument(
        "--enable-notifications",
        action="store_true",
        help="Enable email notifications for price alerts",
    )

    args = parser.parse_args()

    # Run the scheduler
    try:
        asyncio.run(
            main(
                headless=not args.no_headless,
                enable_notifications=args.enable_notifications,
            )
        )
    except KeyboardInterrupt:
        pass