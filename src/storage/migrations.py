"""Database migration manager and schema definitions for SQLite storage."""

import logging
import sqlite3
from typing import Callable, List, Tuple

logger = logging.getLogger("aicodingfactory.storage.migrations")

# List of migrations: (version, name, callable(conn))
MigrationStep = Tuple[int, str, Callable[[sqlite3.Connection], None]]


def _migration_001_initial_schema(conn: sqlite3.Connection) -> None:
    """Create initial schema for run_records and run_attempts."""
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS run_records (
            run_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            finished_at TEXT,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 3,
            log_path TEXT,
            test_results TEXT,
            error_message TEXT,
            metadata TEXT
        );
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_run_records_task_id ON run_records(task_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_run_records_status ON run_records(status);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_run_records_created_at ON run_records(created_at);")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS run_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL REFERENCES run_records(run_id) ON DELETE CASCADE,
            attempt_number INTEGER NOT NULL,
            status TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            log_path TEXT,
            test_results TEXT,
            error_message TEXT
        );
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_run_attempts_run_id ON run_attempts(run_id);")


MIGRATIONS: List[MigrationStep] = [
    (1, "initial_run_records_and_attempts", _migration_001_initial_schema),
]


def init_and_migrate(conn: sqlite3.Connection) -> int:
    """Initialize migration tracking and apply any pending schema migrations.
    
    Returns:
        int: Number of new migrations applied.
    """
    cursor = conn.cursor()

    # Ensure schema_migrations table exists
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL
        );
    """)
    conn.commit()

    # Get already applied versions
    cursor.execute("SELECT version FROM schema_migrations ORDER BY version ASC;")
    applied_versions = {row[0] for row in cursor.fetchall()}

    applied_count = 0
    for version, name, migration_fn in MIGRATIONS:
        if version not in applied_versions:
            logger.info("Applying migration %03d: %s", version, name)
            with conn:
                migration_fn(conn)
                cursor.execute(
                    "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, datetime('now'));",
                    (version, name)
                )
            applied_count += 1
            logger.info("Successfully applied migration %03d", version)

    return applied_count
