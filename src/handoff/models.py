"""Data models for Antigravity task handoff workflow."""

from typing import Dict, List, Optional
from pydantic import BaseModel, Field

from src.storage.models import utc_now_iso


class HandoffContext(BaseModel):
    """Repository and workspace context gathered for generating the Antigravity prompt."""
    workspace_dir: str
    current_branch: str = "main"
    target_branch: str
    agents_md_path: str = "AGENTS.md"
    existing_in_scope_files: List[str] = Field(default_factory=list)
    dependencies_status: Dict[str, str] = Field(default_factory=dict)


class HandoffPacket(BaseModel):
    """Complete handoff package and state for a task dispatched to Antigravity."""
    run_id: str
    task_id: str
    objective: str
    description: str = ""
    target_branch: str
    in_scope: List[str] = Field(default_factory=list)
    out_of_scope: List[str] = Field(default_factory=list)
    acceptance_criteria: List[str] = Field(default_factory=list)
    test_commands: List[str] = Field(default_factory=list)
    forbidden_commands: List[str] = Field(default_factory=list)
    max_fix_attempts: int = 3
    context: HandoffContext
    prompt_text: str
    prompt_path: Optional[str] = None
    packet_path: Optional[str] = None
    status: str = Field(
        default="waiting_for_human",
        description="Trạng thái bàn giao: waiting_for_human, handed_off, hoặc rejected"
    )
    confirmed_by_human: Optional[bool] = None
    confirmed_at: Optional[str] = None
    created_at: str = Field(default_factory=utc_now_iso)
    handoff_notes: Optional[str] = None
