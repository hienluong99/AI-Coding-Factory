"""Exceptions for Task Contract validation and parsing."""

from typing import List, Optional


class TaskContractError(Exception):
    """Base exception for task contract errors."""
    pass


class TaskValidationError(TaskContractError):
    """Raised when a task contract fails schema validation or business rules."""

    def __init__(self, message: str, errors: Optional[List[str]] = None, task_id: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.errors = errors or [message]
        self.task_id = task_id

    def __str__(self) -> str:
        header = f"[TaskContractValidationFailed] Task ID: {self.task_id or 'UNKNOWN'}"
        details = "\n  - ".join(self.errors)
        return f"{header}: {self.message}\nChi tiết lỗi:\n  - {details}"
