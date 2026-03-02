#!/usr/bin/env python3
"""Quick verification that the database migration worked."""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from flightscanner.models.database import init_db
from flightscanner.core.services import RouteService


def main():
    print("Testing database access after migration...")
    print()

    # Initialize database
    engine, SessionLocal = init_db()
    session = SessionLocal()

    try:
        # Test RouteService
        route_service = RouteService(session)

        # Get active routes (this was causing the error before)
        print("Fetching active routes...")
        active_routes = route_service.get_active_routes()
        print(f"✓ Found {len(active_routes)} active route(s)")

        # Display route details
        for route in active_routes:
            print()
            print(f"  Route ID: {route.id}")
            print(f"  Route: {route.origin} → {route.destination}")
            print(f"  Target Date: {route.target_date}")
            print(f"  Target Price: ¥{route.target_price}")
            print(f"  Scrape Interval: {route.scrape_interval} hours")
            print(f"  Is Active: {route.is_active}")

        # Get all routes with latest price
        print()
        print("Fetching all routes with price info...")
        all_routes = route_service.get_all_routes()
        print(f"✓ Found {len(all_routes)} total route(s)")

        for route in all_routes:
            print()
            print(f"  Route: {route.origin} → {route.destination}")
            print(f"  Scrape Interval: {route.scrape_interval} hours")
            print(f"  Latest Price: ¥{route.latest_price}" if route.latest_price else "  Latest Price: No data yet")
            print(f"  Price Count: {route.price_count}")

        print()
        print("=" * 60)
        print("✓ All tests passed! The migration was successful.")
        print("  You can now run: python main.py")
        print("=" * 60)

    except Exception as e:
        print()
        print("=" * 60)
        print(f"✗ Test failed: {e}")
        print("=" * 60)
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        session.close()


if __name__ == "__main__":
    main()
