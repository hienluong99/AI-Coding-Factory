"""Data models for Git working tree status and diff scope validation."""

from typing import List, Optional
from pydantic import BaseModel, Field


class DirtyFileStatus(BaseModel):
    """Represents a single uncommitted, staged, or untracked file in the working tree."""
    path: str
    status_code: str = Field(..., description="Git status code (vd: M, ??, A, D, R)")
    description: str = Field(..., description="Mô tả dễ hiểu (vd: Modified, Untracked, Staged)")


class WorkingTreeStatus(BaseModel):
    """Snapshot of Git working tree cleanliness."""
    is_clean: bool = True
    current_branch: str = "main"
    dirty_files: List[DirtyFileStatus] = Field(default_factory=list)

    @property
    def file_paths(self) -> List[str]:
        return [f.path for f in self.dirty_files]


class DiffScopeResult(BaseModel):
    """Result of checking changed files against in_scope and out_of_scope contracts."""
    is_valid: bool = True
    task_id: Optional[str] = None
    checked_files: List[str] = Field(default_factory=list)
    in_scope_matches: List[str] = Field(default_factory=list)
    out_of_scope_violations: List[str] = Field(default_factory=list)
    unauthorized_files: List[str] = Field(default_factory=list)
    summary: str = ""
