"""Models for Self-Correction Engine and Blocked Handling (ACF-09)."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


def utc_now_iso() -> str:
    """Return current UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()


class CorrectionAction(str, Enum):
    """Action determined by the Self-Correction Engine based on attempt count."""
    RETRY_FIX = "retry_fix"        # Còn lượt thử: sinh fix prompt và giao agent
    HALT_BLOCKED = "halt_blocked"  # Hết lượt thử (>= max_attempts): dừng và đánh dấu BLOCKED
    NOOP_SUCCESS = "noop_success"  # Tất cả test pass: không cần sửa


class FailureDiagnosis(BaseModel):
    """Detailed diagnosis of an individual failed test or command."""
    __test__ = False

    command: str = Field(..., description="Câu lệnh kiểm thử bị thất bại")
    exit_code: int = Field(default=1, description="Mã thoát của lệnh")
    error_summary: str = Field(default="", description="Tóm tắt lỗi ngắn gọn")
    error_type: Optional[str] = Field(default=None, description="Loại lỗi (AssertionError, Timeout, v.v.)")
    failed_tests: List[str] = Field(default_factory=list, description="Danh sách test case cụ thể bị trượt")
    root_cause_snippet: str = Field(default="", description="Đoạn log/traceback quan trọng gây lỗi")
    related_files: List[str] = Field(default_factory=list, description="Các file liên quan đến lỗi được phát hiện")
    suggested_action: Optional[str] = Field(default=None, description="Gợi ý hành động khắc phục")


class BlockedDiagnosticReport(BaseModel):
    """Diagnostic report generated when maximum fix attempts are reached."""
    __test__ = False

    run_id: str
    task_id: str
    total_attempts: int
    max_attempts: int
    final_status: str = "blocked"
    generated_at: str = Field(default_factory=utc_now_iso)
    failures: List[FailureDiagnosis] = Field(default_factory=list)
    operator_guidance: List[str] = Field(default_factory=list)
    attempt_history_summary: List[Dict[str, Any]] = Field(default_factory=list)


class CorrectionDecision(BaseModel):
    """Comprehensive decision and outcome emitted by SelfCorrectionEngine."""
    __test__ = False

    run_id: str
    task_id: str
    attempt_number: int
    max_attempts: int
    remaining_attempts: int
    action: CorrectionAction
    passed: bool
    failures: List[FailureDiagnosis] = Field(default_factory=list)
    fix_prompt_path: Optional[str] = None
    diagnostic_report_path: Optional[str] = None
    message: str = ""
    created_at: str = Field(default_factory=utc_now_iso)
