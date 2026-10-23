"""Data models for the Sequential Orchestrator and Factory CLI."""

from typing import List, Optional
from pydantic import BaseModel, Field

from src.beads.models import BeadsTask
from src.storage.models import RunRecord, utc_now_iso


class TaskClaim(BaseModel):
    """Represents an exclusive lock/claim on a task by an orchestrator process."""
    task_id: str
    owner_id: str = Field(..., description="Định danh tiến trình hoặc worker giữ khóa")
    pid: int = Field(..., description="Process ID của tiến trình")
    claimed_at: str = Field(default_factory=utc_now_iso)
    run_id: Optional[str] = None
    lock_file: Optional[str] = None


class FactoryStatus(BaseModel):
    """Overall status snapshot of the AI Coding Factory."""
    ready_tasks: List[BeadsTask] = Field(default_factory=list)
    blocked_tasks: List[BeadsTask] = Field(default_factory=list)
    active_claims: List[TaskClaim] = Field(default_factory=list)
    active_runs: List[RunRecord] = Field(default_factory=list)
    recent_runs: List[RunRecord] = Field(default_factory=list)


class NextActionPlan(BaseModel):
    """Represents the selected task and planned or executed actions for `factory next`."""
    has_next: bool = False
    dry_run: bool = False
    claimed: bool = False
    task_id: Optional[str] = None
    title: Optional[str] = None
    run_id: Optional[str] = None
    target_branch: Optional[str] = None
    in_scope: List[str] = Field(default_factory=list)
    out_of_scope: List[str] = Field(default_factory=list)
    test_commands: List[str] = Field(default_factory=list)
    planned_actions: List[str] = Field(default_factory=list)
    message: str = ""
