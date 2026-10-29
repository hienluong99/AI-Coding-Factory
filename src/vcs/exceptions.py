"""Exceptions for Git branch management and diff scope guarding."""

from typing import List, Optional


class GitGuardError(Exception):
    """Base exception for Git guard operations."""
    pass


class DirtyWorkingTreeError(GitGuardError):
    """Raised when the working tree has uncommitted modifications, staged changes, or untracked files."""

    def __init__(self, dirty_files: List[str], current_branch: str = "unknown"):
        self.dirty_files = dirty_files
        self.current_branch = current_branch
        file_list = "\n  - ".join(dirty_files) if dirty_files else "(không rõ)"
        super().__init__(
            f"Factory dừng an toàn: Working tree trên nhánh '{current_branch}' đang có thay đổi chưa commit.\n"
            f"Để bảo vệ an toàn và không tự ghi đè thay đổi của người dùng, vui lòng commit hoặc stash trước khi tiếp tục:\n"
            f"  - {file_list}"
        )


class OutOfScopeDiffError(GitGuardError):
    """Raised when modified files fall outside in_scope or touch forbidden out_of_scope patterns."""

    def __init__(
        self,
        unauthorized_files: List[str],
        out_of_scope_violations: Optional[List[str]] = None,
        task_id: Optional[str] = None
    ):
        self.unauthorized_files = unauthorized_files
        self.out_of_scope_violations = out_of_scope_violations or []
        self.task_id = task_id or "UNKNOWN"

        details = []
        if self.out_of_scope_violations:
            forbidden_list = ", ".join(self.out_of_scope_violations)
            details.append(f"Chạm vào file cấm can thiệp (out_of_scope): {forbidden_list}")
        if self.unauthorized_files:
            outside_list = ", ".join(self.unauthorized_files)
            details.append(f"File nằm ngoài phạm vi được phép sửa (in_scope): {outside_list}")

        detail_msg = "\n  * ".join(details)
        super().__init__(
            f"Factory dừng an toàn: Task '{self.task_id}' phát hiện diff vi phạm phạm vi file cho phép:\n"
            f"  * {detail_msg}"
        )


class BranchSwitchError(GitGuardError):
    """Raised when switching or creating a task branch fails."""
    pass
