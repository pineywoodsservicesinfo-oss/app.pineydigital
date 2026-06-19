"""
db_config.py - Database configuration for FieldPulse
Supports both SQLite (development) and PostgreSQL (production)
"""

import os
from pathlib import Path
from typing import Optional
import logging

logger = logging.getLogger(__name__)


class DatabaseConfig:
    """Database configuration manager."""

    def __init__(self):
        self.db_type = os.environ.get('DATABASE_TYPE', 'sqlite').lower()
        self._connection_pool = None

    @property
    def is_postgres(self) -> bool:
        """Check if using PostgreSQL."""
        return self.db_type == 'postgres'

    @property
    def is_sqlite(self) -> bool:
        """Check if using SQLite."""
        return self.db_type == 'sqlite'

    def get_connection_url(self) -> str:
        """Get the database connection URL."""
        if self.is_postgres:
            url = os.environ.get('DATABASE_URL')
            if not url:
                raise ValueError("DATABASE_URL environment variable required for PostgreSQL")
            return url

        # SQLite
        db_path = os.environ.get('SQLITE_PATH', 'data/leads.db')
        return f"sqlite:///{db_path}"

    def get_sqlite_path(self) -> Path:
        """Get SQLite database path."""
        db_path = os.environ.get('SQLITE_PATH', 'data/leads.db')
        return Path(__file__).parent.parent / db_path

    def get_pg_connection_params(self) -> dict:
        """Parse PostgreSQL connection parameters from DATABASE_URL."""
        if not self.is_postgres:
            return {}

        url = self.get_connection_url()

        # Parse: postgresql://user:pass@host:port/dbname
        # or: postgres://user:pass@host:port/dbname
        import re
        from urllib.parse import urlparse

        parsed = urlparse(url)

        return {
            'host': parsed.hostname,
            'port': parsed.port or 5432,
            'database': parsed.path.lstrip('/'),
            'user': parsed.username,
            'password': parsed.password,
        }

    def get_connection_pool(self):
        """Get or create PostgreSQL connection pool."""
        if self._connection_pool is None and self.is_postgres:
            import psycopg2.pool
            params = self.get_pg_connection_params()
            self._connection_pool = psycopg2.pool.SimpleConnectionPool(
                minconn=1,
                maxconn=10,
                **params
            )
            logger.info("PostgreSQL connection pool created")
        return self._connection_pool


# Global config instance
db_config = DatabaseConfig()


def get_db_connection():
    """
    Get database connection based on configuration.
    Uses connection pooling for PostgreSQL.

    Returns:
        SQLite connection or PostgreSQL connection
    """
    if db_config.is_postgres:
        import psycopg2
        from psycopg2.extras import RealDictCursor

        # Use connection pool
        pool = db_config.get_connection_pool()
        if pool:
            conn = pool.getconn()
            conn.autocommit = False
            return conn

        # Fallback to direct connection
        params = db_config.get_pg_connection_params()
        conn = psycopg2.connect(**params)
        conn.autocommit = False
        return conn

    # SQLite (default)
    import sqlite3
    conn = sqlite3.connect(db_config.get_sqlite_path())
    conn.row_factory = sqlite3.Row
    return conn


def release_db_connection(conn):
    """
    Release connection back to pool (PostgreSQL) or close (SQLite).
    """
    if db_config.is_postgres:
        pool = db_config.get_connection_pool()
        if pool:
            pool.putconn(conn)
        else:
            conn.close()
    else:
        conn.close()


def init_database():
    """
    Initialize database schema based on type.

    For PostgreSQL, runs pg_schema.sql
    For SQLite, runs existing init_db()
    """
    if db_config.is_postgres:
        import psycopg2

        conn = get_db_connection()
        cursor = conn.cursor()

        # Read and execute schema
        schema_path = Path(__file__).parent.parent / "migrations" / "pg_schema.sql"

        if schema_path.exists():
            with open(schema_path, 'r') as f:
                sql = f.read()
                cursor.execute(sql)
                conn.commit()
                logger.info("PostgreSQL schema initialized")
        else:
            logger.warning(f"Schema file not found: {schema_path}")

        cursor.close()
        conn.close()

    else:
        # SQLite - use existing init
        from modules.database import init_db
        init_db()
        logger.info("SQLite database initialized")


# Export for convenience
__all__ = ['db_config', 'get_db_connection', 'init_database']