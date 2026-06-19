-- Seed demo data for FieldPulse

-- Insert business
INSERT INTO businesses (id, name, slug, email, phone, plan, active)
VALUES ('a1631c27-4b0d-4ecb-a684-2554c0acaa0e', 'Demo Landscaping Co', 'demo-landscaping', 'owner@demolandscaping.com', '555-0100', 'trial', true)
ON CONFLICT (id) DO NOTHING;

-- Insert user
INSERT INTO users (id, business_id, email, name, role, active)
VALUES ('634e6557-7baf-4894-8324-00058482c290', 'a1631c27-4b0d-4ecb-a684-2554c0acaa0e', 'owner@demolandscaping.com', 'Demo Owner', 'owner', true)
ON CONFLICT (id) DO NOTHING;

-- Insert sample job
INSERT INTO jobs (id, business_id, title, description, customer_name, customer_phone, address, city, scheduled_date, status, estimated_duration_min)
VALUES ('749ede4e-e0d3-4822-9697-d86ad92bfc65', 'a1631c27-4b0d-4ecb-a684-2554c0acaa0e', 'Lawn Maintenance - Oak St', 'Weekly lawn maintenance', 'Jane Smith', '555-1234', '123 Oak St', 'Springfield', NOW(), 'scheduled', 60)
ON CONFLICT (id) DO NOTHING;

-- Verify
SELECT 'Businesses:' as info, COUNT(*) as count FROM businesses
UNION ALL
SELECT 'Users:', COUNT(*) FROM users
UNION ALL
SELECT 'Jobs:', COUNT(*) FROM jobs;
