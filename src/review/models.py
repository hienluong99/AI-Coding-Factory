"""Models for Human-in-the-loop review and automated merge (ACF-11)."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


def utc_now_iso() -> str:
    """Return current UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()


class ReviewAction(str, Enum):
    """Action taken during the review process."""
    APPROVE = "approve"                     # Phê duyệt, tự động merge nhánh và đóng task
    REQUEST_CHANGES = "request_changes"     # Yêu cầu chỉnh sửa kèm feedback phản hồi
    VIEW_ONLY = "view_only"                 # Chỉ xem tóm tắt thông tin review


class ReviewSummary(BaseModel):
    """Snapshot of task delivery artifacts ready for human review."""
    __test__ = False

    task_id: str
    task_title: str
    run_id: str
    run_status: str
    branch: str
    base_branch: str = "main"
    commit_sha: Optional[str] = None
    pr_url: Optional[str] = None
    pr_number: Optional[int] = None
    pr_status: Optional[str] = None
    diff_files: List[str] = Field(default_factory=list)
    quality_gate_passed: bool = True
    test_summary: str = ""
    log_path: Optional[str] = None
    acceptance_criteria: List[str] = Field(default_factory=list)
    in_scope: List[str] = Field(default_factory=list)
    attempt_count: int = 1
    max_attempts: int = 3
    remaining_attempts: int = 2


class ReviewDecision(BaseModel):
    """Outcome of a review decision (approval with merge or request changes)."""
    __test__ = False

    task_id: str
    run_id: str
    action: ReviewAction
    reviewer: str = "human"
    feedback: Optional[str] = None
    merged: bool = False
    merge_commit_sha: Optional[str] = None
    task_closed: bool = False
    lock_released: bool = False
    next_fix_prompt_path: Optional[str] = None
    message: str = ""
    created_at: str = Field(default_factory=utc_now_iso)
