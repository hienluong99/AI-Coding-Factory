"""Pull Request manager for automated delivery packaging and GitHub integration."""

import json
import os
import subprocess
from pathlib import Path
from typing import List, Optional, Union

from src.contracts.parser import parse_task_file
from src.contracts.task import TaskContract, TaskScope
from src.quality.models import QualityGateSummary
from src.storage.models import RunRecord, RunStatus
from src.storage.repository import RunRepository
from src.vcs.git_guard import GitGuard
from src.vcs.models import DiffScopeResult

from .exceptions import DeliveryError, QualityGateNotPassedError
from .formatter import build_conventional_commit, build_pr_body, derive_scope
from .github_client import GitHubAPIError, GitHubClient, parse_github_remote
from .models import PRStatus, PullRequestPackage, utc_now_iso


class PullRequestManager:
    """
    Manages automated commit creation, scope verification, Quality Gate checks,
    and Pull Request dispatching to GitHub or local delivery bundles.
    """

    def __init__(
        self,
        repo_root: Optional[Union[str, Path]] = None,
        run_repo: Optional[RunRepository] = None,
        git_guard: Optional[GitGuard] = None,
        runs_dir: Optional[Union[str, Path]] = None,
        tasks_dir: Optional[Union[str, Path]] = None,
        github_client: Optional[GitHubClient] = None,
    ) -> None:
        self.repo_root = Path(repo_root) if repo_root else Path.cwd()
        self.runs_dir = Path(runs_dir) if runs_dir else (self.repo_root / "runs")
        self.tasks_dir = Path(tasks_dir) if tasks_dir else (self.repo_root / "tasks")
        self.run_repo = run_repo or RunRepository(db_path=self.runs_dir / "factory.db")
        self.git_guard = git_guard or GitGuard(repo_dir=self.repo_root)
        self.github_client = github_client or GitHubClient()

    def load_contract(self, task_id: str) -> TaskContract:
        """Load TaskContract from disk or raise DeliveryError if not found."""
        contract_file = self.tasks_dir / f"{task_id}.json"
        if not contract_file.exists():
            raise DeliveryError(f"Không tìm thấy contract cho task '{task_id}' tại {contract_file}.")
        return parse_task_file(contract_file)

    def _get_remote_url(self) -> Optional[str]:
        """Fetch remote origin URL via git."""
        try:
            res = subprocess.run(
                ["git", "config", "--get", "remote.origin.url"],
                cwd=str(self.repo_root),
                capture_output=True,
                text=True,
                check=False,
            )
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.strip()
        except Exception:
            pass
        return None

    def _get_current_branch(self) -> str:
        """Get currently active git branch."""
        try:
            res = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=str(self.repo_root),
                capture_output=True,
                text=True,
                check=True,
            )
            return res.stdout.strip()
        except Exception:
            return "HEAD"

    def _get_head_commit_sha(self) -> str:
        """Get commit SHA of current HEAD."""
        try:
            res = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=str(self.repo_root),
                capture_output=True,
                text=True,
                check=True,
            )
            return res.stdout.strip()
        except Exception:
            return "unknown_sha"

    def _commit_uncommitted_changes(self, task_contract: TaskContract) -> Optional[str]:
        """Stage in-scope files and commit with Conventional Commits message."""
        tree = self.git_guard.get_working_tree_status()
        if tree.is_clean:
            return self._get_head_commit_sha()

        # Stage only in-scope files that were modified or untracked
        files_to_add = [
            f.path for f in tree.dirty_files
            if self.git_guard._matches_any_pattern(f.path, task_contract.scope.in_scope)
            and not self.git_guard._matches_any_pattern(f.path, task_contract.scope.out_of_scope)
        ]
        if not files_to_add:
            return self._get_head_commit_sha()

        subprocess.run(
            ["git", "add"] + files_to_add,
            cwd=str(self.repo_root),
            capture_output=True,
            check=True,
        )

        commit_msg = build_conventional_commit(task_contract)
        res = subprocess.run(
            ["git", "commit", "-m", commit_msg],
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode != 0:
            raise DeliveryError(f"Lỗi khi thực hiện git commit: {res.stderr}")

        return self._get_head_commit_sha()

    def package_and_deliver(
        self,
        task_id: str,
        run_id: Optional[str] = None,
        base_branch: str = "main",
        push_to_remote: bool = True,
        skip_remote_api: bool = False,
    ) -> PullRequestPackage:
        """
        Validate Quality Gate, verify diff scope, create Conventional Commit,
        dispatch Pull Request to GitHub (or create local bundle), and save artifacts.
        """
        contract = self.load_contract(task_id)

        # 1. Resolve RunRecord
        run: Optional[RunRecord] = None
        if run_id:
            run = self.run_repo.get_run(run_id, include_attempts=True)
            if not run:
                raise DeliveryError(f"Run ID '{run_id}' không tồn tại trong cơ sở dữ liệu.")
        else:
            runs = self.run_repo.list_runs(task_id=task_id, limit=1)
            if runs:
                run = self.run_repo.get_run(runs[0].run_id, include_attempts=True)

        resolved_run_id = run.run_id if run else f"adhoc_{task_id}_{int(utc_now_iso()[:10].replace('-', ''))}"

        # 2. Verify Quality Gate: Must have passed 100%
        if run and run.attempts:
            latest_attempt = run.attempts[-1]
            if latest_attempt.status != RunStatus.SUCCESS:
                raise QualityGateNotPassedError(
                    f"Quality Gate chưa đạt: Attempt gần nhất (#{latest_attempt.attempt_number}) có trạng thái '{latest_attempt.status.value}'. "
                    "Chỉ được phép tạo Pull Request khi Quality Gate PASS 100%."
                )
        elif not run or not run.attempts:
            # If no run recorded, verify if task status is completed
            if contract.status.value != "completed":
                raise QualityGateNotPassedError(
                    f"Task '{task_id}' chưa có kết quả kiểm thử Quality Gate hợp lệ. "
                    "Hãy chạy 'python -m src.factory quality {task_id}' trước khi tạo PR."
                )

        # 3. Verify Diff Scope
        current_branch = self._get_current_branch()
        diff_res: DiffScopeResult = self.git_guard.ensure_diff_in_scope(
            in_scope=contract.scope.in_scope,
            out_of_scope=contract.scope.out_of_scope,
            base_ref=base_branch,
            task_id=task_id,
        )

        # 4. Commit any remaining changes in working tree
        commit_sha = self._commit_uncommitted_changes(contract)
        commit_msg = build_conventional_commit(contract)

        # 5. Build PR title & body
        scope_name = derive_scope(contract)
        pr_title = f"feat({scope_name}): {contract.objective.lower()} [Ref: {contract.id}]"
        pr_body = build_pr_body(
            task_contract=contract,
            diff_files=diff_res.checked_files,
            run_record=run,
            commit_sha=commit_sha,
        )

        # 6. Push to remote if requested
        remote_url = self._get_remote_url()
        pushed = False
        if push_to_remote and remote_url and current_branch != base_branch:
            try:
                push_res = subprocess.run(
                    ["git", "push", "-u", "origin", current_branch],
                    cwd=str(self.repo_root),
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if push_res.returncode == 0:
                    pushed = True
            except Exception:
                pass

        # 7. Create Pull Request on GitHub
        pr_status = PRStatus.LOCAL_BUNDLE
        pr_url: Optional[str] = None
        pr_number: Optional[int] = None

        if not skip_remote_api and remote_url and current_branch != base_branch:
            owner, repo_name, token = parse_github_remote(remote_url)
            env_token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
            actual_token = token or env_token

            if owner and repo_name and actual_token:
                try:
                    pr_data = self.github_client.create_pull_request(
                        owner=owner,
                        repo=repo_name,
                        title=pr_title,
                        head=current_branch,
                        base=base_branch,
                        body=pr_body,
                        token=actual_token,
                    )
                    pr_url = pr_data.get("url")
                    pr_number = pr_data.get("number")
                    pr_status = PRStatus.CREATED
                except GitHubAPIError:
                    pr_status = PRStatus.LOCAL_BUNDLE

        # 8. Save PR artifacts
        run_dir = self.runs_dir / resolved_run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        summary_file = run_dir / "pr_summary.md"
        summary_file.write_text(pr_body, encoding="utf-8")
        rel_summary = str(summary_file.relative_to(self.repo_root)).replace("\\", "/")

        package = PullRequestPackage(
            task_id=task_id,
            run_id=resolved_run_id,
            title=pr_title,
            branch=current_branch,
            base_branch=base_branch,
            commit_sha=commit_sha,
            commit_message=commit_msg,
            pr_body=pr_body,
            pr_url=pr_url,
            pr_number=pr_number,
            status=pr_status,
            diff_files=diff_res.checked_files,
            summary_artifact_path=rel_summary,
            created_at=utc_now_iso(),
        )

        json_file = run_dir / "pr_package.json"
        json_file.write_text(
            json.dumps(package.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        rel_json = str(json_file.relative_to(self.repo_root)).replace("\\", "/")
        package.json_artifact_path = rel_json

        # 9. Update SQLite RunRecord
        if run:
            self.run_repo.update_run(
                run_id=run.run_id,
                status=RunStatus.SUCCESS,
                metadata_update={
                    "pr": package.model_dump(),
                    "delivered_at": utc_now_iso(),
                },
            )

        return package
