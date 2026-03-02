#!/usr/bin/env python3
"""Migration script to add scrape_interval column to routes table.

This script safely adds the scrape_interval column to existing routes tables
that were created before v1.0.1.

Usage:
    python scripts/migrate_add_scrape_interval.py
"""

import sqlite3
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from flightscanner.utils.config import settings


def check_column_exists(cursor, table_name: str, column_name: str) -> bool:
    """Check if a column exists in a table.

    Args:
        cursor: SQLite cursor
        table_name: Name of the table
        column_name: Name of the column to check

    Returns:
        True if column exists, False otherwise
    """
    cursor.execute(f"PRAGMA table_info({table_name})")
    columns = [row[1] for row in cursor.fetchall()]
    return column_name in columns


def migrate_database(db_path: str):
    """Add scrape_interval column to routes table if it doesn't exist.

    Args:
        db_path: Path to the SQLite database file
    """
    print(f"Migrating database: {db_path}")

    # Connect to database
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        # Check if routes table exists
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='routes'"
        )
        if not cursor.fetchone():
            print("✓ Routes table doesn't exist yet - no migration needed")
            return

        # Check if scrape_interval column exists
        if check_column_exists(cursor, "routes", "scrape_interval"):
            print("✓ Column 'scrape_interval' already exists - no migration needed")
            return

        print("Adding 'scrape_interval' column to routes table...")

        # Add the column with default value of 6 (hours)
        cursor.execute(
            "ALTER TABLE routes ADD COLUMN scrape_interval INTEGER NOT NULL DEFAULT 6"
        )

        # Verify the column was added
        if check_column_exists(cursor, "routes", "scrape_interval"):
            print("✓ Column 'scrape_interval' added successfully")

            # Check how many routes were updated
            cursor.execute("SELECT COUNT(*) FROM routes")
            count = cursor.fetchone()[0]
            print(f"✓ Updated {count} existing route(s) with default interval of 6 hours")
        else:
            print("✗ Failed to add column")
            return

        # Commit changes
        conn.commit()
        print("✓ Migration completed successfully")

    except sqlite3.Error as e:
        print(f"✗ Migration failed: {e}")
        conn.rollback()
        raise
    finally:
        conn.close()


def main():
    """Main migration function."""
    print("=" * 60)
    print("Database Migration: Add scrape_interval column")
    print("=" * 60)
    print()

    # Get database path from settings
    db_url = settings.database_url
    if db_url.startswith("sqlite:///"):
        db_path = db_url.replace("sqlite:///", "")
    else:
        print(f"✗ Unsupported database URL: {db_url}")
        print("  This migration script only supports SQLite databases")
        sys.exit(1)

    # Run migration
    try:
        migrate_database(db_path)
        print()
        print("=" * 60)
        print("Migration completed! You can now restart the application.")
        print("=" * 60)
    except Exception as e:
        print()
        print("=" * 60)
        print(f"Migration failed: {e}")
        print("=" * 60)
        sys.exit(1)


if __name__ == "__main__":
    main()
