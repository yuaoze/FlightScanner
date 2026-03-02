"""Command-line interface for FlightScanner.

This module provides CLI commands for flight search, price history queries,
and price monitoring.
"""

import asyncio
import logging
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional

import click

from flightscanner.analyzers import RuleBasedAnalyzer
from flightscanner.interfaces import SearchParams
from flightscanner.models import init_db
from flightscanner.notifiers import EmailNotifier
from flightscanner.repositories import SQLAlchemyRepository
from flightscanner.scrapers import CtripScraper
from flightscanner.utils.config import settings

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


@click.group()
@click.version_option(version="0.1.0")
def cli():
    """FlightScanner - Flight price monitoring and analysis system.

    Monitor flight prices, analyze trends, and receive alerts when prices drop.
    """
    pass


@cli.command()
@click.option(
    "-d", "--departure", required=True, help="Departure city (e.g., 北京)"
)
@click.option(
    "-a", "--arrival", required=True, help="Arrival city (e.g., 上海)"
)
@click.option(
    "-D", "--date", "departure_date", required=True, type=click.DATE_FORMATS[0],
    help="Departure date (YYYY-MM-DD)"
)
@click.option(
    "-r", "--return-date", type=click.DATE_FORMATS[0],
    help="Return date for round-trip (YYYY-MM-DD)"
)
@click.option(
    "--threshold", type=int, default=settings.alert_price_threshold,
    help=f"Price threshold for alerts (default: {settings.alert_price_threshold})"
)
@click.option(
    "--no-alert", is_flag=True, help="Disable price alerts"
)
@click.option(
    "--headless/--no-headless", default=True,
    help="Run scraper in headless mode (default: headless)"
)
def search(
    departure: str,
    arrival: str,
    departure_date: date,
    return_date: Optional[date],
    threshold: int,
    no_alert: bool,
    headless: bool,
):
    """Search for flights and analyze prices.

    This command:
    1. Scrapes flight data from Ctrip
    2. Saves prices to database
    3. Analyzes price trends
    4. Sends alerts if conditions are met

    Example:
        flightscanner search -d 北京 -a 上海 -D 2024-03-15 --threshold 700
    """
    async def run_search():
        """Run the async search process."""
        scraper = None
        try:
            # Initialize database
            logger.info("Initializing database...")
            engine, SessionLocal = init_db(settings.database_url)
            session = SessionLocal()

            # Initialize components
            repository = SQLAlchemyRepository(session)
            analyzer = RuleBasedAnalyzer()
            notifier = EmailNotifier(settings) if not no_alert else None

            # Create search parameters
            params = SearchParams(
                departure_city=departure,
                arrival_city=arrival,
                departure_date=departure_date,
                return_date=return_date,
            )

            # Step 1: Scrape flights
            logger.info(f"Searching flights: {departure} → {arrival} on {departure_date}")
            scraper = CtripScraper(headless=headless)
            flight_prices = await scraper.search_flights(params)

            if not flight_prices:
                click.echo("No flights found.")
                return

            click.echo(f"\nFound {len(flight_prices)} flights:\n")
            click.echo("-" * 80)
            click.echo(
                f"{'Flight':<12} {'Airline':<15} {'Time':<15} "
                f"{'Price':>10} {'Class':<10}"
            )
            click.echo("-" * 80)

            # Step 2: Save prices and display
            for fp in flight_prices:
                # Save to database
                price_id = repository.save_price(fp)
                logger.debug(f"Saved price record ID: {price_id}")

                # Display
                flight_info = fp.flight_info
                click.echo(
                    f"{flight_info.flight_no:<12} "
                    f"{flight_info.airline:<15} "
                    f"{flight_info.departure_time}-{flight_info.arrival_time:<15} "
                    f"¥{fp.price:>9} {fp.seat_class:<10}"
                )

            click.echo("-" * 80)

            # Step 3: Get historical prices and analyze
            logger.info("Analyzing price trends...")
            historical_prices = repository.get_history(
                departure_city=departure,
                arrival_city=arrival,
                days=30,
            )

            if historical_prices:
                trend = analyzer.predict_trend(historical_prices, departure_date)

                click.echo(f"\nPrice Analysis:")
                click.echo(f"  Trend: {trend.direction.upper()}")
                click.echo(f"  Confidence: {trend.confidence:.0%}")
                click.echo(f"  Recommendation: {trend.recommendation}")

                if trend.predicted_lowest_price:
                    click.echo(f"  Predicted Lowest Price: ¥{trend.predicted_lowest_price}")

                # Step 4: Check alert conditions
                if not no_alert and notifier and flight_prices:
                    # Get the lowest current price
                    lowest_price = min(fp.price for fp in flight_prices)

                    if analyzer.should_alert(lowest_price, trend, Decimal(str(threshold))):
                        # Find the cheapest flight
                        cheapest_fp = min(flight_prices, key=lambda fp: fp.price)

                        alert_message = (
                            f"Good news! Found a flight from {departure} to {arrival} "
                            f"for ¥{lowest_price}, which is below your threshold of ¥{threshold}."
                        )

                        try:
                            await notifier.send_alert(cheapest_fp, trend, alert_message)
                            click.echo(f"\n✓ Alert sent to {settings.smtp_user}")
                        except Exception as e:
                            logger.error(f"Failed to send alert: {e}")
                            click.echo(f"\n✗ Failed to send alert: {e}", err=True)
                    else:
                        click.echo(
                            f"\nNo alert sent (price ¥{lowest_price} "
                            f"does not meet conditions)"
                        )
            else:
                click.echo("\nNo historical data available for analysis.")

            session.close()

        except Exception as e:
            logger.error(f"Search failed: {e}")
            click.echo(f"\nError: {e}", err=True)
            sys.exit(1)
        finally:
            if scraper:
                await scraper.close()

    # Run async function
    asyncio.run(run_search())


@cli.command()
@click.option(
    "-d", "--departure", required=True, help="Departure city"
)
@click.option(
    "-a", "--arrival", required=True, help="Arrival city"
)
@click.option(
    "--days", type=int, default=30, help="Number of days to look back (default: 30)"
)
def history(departure: str, arrival: str, days: int):
    """View historical flight prices for a route.

    This command queries the database for historical price data
    and displays it in a table format.

    Example:
        flightscanner history -d 北京 -a 上海 --days 30
    """
    try:
        # Initialize database
        engine, SessionLocal = init_db(settings.database_url)
        session = SessionLocal()
        repository = SQLAlchemyRepository(session)

        # Query historical prices
        logger.info(f"Querying price history for {departure} → {arrival} (last {days} days)")
        historical_prices = repository.get_history(
            departure_city=departure,
            arrival_city=arrival,
            days=days,
        )

        if not historical_prices:
            click.echo(f"No price history found for {departure} → {arrival}")
            return

        click.echo(f"\nPrice History: {departure} → {arrival} (last {days} days)\n")
        click.echo("-" * 100)
        click.echo(
            f"{'Flight':<12} {'Date':<12} {'Time':<15} "
            f"{'Price':>10} {'Class':<10} {'Scraped':<20}"
        )
        click.echo("-" * 100)

        # Display prices
        for fp in historical_prices:
            flight_info = fp.flight_info
            click.echo(
                f"{flight_info.flight_no:<12} "
                f"{str(flight_info.departure_date):<12} "
                f"{flight_info.departure_time}-{flight_info.arrival_time:<15} "
                f"¥{fp.price:>9} {fp.seat_class:<10} "
                f"{fp.scraped_at.strftime('%Y-%m-%d %H:%M'):<20}"
            )

        click.echo("-" * 100)
        click.echo(f"\nTotal records: {len(historical_prices)}")

        # Show statistics
        prices = [float(fp.price) for fp in historical_prices]
        avg_price = sum(prices) / len(prices)
        min_price = min(prices)
        max_price = max(prices)

        click.echo(f"\nStatistics:")
        click.echo(f"  Average Price: ¥{avg_price:.2f}")
        click.echo(f"  Lowest Price: ¥{min_price:.2f}")
        click.echo(f"  Highest Price: ¥{max_price:.2f}")

        session.close()

    except Exception as e:
        logger.error(f"Failed to query history: {e}")
        click.echo(f"\nError: {e}", err=True)
        sys.exit(1)


def main():
    """Main entry point for the CLI."""
    try:
        cli()
    except KeyboardInterrupt:
        click.echo("\n\nOperation cancelled by user.")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        click.echo(f"\nError: {e}", err=True)
        sys.exit(1)


if __name__ == "__main__":
    main()