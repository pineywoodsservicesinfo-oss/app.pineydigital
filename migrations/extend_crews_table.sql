-- Migration: Extend crews table with additional fields
-- Run this to add fields needed for crew management UI

ALTER TABLE crews
    ADD COLUMN IF NOT EXISTS color VARCHAR(50) DEFAULT 'emerald',
    ADD COLUMN IF NOT EXISTS role VARCHAR(255),
    ADD COLUMN IF NOT EXISTS email VARCHAR(255),
    ADD COLUMN IF NOT EXISTS phone VARCHAR(50);

-- Create index on active field for faster filtering
CREATE INDEX IF NOT EXISTS idx_crews_active ON crews(active);
