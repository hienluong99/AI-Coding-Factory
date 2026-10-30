"""Pydantic data models for the Auto-Pilot Orchestrator Loop and Factory Dashboard."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class TaskStepResult(BaseModel):
    """Execution outcome of a single task in the Auto-Pilot loop."""
    task_id: str
    run_id: Optional[str] = None
    title: str = ""
    branch: Optional[str] = None
    status: str = "pending"  # "success", "blocked", "waiting_for_review", "error", "skipped"
    attempts: int = 0
    quality_passed: bool = False
    pr_created: bool = False
    pr_url: Optional[str] = None
    merged: bool = False
    closed_in_beads: bool = False
    error_message: Optional[str] = None
    duration_seconds: float = 0.0


class AutoPilotConfig(BaseModel):
    """Configuration options for the Auto-Pilot Orchestrator Loop."""
    max_tasks: Optional[int] = None
    target_task_ids: Optional[List[str]] = None
    auto_approve_handoff: bool = True
    auto_approve_review: bool = True
    stop_on_blocked: bool = True
    base_branch: str = "main"
    push_to_remote: bool = False
    skip_remote_api: bool = True
    timeout_per_command: Optional[int] = None
    dry_run: bool = False


class AutoPilotResult(BaseModel):
    """Aggregate result of an Auto-Pilot loop execution session."""
    total_tasks_processed: int = 0
    successful_tasks: List[str] = Field(default_factory=list)
    blocked_tasks: List[str] = Field(default_factory=list)
    pending_review_tasks: List[str] = Field(default_factory=list)
    skipped_tasks: List[str] = Field(default_factory=list)
    task_results: List[TaskStepResult] = Field(default_factory=list)
    duration_seconds: float = 0.0
    status: str = "completed"  # "completed", "stopped_on_blocked", "dry_run", "no_ready_tasks", "error"
    message: str = ""


class DashboardSummary(BaseModel):
    """High-level metrics and current snapshot of the AI Coding Factory."""
    factory_name: str = "AI Coding Factory"
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    git_branch: str = ""
    working_tree_clean: bool = True
    ready_count: int = 0
    blocked_count: int = 0
    active_claims_count: int = 0
    total_runs_count: int = 0
    successful_runs_count: int = 0
    blocked_runs_count: int = 0
    pending_runs_count: int = 0
    ready_tasks: List[Dict[str, Any]] = Field(default_factory=list)
    blocked_tasks: List[Dict[str, Any]] = Field(default_factory=list)
    active_claims: List[Dict[str, Any]] = Field(default_factory=list)
    recent_runs: List[Dict[str, Any]] = Field(default_factory=list)
