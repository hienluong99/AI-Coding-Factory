"""Exceptions for Beads CLI integration."""


class BeadsError(Exception):
    """Base exception for Beads errors."""
    pass


class BeadsCLIError(BeadsError):
    """Raised when the Beads CLI execution fails."""

    def __init__(self, command: str, exit_code: int, stderr: str):
        super().__init__(f"Beads CLI '{command}' thất bại (mã thoát: {exit_code}): {stderr}")
        self.command = command
        self.exit_code = exit_code
        self.stderr = stderr


class TaskBlockedError(BeadsError):
    """Raised when attempting to dispatch or execute a blocked task."""

    def __init__(self, task_id: str, blocked_by: list):
        self.task_id = task_id
        self.blocked_by = blocked_by
        super().__init__(
            f"Không thể thực thi task '{task_id}' vì đang bị chặn (BLOCKED) bởi các task chưa hoàn thành: {', '.join(blocked_by)}"
        )
