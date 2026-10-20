"""Models for Beads tasks and dependencies."""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class BeadsDependency(BaseModel):
    """Dependency information for a Beads issue."""
    id: Optional[str] = None
    depends_on_id: Optional[str] = None
    title: Optional[str] = None
    status: Optional[str] = None
    type: Optional[str] = Field(default="blocks", alias="dependency_type")

    model_config = {
        "populate_by_name": True,
        "extra": "ignore"
    }

    @property
    def target_id(self) -> str:
        """Return the target dependency ID regardless of API representation."""
        return self.id or self.depends_on_id or ""


class BeadsTask(BaseModel):
    """Representation of a task returned by Beads CLI."""
    id: str
    title: str
    description: Optional[str] = ""
    status: str
    priority: int = 2
    issue_type: str = "task"
    dependencies: List[BeadsDependency] = Field(default_factory=list)
    blocked_by: List[str] = Field(default_factory=list)
    blocked_by_count: int = 0
    dependency_count: int = 0
    dependent_count: int = 0
    raw: Dict[str, Any] = Field(default_factory=dict)

    model_config = {
        "extra": "ignore"
    }

    @property
    def is_blocked(self) -> bool:
        """Check if this task is currently blocked."""
        if self.blocked_by_count > 0 or len(self.blocked_by) > 0:
            return True
        # If any dependencies are open and blocking
        for dep in self.dependencies:
            if dep.status and dep.status != "closed":
                return True
        return False
