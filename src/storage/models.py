"""Data models for Run records, test results, and execution history."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field


def utc_now_iso() -> str:
    """Return current UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()


class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    BLOCKED = "blocked"


class TestResultRecord(BaseModel):
    """Structured result of automated tests run during an attempt."""
    __test__ = False  # Prevent pytest from discovering this model as a test suite
    command: str = Field(..., description="Lệnh kiểm thử đã chạy (vd: pytest tests/...)")
    passed: bool = Field(..., description="Trạng thái đạt hoặc không đạt")
    exit_code: int = Field(default=0, description="Mã thoát của lệnh")
    summary: Optional[str] = Field(default="", description="Tóm tắt kết quả (vd: 20 passed, 0 failed)")
    stdout: Optional[str] = Field(default="", description="Standard output log")
    stderr: Optional[str] = Field(default="", description="Standard error log")
    executed_at: str = Field(default_factory=utc_now_iso, description="Thời điểm chạy kiểm thử")


class RunAttempt(BaseModel):
    """Detailed record of an individual execution/fix attempt within a run."""
    id: Optional[int] = None
    run_id: str
    attempt_number: int
    status: RunStatus
    started_at: str = Field(default_factory=utc_now_iso)
    finished_at: Optional[str] = None
    log_path: Optional[str] = None
    test_results: List[TestResultRecord] = Field(default_factory=list)
    error_message: Optional[str] = None


class RunRecord(BaseModel):
    """Persistent execution record for a task dispatched to an AI agent."""
    run_id: str
    task_id: str
    status: RunStatus = Field(default=RunStatus.PENDING)
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)
    finished_at: Optional[str] = None
    attempt_count: int = 0
    max_attempts: int = 3
    log_path: Optional[str] = None
    test_results: List[TestResultRecord] = Field(default_factory=list)
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    attempts: List[RunAttempt] = Field(default_factory=list)

    @property
    def is_finished(self) -> bool:
        """Return True if the run is completed (success, failed, or blocked)."""
        return self.status in (RunStatus.SUCCESS, RunStatus.FAILED, RunStatus.BLOCKED)
