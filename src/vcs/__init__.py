"""Git working tree and diff guard package for AI Coding Factory."""

from .exceptions import (
    BranchSwitchError,
    DirtyWorkingTreeError,
    GitGuardError,
    OutOfScopeDiffError,
)
from .git_guard import GitGuard
from .models import DiffScopeResult, DirtyFileStatus, WorkingTreeStatus

__all__ = [
    "GitGuard",
    "GitGuardError",
    "DirtyWorkingTreeError",
    "OutOfScopeDiffError",
    "BranchSwitchError",
    "WorkingTreeStatus",
    "DirtyFileStatus",
    "DiffScopeResult",
]
