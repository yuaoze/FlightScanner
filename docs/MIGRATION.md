# Database Migration Guide

## Overview

This guide helps you migrate your FlightScanner database when upgrading between versions.

## v1.0.0 → v1.0.1 Migration

### Issue

If you're upgrading from v1.0.0 to v1.0.1, you may encounter this error:

```
sqlite3.OperationalError: no such column: routes.scrape_interval
```

This happens because v1.0.1 adds a new `scrape_interval` column to the routes table to support per-route monitoring intervals.

### Solution

Run the migration script:

```bash
python scripts/migrate_add_scrape_interval.py
```

**Expected Output:**

```
============================================================
Database Migration: Add scrape_interval column
============================================================

Migrating database: flightscanner.db
Adding 'scrape_interval' column to routes table...
✓ Column 'scrape_interval' added successfully
✓ Updated N existing route(s) with default interval of 6 hours
✓ Migration completed successfully

============================================================
Migration completed! You can now restart the application.
============================================================
```

### Verify Migration

After running the migration, verify it worked:

```bash
python scripts/verify_migration.py
```

This will check that:
- The `scrape_interval` column exists
- All routes can be queried successfully
- Existing routes have the default 6-hour interval

### What Changed

- All existing routes now have a `scrape_interval` of 6 hours (the previous global default)
- You can now adjust each route's interval independently via the Web UI
- The scheduler will use per-route intervals instead of a global 6-hour interval

## Alternative: Fresh Database

If you don't have important data to preserve, you can also:

1. **Backup old database** (optional):
   ```bash
   mv flightscanner.db flightscanner.db.backup
   ```

2. **Restart application** - it will create a new database with the correct schema:
   ```bash
   python main.py
   # or
   streamlit run ui/app.py
   ```

## Troubleshooting

### Migration Script Fails

If the migration script fails with an error:

1. **Check database file exists**:
   ```bash
   ls -lh flightscanner.db
   ```

2. **Check database is not locked**:
   ```bash
   # Stop all running FlightScanner processes
   pkill -f "python main.py"
   pkill -f "streamlit run"

   # Then retry migration
   python scripts/migrate_add_scrape_interval.py
   ```

3. **Check database permissions**:
   ```bash
   # Ensure the database file is writable
   chmod 644 flightscanner.db
   ```

### Column Already Exists

If you see:

```
✓ Column 'scrape_interval' already exists - no migration needed
```

The migration has already been applied. No further action needed!

### Other Database Errors

For other database-related errors:

1. **Check SQLAlchemy version**:
   ```bash
   pip show sqlalchemy
   ```

2. **Verify database integrity**:
   ```bash
   sqlite3 flightscanner.db "PRAGMA integrity_check;"
   ```

3. **If all else fails**, backup and recreate:
   ```bash
   # Export route data
   sqlite3 flightscanner.db ".mode insert routes" ".output routes_backup.sql" "SELECT * FROM routes;"

   # Recreate database
   mv flightscanner.db flightscanner.db.old
   python main.py  # Creates new database

   # Manually re-add routes via Web UI
   ```

## Future Migrations

As the project evolves, migration scripts will be added to the `scripts/` directory with the naming pattern:

```
scripts/migrate_<version>_<description>.py
```

Always check the `feature_log/` directory for migration notes when upgrading.

## Support

If you encounter issues not covered here:

1. Check the [GitHub Issues](https://github.com/yourusername/FlightScanner/issues)
2. Create a new issue with:
   - Error message
   - Python version
   - SQLAlchemy version
   - Database file size
   - Migration script output
