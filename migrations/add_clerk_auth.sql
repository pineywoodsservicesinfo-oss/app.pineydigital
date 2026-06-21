-- Migration: Add Clerk authentication support
-- Run this to enable Clerk auth alongside legacy auth

-- Add Clerk IDs to businesses table for multi-tenant organization support
ALTER TABLE businesses
ADD COLUMN IF NOT EXISTS clerk_org_id VARCHAR(255),
ADD COLUMN IF NOT EXISTS clerk_user_id VARCHAR(255);

-- Add index for Clerk lookups
CREATE INDEX IF NOT EXISTS idx_businesses_clerk_org ON businesses(clerk_org_id);
CREATE INDEX IF NOT EXISTS idx_businesses_clerk_user ON businesses(clerk_user_id);

-- Add Clerk IDs to users table
ALTER TABLE users
ADD COLUMN IF NOT EXISTS clerk_user_id VARCHAR(255),
ADD COLUMN IF NOT EXISTS email_verified_clerk BOOLEAN DEFAULT FALSE;

-- Index for Clerk user lookups
CREATE INDEX IF NOT EXISTS idx_users_clerk ON users(clerk_user_id);

-- Make password_hash nullable for Clerk-only users (optional migration)
-- ALTER TABLE users ALTER COLUMN password_hash DROP NOT NULL;

-- Note: For Clerk integration, you have two options:
-- 1. Keep password_hash for users who want password auth
-- 2. Remove it entirely and rely on Clerk for all auth

-- Create a sync function to map Clerk users to your business
COMMENT ON TABLE businesses IS 'Multi-tenant businesses with optional Clerk organization mapping';
COMMENT ON TABLE users IS 'Users with optional Clerk ID mapping for JWT auth';
