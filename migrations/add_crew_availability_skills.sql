-- Migration: Add crew availability and skills columns
-- Date: 2026-06-27

-- Add availability columns (working hours)
ALTER TABLE crews ADD COLUMN IF NOT EXISTS availability_start TIME DEFAULT '08:00';
ALTER TABLE crews ADD COLUMN IF NOT EXISTS availability_end TIME DEFAULT '18:00';

-- Add work days (bitmask: Sun=1, Mon=2, Tue=4, Wed=8, Thu=16, Fri=32, Sat=64)
-- Default Mon-Fri = 2+4+8+16+32 = 62
ALTER TABLE crews ADD COLUMN IF NOT EXISTS work_days INTEGER DEFAULT 62;

-- Add skills/tags
ALTER TABLE crews ADD COLUMN IF NOT EXISTS skills TEXT;