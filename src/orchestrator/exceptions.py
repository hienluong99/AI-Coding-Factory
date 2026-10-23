"""Exceptions for the AI Coding Factory Orchestrator."""

from typing import Optional


class OrchestratorError(Exception):
    """Base exception for orchestrator operations."""
    pass


class TaskAlreadyClaimedError(OrchestratorError):
    """Raised when attempting to claim a task that is already locked by another process."""

    def __init__(
        self,
        task_id: str,
        owner_id: Optional[str] = None,
        pid: Optional[int] = None,
        claimed_at: Optional[str] = None
    ):
        self.task_id = task_id
        self.owner_id = owner_id
        self.pid = pid
        self.claimed_at = claimed_at
        details = []
        if owner_id:
            details.append(f"owner={owner_id}")
        if pid:
            details.append(f"pid={pid}")
        if claimed_at:
            details.append(f"at={claimed_at}")
        detail_str = f" ({', '.join(details)})" if details else ""
        super().__init__(
            f"Task '{task_id}' đã được claim bởi một tiến trình khác{detail_str}. "
            "Không thể claim trùng lặp."
        )
