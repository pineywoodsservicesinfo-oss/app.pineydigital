#!/usr/bin/env python3
"""
test_pg_connection.py - Test PostgreSQL connection for FieldPulse
"""

import os
import sys
from pathlib import Path

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

def test_connection():
    """Test PostgreSQL connection."""
    try:
        import psycopg2
        print("✓ psycopg2 installed")
    except ImportError:
        print("✗ psycopg2 not installed")
        print("  Run: pip install psycopg2-binary")
        return False

    # Get connection URL from Railway
    db_url = os.environ.get('DATABASE_URL')

    if not db_url:
        print("\n✗ DATABASE_URL not set")
        print("\nTo get your DATABASE_URL from Railway:")
        print("  1. Run: railway variables")
        print("  2. Look for DATABASE_URL line")
        print("  3. Set it: export DATABASE_URL='your-url-here'")
        print("\nOr set it in .env file:")
        print("  DATABASE_URL=postgresql://postgres:PASSWORD@HOST:PORT/railway")
        return False

    # Parse and display connection info
    from urllib.parse import urlparse
    parsed = urlparse(db_url)

    print(f"\n PostgreSQL Connection Info:")
    print(f"  Host: {parsed.hostname}")
    print(f"  Port: {parsed.port}")
    print(f"  Database: {parsed.path.lstrip('/')}")
    print(f"  User: {parsed.username}")

    # Try to connect
    try:
        conn = psycopg2.connect(
            host=parsed.hostname,
            port=parsed.port,
            database=parsed.path.lstrip('/'),
            user=parsed.username,
            password=parsed.password
        )
        cursor = conn.cursor()

        # Test query
        cursor.execute("SELECT version();")
        version = cursor.fetchone()[0]
        print(f"\n✓ Connected successfully!")
        print(f"  PostgreSQL version: {version.split(',')[0]}")

        # Check if tables exist
        cursor.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public'
            ORDER BY table_name
        """)
        tables = cursor.fetchall()

        if tables:
            print(f"\n✓ Found {len(tables)} tables:")
            for t in tables:
                print(f"  - {t[0]}")
        else:
            print("\n! No tables found - schema not yet created")
            print("  Run: python migrations/migrate_to_postgres.py --create-schema")

        cursor.close()
        conn.close()
        return True

    except Exception as e:
        print(f"\n✗ Connection failed: {e}")
        return False


if __name__ == '__main__':
    print("="*50)
    print("FieldPulse PostgreSQL Connection Test")
    print("="*50)
    test_connection()