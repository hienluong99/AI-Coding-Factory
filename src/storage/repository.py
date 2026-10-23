"""SQLite repository for storing and querying Run records and attempt history."""

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .migrations import init_and_migrate
from .models import RunAttempt, RunRecord, RunStatus, TestResultRecord, utc_now_iso


class RunRepository:
    """Manages persistent SQLite operations for AI Coding Factory runs."""

    def __init__(
        self,
        db_path: Optional[Union[str, Path]] = None,
        auto_migrate: bool = True
    ):
        raw_path = db_path or os.getenv("FACTORY_DB_PATH", "runs/factory.db")
        self.db_path = Path(raw_path).resolve()
        
        # Ensure parent directory exists
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._init_db(auto_migrate)

    def _get_connection(self) -> sqlite3.Connection:
        """Create and configure a SQLite connection."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")
        return conn

    def _init_db(self, auto_migrate: bool) -> None:
        """Run auto-migrations on initial connection."""
        if auto_migrate:
            with self._get_connection() as conn:
                init_and_migrate(conn)

    @staticmethod
    def generate_run_id(task_id: str) -> str:
        """Generate a human-readable unique run_id timestamped with uuid snippet."""
        now = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        short_id = uuid.uuid4().hex[:6]
        safe_task = task_id.replace("/", "_").replace(" ", "_")
        return f"run_{safe_task}_{now}_{short_id}"

    def create_run(
        self,
        task_id: str,
        max_attempts: int = 3,
        metadata: Optional[Dict[str, Any]] = None,
        log_path: Optional[str] = None,
        run_id: Optional[str] = None
    ) -> RunRecord:
        """Create and persist a new Run record."""
        final_run_id = run_id or self.generate_run_id(task_id)
        now = utc_now_iso()
        meta_dict = metadata or {}
        default_log = log_path or f"runs/{final_run_id}/execution.log"

        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO run_records (
                    run_id, task_id, status, created_at, updated_at,
                    finished_at, attempt_count, max_attempts, log_path,
                    test_results, error_message, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    final_run_id,
                    task_id,
                    RunStatus.PENDING.value,
                    now,
                    now,
                    None,
                    0,
                    max_attempts,
                    default_log,
                    json.dumps([]),
                    None,
                    json.dumps(meta_dict)
                )
            )

        run = self.get_run(final_run_id)
        assert run is not None
        return run

    def get_run(self, run_id: str, include_attempts: bool = True) -> Optional[RunRecord]:
        """Fetch a single Run record by ID with optional attempts."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM run_records WHERE run_id = ?;", (run_id,))
            row = cursor.fetchone()
            if not row:
                return None

            attempts: List[RunAttempt] = []
            if include_attempts:
                cursor.execute(
                    "SELECT * FROM run_attempts WHERE run_id = ? ORDER BY attempt_number ASC;",
                    (run_id,)
                )
                attempt_rows = cursor.fetchall()
                for a_row in attempt_rows:
                    raw_tests = json.loads(a_row["test_results"] or "[]")
                    tests = [TestResultRecord.model_validate(t) for t in raw_tests]
                    attempts.append(RunAttempt(
                        id=a_row["id"],
                        run_id=a_row["run_id"],
                        attempt_number=a_row["attempt_number"],
                        status=RunStatus(a_row["status"]),
                        started_at=a_row["started_at"],
                        finished_at=a_row["finished_at"],
                        log_path=a_row["log_path"],
                        test_results=tests,
                        error_message=a_row["error_message"]
                    ))

            raw_tests = json.loads(row["test_results"] or "[]")
            tests = [TestResultRecord.model_validate(t) for t in raw_tests]
            raw_meta = json.loads(row["metadata"] or "{}")

            return RunRecord(
                run_id=row["run_id"],
                task_id=row["task_id"],
                status=RunStatus(row["status"]),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                finished_at=row["finished_at"],
                attempt_count=row["attempt_count"],
                max_attempts=row["max_attempts"],
                log_path=row["log_path"],
                test_results=tests,
                error_message=row["error_message"],
                metadata=raw_meta,
                attempts=attempts
            )

    def list_runs(
        self,
        task_id: Optional[str] = None,
        status: Optional[RunStatus] = None,
        limit: int = 50
    ) -> List[RunRecord]:
        """List runs with optional filters ordered newest first."""
        query = "SELECT * FROM run_records WHERE 1=1"
        params: List[Any] = []

        if task_id:
            query += " AND task_id = ?"
            params.append(task_id)
        if status:
            query += " AND status = ?"
            params.append(status.value if isinstance(status, RunStatus) else str(status))

        query += " ORDER BY created_at DESC LIMIT ?;"
        params.append(limit)

        runs: List[RunRecord] = []
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, tuple(params))
            for row in cursor.fetchall():
                raw_tests = json.loads(row["test_results"] or "[]")
                tests = [TestResultRecord.model_validate(t) for t in raw_tests]
                raw_meta = json.loads(row["metadata"] or "{}")
                runs.append(RunRecord(
                    run_id=row["run_id"],
                    task_id=row["task_id"],
                    status=RunStatus(row["status"]),
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                    finished_at=row["finished_at"],
                    attempt_count=row["attempt_count"],
                    max_attempts=row["max_attempts"],
                    log_path=row["log_path"],
                    test_results=tests,
                    error_message=row["error_message"],
                    metadata=raw_meta,
                    attempts=[]
                ))
        return runs

    def record_attempt(
        self,
        run_id: str,
        status: RunStatus,
        log_path: Optional[str] = None,
        test_results: Optional[List[Union[TestResultRecord, Dict[str, Any]]]] = None,
        error_message: Optional[str] = None,
        started_at: Optional[str] = None,
        finished_at: Optional[str] = None
    ) -> RunAttempt:
        """Record an execution/fix attempt, incrementing run attempt count."""
        now = utc_now_iso()
        s_at = started_at or now
        f_at = finished_at or now

        # Convert test_results into models
        clean_tests: List[TestResultRecord] = []
        if test_results:
            for item in test_results:
                if isinstance(item, TestResultRecord):
                    clean_tests.append(item)
                elif isinstance(item, dict):
                    clean_tests.append(TestResultRecord.model_validate(item))

        tests_json = json.dumps([t.model_dump() for t in clean_tests])

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT attempt_count FROM run_records WHERE run_id = ?;", (run_id,))
            row = cursor.fetchone()
            if not row:
                raise ValueError(f"Run ID '{run_id}' không tồn tại trong cơ sở dữ liệu.")

            current_count = row["attempt_count"]
            next_count = current_count + 1

            # Insert into run_attempts
            cursor.execute(
                """
                INSERT INTO run_attempts (
                    run_id, attempt_number, status, started_at,
                    finished_at, log_path, test_results, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    run_id,
                    next_count,
                    status.value,
                    s_at,
                    f_at,
                    log_path,
                    tests_json,
                    error_message
                )
            )
            attempt_id = cursor.lastrowid

            # Update parent run_records
            cursor.execute(
                """
                UPDATE run_records
                SET attempt_count = ?,
                    status = ?,
                    updated_at = ?,
                    log_path = COALESCE(?, log_path),
                    test_results = ?,
                    error_message = ?
                WHERE run_id = ?;
                """,
                (
                    next_count,
                    status.value,
                    now,
                    log_path,
                    tests_json,
                    error_message,
                    run_id
                )
            )

        return RunAttempt(
            id=attempt_id,
            run_id=run_id,
            attempt_number=next_count,
            status=status,
            started_at=s_at,
            finished_at=f_at,
            log_path=log_path,
            test_results=clean_tests,
            error_message=error_message
        )

    def finish_run(
        self,
        run_id: str,
        status: RunStatus,
        test_results: Optional[List[Union[TestResultRecord, Dict[str, Any]]]] = None,
        error_message: Optional[str] = None,
        log_path: Optional[str] = None
    ) -> RunRecord:
        """Mark a Run record as finalized (SUCCESS, FAILED, or BLOCKED)."""
        now = utc_now_iso()

        clean_tests: Optional[str] = None
        if test_results is not None:
            records = [
                t.model_dump() if isinstance(t, TestResultRecord) else t
                for t in test_results
            ]
            clean_tests = json.dumps(records)

        with self._get_connection() as conn:
            cursor = conn.cursor()
            if clean_tests is not None:
                cursor.execute(
                    """
                    UPDATE run_records
                    SET status = ?,
                        updated_at = ?,
                        finished_at = ?,
                        test_results = ?,
                        error_message = ?,
                        log_path = COALESCE(?, log_path)
                    WHERE run_id = ?;
                    """,
                    (status.value, now, now, clean_tests, error_message, log_path, run_id)
                )
            else:
                cursor.execute(
                    """
                    UPDATE run_records
                    SET status = ?,
                        updated_at = ?,
                        finished_at = ?,
                        error_message = ?,
                        log_path = COALESCE(?, log_path)
                    WHERE run_id = ?;
                    """,
                    (status.value, now, now, error_message, log_path, run_id)
                )

        run = self.get_run(run_id)
        if not run:
            raise ValueError(f"Run ID '{run_id}' không tồn tại.")
        return run
