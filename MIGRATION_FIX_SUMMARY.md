# Database Migration Fix - Summary

## Problem Identified

**Error**: `sqlite3.OperationalError: no such column: routes.scrape_interval`

**Root Cause**: The database was created with v1.0.0 schema before the `scrape_interval` column was added in v1.0.1. The code expected this column to exist but the database schema hadn't been migrated.

## Solution Implemented

Created a migration system with three components:

### 1. Migration Script (`scripts/migrate_add_scrape_interval.py`)
- Safely adds the `scrape_interval` column to existing routes tables
- Sets default value of 6 hours for existing routes
- Checks if migration is needed before running
- Provides clear success/failure messages

### 2. Verification Script (`scripts/verify_migration.py`)
- Tests database access after migration
- Verifies all routes can be queried with the new column
- Displays route information including scrape intervals

### 3. Migration Documentation (`docs/MIGRATION.md`)
- Comprehensive guide for database migrations
- Troubleshooting steps for common issues
- Alternative approaches (fresh database option)

## Migration Results

**Your Database**:
- ✓ Successfully added `scrape_interval` column
- ✓ Updated 1 existing route (上海 → 成都) with default 6-hour interval
- ✓ All routes now have independent interval settings

**Verification Output**:
```
Route: 上海 → 成都
Target Date: 2026-03-06
Target Price: ¥800.00
Scrape Interval: 6 hours
Is Active: Yes
```

**Application Startup** (from flightscanner.log):
```
✓ Scheduler started
✓ Scheduled route 1 (上海 → 成都) with 6-hour interval
✓ Rescheduled 1 active routes
✓ Running initial scrape...
✓ Scheduler started successfully
```

## What Changed

### Database Schema
```sql
-- Added column with default value
ALTER TABLE routes ADD COLUMN scrape_interval INTEGER NOT NULL DEFAULT 6
```

### Existing Routes
All existing routes now have:
- `scrape_interval` = 6 hours (preserves previous global default)
- Can be adjusted independently via Web UI

### Application Behavior
- Each route now runs on its own schedule
- No more global 6-hour interval
- Supports 1/2/3/4/6/8/12/24 hour intervals per route

## Files Created/Modified

### New Files
1. `scripts/migrate_add_scrape_interval.py` - Migration script
2. `scripts/verify_migration.py` - Verification script
3. `docs/MIGRATION.md` - Migration documentation

### Updated Files
1. `README.md` - Added migration step (section 0)
2. `feature_log/v1.0.1.md` - Updated upgrade guide with migration steps

## Next Steps

### For You
The application is now ready to use! You can:

1. **Start the Web UI**:
   ```bash
   streamlit run ui/app.py
   ```

2. **Start the Background Scheduler**:
   ```bash
   python main.py
   ```

3. **Adjust Route Intervals** (via Web UI):
   - Navigate to the route list
   - Click "⚙️ 采集间隔：6小时" next to any route
   - Select new interval (1-24 hours)
   - Click "更新"

4. **Use Debug Mode**:
   - Click the 🔄 button next to any route for immediate collection

### For Future Users
If anyone else upgrades from v1.0.0 to v1.0.1, they will:

1. See the error message clearly
2. Find migration instructions in README.md
3. Run the migration script
4. Continue using the application

## Validation

### Before Migration
```
❌ sqlite3.OperationalError: no such column: routes.scrape_interval
❌ Application failed to start
❌ Scheduler initialization failed
```

### After Migration
```
✓ Database schema updated
✓ Application starts successfully
✓ Scheduler runs with per-route intervals
✓ Route: 上海 → 成都 scheduled with 6-hour interval
✓ All features working as expected
```

## Technical Details

### Migration Safety
- ✓ Checks if column exists before adding
- ✓ Uses SQLite ALTER TABLE (safe operation)
- ✓ Sets NOT NULL with DEFAULT value
- ✓ Commits transaction atomically
- ✓ Rollback on error

### Backward Compatibility
- ✓ No data loss
- ✓ Existing routes preserved
- ✓ Default interval maintains previous behavior
- ✓ CLI commands still work

### Database Verification
```bash
# Check schema
sqlite3 flightscanner.db "PRAGMA table_info(routes);"

# Column 8: scrape_interval|INTEGER|1|6|0
# Position: 8
# Type: INTEGER
# Not Null: Yes (1)
# Default: 6
```

## Support

If you encounter any issues:

1. Check the log file: `tail -f flightscanner.log`
2. Re-run verification: `python scripts/verify_migration.py`
3. See troubleshooting: [docs/MIGRATION.md](docs/MIGRATION.md)

---

**Migration completed successfully!** 🎉

You can now enjoy all v1.0.1 features:
- ✨ Per-route monitoring intervals
- ✨ Debug mode instant collection (🔄 button)
- ✨ Auto-login with QR code in headless mode
