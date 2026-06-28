-- Migration: Add job_templates table
-- Date: 2026-06-28

-- Job templates for quick job creation
CREATE TABLE IF NOT EXISTS job_templates (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    business_id         UUID REFERENCES businesses(id) ON DELETE CASCADE,
    name                VARCHAR(255) NOT NULL,  -- Template name (e.g., "Weekly Lawn Care")
    title               VARCHAR(255) NOT NULL,  -- Default job title
    description         TEXT,
    customer_name       VARCHAR(255),           -- Pre-fill customer name
    customer_phone      VARCHAR(50),
    customer_email      VARCHAR(255),
    address             TEXT,
    city                VARCHAR(100),
    estimated_duration  INTEGER DEFAULT 60,     -- Duration in minutes
    crew_id             UUID REFERENCES crews(id) ON DELETE SET NULL,
    created_at          TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at          TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_job_templates_business ON job_templates(business_id);