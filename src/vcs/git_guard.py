"""Git working tree check, task branch management, and diff scope guard."""

from fnmatch import fnmatch
import os
from pathlib import Path
import subprocess
from typing import List, Optional, Set, Union

from .exceptions import (
    BranchSwitchError,
    DirtyWorkingTreeError,
    GitGuardError,
    OutOfScopeDiffError,
)
from .models import DiffScopeResult, DirtyFileStatus, WorkingTreeStatus


STATUS_DESCRIPTIONS = {
    "M": "Modified (Đã chỉnh sửa)",
    "A": "Added (File mới staged)",
    "D": "Deleted (Đã xóa)",
    "R": "Renamed (Đã đổi tên)",
    "C": "Copied (Đã sao chép)",
    "??": "Untracked (File mới chưa track)",
    "!!": "Ignored (Bị bỏ qua)",
}


class GitGuard:
    """Safeguards Git working tree state, branch switches, and file diff boundaries."""

    def __init__(
        self,
        repo_dir: Optional[Union[str, Path]] = None,
        git_binary: str = "git"
    ):
        self.repo_dir = Path(repo_dir or os.getenv("GIT_REPO_DIR", ".")).resolve()
        self.git_binary = git_binary

    def is_git_repo(self) -> bool:
        """Check if repo_dir is inside a valid Git work tree."""
        try:
            res = subprocess.run(
                [self.git_binary, "rev-parse", "--is-inside-work-tree"],
                cwd=str(self.repo_dir),
                capture_output=True,
                text=True,
            )
            return res.returncode == 0
        except Exception:
            return False

    def _run_git(self, args: List[str]) -> str:
        """Execute a git command within the target repository."""
        cmd = [self.git_binary] + args
        try:
            res = subprocess.run(
                cmd,
                cwd=str(self.repo_dir),
                capture_output=True,
                text=True,
                encoding="utf-8"
            )
        except Exception as e:
            raise GitGuardError(f"Không thể thực thi lệnh git '{' '.join(cmd)}': {e}") from e

        if res.returncode != 0:
            err_msg = res.stderr.strip() or res.stdout.strip()
            raise GitGuardError(f"Lệnh git '{' '.join(args)}' thất bại (code {res.returncode}): {err_msg}")

        return res.stdout.strip()

    def get_current_branch(self) -> str:
        """Return the current branch name (e.g. main, task/ACF-07)."""
        if not self.is_git_repo():
            return "main"
        return self._run_git(["rev-parse", "--abbrev-ref", "HEAD"])

    def get_working_tree_status(self) -> WorkingTreeStatus:
        """Check whether the working tree is clean or has uncommitted changes."""
        if not self.is_git_repo():
            return WorkingTreeStatus(is_clean=True, current_branch="main", dirty_files=[])

        raw_status = self._run_git(["status", "--porcelain=v1", "-uall"])
        current_branch = self.get_current_branch()

        if not raw_status:
            return WorkingTreeStatus(is_clean=True, current_branch=current_branch, dirty_files=[])

        dirty_files: List[DirtyFileStatus] = []
        for line in raw_status.splitlines():
            line = line.strip()
            if not line:
                continue
            
            # Format: XY PATH or ?? PATH
            if line.startswith("??"):
                code = "??"
                path = line[2:].strip().strip('"')
            else:
                code = line[:2].strip()
                path = line[2:].strip().strip('"')

            # Handle rename arrows (R file_old -> file_new)
            if " -> " in path:
                path = path.split(" -> ")[-1].strip().strip('"')

            norm_path = Path(path).as_posix()
            desc = STATUS_DESCRIPTIONS.get(code, f"Changed ({code})")
            dirty_files.append(DirtyFileStatus(path=norm_path, status_code=code, description=desc))

        return WorkingTreeStatus(
            is_clean=len(dirty_files) == 0,
            current_branch=current_branch,
            dirty_files=dirty_files
        )

    def is_working_tree_clean(self) -> bool:
        """Return True if working tree has no uncommitted/untracked changes."""
        return self.get_working_tree_status().is_clean

    def ensure_clean_working_tree(self) -> None:
        """Verify the working tree is completely clean.
        
        Raises:
            DirtyWorkingTreeError: If there are uncommitted or untracked changes,
            guaranteeing user modifications are never overwritten.
        """
        status = self.get_working_tree_status()
        if not status.is_clean:
            raise DirtyWorkingTreeError(
                dirty_files=status.file_paths,
                current_branch=status.current_branch
            )

    def branch_exists(self, branch_name: str) -> bool:
        """Check if a local branch exists."""
        if not self.is_git_repo():
            return False
        try:
            self._run_git(["show-ref", "--verify", "--quiet", f"refs/heads/{branch_name}"])
            return True
        except GitGuardError:
            return False

    def ensure_task_branch(
        self,
        task_id: str,
        branch_pattern: str = "task/{id}",
        base_branch: Optional[str] = None
    ) -> str:
        """Safely switch to or create an isolated branch for the task.
        
        Guarantees:
            1. Fails safely if working tree is dirty, preventing overwriting user code.
            2. Switches to existing task branch or creates a new one from base_branch.
        """
        target_branch = branch_pattern.replace("{id}", task_id)
        if not self.is_git_repo():
            return target_branch

        # STEP 1: Safeguard existing user modifications
        self.ensure_clean_working_tree()

        current = self.get_current_branch()
        if current == target_branch:
            return target_branch

        # STEP 2: Checkout existing or create new branch
        try:
            if self.branch_exists(target_branch):
                self._run_git(["checkout", target_branch])
            else:
                args = ["checkout", "-b", target_branch]
                if base_branch:
                    args.append(base_branch)
                self._run_git(args)
        except GitGuardError as e:
            raise BranchSwitchError(f"Không thể chuyển sang nhánh '{target_branch}': {e}") from e

        return target_branch

    def get_changed_files(self, base_ref: Optional[str] = None) -> List[str]:
        """Collect all files modified, staged, or untracked compared to base_ref."""
        if not self.is_git_repo():
            return []

        changed: Set[str] = set()

        # 1. Uncommitted changes in working tree (staged & unstaged)
        try:
            diff_unstaged = self._run_git(["diff", "--name-only"])
            if diff_unstaged:
                changed.update(line.strip() for line in diff_unstaged.splitlines() if line.strip())
            
            diff_staged = self._run_git(["diff", "--cached", "--name-only"])
            if diff_staged:
                changed.update(line.strip() for line in diff_staged.splitlines() if line.strip())
        except GitGuardError:
            pass

        # 2. Untracked files
        try:
            status_out = self._run_git(["status", "--porcelain=v1", "-uall"])
            for line in status_out.splitlines():
                if line.startswith("??"):
                    f = line[2:].strip().strip('"')
                    if f:
                        changed.add(f)
        except GitGuardError:
            pass

        # 3. Committed changes between current branch and base_ref
        if base_ref:
            try:
                diff_base = self._run_git(["diff", "--name-only", f"{base_ref}...HEAD"])
                if diff_base:
                    changed.update(line.strip() for line in diff_base.splitlines() if line.strip())
            except GitGuardError:
                # If three-dot diff fails, try two-dot
                try:
                    diff_base2 = self._run_git(["diff", "--name-only", base_ref, "HEAD"])
                    if diff_base2:
                        changed.update(line.strip() for line in diff_base2.splitlines() if line.strip())
                except GitGuardError:
                    pass

        # Normalize all paths to POSIX format
        return sorted({Path(p).as_posix() for p in changed})

    @staticmethod
    def match_path_pattern(file_path: str, pattern: str) -> bool:
        """Check if file_path matches a glob or path pattern.
        
        Handles:
            - Exact matches: "README.md"
            - Wildcards: "*.env", "*.pem"
            - Directory wildcards: "src/vcs/*", "src/vcs/**"
            - Sub-path prefix matching: "src/vcs/"
        """
        norm_path = Path(file_path).as_posix()
        norm_pattern = Path(pattern).as_posix()

        # Exact match
        if norm_path == norm_pattern:
            return True

        # Standard fnmatch
        if fnmatch(norm_path, norm_pattern):
            return True

        # Basename fnmatch (for patterns like "*.pem", "*.key", ".env*")
        file_name = Path(norm_path).name
        if fnmatch(file_name, norm_pattern):
            return True

        # Directory recursive pattern matching (e.g. "src/vcs/*" or "src/vcs/**")
        clean_dir_pattern = norm_pattern.rstrip("*").rstrip("/")
        if clean_dir_pattern and (norm_path == clean_dir_pattern or norm_path.startswith(f"{clean_dir_pattern}/")):
            return True

        return False

    def validate_diff_scope(
        self,
        changed_files: List[str],
        in_scope: List[str],
        out_of_scope: List[str],
        task_id: Optional[str] = None
    ) -> DiffScopeResult:
        """Validate a list of changed files against in_scope and out_of_scope rules."""
        in_scope_matches: List[str] = []
        out_of_scope_violations: List[str] = []
        unauthorized_files: List[str] = []

        for f in changed_files:
            # Check for forbidden out_of_scope matches FIRST
            is_forbidden = any(self.match_path_pattern(f, pat) for pat in out_of_scope)
            if is_forbidden:
                out_of_scope_violations.append(f)
                continue

            # Check if file matches authorized in_scope
            is_allowed = any(self.match_path_pattern(f, pat) for pat in in_scope)
            if is_allowed:
                in_scope_matches.append(f)
            else:
                unauthorized_files.append(f)

        is_valid = len(out_of_scope_violations) == 0 and len(unauthorized_files) == 0

        if is_valid:
            summary = f"Diff hợp lệ: Tất cả {len(changed_files)} file thay đổi đều nằm trong phạm vi in-scope."
        else:
            reasons = []
            if out_of_scope_violations:
                reasons.append(f"{len(out_of_scope_violations)} file chạm vào phạm vi cấm (out_of_scope)")
            if unauthorized_files:
                reasons.append(f"{len(unauthorized_files)} file nằm ngoài phạm vi được phép (in_scope)")
            summary = f"Diff không hợp lệ: {', '.join(reasons)}."

        return DiffScopeResult(
            is_valid=is_valid,
            task_id=task_id,
            checked_files=changed_files,
            in_scope_matches=in_scope_matches,
            out_of_scope_violations=out_of_scope_violations,
            unauthorized_files=unauthorized_files,
            summary=summary
        )

    def ensure_diff_in_scope(
        self,
        in_scope: List[str],
        out_of_scope: List[str],
        base_ref: Optional[str] = "main",
        task_id: Optional[str] = None
    ) -> DiffScopeResult:
        """Collect changed files and strictly enforce diff scope boundaries.
        
        Raises:
            OutOfScopeDiffError: If any changed file touches out_of_scope or falls outside in_scope.
        """
        changed_files = self.get_changed_files(base_ref=base_ref)
        if not changed_files:
            return DiffScopeResult(
                is_valid=True,
                task_id=task_id,
                checked_files=[],
                summary="Không có file nào thay đổi."
            )

        result = self.validate_diff_scope(
            changed_files=changed_files,
            in_scope=in_scope,
            out_of_scope=out_of_scope,
            task_id=task_id
        )

        if not result.is_valid:
            raise OutOfScopeDiffError(
                unauthorized_files=result.unauthorized_files,
                out_of_scope_violations=result.out_of_scope_violations,
                task_id=task_id
            )

        return result
