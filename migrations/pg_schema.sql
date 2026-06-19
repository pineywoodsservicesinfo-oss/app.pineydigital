-- FieldPulse PostgreSQL Schema
-- Migrated from SQLite for multi-tenant SaaS platform

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ── CORE TABLES ────────────────────────────────────────────────────────

-- Businesses (SaaS tenants) - MUST BE FIRST (referenced by users)
CREATE TABLE IF NOT EXISTS businesses (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    -- From leads table (conversion path)
    lead_id         INTEGER,  -- Reference to original lead (if converted)

    -- Business info
    name            VARCHAR(255) NOT NULL,
    type            VARCHAR(100),           -- Industry category
    description     TEXT,

    -- Contact
    address         TEXT,
    city            VARCHAR(100),
    phone           VARCHAR(50),
    website         VARCHAR(255),
    email           VARCHAR(255),

    -- Branding (white-label)
    logo_url        VARCHAR(500),
    primary_color   VARCHAR(7) DEFAULT '#4F46E5',  -- Default indigo
    secondary_color VARCHAR(7) DEFAULT '#818CF8',
    domain          VARCHAR(255) UNIQUE,    -- Custom domain for white-label

    -- Settings
    timezone        VARCHAR(50) DEFAULT 'America/Chicago',
    currency        VARCHAR(3) DEFAULT 'USD',

    -- Loyalty settings
    punches_needed  INTEGER DEFAULT 5,
    discount_percent INTEGER DEFAULT 15,

    -- Status
    active          BOOLEAN DEFAULT TRUE,
    plan            VARCHAR(50) DEFAULT 'starter',  -- starter/professional/enterprise

    -- Timestamps
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_businesses_lead ON businesses(lead_id);
CREATE INDEX idx_businesses_domain ON businesses(domain);

-- Users (multi-tenant auth) - AFTER businesses table
CREATE TABLE IF NOT EXISTS users (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email           VARCHAR(255) NOT NULL UNIQUE,
    password_hash   VARCHAR(255) NOT NULL,

    -- Business association
    business_id     UUID REFERENCES businesses(id) ON DELETE CASCADE,

    -- Profile
    name            VARCHAR(255),
    role            VARCHAR(50) DEFAULT 'owner',  -- owner/admin/staff/customer

    -- Verification
    email_verified  BOOLEAN DEFAULT FALSE,
    verification_token VARCHAR(255),

    -- 2FA
    two_fa_enabled  BOOLEAN DEFAULT FALSE,
    two_fa_secret   VARCHAR(255),
    two_fa_backup_codes TEXT[],

    -- Subscription
    stripe_customer_id VARCHAR(255),
    subscription_id    VARCHAR(255),
    subscription_status VARCHAR(50),  -- active/past_due/canceled/trialing

    -- Timestamps
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    last_login_at   TIMESTAMP WITH TIME ZONE
);

CREATE INDEX idx_users_email ON users(email);
CREATE INDEX idx_users_business ON users(business_id);

-- ── LEAD MANAGEMENT (for Piney Outreach compatibility) ─────────────────────

CREATE TABLE IF NOT EXISTS leads (
    id              SERIAL PRIMARY KEY,

    -- Business info
    business_name   VARCHAR(255) NOT NULL,
    category        VARCHAR(100),
    city            VARCHAR(100),
    address         TEXT,
    phone           VARCHAR(50),
    website         VARCHAR(500),
    google_maps_url VARCHAR(500),
    rating          DECIMAL(3,2),
    review_count    INTEGER,

    -- Website qualification
    has_website     BOOLEAN,
    site_status     VARCHAR(50),      -- none/parked/outdated/modern
    site_last_updated DATE,

    -- Contact enrichment
    owner_name      VARCHAR(255),
    owner_email     VARCHAR(255),
    email_source    VARCHAR(50),     -- scraped/hunter/manual

    -- Outreach status
    lead_score      INTEGER DEFAULT 0,
    outreach_status VARCHAR(50) DEFAULT 'new',  -- new/queued/sent/replied/booked/dead
    email_sent_at   TIMESTAMP WITH TIME ZONE,
    sms_sent_at     TIMESTAMP WITH TIME ZONE,
    last_reply_at   TIMESTAMP WITH TIME ZONE,
    reply_intent    VARCHAR(50),     -- interested/not_interested/question

    -- Call outreach
    call_status     VARCHAR(50),     -- new/queued/called/voicemail/interested/transferred/declined/no_answer
    call_sid        VARCHAR(255),
    call_transcript TEXT,
    call_summary    TEXT,
    call_duration   INTEGER,
    call_attempts   INTEGER DEFAULT 0,
    last_call_at    TIMESTAMP WITH TIME ZONE,

    -- Enterprise fields
    pipeline_type   VARCHAR(50) DEFAULT 'enterprise',
    parent_company  VARCHAR(255),
    franchise_brand VARCHAR(255),
    locations_count INTEGER DEFAULT 1,
    employee_count  VARCHAR(50),
    estimated_revenue VARCHAR(100),
    tech_stack      TEXT,
    growth_signals  TEXT,
    decision_makers JSONB,
    lead_source     VARCHAR(50) DEFAULT 'google_maps',

    -- Meta
    scraped_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    notes           TEXT
);

CREATE INDEX idx_leads_status ON leads(outreach_status);
CREATE INDEX idx_leads_city ON leads(city);
CREATE INDEX idx_leads_category ON leads(category);
CREATE INDEX idx_leads_call ON leads(call_status);

-- Outreach log
CREATE TABLE IF NOT EXISTS outreach_log (
    id            SERIAL PRIMARY KEY,
    lead_id       INTEGER REFERENCES leads(id) ON DELETE CASCADE,
    channel       VARCHAR(50),       -- email/sms/call
    direction     VARCHAR(50),       -- outbound/inbound
    subject       VARCHAR(500),
    body          TEXT,
    transcript    TEXT,               -- For calls
    duration      INTEGER,            -- Call duration in seconds
    status        VARCHAR(50),        -- sent/failed/received/voicemail/transferred/no_answer
    external_id   VARCHAR(255),       -- Twilio/Vapi ID
    sequence_step INTEGER DEFAULT 1,
    sent_at       TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_outreach_lead ON outreach_log(lead_id);
CREATE INDEX idx_outreach_sent ON outreach_log(sent_at);

-- Scrape runs
CREATE TABLE IF NOT EXISTS scrape_runs (
    id          SERIAL PRIMARY KEY,
    city        VARCHAR(100),
    category    VARCHAR(100),
    results     INTEGER,
    new_leads   INTEGER,
    started_at  TIMESTAMP WITH TIME ZONE,
    finished_at TIMESTAMP WITH TIME ZONE
);

-- Decision makers (enterprise pipeline)
CREATE TABLE IF NOT EXISTS decision_makers (
    id              SERIAL PRIMARY KEY,
    lead_id         INTEGER REFERENCES leads(id) ON DELETE CASCADE,
    name            VARCHAR(255) NOT NULL,
    title           VARCHAR(255),
    email           VARCHAR(255),
    phone           VARCHAR(50),
    linkedin_url    VARCHAR(500),
    is_primary      BOOLEAN DEFAULT FALSE,
    outreach_status VARCHAR(50) DEFAULT 'new',
    last_contact_at TIMESTAMP WITH TIME ZONE,
    notes           TEXT,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_dm_lead ON decision_makers(lead_id);

-- Outreach sequences
CREATE TABLE IF NOT EXISTS outreach_sequences (
    id              SERIAL PRIMARY KEY,
    lead_id         INTEGER REFERENCES leads(id) ON DELETE CASCADE,
    sequence_id     INTEGER DEFAULT 1,
    current_step    INTEGER DEFAULT 0,
    status          VARCHAR(50) DEFAULT 'active',
    last_sent_at    TIMESTAMP WITH TIME ZONE,
    next_send_at    TIMESTAMP WITH TIME ZONE,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_seq_lead ON outreach_sequences(lead_id);
CREATE INDEX idx_seq_next ON outreach_sequences(next_send_at) WHERE status = 'active';

-- Outreach campaigns
CREATE TABLE IF NOT EXISTS outreach_campaigns (
    id              SERIAL PRIMARY KEY,
    name            VARCHAR(255) NOT NULL,
    pipeline_type   VARCHAR(50) DEFAULT 'enterprise',
    industry        VARCHAR(100),
    sequence_config JSONB,
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- ── AUTH & SESSIONS ─────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS admin_sessions (
    id              SERIAL PRIMARY KEY,
    session_token   VARCHAR(255) NOT NULL UNIQUE,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    expires_at      TIMESTAMP WITH TIME ZONE NOT NULL,
    ip_address      VARCHAR(50),
    user_agent      TEXT,
    two_fa_verified BOOLEAN DEFAULT FALSE,
    remember_device VARCHAR(255)        -- Token for "remember this device"
);

CREATE INDEX idx_admin_sessions_token ON admin_sessions(session_token);

CREATE TABLE IF NOT EXISTS password_reset_tokens (
    id              SERIAL PRIMARY KEY,
    email           VARCHAR(255) NOT NULL,
    token           VARCHAR(255) NOT NULL UNIQUE,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    expires_at      TIMESTAMP WITH TIME ZONE NOT NULL,
    used            BOOLEAN DEFAULT FALSE
);

CREATE INDEX idx_reset_token ON password_reset_tokens(token);

CREATE TABLE IF NOT EXISTS audit_log (
    id              SERIAL PRIMARY KEY,
    event_type      VARCHAR(50) NOT NULL,  -- login/login_failed/logout/2fa/2fa_failed/password_change
    user_type       VARCHAR(50),           -- admin/business
    user_id         UUID,
    email           VARCHAR(255),
    ip_address     VARCHAR(50),
    user_agent      TEXT,
    details         JSONB,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_audit_created ON audit_log(created_at);

-- Settings
CREATE TABLE IF NOT EXISTS settings (
    key VARCHAR(100) PRIMARY KEY,
    value TEXT,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- ── LOYALTY PROGRAM ──────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS loyalty_customers (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name            VARCHAR(255) NOT NULL,
    email           VARCHAR(255),
    phone           VARCHAR(50),
    password_hash   VARCHAR(255),

    -- For multi-tenant, link to business
    primary_business_id UUID REFERENCES businesses(id),

    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_loyalty_customers_email ON loyalty_customers(email);
CREATE INDEX idx_loyalty_customers_phone ON loyalty_customers(phone);

CREATE TABLE IF NOT EXISTS loyalty_cards (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    customer_id     UUID REFERENCES loyalty_customers(id) ON DELETE CASCADE,
    business_id     UUID REFERENCES businesses(id) ON DELETE CASCADE,
    punches         INTEGER DEFAULT 0,
    rewards_earned  INTEGER DEFAULT 0,
    last_punch_at   TIMESTAMP WITH TIME ZONE,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),

    UNIQUE(customer_id, business_id)
);

CREATE INDEX idx_cards_customer ON loyalty_cards(customer_id);
CREATE INDEX idx_cards_business ON loyalty_cards(business_id);

CREATE TABLE IF NOT EXISTS punch_history (
    id              SERIAL PRIMARY KEY,
    card_id         UUID REFERENCES loyalty_cards(id) ON DELETE CASCADE,
    business_id     UUID REFERENCES businesses(id) ON DELETE CASCADE,
    customer_id     UUID REFERENCES loyalty_customers(id) ON DELETE CASCADE,
    punched_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    punched_by      VARCHAR(50),          -- business/customer/system
    notes           TEXT
);

CREATE INDEX idx_punch_card ON punch_history(card_id);
CREATE INDEX idx_punch_business ON punch_history(business_id);

CREATE TABLE IF NOT EXISTS reward_redemptions (
    id              SERIAL PRIMARY KEY,
    card_id         UUID REFERENCES loyalty_cards(id) ON DELETE CASCADE,
    business_id     UUID REFERENCES businesses(id) ON DELETE CASCADE,
    customer_id     UUID REFERENCES loyalty_customers(id) ON DELETE CASCADE,
    discount_percent INTEGER,
    redeemed_at     TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    redeemed_by     VARCHAR(50),
    notes           TEXT
);

CREATE INDEX idx_redemptions_card ON reward_redemptions(card_id);

-- ── BOOKING SYSTEM ───────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS booking_services (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    business_id     UUID REFERENCES businesses(id) ON DELETE CASCADE,
    name            VARCHAR(255) NOT NULL,
    description     TEXT,
    duration_min    INTEGER DEFAULT 30,
    price           DECIMAL(10,2) DEFAULT 0,
    active          BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_services_business ON booking_services(business_id);

CREATE TABLE IF NOT EXISTS booking_staff (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    business_id     UUID REFERENCES businesses(id) ON DELETE CASCADE,
    name            VARCHAR(255) NOT NULL,
    role            VARCHAR(100),
    email           VARCHAR(255),
    phone           VARCHAR(50),
    hourly_rate     DECIMAL(10,2),
    certifications  TEXT[],
    active          BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_staff_business ON booking_staff(business_id);

CREATE TABLE IF NOT EXISTS staff_availability (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    staff_id        UUID REFERENCES booking_staff(id) ON DELETE CASCADE,
    day_of_week     INTEGER NOT NULL,     -- 0=Monday, 6=Sunday
    start_time      TIME NOT NULL,
    end_time        TIME NOT NULL,
    is_working      BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_avail_staff ON staff_availability(staff_id);

CREATE TABLE IF NOT EXISTS staff_time_off (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    staff_id        UUID REFERENCES booking_staff(id) ON DELETE CASCADE,
    date            DATE NOT NULL,
    reason          TEXT,
    is_all_day      BOOLEAN DEFAULT TRUE,
    start_time      TIME,
    end_time        TIME,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_timeoff_staff ON staff_time_off(staff_id);
CREATE INDEX idx_timeoff_date ON staff_time_off(date);

CREATE TABLE IF NOT EXISTS recurring_bookings (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    business_id     UUID REFERENCES businesses(id) ON DELETE CASCADE,
    customer_id     UUID REFERENCES loyalty_customers(id) ON DELETE SET NULL,
    staff_id        UUID REFERENCES booking_staff(id) ON DELETE SET NULL,
    service_id      UUID REFERENCES booking_services(id) ON DELETE SET NULL,

    -- Recurrence pattern
    recurrence_type VARCHAR(50) NOT NULL,  -- weekly/biweekly/monthly
    day_of_week     INTEGER,              -- 0-6 (for weekly)
    day_of_month    INTEGER,              -- 1-31 (for monthly)
    interval_weeks  INTEGER DEFAULT 1,   -- For biweekly = 2

    -- Time slot
    booking_time    TIME NOT NULL,
    duration_min    INTEGER DEFAULT 30,

    -- Date range
    start_date      DATE NOT NULL,
    end_date        DATE,
    max_occurrences INTEGER,

    -- Customer info
    customer_name   VARCHAR(255) NOT NULL,
    customer_phone  VARCHAR(50),
    customer_email  VARCHAR(255),
    notes           TEXT,

    -- Status
    active          BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_recurring_business ON recurring_bookings(business_id);

CREATE TABLE IF NOT EXISTS bookings (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    business_id     UUID REFERENCES businesses(id) ON DELETE CASCADE,
    customer_id     UUID REFERENCES loyalty_customers(id) ON DELETE SET NULL,
    staff_id        UUID REFERENCES booking_staff(id) ON DELETE SET NULL,
    service_id      UUID REFERENCES booking_services(id) ON DELETE SET NULL,
    recurring_id    UUID REFERENCES recurring_bookings(id) ON DELETE SET NULL,

    -- Booking details
    booking_date    DATE NOT NULL,
    booking_time    TIME NOT NULL,
    duration_min    INTEGER DEFAULT 30,
    end_time        TIME,

    -- Customer info (snapshot at booking time)
    customer_name   VARCHAR(255) NOT NULL,
    customer_phone  VARCHAR(50),
    customer_email  VARCHAR(255),

    -- Status & metadata
    status          VARCHAR(50) DEFAULT 'pending',  -- pending/confirmed/completed/cancelled/no_show
    notes           TEXT,
    internal_notes  TEXT,

    -- Timestamps
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    confirmed_at    TIMESTAMP WITH TIME ZONE,
    completed_at    TIMESTAMP WITH TIME ZONE,
    cancelled_at    TIMESTAMP WITH TIME ZONE,

    -- Source tracking
    source          VARCHAR(50) DEFAULT 'web',  -- web/phone/walk_in/recurring
    reminder_sent   BOOLEAN DEFAULT FALSE,
    loyalty_punch_added BOOLEAN DEFAULT FALSE
);

CREATE INDEX idx_bookings_business ON bookings(business_id);
CREATE INDEX idx_bookings_date ON bookings(booking_date);
CREATE INDEX idx_bookings_status ON bookings(status);
CREATE INDEX idx_bookings_customer ON bookings(customer_id);
CREATE INDEX idx_bookings_staff ON bookings(staff_id);

CREATE TABLE IF NOT EXISTS booking_notifications (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    booking_id      UUID REFERENCES bookings(id) ON DELETE CASCADE,
    type            VARCHAR(50),          -- confirmation/reminder/cancellation
    channel         VARCHAR(50) DEFAULT 'sms',
    sent_at         TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    status          VARCHAR(50) DEFAULT 'sent',
    message_sid     VARCHAR(255)
);

CREATE INDEX idx_notif_booking ON booking_notifications(booking_id);

-- ── REVIEW SYSTEM ───────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS review_settings (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    business_id     UUID REFERENCES businesses(id) ON DELETE CASCADE,
    enabled         BOOLEAN DEFAULT TRUE,
    delay_hours     INTEGER DEFAULT 2,
    google_url      VARCHAR(500),
    yelp_url        VARCHAR(500),
    custom_message  TEXT,
    min_stars_public INTEGER DEFAULT 4,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),

    UNIQUE(business_id)
);

CREATE TABLE IF NOT EXISTS review_requests (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    business_id     UUID REFERENCES businesses(id) ON DELETE CASCADE,
    customer_id     UUID REFERENCES loyalty_customers(id) ON DELETE CASCADE,
    card_id         UUID REFERENCES loyalty_cards(id) ON DELETE SET NULL,
    sent_at         TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    opened_at      TIMESTAMP WITH TIME ZONE,
    rated_at       TIMESTAMP WITH TIME ZONE,
    status          VARCHAR(50) DEFAULT 'sent',  -- sent/opened/rated/ignored
    channel         VARCHAR(50) DEFAULT 'sms',
    message_sid     VARCHAR(255)
);

CREATE INDEX idx_requests_business ON review_requests(business_id);
CREATE INDEX idx_requests_customer ON review_requests(customer_id);
CREATE INDEX idx_requests_status ON review_requests(status);

CREATE TABLE IF NOT EXISTS review_ratings (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    request_id      UUID REFERENCES review_requests(id) ON DELETE CASCADE,
    business_id     UUID REFERENCES businesses(id) ON DELETE CASCADE,
    customer_id     UUID REFERENCES loyalty_customers(id) ON DELETE CASCADE,
    stars           INTEGER NOT NULL CHECK (stars >= 1 AND stars <= 5),
    feedback        TEXT,
    is_public       BOOLEAN DEFAULT FALSE,
    submitted_at    TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    source          VARCHAR(50) DEFAULT 'link'  -- link/qr/direct
);

CREATE INDEX idx_ratings_business ON review_ratings(business_id);
CREATE INDEX idx_ratings_stars ON review_ratings(stars);

-- ── REFERRAL SYSTEM ─────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS referral_settings (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    business_id     UUID REFERENCES businesses(id) ON DELETE CASCADE,
    enabled         BOOLEAN DEFAULT TRUE,
    referrer_reward_type VARCHAR(50) DEFAULT 'punches',  -- punches/discount/credits
    referrer_reward_value INTEGER DEFAULT 2,
    referee_reward_type VARCHAR(50) DEFAULT 'punches',
    referee_reward_value INTEGER DEFAULT 1,
    max_referrals   INTEGER,            -- NULL = unlimited
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),

    UNIQUE(business_id)
);

CREATE TABLE IF NOT EXISTS referral_codes (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    customer_id     UUID REFERENCES loyalty_customers(id) ON DELETE CASCADE,
    business_id     UUID REFERENCES businesses(id) ON DELETE CASCADE,
    code            VARCHAR(20) UNIQUE NOT NULL,
    clicks          INTEGER DEFAULT 0,
    conversions     INTEGER DEFAULT 0,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    last_used_at    TIMESTAMP WITH TIME ZONE,

    UNIQUE(customer_id, business_id)
);

CREATE INDEX idx_referrals_code ON referral_codes(code);

CREATE TABLE IF NOT EXISTS referrals (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    code_id         UUID REFERENCES referral_codes(id) ON DELETE CASCADE,
    referrer_id     UUID REFERENCES loyalty_customers(id) ON DELETE CASCADE,
    referee_id      UUID REFERENCES loyalty_customers(id) ON DELETE CASCADE,
    business_id     UUID REFERENCES businesses(id) ON DELETE CASCADE,
    status          VARCHAR(50) DEFAULT 'pending',  -- pending/completed/rewarded
    referrer_reward_given BOOLEAN DEFAULT FALSE,
    referee_reward_given BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    completed_at    TIMESTAMP WITH TIME ZONE,
    rewarded_at     TIMESTAMP WITH TIME ZONE
);

CREATE INDEX idx_referrals_referrer ON referrals(referrer_id);
CREATE INDEX idx_referrals_referee ON referrals(referee_id);
CREATE INDEX idx_referrals_business ON referrals(business_id);

CREATE TABLE IF NOT EXISTS referral_clicks (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    code_id         UUID REFERENCES referral_codes(id) ON DELETE CASCADE,
    clicked_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    ip_address      VARCHAR(50),
    user_agent      TEXT,
    converted       BOOLEAN DEFAULT FALSE
);

CREATE INDEX idx_clicks_code ON referral_clicks(code_id);

-- ── TIME TRACKING (for FieldPulse) ───────────────────────────────────────

CREATE TABLE IF NOT EXISTS time_entries (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    business_id     UUID REFERENCES businesses(id) ON DELETE CASCADE,
    staff_id        UUID REFERENCES booking_staff(id) ON DELETE CASCADE,
    booking_id      UUID REFERENCES bookings(id) ON DELETE SET NULL,

    -- Time tracking
    clock_in        TIMESTAMP WITH TIME ZONE NOT NULL,
    clock_out       TIMESTAMP WITH TIME ZONE,
    duration_min    INTEGER,            -- Calculated on clock out
    gps_lat_clock_in  DECIMAL(10,8),
    gps_lng_clock_in  DECIMAL(11,8),
    gps_lat_clock_out DECIMAL(10,8),
    gps_lng_clock_out DECIMAL(11,8),

    -- Notes
    notes           TEXT,

    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_time_business ON time_entries(business_id);
CREATE INDEX idx_time_staff ON time_entries(staff_id);
CREATE INDEX idx_time_booking ON time_entries(booking_id);

-- ── JOBS (for FieldPulse dispatch) ───────────────────────────────────────

-- Jobs are separate from bookings - they represent work to be done
CREATE TABLE IF NOT EXISTS jobs (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    business_id     UUID REFERENCES businesses(id) ON DELETE CASCADE,

    -- Customer (can be linked to loyalty_customers or be ad-hoc)
    customer_id     UUID REFERENCES loyalty_customers(id) ON DELETE SET NULL,
    customer_name   VARCHAR(255) NOT NULL,
    customer_phone  VARCHAR(50),
    customer_email  VARCHAR(255),
    customer_address TEXT,

    -- Job details
    title           VARCHAR(255) NOT NULL,
    description     TEXT,
    service_id      UUID REFERENCES booking_services(id) ON DELETE SET NULL,

    -- Scheduling
    scheduled_date  DATE,
    scheduled_time  TIME,
    estimated_duration_min INTEGER,

    -- Assignment
    crew_id         UUID,               -- Group of staff (for multi-person jobs)
    staff_ids       UUID[],             -- Array of staff IDs

    -- Status
    status          VARCHAR(50) DEFAULT 'pending',  -- pending/assigned/in_progress/completed/cancelled

    -- Location
    address         TEXT,
    city            VARCHAR(100),
    lat             DECIMAL(10,8),
    lng             DECIMAL(11,8),

    -- Route optimization
    route_order     INTEGER,            -- Order in daily route

    -- Timestamps
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    started_at      TIMESTAMP WITH TIME ZONE,
    completed_at    TIMESTAMP WITH TIME ZONE,

    -- Financial
    estimated_price DECIMAL(10,2),
    actual_price    DECIMAL(10,2),
    invoice_id      UUID                -- Link to invoice (when created)
);

CREATE INDEX idx_jobs_business ON jobs(business_id);
CREATE INDEX idx_jobs_status ON jobs(status);
CREATE INDEX idx_jobs_date ON jobs(scheduled_date);
CREATE INDEX idx_jobs_staff ON jobs USING GIN(staff_ids);

-- Job photos/notes
CREATE TABLE IF NOT EXISTS job_photos (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    job_id          UUID REFERENCES jobs(id) ON DELETE CASCADE,
    uploaded_by     UUID REFERENCES booking_staff(id) ON DELETE CASCADE,
    photo_url       VARCHAR(500) NOT NULL,
    photo_type      VARCHAR(50) DEFAULT 'progress',  -- before/after/progress/issue
    caption         TEXT,
    lat             DECIMAL(10,8),
    lng             DECIMAL(11,8),
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_photos_job ON job_photos(job_id);

-- Crews (groups of staff)
CREATE TABLE IF NOT EXISTS crews (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    business_id     UUID REFERENCES businesses(id) ON DELETE CASCADE,
    name            VARCHAR(255) NOT NULL,
    lead_staff_id   UUID REFERENCES booking_staff(id) ON DELETE SET NULL,
    staff_ids       UUID[] NOT NULL,
    active          BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_crews_business ON crews(business_id);

-- ── VIEWS FOR COMMON QUERIES ─────────────────────────────────────────────

-- Business dashboard stats
CREATE OR REPLACE VIEW business_dashboard AS
SELECT
    b.id as business_id,
    b.name as business_name,
    COUNT(DISTINCT lc.id) as total_customers,
    COUNT(DISTINCT bk.id) FILTER (WHERE bk.status != 'cancelled') as total_bookings,
    COUNT(DISTINCT bk.id) FILTER (WHERE bk.booking_date = CURRENT_DATE) as today_bookings,
    COUNT(DISTINCT j.id) FILTER (WHERE j.status IN ('pending', 'assigned')) as pending_jobs,
    COUNT(DISTINCT j.id) FILTER (WHERE j.scheduled_date = CURRENT_DATE) as today_jobs,
    SUM(lc.punches) as total_punches,
    COUNT(DISTINCT rr.id) as total_reviews,
    AVG(rr.stars) as avg_rating
FROM businesses b
LEFT JOIN loyalty_cards lc ON b.id = lc.business_id
LEFT JOIN bookings bk ON b.id = bk.business_id
LEFT JOIN jobs j ON b.id = j.business_id
LEFT JOIN review_ratings rr ON b.id = rr.business_id
GROUP BY b.id;

-- ── TRIGGER FOR UPDATED_AT ───────────────────────────────────────────────

CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Apply trigger to tables with updated_at
CREATE TRIGGER update_businesses_updated_at BEFORE UPDATE ON businesses
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_users_updated_at BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_leads_updated_at BEFORE UPDATE ON leads
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_loyalty_customers_updated_at BEFORE UPDATE ON loyalty_customers
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_loyalty_cards_updated_at BEFORE UPDATE ON loyalty_cards
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_booking_services_updated_at BEFORE UPDATE ON booking_services
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_booking_staff_updated_at BEFORE UPDATE ON booking_staff
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_bookings_updated_at BEFORE UPDATE ON bookings
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_recurring_bookings_updated_at BEFORE UPDATE ON recurring_bookings
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- ── INITIAL SEED DATA ────────────────────────────────────────────────────

-- Insert default settings
INSERT INTO settings (key, value) VALUES
    ('app_name', 'FieldPulse'),
    ('version', '1.0.0'),
    ('default_timezone', 'America/Chicago')
ON CONFLICT (key) DO NOTHING;