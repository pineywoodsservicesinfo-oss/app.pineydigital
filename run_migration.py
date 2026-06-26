#!/usr/bin/env python3
"""
Run database migrations for FieldPulse
Usage: python run_migration.py
"""

import os
import sys

# Load environment variables
from modules.utils import load_env
load_env()

from migrations.db_config import get_db_connection

def run_migration():
    """Run the crews and waitlist migration."""

    migration_sql = """
    -- 1. Fix crews table - remove staff_ids if it exists with NOT NULL constraint
    DO $$
    BEGIN
        -- Check if staff_ids column exists and drop it (it was added incorrectly)
        IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'crews' AND column_name = 'staff_ids'
        ) THEN
            ALTER TABLE crews DROP COLUMN staff_ids;
        END IF;
    END $$;

    -- 2. Ensure crews table has correct columns
    ALTER TABLE crews
        ADD COLUMN IF NOT EXISTS color VARCHAR(50) DEFAULT 'emerald',
        ADD COLUMN IF NOT EXISTS role VARCHAR(255),
        ADD COLUMN IF NOT EXISTS email VARCHAR(255),
        ADD COLUMN IF NOT EXISTS phone VARCHAR(50);

    -- 3. Create waitlist_entries table
    CREATE TABLE IF NOT EXISTS waitlist_entries (
        id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        email           VARCHAR(255) UNIQUE NOT NULL,
        name            VARCHAR(255),
        company_name    VARCHAR(255),
        phone           VARCHAR(50),
        industry        VARCHAR(100),
        company_size    VARCHAR(50),

        -- Status tracking
        status          VARCHAR(50) DEFAULT 'pending',

        -- Metadata
        source          VARCHAR(100) DEFAULT 'website',
        utm_campaign    VARCHAR(255),
        utm_source      VARCHAR(255),
        utm_medium      VARCHAR(255),
        ip_address      INET,
        user_agent      TEXT,

        -- Email tracking
        confirmation_sent   BOOLEAN DEFAULT FALSE,
        confirmation_sent_at TIMESTAMP WITH TIME ZONE,
        notification_sent   BOOLEAN DEFAULT FALSE,
        notification_sent_at TIMESTAMP WITH TIME ZONE,

        -- Conversion tracking
        converted_to_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
        converted_at      TIMESTAMP WITH TIME ZONE,

        -- Timestamps
        created_at        TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
        updated_at        TIMESTAMP WITH TIME ZONE DEFAULT NOW()
    );

    -- 4. Create indexes for waitlist
    CREATE INDEX IF NOT EXISTS idx_waitlist_email ON waitlist_entries(email);
    CREATE INDEX IF NOT EXISTS idx_waitlist_status ON waitlist_entries(status);
    CREATE INDEX IF NOT EXISTS idx_waitlist_created ON waitlist_entries(created_at);

    -- 5. Create trigger for updated_at (if function exists)
    DO $$
    BEGIN
        IF EXISTS (
            SELECT 1 FROM pg_proc WHERE proname = 'update_updated_at_column'
        ) THEN
            -- Check if trigger already exists
            IF NOT EXISTS (
                SELECT 1 FROM pg_trigger WHERE tgname = 'update_waitlist_updated_at'
            ) THEN
                CREATE TRIGGER update_waitlist_updated_at
                BEFORE UPDATE ON waitlist_entries
                FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
            END IF;
        END IF;
    END $$;

    -- 6. Ensure crews indexes exist
    CREATE INDEX IF NOT EXISTS idx_crews_active ON crews(active);
    """

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        print("Running migration...")
        cursor.execute(migration_sql)
        conn.commit()

        print("✓ Migration completed successfully!")
        print("  - Fixed crews table (removed staff_ids column if existed)")
        print("  - Added missing columns to crews table")
        print("  - Created waitlist_entries table")
        print("  - Created indexes")

    except Exception as e:
        print(f"✗ Migration failed: {e}")
        sys.exit(1)
    finally:
        if conn:
            conn.close()

if __name__ == "__main__":
    run_migration()
