#!/usr/bin/env python3
"""
migrate_to_postgres.py - Migrate from SQLite to PostgreSQL for FieldPulse

Usage:
    python migrations/migrate_to_postgres.py --create-schema
    python migrations/migrate_to_postgres.py --migrate-data
    python migrations/migrate_to_postgres.py --all
"""

import os
import sys
import argparse
import sqlite3
import logging
from pathlib import Path
from datetime import datetime

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    print("Installing psycopg2-binary...")
    os.system("pip install psycopg2-binary")
    import psycopg2
    from psycopg2.extras import RealDictCursor

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Database paths
SQLITE_DB_PATH = Path(__file__).parent.parent / "data" / "leads.db"

# PostgreSQL connection (from environment or .env file)
def get_pg_connection():
    """Get PostgreSQL connection from environment variables."""
    db_url = os.environ.get('DATABASE_URL')

    if not db_url:
        # Try to load from .env file
        env_path = Path(__file__).parent.parent / ".env"
        if env_path.exists():
            with open(env_path) as f:
                for line in f:
                    if line.startswith('DATABASE_URL='):
                        db_url = line.strip().split('=', 1)[1].strip('"\'')
                        break

    if not db_url:
        raise ValueError("DATABASE_URL not found in environment or .env file")

    # Parse Railway connection string if needed
    # Railway uses: postgresql://user:pass@host:port/db
    return psycopg2.connect(db_url)


def create_schema():
    """Create PostgreSQL schema from SQL file."""
    logger.info("Creating PostgreSQL schema...")

    schema_path = Path(__file__).parent / "pg_schema.sql"

    if not schema_path.exists():
        logger.error(f"Schema file not found: {schema_path}")
        return False

    conn = get_pg_connection()
    cursor = conn.cursor()

    try:
        with open(schema_path, 'r') as f:
            sql = f.read()
            cursor.execute(sql)

        conn.commit()
        logger.info("Schema created successfully!")
        return True

    except Exception as e:
        logger.error(f"Error creating schema: {e}")
        conn.rollback()
        return False

    finally:
        cursor.close()
        conn.close()


def migrate_table(pg_cursor, sqlite_cursor, table_name, column_mapping=None):
    """Migrate a single table from SQLite to PostgreSQL."""
    logger.info(f"Migrating table: {table_name}")

    # Get SQLite data
    sqlite_cursor.execute(f"SELECT * FROM {table_name}")
    rows = sqlite_cursor.fetchall()
    columns = [description[0] for description in sqlite_cursor.description]

    if not rows:
        logger.info(f"  No data in {table_name}")
        return 0

    # Apply column mapping if provided
    if column_mapping:
        columns = [column_mapping.get(col, col) for col in columns]

    # Build INSERT statement
    placeholders = ', '.join(['%s'] * len(columns))
    columns_str = ', '.join(columns)
    insert_sql = f"INSERT INTO {table_name} ({columns_str}) VALUES ({placeholders}) ON CONFLICT DO NOTHING"

    count = 0
    for row in sqlite_cursor.fetchall():
        try:
            # Convert row to list and handle None values
            values = [None if val == '' or val is None else val for val in row]
            pg_cursor.execute(insert_sql, values)
            count += 1
        except Exception as e:
            logger.warning(f"  Skipping row in {table_name}: {e}")

    return count


def migrate_data():
    """Migrate data from SQLite to PostgreSQL."""
    logger.info("Starting data migration...")

    if not SQLITE_DB_PATH.exists():
        logger.error(f"SQLite database not found: {SQLITE_DB_PATH}")
        return False

    # Connect to both databases
    sqlite_conn = sqlite3.connect(SQLITE_DB_PATH)
    sqlite_conn.row_factory = sqlite3.Row
    sqlite_cursor = sqlite_conn.cursor()

    pg_conn = get_pg_connection()
    pg_cursor = pg_conn.cursor()

    migration_count = {}

    try:
        # Migrate leads (core table)
        logger.info("Migrating leads...")
        sqlite_cursor.execute("SELECT * FROM leads")
        leads = sqlite_cursor.fetchall()

        for lead in leads:
            try:
                pg_cursor.execute("""
                    INSERT INTO leads (
                        business_name, category, city, address, phone, website,
                        google_maps_url, rating, review_count, has_website, site_status,
                        site_last_updated, owner_name, owner_email, email_source,
                        lead_score, outreach_status, email_sent_at, sms_sent_at,
                        last_reply_at, reply_intent, call_status, call_sid,
                        call_transcript, call_summary, call_duration, call_attempts,
                        last_call_at, pipeline_type, parent_company, franchise_brand,
                        locations_count, employee_count, estimated_revenue, tech_stack,
                        growth_signals, decision_makers, lead_source, scraped_at,
                        updated_at, notes
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                """, (
                    lead['business_name'], lead['category'], lead['city'],
                    lead['address'], lead['phone'], lead['website'],
                    lead['google_maps_url'], lead['rating'], lead['review_count'],
                    lead['has_website'], lead['site_status'], lead['site_last_updated'],
                    lead['owner_name'], lead['owner_email'], lead['email_source'],
                    lead['lead_score'], lead['outreach_status'], lead['email_sent_at'],
                    lead['sms_sent_at'], lead['last_reply_at'], lead['reply_intent'],
                    lead['call_status'], lead['call_sid'], lead['call_transcript'],
                    lead['call_summary'], lead['call_duration'], lead['call_attempts'],
                    lead['last_call_at'], lead['pipeline_type'], lead['parent_company'],
                    lead['franchise_brand'], lead['locations_count'], lead['employee_count'],
                    lead['estimated_revenue'], lead['tech_stack'], lead['growth_signals'],
                    lead['decision_makers'], lead['lead_source'], lead['scraped_at'],
                    lead['updated_at'], lead['notes']
                ))
            except Exception as e:
                logger.warning(f"  Skipping lead: {e}")

        migration_count['leads'] = len(leads)
        logger.info(f"  Migrated {len(leads)} leads")

        # Migrate business_users -> users (create corresponding businesses first)
        logger.info("Migrating business_users...")
        sqlite_cursor.execute("SELECT * FROM business_users")
        users = sqlite_cursor.fetchall()

        for user in users:
            try:
                # Create a business record for each user (if they have business_id)
                if user['business_id']:
                    pg_cursor.execute("""
                        INSERT INTO businesses (id, name, created_at)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (id) DO NOTHING
                    """, (user['business_id'], user['owner_name'] or 'Business', user['created_at']))

                # Insert user
                pg_cursor.execute("""
                    INSERT INTO users (
                        email, password_hash, business_id, name, email_verified,
                        verification_token, two_fa_enabled, two_fa_secret,
                        two_fa_backup_codes, stripe_customer_id, subscription_id,
                        subscription_status, created_at, updated_at, last_login_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                """, (
                    user['email'], user['password_hash'], user['business_id'],
                    user['owner_name'], user['email_verified'], user['verification_token'],
                    user['two_fa_enabled'], user['two_fa_secret'], user['two_fa_backup_codes'],
                    user['stripe_customer_id'], user['subscription_id'], user['subscription_status'],
                    user['created_at'], user['updated_at'], user['last_login_at']
                ))
            except Exception as e:
                logger.warning(f"  Skipping user {user['email']}: {e}")

        migration_count['users'] = len(users)
        logger.info(f"  Migrated {len(users)} users")

        # Migrate loyalty_businesses -> businesses
        logger.info("Migrating loyalty_businesses...")
        sqlite_cursor.execute("SELECT * FROM loyalty_businesses")
        businesses = sqlite_cursor.fetchall()

        for biz in businesses:
            try:
                pg_cursor.execute("""
                    INSERT INTO businesses (
                        id, lead_id, name, type, description, address, city,
                        phone, website, logo_url, punches_needed, discount_percent,
                        active, created_at, updated_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    ON CONFLICT (id) DO UPDATE SET
                        name = EXCLUDED.name,
                        type = EXCLUDED.type,
                        description = EXCLUDED.description
                """, (
                    biz['id'], biz['lead_id'], biz['name'], biz['type'],
                    biz['description'], biz['address'], biz['city'],
                    biz['phone'], biz['website'], biz['logo_url'],
                    biz['punches_needed'], biz['discount_percent'],
                    biz['active'], biz['created_at'], biz['updated_at']
                ))
            except Exception as e:
                logger.warning(f"  Skipping business {biz['name']}: {e}")

        migration_count['businesses'] = len(businesses)
        logger.info(f"  Migrated {len(businesses)} businesses")

        # Migrate loyalty_customers
        logger.info("Migrating loyalty_customers...")
        sqlite_cursor.execute("SELECT * FROM loyalty_customers")
        customers = sqlite_cursor.fetchall()

        for cust in customers:
            try:
                pg_cursor.execute("""
                    INSERT INTO loyalty_customers (
                        id, name, email, phone, password_hash, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO NOTHING
                """, (
                    cust['id'], cust['name'], cust['email'],
                    cust['phone'], cust['password_hash'],
                    cust['created_at'], cust['updated_at']
                ))
            except Exception as e:
                logger.warning(f"  Skipping customer: {e}")

        migration_count['loyalty_customers'] = len(customers)
        logger.info(f"  Migrated {len(customers)} customers")

        # Migrate loyalty_cards
        logger.info("Migrating loyalty_cards...")
        sqlite_cursor.execute("SELECT * FROM loyalty_cards")
        cards = sqlite_cursor.fetchall()

        for card in cards:
            try:
                pg_cursor.execute("""
                    INSERT INTO loyalty_cards (
                        id, customer_id, business_id, punches, rewards_earned,
                        last_punch_at, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (customer_id, business_id) DO NOTHING
                """, (
                    card['id'], card['customer_id'], card['business_id'],
                    card['punches'], card['rewards_earned'],
                    card['last_punch_at'], card['created_at'], card['updated_at']
                ))
            except Exception as e:
                logger.warning(f"  Skipping card: {e}")

        migration_count['loyalty_cards'] = len(cards)
        logger.info(f"  Migrated {len(cards)} cards")

        # Migrate bookings
        logger.info("Migrating bookings...")
        sqlite_cursor.execute("SELECT * FROM bookings")
        bookings = sqlite_cursor.fetchall()

        for booking in bookings:
            try:
                pg_cursor.execute("""
                    INSERT INTO bookings (
                        id, business_id, customer_id, staff_id, service_id,
                        recurring_id, booking_date, booking_time, duration_min,
                        end_time, customer_name, customer_phone, customer_email,
                        status, notes, internal_notes, created_at, confirmed_at,
                        completed_at, cancelled_at, source, reminder_sent, loyalty_punch_added
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO NOTHING
                """, (
                    booking['id'], booking['business_id'], booking['customer_id'],
                    booking['staff_id'], booking['service_id'], booking['recurring_id'],
                    booking['booking_date'], booking['booking_time'], booking['duration_min'],
                    booking['end_time'], booking['customer_name'], booking['customer_phone'],
                    booking['customer_email'], booking['status'], booking['notes'],
                    booking['internal_notes'], booking['created_at'], booking['confirmed_at'],
                    booking['completed_at'], booking['cancelled_at'], booking['source'],
                    booking['reminder_sent'], booking['loyalty_punch_added']
                ))
            except Exception as e:
                logger.warning(f"  Skipping booking: {e}")

        migration_count['bookings'] = len(bookings)
        logger.info(f"  Migrated {len(bookings)} bookings")

        # Migrate booking_services
        logger.info("Migrating booking_services...")
        sqlite_cursor.execute("SELECT * FROM booking_services")
        services = sqlite_cursor.fetchall()

        for svc in services:
            try:
                pg_cursor.execute("""
                    INSERT INTO booking_services (
                        id, business_id, name, description, duration_min, price, active, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO NOTHING
                """, (
                    svc['id'], svc['business_id'], svc['name'], svc['description'],
                    svc['duration_min'], svc['price'], svc['active'],
                    svc['created_at'], svc['updated_at']
                ))
            except Exception as e:
                logger.warning(f"  Skipping service: {e}")

        migration_count['booking_services'] = len(services)
        logger.info(f"  Migrated {len(services)} services")

        # Migrate booking_staff
        logger.info("Migrating booking_staff...")
        sqlite_cursor.execute("SELECT * FROM booking_staff")
        staff = sqlite_cursor.fetchall()

        for s in staff:
            try:
                pg_cursor.execute("""
                    INSERT INTO booking_staff (
                        id, business_id, name, role, email, phone, active, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO NOTHING
                """, (
                    s['id'], s['business_id'], s['name'], s['role'],
                    s['email'], s['phone'], s['active'],
                    s['created_at'], s['updated_at']
                ))
            except Exception as e:
                logger.warning(f"  Skipping staff: {e}")

        migration_count['booking_staff'] = len(staff)
        logger.info(f"  Migrated {len(staff)} staff")

        # Migrate outreach_log
        logger.info("Migrating outreach_log...")
        sqlite_cursor.execute("SELECT * FROM outreach_log")
        logs = sqlite_cursor.fetchall()

        for log in logs:
            try:
                pg_cursor.execute("""
                    INSERT INTO outreach_log (
                        lead_id, channel, direction, subject, body, transcript,
                        duration, status, external_id, sequence_step, sent_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    log['lead_id'], log['channel'], log['direction'],
                    log['subject'], log['body'], log['transcript'],
                    log['duration'], log['status'], log['external_id'],
                    log['sequence_step'], log['sent_at']
                ))
            except Exception as e:
                logger.warning(f"  Skipping log: {e}")

        migration_count['outreach_log'] = len(logs)
        logger.info(f"  Migrated {len(logs)} log entries")

        # Commit all changes
        pg_conn.commit()

        # Print summary
        logger.info("\n" + "="*50)
        logger.info("MIGRATION SUMMARY")
        logger.info("="*50)
        for table, count in migration_count.items():
            logger.info(f"  {table}: {count} records")

        return True

    except Exception as e:
        logger.error(f"Migration failed: {e}")
        pg_conn.rollback()
        return False

    finally:
        sqlite_cursor.close()
        sqlite_conn.close()
        pg_cursor.close()
        pg_conn.close()


def main():
    parser = argparse.ArgumentParser(description='Migrate SQLite to PostgreSQL for FieldPulse')
    parser.add_argument('--create-schema', action='store_true', help='Create PostgreSQL schema')
    parser.add_argument('--migrate-data', action='store_true', help='Migrate data from SQLite')
    parser.add_argument('--all', action='store_true', help='Create schema and migrate data')

    args = parser.parse_args()

    if args.all:
        args.create_schema = True
        args.migrate_data = True

    if not args.create_schema and not args.migrate_data:
        parser.print_help()
        return

    if args.create_schema:
        if not create_schema():
            sys.exit(1)

    if args.migrate_data:
        if not migrate_data():
            sys.exit(1)

    logger.info("\nMigration completed successfully!")


if __name__ == '__main__':
    main()