-- Migration: Add demo pass columns to waitlist_entries
-- Date: 2026-07-02
-- Purpose: When someone joins the waitlist, grant them 7 days of read-only
-- access to the dashboard so they can see what FieldPulse looks like
-- before going through full Clerk signup.

ALTER TABLE waitlist_entries
    ADD COLUMN IF NOT EXISTS demo_access_granted BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS demo_access_expires_at TIMESTAMP WITH TIME ZONE,
    ADD COLUMN IF NOT EXISTS demo_session_token VARCHAR(255);

-- Index for fast lookup when validating demo session
CREATE INDEX IF NOT EXISTS idx_waitlist_demo_token ON waitlist_entries(demo_session_token)
    WHERE demo_session_token IS NOT NULL;

-- Index for expiry cleanup jobs
CREATE INDEX IF NOT EXISTS idx_waitlist_demo_expires ON waitlist_entries(demo_access_expires_at)
    WHERE demo_access_granted = TRUE;
