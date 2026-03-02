#!/usr/bin/env python3
"""Verification script for database layer.

This script verifies that the database layer is working correctly by:
1. Creating database tables
2. Inserting a test flight and price record
3. Querying the records
4. Deleting the records
5. Verifying all operations succeeded

Usage:
    python scripts/verify_db.py
"""

import sys
from pathlib import Path
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from sqlalchemy.orm import Session
from flightscanner.models import Flight, PriceHistory, init_db


def print_header(title: str) -> None:
    """Print a formatted header."""
    print("\n" + "=" * 60)
    print(f" {title}")
    print("=" * 60)


def verify_database() -> bool:
    """Verify database operations.

    Returns:
        True if all verifications pass, False otherwise.
    """
    print_header("Database Verification Script")

    # Step 1: Initialize database
    print("\n[1/5] Initializing database...")
    try:
        # Use in-memory database for testing
        engine, SessionLocal = init_db("sqlite:///:memory:")
        print("✓ Database tables created successfully")
    except Exception as e:
        print(f"✗ Failed to initialize database: {e}")
        return False

    # Step 2: Insert test flight record
    print("\n[2/5] Inserting test flight record...")
    session: Session = SessionLocal()

    try:
        test_flight = Flight(
            flight_no="CA1234",
            airline="中国国航",
            departure_city="北京",
            arrival_city="上海",
            departure_time="08:00",
            arrival_time="10:30",
            departure_date=date.today() + timedelta(days=7),
            direction="departure",
        )
        session.add(test_flight)
        session.commit()
        flight_id = test_flight.id
        print(f"✓ Flight record inserted with ID: {flight_id}")
        print(f"  - Flight: {test_flight.flight_no}")
        print(f"  - Route: {test_flight.departure_city} -> {test_flight.arrival_city}")
        print(f"  - Date: {test_flight.departure_date}")
    except Exception as e:
        session.rollback()
        print(f"✗ Failed to insert flight record: {e}")
        session.close()
        return False

    # Step 3: Insert test price record
    print("\n[3/5] Inserting test price record...")
    try:
        test_price = PriceHistory(
            flight_id=flight_id,
            price=Decimal("680.00"),
            currency="CNY",
            seat_class="经济舱",
            available_seats=15,
            source="ctrip",
            scraped_at=datetime.now(timezone.utc),
        )
        session.add(test_price)
        session.commit()
        price_id = test_price.id
        print(f"✓ Price record inserted with ID: {price_id}")
        print(f"  - Price: ¥{test_price.price}")
        print(f"  - Seat Class: {test_price.seat_class}")
        print(f"  - Available Seats: {test_price.available_seats}")
    except Exception as e:
        session.rollback()
        print(f"✗ Failed to insert price record: {e}")
        session.close()
        return False

    # Step 4: Query and verify records
    print("\n[4/5] Querying records...")
    try:
        # Query flight
        flight = session.query(Flight).filter_by(id=flight_id).first()
        if not flight:
            print("✗ Flight record not found!")
            session.close()
            return False

        # Query price history
        prices = session.query(PriceHistory).filter_by(flight_id=flight_id).all()
        if not prices:
            print("✗ Price records not found!")
            session.close()
            return False

        print(f"✓ Flight record found: {flight}")
        print(f"✓ Price record found: {prices[0]}")

        # Verify relationship
        if len(flight.price_histories) != 1:
            print("✗ Flight-Price relationship not working correctly!")
            session.close()
            return False

        print("✓ Flight-Price relationship verified")

        # Query by route and date
        flights_by_route = (
            session.query(Flight)
            .filter_by(
                departure_city="北京",
                arrival_city="上海",
                departure_date=flight.departure_date,
            )
            .all()
        )
        if not flights_by_route:
            print("✗ Route query failed!")
            session.close()
            return False

        print(f"✓ Route query successful, found {len(flights_by_route)} flight(s)")

    except Exception as e:
        print(f"✗ Query failed: {e}")
        session.close()
        return False

    # Step 5: Delete records
    print("\n[5/5] Deleting test records...")
    try:
        # Delete price records first (due to foreign key)
        session.query(PriceHistory).filter_by(flight_id=flight_id).delete()
        # Delete flight
        session.query(Flight).filter_by(id=flight_id).delete()
        session.commit()

        # Verify deletion
        flight = session.query(Flight).filter_by(id=flight_id).first()
        if flight:
            print("✗ Flight record not deleted!")
            session.close()
            return False

        print("✓ Records deleted successfully")

    except Exception as e:
        session.rollback()
        print(f"✗ Deletion failed: {e}")
        session.close()
        return False
    finally:
        session.close()

    return True


def main() -> int:
    """Main entry point."""
    try:
        success = verify_database()
        if success:
            print_header("VERIFICATION PASSED")
            print("\n✓ All database operations completed successfully!")
            print("✓ Database layer is ready for use.\n")
            return 0
        else:
            print_header("VERIFICATION FAILED")
            print("\n✗ Some database operations failed.")
            print("✗ Please check the error messages above.\n")
            return 1
    except Exception as e:
        print_header("UNEXPECTED ERROR")
        print(f"\n✗ An unexpected error occurred: {e}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
