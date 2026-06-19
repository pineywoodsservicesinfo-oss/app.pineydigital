"""
bookings_db.py — Online Booking Database Module
Piney Digital Outreach System — Booking Management

Manages services, staff, availability, and appointments.
"""

import sqlite3
import logging
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "data" / "leads.db"


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_booking_tables():
    """Create booking management tables."""
    conn = get_connection()
    c = conn.cursor()

    c.executescript("""
        -- Services offered by businesses
        CREATE TABLE IF NOT EXISTS booking_services (
            id              TEXT PRIMARY KEY,
            business_id     TEXT REFERENCES loyalty_businesses(id),
            name            TEXT NOT NULL,
            description     TEXT,
            duration_min    INTEGER DEFAULT 30,
            price           REAL DEFAULT 0,
            active          INTEGER DEFAULT 1,
            created_at      TEXT DEFAULT (datetime('now')),
            updated_at      TEXT DEFAULT (datetime('now'))
        );

        -- Staff members
        CREATE TABLE IF NOT EXISTS booking_staff (
            id              TEXT PRIMARY KEY,
            business_id     TEXT REFERENCES loyalty_businesses(id),
            name            TEXT NOT NULL,
            role            TEXT,
            email           TEXT,
            phone           TEXT,
            active          INTEGER DEFAULT 1,
            created_at      TEXT DEFAULT (datetime('now')),
            updated_at      TEXT DEFAULT (datetime('now'))
        );

        -- Staff availability (working hours by day of week)
        CREATE TABLE IF NOT EXISTS staff_availability (
            id              TEXT PRIMARY KEY,
            staff_id        TEXT REFERENCES booking_staff(id),
            day_of_week     INTEGER NOT NULL,     -- 0=Monday, 6=Sunday
            start_time      TEXT NOT NULL,        -- HH:MM format
            end_time        TEXT NOT NULL,        -- HH:MM format
            is_working      INTEGER DEFAULT 1,
            created_at      TEXT DEFAULT (datetime('now'))
        );

        -- Specific date overrides (holidays, time off)
        CREATE TABLE IF NOT EXISTS staff_time_off (
            id              TEXT PRIMARY KEY,
            staff_id        TEXT REFERENCES booking_staff(id),
            date            TEXT NOT NULL,        -- YYYY-MM-DD
            reason          TEXT,
            is_all_day      INTEGER DEFAULT 1,
            start_time      TEXT,                 -- If not all day
            end_time        TEXT,
            created_at      TEXT DEFAULT (datetime('now'))
        );

        -- Recurring appointment templates
        CREATE TABLE IF NOT EXISTS recurring_bookings (
            id              TEXT PRIMARY KEY,
            business_id     TEXT REFERENCES loyalty_businesses(id),
            customer_id     TEXT REFERENCES loyalty_customers(id),
            staff_id        TEXT REFERENCES booking_staff(id),
            service_id      TEXT REFERENCES booking_services(id),
            
            -- Recurrence pattern
            recurrence_type TEXT NOT NULL,        -- weekly/biweekly/monthly
            day_of_week     INTEGER,              -- 0-6 (for weekly)
            day_of_month    INTEGER,              -- 1-31 (for monthly)
            interval_weeks  INTEGER DEFAULT 1,    -- For biweekly = 2
            
            -- Time slot
            booking_time    TEXT NOT NULL,
            duration_min    INTEGER DEFAULT 30,
            
            -- Date range
            start_date      TEXT NOT NULL,        -- First occurrence
            end_date        TEXT,                 -- Last occurrence (optional)
            max_occurrences INTEGER,              -- Max number of bookings
            
            -- Customer info
            customer_name   TEXT NOT NULL,
            customer_phone  TEXT,
            customer_email  TEXT,
            notes         TEXT,
            
            -- Status
            active          INTEGER DEFAULT 1,
            created_at      TEXT DEFAULT (datetime('now')),
            updated_at      TEXT DEFAULT (datetime('now'))
        );
        
        -- Bookings/Appointments
        CREATE TABLE IF NOT EXISTS bookings (
            id              TEXT PRIMARY KEY,
            business_id     TEXT REFERENCES loyalty_businesses(id),
            customer_id     TEXT REFERENCES loyalty_customers(id),
            staff_id        TEXT REFERENCES booking_staff(id),
            service_id      TEXT REFERENCES booking_services(id),
            recurring_id    TEXT REFERENCES recurring_bookings(id),  -- Link to recurring template
            
            -- Booking details
            booking_date    TEXT NOT NULL,        -- YYYY-MM-DD
            booking_time    TEXT NOT NULL,        -- HH:MM
            duration_min    INTEGER DEFAULT 30,
            end_time        TEXT,                 -- Calculated
            
            -- Customer info (snapshot at booking time)
            customer_name   TEXT NOT NULL,
            customer_phone  TEXT,
            customer_email  TEXT,
            
            -- Status & metadata
            status          TEXT DEFAULT 'pending',  -- pending/confirmed/completed/cancelled/no_show
            notes         TEXT,
            internal_notes  TEXT,
            
            -- Timestamps
            created_at      TEXT DEFAULT (datetime('now')),
            confirmed_at    TEXT,
            completed_at    TEXT,
            cancelled_at    TEXT,
            
            -- Source tracking
            source          TEXT DEFAULT 'web',     -- web/phone/walk_in
            reminder_sent   INTEGER DEFAULT 0,
            loyalty_punch_added INTEGER DEFAULT 0
        );

        -- Booking notifications log
        CREATE TABLE IF NOT EXISTS booking_notifications (
            id              TEXT PRIMARY KEY,
            booking_id      TEXT REFERENCES bookings(id),
            type            TEXT,                 -- confirmation/reminder/cancellation
            channel         TEXT DEFAULT 'sms',
            sent_at         TEXT DEFAULT (datetime('now')),
            status          TEXT DEFAULT 'sent',
            message_sid     TEXT
        );

        -- Indexes for performance
        CREATE INDEX IF NOT EXISTS idx_services_business ON booking_services(business_id);
        CREATE INDEX IF NOT EXISTS idx_staff_business ON booking_staff(business_id);
        CREATE INDEX IF NOT EXISTS idx_availability_staff ON staff_availability(staff_id);
        CREATE INDEX IF NOT EXISTS idx_bookings_business ON bookings(business_id);
        CREATE INDEX IF NOT EXISTS idx_bookings_date ON bookings(booking_date);
        CREATE INDEX IF NOT EXISTS idx_bookings_status ON bookings(status);
        CREATE INDEX IF NOT EXISTS idx_bookings_customer ON bookings(customer_id);
    """)

    conn.commit()
    conn.close()
    logger.info("Booking tables initialised")


def generate_id(prefix: str) -> str:
    """Generate a unique ID with prefix."""
    import uuid
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# ── Service Operations ─────────────────────────────────────

def create_service(business_id: str, name: str, duration_min: int = 30,
                   price: float = 0, description: str = None) -> str:
    """Create a new service."""
    conn = get_connection()
    c = conn.cursor()
    
    service_id = generate_id("svc")
    c.execute("""
        INSERT INTO booking_services (id, business_id, name, description, duration_min, price)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (service_id, business_id, name, description, duration_min, price))
    
    conn.commit()
    conn.close()
    return service_id


def get_business_services(business_id: str, active_only: bool = True) -> list:
    """Get all services for a business."""
    conn = get_connection()
    c = conn.cursor()
    
    query = "SELECT * FROM booking_services WHERE business_id = ?"
    if active_only:
        query += " AND active = 1"
    query += " ORDER BY name"
    
    c.execute(query, (business_id,))
    rows = [dict(row) for row in c.fetchall()]
    conn.close()
    return rows


def update_service(service_id: str, fields: dict):
    """Update service details."""
    fields["updated_at"] = datetime.now().isoformat()
    fields["id"] = service_id
    
    set_clause = ", ".join(f"{k} = :{k}" for k in fields if k != "id")
    conn = get_connection()
    conn.execute(f"UPDATE booking_services SET {set_clause} WHERE id = :id", fields)
    conn.commit()
    conn.close()


def delete_service(service_id: str):
    """Soft delete a service."""
    conn = get_connection()
    conn.execute("UPDATE booking_services SET active = 0, updated_at = ? WHERE id = ?",
                 (datetime.now().isoformat(), service_id))
    conn.commit()
    conn.close()


# ── Staff Operations ─────────────────────────────────────

def create_staff(business_id: str, name: str, role: str = None,
                 email: str = None, phone: str = None) -> str:
    """Create a new staff member."""
    conn = get_connection()
    c = conn.cursor()
    
    staff_id = generate_id("stf")
    c.execute("""
        INSERT INTO booking_staff (id, business_id, name, role, email, phone)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (staff_id, business_id, name, role, email, phone))
    
    conn.commit()
    conn.close()
    return staff_id


def get_business_staff(business_id: str, active_only: bool = True) -> list:
    """Get all staff for a business."""
    conn = get_connection()
    c = conn.cursor()
    
    query = "SELECT * FROM booking_staff WHERE business_id = ?"
    if active_only:
        query += " AND active = 1"
    query += " ORDER BY name"
    
    c.execute(query, (business_id,))
    rows = [dict(row) for row in c.fetchall()]
    conn.close()
    return rows


def set_staff_availability(staff_id: str, day_of_week: int, start_time: str,
                           end_time: str, is_working: bool = True):
    """Set staff availability for a day of week."""
    conn = get_connection()
    c = conn.cursor()
    
    # Check if exists
    c.execute("""
        SELECT id FROM staff_availability 
        WHERE staff_id = ? AND day_of_week = ?
    """, (staff_id, day_of_week))
    row = c.fetchone()
    
    if row:
        c.execute("""
            UPDATE staff_availability 
            SET start_time = ?, end_time = ?, is_working = ?
            WHERE staff_id = ? AND day_of_week = ?
        """, (start_time, end_time, is_working, staff_id, day_of_week))
    else:
        avail_id = generate_id("avl")
        c.execute("""
            INSERT INTO staff_availability (id, staff_id, day_of_week, start_time, end_time, is_working)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (avail_id, staff_id, day_of_week, start_time, end_time, is_working))
    
    conn.commit()
    conn.close()


def get_staff_availability(staff_id: str) -> dict:
    """Get staff availability by day of week."""
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        SELECT day_of_week, start_time, end_time, is_working
        FROM staff_availability WHERE staff_id = ?
        ORDER BY day_of_week
    """, (staff_id,))
    
    availability = {}
    for row in c.fetchall():
        availability[row["day_of_week"]] = {
            "start": row["start_time"],
            "end": row["end_time"],
            "working": row["is_working"]
        }
    
    conn.close()
    return availability


def add_staff_time_off(staff_id: str, date: str, reason: str = None,
                       is_all_day: bool = True, start_time: str = None,
                       end_time: str = None) -> str:
    """Add time off for staff member."""
    conn = get_connection()
    c = conn.cursor()
    
    time_off_id = generate_id("tof")
    c.execute("""
        INSERT INTO staff_time_off (id, staff_id, date, reason, is_all_day, start_time, end_time)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (time_off_id, staff_id, date, reason, is_all_day, start_time, end_time))
    
    conn.commit()
    conn.close()
    return time_off_id


# ── Booking Operations ─────────────────────────────────────

def create_booking(business_id: str, customer_name: str, booking_date: str,
                   booking_time: str, service_id: str, staff_id: str = None,
                   customer_id: str = None, customer_phone: str = None,
                   customer_email: str = None, notes: str = None,
                   source: str = "web") -> str:
    """Create a new booking."""
    conn = get_connection()
    c = conn.cursor()
    
    # Get service duration
    c.execute("SELECT duration_min, name FROM booking_services WHERE id = ?", (service_id,))
    service = c.fetchone()
    
    if not service:
        conn.close()
        return None
    
    duration = service["duration_min"]
    
    # Calculate end time
    start_dt = datetime.strptime(f"{booking_date} {booking_time}", "%Y-%m-%d %H:%M")
    end_dt = start_dt + timedelta(minutes=duration)
    end_time = end_dt.strftime("%H:%M")
    
    booking_id = generate_id("bok")
    c.execute("""
        INSERT INTO bookings 
        (id, business_id, customer_id, staff_id, service_id,
         booking_date, booking_time, duration_min, end_time,
         customer_name, customer_phone, customer_email, notes, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (booking_id, business_id, customer_id, staff_id, service_id,
          booking_date, booking_time, duration, end_time,
          customer_name, customer_phone, customer_email, notes, source))
    
    conn.commit()
    conn.close()
    return booking_id


def confirm_booking(booking_id: str):
    """Confirm a pending booking."""
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        UPDATE bookings 
        SET status = 'confirmed', confirmed_at = ?
        WHERE id = ?
    """, (datetime.now().isoformat(), booking_id))
    conn.commit()
    conn.close()


def cancel_booking(booking_id: str, reason: str = None):
    """Cancel a booking."""
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        UPDATE bookings 
        SET status = 'cancelled', cancelled_at = ?, internal_notes = ?
        WHERE id = ?
    """, (datetime.now().isoformat(), reason, booking_id))
    conn.commit()
    conn.close()


def complete_booking(booking_id: str):
    """Mark booking as completed."""
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        UPDATE bookings 
        SET status = 'completed', completed_at = ?
        WHERE id = ?
    """, (datetime.now().isoformat(), booking_id))
    conn.commit()
    conn.close()


def get_booking(booking_id: str) -> dict:
    """Get single booking details."""
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        SELECT b.*, s.name as service_name, s.price, s.duration_min as service_duration,
               stf.name as staff_name
        FROM bookings b
        LEFT JOIN booking_services s ON b.service_id = s.id
        LEFT JOIN booking_staff stf ON b.staff_id = stf.id
        WHERE b.id = ?
    """, (booking_id,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


def get_business_bookings(business_id: str, start_date: str = None,
                          end_date: str = None, status: str = None) -> list:
    """Get bookings for a business."""
    conn = get_connection()
    c = conn.cursor()
    
    query = """
        SELECT b.*, s.name as service_name, s.price,
               stf.name as staff_name,
               c.name as customer_full_name
        FROM bookings b
        LEFT JOIN booking_services s ON b.service_id = s.id
        LEFT JOIN booking_staff stf ON b.staff_id = stf.id
        LEFT JOIN loyalty_customers c ON b.customer_id = c.id
        WHERE b.business_id = ?
    """
    params = [business_id]
    
    if start_date:
        query += " AND b.booking_date >= ?"
        params.append(start_date)
    if end_date:
        query += " AND b.booking_date <= ?"
        params.append(end_date)
    if status:
        query += " AND b.status = ?"
        params.append(status)
    
    query += " ORDER BY b.booking_date, b.booking_time"
    
    c.execute(query, params)
    rows = [dict(row) for row in c.fetchall()]
    conn.close()
    return rows


def get_staff_bookings(staff_id: str, date: str = None) -> list:
    """Get bookings for a specific staff member."""
    conn = get_connection()
    c = conn.cursor()
    
    query = """
        SELECT b.*, s.name as service_name
        FROM bookings b
        LEFT JOIN booking_services s ON b.service_id = s.id
        WHERE b.staff_id = ? AND b.status != 'cancelled'
    """
    params = [staff_id]
    
    if date:
        query += " AND b.booking_date = ?"
        params.append(date)
    
    query += " ORDER BY b.booking_time"
    
    c.execute(query, params)
    rows = [dict(row) for row in c.fetchall()]
    conn.close()
    return rows


# ── Availability Checking ─────────────────────────────────────

def get_available_slots(business_id: str, staff_id: str, service_id: str,
                        date: str) -> list:
    """Get available time slots for a given date."""
    conn = get_connection()
    c = conn.cursor()
    
    # Get service duration
    c.execute("SELECT duration_min FROM booking_services WHERE id = ?", (service_id,))
    service = c.fetchone()
    if not service:
        conn.close()
        return []
    
    duration = service["duration_min"]
    
    # Get staff availability for this day of week
    day_of_week = datetime.strptime(date, "%Y-%m-%d").weekday()
    c.execute("""
        SELECT start_time, end_time FROM staff_availability
        WHERE staff_id = ? AND day_of_week = ? AND is_working = 1
    """, (staff_id, day_of_week))
    availability = c.fetchone()
    
    if not availability:
        conn.close()
        return []  # Staff not working this day
    
    # Get existing bookings for this date
    c.execute("""
        SELECT booking_time, end_time FROM bookings
        WHERE staff_id = ? AND booking_date = ? AND status != 'cancelled'
    """, (staff_id, date))
    existing = [dict(row) for row in c.fetchall()]
    conn.close()
    
    # Generate slots
    slots = []
    current_time = datetime.strptime(availability["start_time"], "%H:%M")
    end_time = datetime.strptime(availability["end_time"], "%H:%M")
    
    while current_time + timedelta(minutes=duration) <= end_time:
        slot_time = current_time.strftime("%H:%M")
        slot_end = (current_time + timedelta(minutes=duration)).strftime("%H:%M")
        
        # Check if slot conflicts with existing bookings
        conflict = False
        for booking in existing:
            if not (slot_end <= booking["booking_time"] or slot_time >= booking["end_time"]):
                conflict = True
                break
        
        if not conflict:
            slots.append(slot_time)
        
        # Move to next slot (15-min increments)
        current_time += timedelta(minutes=15)
    
    return slots


def is_slot_available(business_id: str, staff_id: str, date: str,
                      time: str, duration_min: int) -> bool:
    """Check if a specific time slot is available."""
    conn = get_connection()
    c = conn.cursor()
    
    # Calculate end time
    start_dt = datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M")
    end_dt = start_dt + timedelta(minutes=duration_min)
    end_time = end_dt.strftime("%H:%M")
    
    # Check for conflicts
    c.execute("""
        SELECT COUNT(*) as n FROM bookings
        WHERE staff_id = ? AND booking_date = ? AND status != 'cancelled'
        AND NOT (end_time <= ? OR booking_time >= ?)
    """, (staff_id, date, time, end_time))
    
    conflicts = c.fetchone()["n"]
    conn.close()
    
    return conflicts == 0


# ── Recurring Appointments ─────────────────────────────────────

def create_recurring_booking(business_id: str, customer_name: str,
                             customer_id: str = None, staff_id: str = None,
                             service_id: str = None, recurrence_type: str = "weekly",
                             day_of_week: int = None, day_of_month: int = None,
                             interval_weeks: int = 1, booking_time: str = None,
                             duration_min: int = 30, start_date: str = None,
                             end_date: str = None, max_occurrences: int = None,
                             customer_phone: str = None, customer_email: str = None,
                             notes: str = None) -> str:
    """Create a recurring booking template."""
    conn = get_connection()
    c = conn.cursor()
    
    recurring_id = generate_id("rec")
    c.execute("""
        INSERT INTO recurring_bookings 
        (id, business_id, customer_id, staff_id, service_id,
         recurrence_type, day_of_week, day_of_month, interval_weeks,
         booking_time, duration_min, start_date, end_date, max_occurrences,
         customer_name, customer_phone, customer_email, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (recurring_id, business_id, customer_id, staff_id, service_id,
          recurrence_type, day_of_week, day_of_month, interval_weeks,
          booking_time, duration_min, start_date, end_date, max_occurrences,
          customer_name, customer_phone, customer_email, notes))
    
    # Generate first occurrence
    if start_date:
        create_booking(
            business_id=business_id,
            customer_name=customer_name,
            booking_date=start_date,
            booking_time=booking_time,
            service_id=service_id,
            staff_id=staff_id,
            customer_id=customer_id,
            customer_phone=customer_phone,
            customer_email=customer_email,
            notes=notes,
            source="recurring"
        )
    
    conn.commit()
    conn.close()
    return recurring_id


def generate_recurring_occurrences(recurring_id: str, until_date: str = None):
    """Generate individual bookings from a recurring template."""
    conn = get_connection()
    c = conn.cursor()
    
    # Get recurring template
    c.execute("""
        SELECT * FROM recurring_bookings WHERE id = ? AND active = 1
    """, (recurring_id,))
    rec = c.fetchone()
    
    if not rec:
        conn.close()
        return 0
    
    from datetime import timedelta
    
    # Calculate occurrences
    start = datetime.strptime(rec["start_date"], "%Y-%m-%d")
    end = datetime.strptime(until_date, "%Y-%m-%d") if until_date else datetime.now() + timedelta(days=90)
    
    generated = 0
    current = start
    
    while current <= end:
        # Check if we already have this booking
        c.execute("""
            SELECT id FROM bookings 
            WHERE recurring_id = ? AND booking_date = ?
        """, (recurring_id, current.strftime("%Y-%m-%d")))
        
        if not c.fetchone():
            # Create booking
            create_booking(
                business_id=rec["business_id"],
                customer_name=rec["customer_name"],
                booking_date=current.strftime("%Y-%m-%d"),
                booking_time=rec["booking_time"],
                service_id=rec["service_id"],
                staff_id=rec["staff_id"],
                customer_id=rec["customer_id"],
                customer_phone=rec["customer_phone"],
                customer_email=rec["customer_email"],
                notes=rec["notes"],
                source="recurring"
            )
            generated += 1
        
        # Move to next occurrence
        if rec["recurrence_type"] == "weekly":
            current += timedelta(weeks=rec["interval_weeks"] or 1)
        elif rec["recurrence_type"] == "biweekly":
            current += timedelta(weeks=2)
        elif rec["recurrence_type"] == "monthly":
            # Add month
            month = current.month + 1
            year = current.year
            if month > 12:
                month = 1
                year += 1
            try:
                current = current.replace(year=year, month=month)
            except ValueError:
                # Day doesn't exist in next month (e.g., Jan 31 -> Feb 31)
                current = current.replace(year=year, month=month, day=1)
    
    conn.commit()
    conn.close()
    return generated


def get_staff_calendar(staff_id: str, start_date: str, end_date: str) -> list:
    """Get all bookings for a staff member in date range."""
    conn = get_connection()
    c = conn.cursor()
    
    c.execute("""
        SELECT b.*, s.name as service_name, s.price,
               c.name as customer_full_name
        FROM bookings b
        LEFT JOIN booking_services s ON b.service_id = s.id
        LEFT JOIN loyalty_customers c ON b.customer_id = c.id
        WHERE b.staff_id = ? 
        AND b.booking_date BETWEEN ? AND ?
        AND b.status != 'cancelled'
        ORDER BY b.booking_date, b.booking_time
    """, (staff_id, start_date, end_date))
    
    rows = [dict(row) for row in c.fetchall()]
    conn.close()
    return rows
