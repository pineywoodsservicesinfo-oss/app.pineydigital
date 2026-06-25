# FieldPulse - Product Requirements Document

**Version:** 1.0  
**Date:** June 23, 2026  
**Status:** Draft - Beta Phase  
**Product Owner:** Joel Escoto, Piney Digital  

---

## 1. Executive Summary

**What is FieldPulse?**  
FieldPulse is a comprehensive field service management platform designed for established service businesses with 1-30 crews. Unlike competitors that force businesses to choose between underpowered tools (Jobber, max 15 users) or overpriced enterprise solutions (ServiceTitan, $245/tech/mo + $5K-50K setup), FieldPulse fills the gap with intelligent crew-manager-only access, white-label customization, and a unique hybrid revenue model.

**Mission Statement**  
Empower service business owners and crew managers to efficiently schedule, dispatch, and manage field operations while providing seamless customer-facing booking experiences—all through a single, customizable platform.

**Target Launch**  
- Beta: September 23, 2026 (3 months from now)
- Public Launch: Q1 2027

---

## 2. Problem Statement

### Market Pain Points

**For Business Owners (Primary Users):**
1. **Tool Gap:** Current solutions either cap at 15 users (Jobber) or require $60K+/year minimum (ServiceTitan). Businesses with 15-30 crews have no good options.
2. **Per-User Pricing Penalty:** Competitors charge per technician, but technicians don't need full app access—only crew managers do.
3. **Rigid Platforms:** ServiceTitan/Jobber offer limited customization. Businesses can't adapt the platform to their specific workflows.
4. **Slow Implementation:** ServiceTitan takes 6-12 months to onboard. Businesses need to be operational in days, not quarters.

**For Crews:**
- Current apps are bloated with features crews don't need
- Mobile apps are often slow, buggy, or require excessive permissions
- No offline capability in most solutions

**For Clients:**
- Booking through generic forms that don't show real availability
- No transparency into crew location or ETA
- No loyalty/rewards program integration

### Current Solutions Analysis

| Competitor | Price | Max Users | Setup Time | Customization | White-Label |
|------------|-------|-----------|------------|---------------|-------------|
| Jobber | $39-349/mo | 15 users | Hours | Limited | No |
| Housecall Pro | $59-299/mo | Unlimited | Days | Moderate | No |
| ServiceTitan | $245-500/tech + $5K-50K | Unlimited | 6-12 months | Moderate | No |
| FieldEdge | $125/tech/mo | Unlimited | Weeks | Limited | No |
| **FieldPulse** | **Free-$99/mo** | **Unlimited** | **Hours** | **Full** | **Yes** |

**Key Differentiator:**  
FieldPulse's "Manager-Only Access" model means you pay for crew managers (who actually need the app), not every technician. This cuts costs by 60-80% compared to per-user pricing while maintaining full functionality.

---

## 3. Solution Overview

### Core Value Proposition

**"FieldPulse: The field service platform that scales with your crews, not your costs."**

### Platform Architecture

**Three-App Ecosystem:**

1. **FieldPulse Manager** (Web + Mobile)
   - Full-featured dashboard for owners and crew managers
   - Schedule, dispatch, track crews, manage clients
   - Only app requiring login credentials

2. **FieldPulse Crew** (Web + Mobile - No Login Required)
   - Simplified mobile view for technicians
   - View assigned jobs, update status, add notes
   - Access via secure link or QR code—no password needed
   - Offline-capable

3. **FieldPulse Client** (Web + Mobile)
   - Customer-facing booking portal
   - Real-time availability, crew tracking, payment
   - Can be white-labeled to business's domain

### Key Features

**MVP (Beta - September 2026):**
- ✅ Clerk Authentication (Completed)
- ✅ Crew Management (Polishing)
- ⏳ Waitlist Landing Page (In Progress)
- ⏳ Calendar View (Phase 2)
- ⏳ Job Scheduling & Dispatching
- ⏳ Client Booking Frontend
- ⏳ Mobile-Optimized Experience
- ⏳ Email Notifications

**v2 (Q4 2026):**
- SMS Notifications
- Route Optimization
- Basic Reporting
- Payment Processing (Stripe Integration)

**v3 (Q1 2027):**
- Commission/Revenue Share Model
- Advanced Analytics (AI-Powered)
- Third-Party Integrations (QuickBooks, Zapier)
- White-Label API for Developers

---

## 4. User Personas

### Primary Persona: "Owner Olivia"

**Demographics:**
- Age: 35-50
- Role: Owner/Operations Manager
- Company Size: 10-30 employees (2-5 crews)
- Revenue: $2M-$5M annually
- Location: East Texas (initial market)
- Industry: Landscaping, HVAC, Plumbing, General Contracting

**Goals:**
- Reduce scheduling time by 50%
- Eliminate double-bookings and missed appointments
- Track crew productivity
- Scale from 3 crews to 10 crews without adding administrative overhead

**Pain Points:**
- Currently using spreadsheets or basic tools (Google Calendar)
- ServiceTitan too expensive, Jobber too limiting
- Can't afford dedicated office staff
- Needs to be "on the tools" while managing operations

**Quote:**  
"I need something that works as hard as I do, but doesn't require a computer science degree to set up."

---

### Secondary Persona: "Manager Mike"

**Demographics:**
- Age: 28-40
- Role: Crew Manager/Lead Technician
- Reports to: Owner Olivia
- Manages: 3-8 technicians

**Goals:**
- Know where every crew is in real-time
- Quickly reassign jobs when emergencies happen
- See job history and client notes before arriving
- Communicate with office without phone tag

**Pain Points:**
- Current app crashes on old phones
- Too many features he doesn't use
- Can't update job status without calling office
- No way to share photos of completed work

---

### Tertiary Persona: "Client Clara"

**Demographics:**
- Age: 30-65
- Role: Homeowner or Property Manager
- Uses: Mobile phone for everything

**Goals:**
- Book service without calling
- Know exactly when crew will arrive
- See crew photo and reviews before they arrive
- Pay online, get receipt automatically

**Pain Points:**
- Current booking systems show "call for availability"
- No confirmation or reminders
- Missed appointment windows with no updates

---

## 5. Feature Requirements

### 5.1 MVP (Beta Launch - September 2026)

#### Authentication & User Management

| ID | Feature | Priority | Acceptance Criteria |
|----|---------|----------|---------------------|
| AUTH-001 | Clerk Authentication Integration | ✅ DONE | Users can sign up/login via Clerk modal |
| AUTH-002 | Role-Based Access (Owner/Manager/Crew/Client) | P0 | Different roles see different UI |
| AUTH-003 | Secure Crew Access (No Login Required) | P0 | Crews access via secure token/link |

#### Crew Management

| ID | Feature | Priority | Acceptance Criteria |
|----|---------|----------|---------------------|
| CREW-001 | Add/Edit/Deactivate Crews | ✅ DONE | Managers can manage crew roster |
| CREW-002 | Crew Profiles (Name, Photo, Skills) | P0 | Each crew has profile page |
| CREW-003 | Assign Crews to Jobs | P0 | One-click crew assignment |
| CREW-004 | Crew Availability Tracking | P1 | Mark crews as available/unavailable |

#### Job Scheduling (MVP)

| ID | Feature | Priority | Acceptance Criteria |
|----|---------|----------|---------------------|
| JOB-001 | Create New Jobs | ✅ DONE | Form with client info, service type, date/time |
| JOB-002 | Job Status Workflow | ✅ DONE | Scheduled → In Progress → Completed |
| JOB-003 | Calendar View (Basic) | P0 | Weekly view of all jobs |
| JOB-004 | Job Notes & Photos | ✅ DONE | Attach notes and photos to jobs |
| JOB-005 | Client Management | P0 | Store client history and contact info |

#### Client Frontend (Booking)

| ID | Feature | Priority | Acceptance Criteria |
|----|---------|----------|---------------------|
| CLIENT-001 | Public Booking Page | P0 | Clients can book without login |
| CLIENT-002 | Real-Time Availability | P1 | Shows open slots based on crew availability |
| CLIENT-003 | Service Selection | P0 | Choose from business's service list |
| CLIENT-004 | Email Confirmations | P0 | Automated booking confirmation emails |

#### Waitlist Landing Page

| ID | Feature | Priority | Acceptance Criteria |
|----|---------|----------|---------------------|
| LP-001 | Public Landing Page | P0 | Single-page marketing site |
| LP-002 | Clerk Waitlist Integration | P0 | Sign up via Clerk, redirect to thank-you page |
| LP-003 | FAQ Section | P1 | 5-10 common questions answered |
| LP-004 | Piney Digital Footer | P0 | "A Product of Piney Digital" with link |

#### Notifications

| ID | Feature | Priority | Acceptance Criteria |
|----|---------|----------|---------------------|
| NOTIF-001 | Email Notifications | P0 | Booking confirmations, reminders |
| NOTIF-002 | Status Change Alerts | P1 | Notify when job status updates |

---

### 5.2 v2 (Q4 2026)

| Category | Features |
|----------|----------|
| **Scheduling** | Drag-and-drop calendar, recurring jobs, route optimization |
| **Mobile** | Native-feel mobile app (PWA), offline mode, push notifications |
| **Payments** | Stripe integration, invoicing, automatic billing |
| **Communications** | SMS notifications, in-app messaging, automated review requests |
| **Reporting** | Basic analytics dashboard, job completion rates, revenue tracking |

---

### 5.3 v3 (Q1 2027)

| Category | Features |
|----------|----------|
| **Revenue Model** | Commission on bookings (e.g., 2-5% of transaction) |
| **Integrations** | QuickBooks, Xero, Zapier, Slack |
| **AI Features** | Predictive scheduling, automatic route optimization, churn prediction |
| **White-Label** | API access, custom CSS/JS, custom domains |
| **Marketplace** | Third-party app marketplace for custom integrations |

---

## 6. Technical Architecture

### Stack

| Layer | Technology |
|-------|------------|
| Frontend | HTML + Tailwind CSS + Vanilla JavaScript |
| Backend | Python Flask |
| Database | PostgreSQL (Railway) |
| Authentication | Clerk |
| Hosting | Railway (Auto-deploy from GitHub) |
| File Storage | AWS S3 (for job photos) |
| Email | SendGrid or Mailgun |
| CDN | Cloudflare (future) |

### Database Schema (Key Tables)

**businesses**
- id (UUID, PK)
- name, slug, phone, email
- plan (free/starter/pro)
- active (boolean)
- clerk_org_id (for multi-org support)
- created_at

**users**
- id (UUID, PK)
- business_id (FK)
- clerk_user_id (unique)
- email, name, role
- created_at

**crews**
- id (UUID, PK)
- business_id (FK)
- name, email, phone, color
- active

**jobs**
- id (UUID, PK)
- business_id (FK)
- crew_id (FK, nullable)
- title, description
- customer_name, customer_email, customer_phone, address
- scheduled_date, status
- created_at

**waitlist**
- id (UUID, PK)
- email, name, company_name
- service_type (interested features)
- signup_date
- converted (boolean)

### API Endpoints (MVP)

```
# Authentication
POST /api/clerk-verify

# Crews
GET    /api/crews
POST   /api/crews
PUT    /api/crews/{id}
DELETE /api/crews/{id}

# Jobs
GET    /api/jobs
POST   /api/jobs
PUT    /api/jobs/{id}
POST   /api/jobs/{id}/status

# Clients (Public)
POST   /api/public/book
GET    /api/public/availability

# Waitlist
POST   /api/waitlist/join
```

---

## 7. Pricing Strategy

### Tier Structure

| Plan | Price | Crews | Jobs/Month | Features |
|------|-------|-------|------------|----------|
| **Free** | $0 | 1 | 10 | Basic scheduling, Client booking, Email notifications |
| **Starter** | $49/mo | 5 | Unlimited | All Free features + Calendar view, Crew assignment, Priority support |
| **Pro** | $99/mo | Unlimited | Unlimited | All Starter features + Route optimization, Advanced reporting, White-label options |
| **Enterprise** | Custom | Unlimited | Unlimited | Dedicated support, Custom integrations, SLA guarantees |

### Beta Pricing (Launch - December 2026)

- **50% off** all paid tiers for first 6 months
- Free tier remains free permanently
- Beta users locked into discounted rates

### Future Revenue Streams

1. **Commission Model (v3)**
   - 2-5% commission on bookings made through FieldPulse
   - Revenue share with payment processing

2. **White-Label Setup Fee**
   - $500-2,000 one-time fee for full white-label customization

3. **Integration Marketplace**
   - Third-party developers pay listing fee
   - Revenue share on premium integrations

---

## 8. Compliance & Security

### Regulatory Requirements

| Regulation | Status | Action Required |
|------------|--------|-----------------|
| CCPA/CPRA (California) | Required | Privacy policy, data deletion process |
| GDPR (EU Customers) | Required | If any EU customers, data processing agreement |
| PCI DSS | Future | Required before accepting credit cards |
| State Privacy Laws | Monitor | Texas TDPSA, etc. |

### Security Measures

- **End-to-End Encryption:** TLS 1.2+ in transit, AES-256 at rest
- **Authentication:** Clerk handles security (SOC 2 compliant)
- **Role-Based Access:** Managers see all, crews see assigned only
- **Audit Logging:** Track all data access and changes
- **Data Retention:** 7-year default, configurable by business
- **Backups:** Daily automated backups (3-2-1 rule)

### Privacy Checklist

- [ ] Privacy Policy page
- [ ] Terms of Service
- [ ] Data Processing Agreement (for B2B)
- [ ] Cookie consent banner
- [ ] "Right to be forgotten" process
- [ ] Data export functionality

---

## 9. Go-to-Market Strategy

### Target Market (Beta)

**Geographic:** East Texas (initially)  
**Industries:** Landscaping, HVAC, Plumbing, General Contracting  
**Business Size:** 5-30 employees, $500K-$5M revenue

### Acquisition Strategy

1. **Waitlist (Pre-launch)**
   - Landing page with Clerk waitlist
   - Target: 500 signups by September
   - Channels: Facebook Groups, Reddit r/smallbusiness, Local SEO

2. **Beta Program**
   - 10 businesses free for 3 months
   - Requirement: Provide weekly feedback
   - Goal: Case studies and testimonials

3. **Launch**
   - Product Hunt launch
   - Local business association partnerships
   - Google Ads (targeted by industry + location)

### Marketing Channels

| Channel | Strategy | Budget |
|---------|----------|--------|
| SEO | "Best field service software for [industry]" | $1,000/mo |
| Content Marketing | Blog: Crew management tips, industry guides | $500/mo |
| Facebook/Instagram | Targeted ads to business owners | $2,000/mo |
| Partnerships | Local business associations, supplier networks | Time |
| Referrals | $100 credit for successful referrals | Variable |

---

## 10. Timeline & Milestones

### Phase 1: MVP (June 23 - September 23, 2026)

| Week | Milestone | Deliverables |
|------|-----------|--------------|
| Week 1 (Now) | Waitlist Page | Landing page with Clerk waitlist |
| Week 2-4 | Polish Crews | Finalize crew management, testing |
| Week 5-8 | Calendar View | Interactive scheduling calendar |
| Week 9-11 | Client Frontend | Public booking portal |
| Week 12 | Beta Launch | 10 businesses onboarded |

### Phase 2: v2 (October - December 2026)

- Payment processing integration
- Mobile app improvements
- SMS notifications
- Route optimization

### Phase 3: v3 (Q1 2027)

- Public launch
- Commission model
- Advanced integrations
- Scale to 100+ paying customers

---

## 11. Success Metrics

### Beta Phase (By December 2026)

| Metric | Target |
|--------|--------|
| Waitlist Signups | 500 |
| Beta Businesses | 10 active users |
| Job Bookings | 500 total |
| App Uptime | 99.5% |
| NPS Score | 50+ |

### Launch Phase (By June 2027)

| Metric | Target |
|--------|--------|
| Paying Customers | 100 |
| Monthly Recurring Revenue | $10,000 |
| Customer Churn | <5%/month |
| Feature Adoption | 70% use 3+ core features |

### Long-term (By December 2027)

| Metric | Target |
|--------|--------|
| Total Customers | 500 |
| ARR | $100,000 |
| Team Size | 2-3 employees |
| Geographic Expansion | Texas + 2 adjacent states |

---

## 12. Risks & Mitigation

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Competitor price drop | Medium | High | Focus on white-label differentiation |
| Technical delays | High | Medium | Weekly sprints, cut scope if needed |
| Low beta engagement | Medium | High | Weekly check-ins, incentivize feedback |
| Compliance issues | Low | High | Legal review before launch, conservative approach |
| Market too small | Low | Medium | East Texas test first, expand if successful |

---

## 13. Open Questions

1. Should we build a native mobile app or stick with PWA for v1?
2. Do we need insurance verification features for crews?
3. What happens to data if a business cancels (export only or keep for X months)?
4. Should we integrate with specific industry tools (e.g., landscaping design software)?

---

## 14. Appendices

### Appendix A: Competitive Feature Matrix

| Feature | FieldPulse | Jobber | Housecall Pro | ServiceTitan |
|---------|------------|--------|---------------|--------------|
| Free Tier | ✅ | ❌ | ❌ | ❌ |
| Unlimited Users | ✅ | ❌ | ✅ | ✅ |
| White-Label | ✅ | ❌ | ❌ | ❌ |
| Manager-Only Pricing | ✅ | ❌ | ❌ | ❌ |
| Commission Model | ✅ (v3) | ❌ | ❌ | ❌ |
| Route Optimization | P1 | ✅ | ❌ | ✅ |
| Client Portal | ✅ | ✅ | ✅ | ✅ |
| API Access | P2 | ✅ | ✅ | ✅ |
| Setup Time | Hours | Hours | Days | Months |
| Custom Pricing | $49-99/mo | $39-349/mo | $59-299/mo | $245+/mo |

### Appendix B: User Interview Questions

**For Business Owners:**
1. How do you currently schedule crews and track jobs?
2. What software do you pay for now? What do you like/dislike?
3. How do clients currently book with you?
4. What's your biggest operational headache?
5. Would you pay $49-99/mo to solve it?

**For Crew Managers:**
1. How do you get your daily schedule?
2. How do you update the office on job status?
3. What app features would make your job easier?
4. Do you have reliable mobile data service?

---

## Document Control

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.1 | 2026-06-23 | Joel Escoto | Initial draft |
| 1.0 | TBD | TBD | Beta launch version |

---

**Next Steps:**
1. Review and finalize PRD
2. Create detailed wireframes/mockups
3. Set up project management (Notion/Linear)
4. Begin Week 1: Waitlist Page development

---

*This document is a living document and will be updated as the product evolves.*
