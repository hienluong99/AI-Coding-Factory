"""Sequential Orchestrator for selecting, locking, and dispatching ready tasks."""

import os
from pathlib import Path
from typing import List, Optional, Union

from src.beads.client import BeadsClient
from src.beads.exceptions import TaskBlockedError
from src.beads.models import BeadsTask
from src.contracts.parser import parse_task_file
from src.contracts.task import TaskContract
from src.quality.models import QualityGateSummary
from src.quality.runner import QualityGateRunner
from src.storage.models import RunRecord, RunStatus
from src.storage.repository import RunRepository
from src.vcs.git_guard import GitGuard
from src.vcs.models import DiffScopeResult

from .exceptions import OrchestratorError, TaskAlreadyClaimedError
from .lock import TaskLockManager
from .models import FactoryStatus, NextActionPlan


class SequentialOrchestrator:
    """Orchestrates sequential task selection, locking, dry-run planning, git guarding, and execution."""

    def __init__(
        self,
        workspace_dir: Optional[Union[str, Path]] = None,
        beads_client: Optional[BeadsClient] = None,
        run_repo: Optional[RunRepository] = None,
        lock_manager: Optional[TaskLockManager] = None,
        git_guard: Optional[GitGuard] = None,
        tasks_dir: Optional[Union[str, Path]] = None,
        db_path: Optional[Union[str, Path]] = None,
    ):
        self.workspace_dir = Path(workspace_dir or os.getenv("BEADS_WORKSPACE", ".")).resolve()
        self.beads = beads_client or BeadsClient(workspace_dir=self.workspace_dir)
        self.tasks_dir = Path(tasks_dir) if tasks_dir else (self.workspace_dir / "tasks")
        self._db_path = Path(db_path or os.getenv("FACTORY_DB_PATH", str(self.workspace_dir / "runs" / "factory.db"))).resolve()
        self._run_repo = run_repo
        self.lock_manager = lock_manager or TaskLockManager(
            lock_dir=self.workspace_dir / "runs" / "locks"
        )
        self.git_guard = git_guard or GitGuard(repo_dir=self.workspace_dir)

    def _get_run_repo(self, read_only_if_missing: bool = False) -> Optional[RunRepository]:
        """Return RunRepository instance. Avoids creating DB file on disk if read_only_if_missing=True."""
        if self._run_repo is not None:
            return self._run_repo
        if read_only_if_missing and not self._db_path.exists():
            return None
        self._run_repo = RunRepository(db_path=self._db_path)
        return self._run_repo

    def load_task_contract(self, task_id: str) -> Optional[TaskContract]:
        """Load and validate the JSON task contract for task_id if present."""
        contract_file = self.tasks_dir / f"{task_id}.json"
        if contract_file.exists():
            return parse_task_file(contract_file)
        return None

    def get_status(self) -> FactoryStatus:
        """Return the current status of the Factory (ready, blocked, claimed, and runs)."""
        ready_tasks = self.beads.get_ready_tasks()
        blocked_tasks = self.beads.get_blocked_tasks()
        active_claims = self.lock_manager.list_claims()

        repo = self._get_run_repo(read_only_if_missing=True)
        recent_runs: List[RunRecord] = []
        active_runs: List[RunRecord] = []
        if repo is not None:
            recent_runs = repo.list_runs(limit=20)
            active_runs = [
                r for r in recent_runs
                if r.status in (RunStatus.PENDING, RunStatus.WAITING_FOR_HUMAN, RunStatus.RUNNING)
            ]

        return FactoryStatus(
            ready_tasks=ready_tasks,
            blocked_tasks=blocked_tasks,
            active_claims=active_claims,
            active_runs=active_runs,
            recent_runs=recent_runs
        )

    def select_next_task(self, target_task_id: Optional[str] = None) -> Optional[BeadsTask]:
        """Select strictly ONE unblocked and unclaimed task ready for execution.

        Raises:
            TaskBlockedError: If target_task_id is specified and is blocked.
            TaskAlreadyClaimedError: If target_task_id is specified and is already claimed.
        """
        if target_task_id:
            # Verify not blocked first
            task = self.beads.ensure_task_ready_for_dispatch(target_task_id)
            # Verify not already claimed
            if self.lock_manager.is_claimed(target_task_id):
                existing = self.lock_manager.get_claim(target_task_id)
                raise TaskAlreadyClaimedError(
                    task_id=target_task_id,
                    owner_id=existing.owner_id if existing else None,
                    pid=existing.pid if existing else None,
                    claimed_at=existing.claimed_at if existing else None,
                )
            return task

        ready_tasks = self.beads.get_ready_tasks()
        blocked_ids = {b.id for b in self.beads.get_blocked_tasks()}

        for candidate in ready_tasks:
            if candidate.id in blocked_ids or candidate.is_blocked:
                continue
            if self.lock_manager.is_claimed(candidate.id):
                continue
            # Strictly select only the first valid ready & unclaimed task
            return candidate

        return None

    def _build_action_plan(
        self,
        task: BeadsTask,
        contract: Optional[TaskContract],
        dry_run: bool
    ) -> NextActionPlan:
        """Build the NextActionPlan describing what will be (or was) executed."""
        branch_pattern = contract.constraints.branch_pattern if (contract and contract.constraints.branch_pattern) else "task/{id}"
        target_branch = branch_pattern.replace("{id}", task.id)
        in_scope = contract.scope.in_scope if contract else ["src/*", "tests/*"]
        out_of_scope = contract.scope.out_of_scope if contract else [".env", ".env.*", "*.pem", "*.key"]
        test_commands = contract.test_commands if contract else ["pytest"]

        planned_actions = [
            f"1. [Guard] Kiểm tra working tree sạch (bảo vệ, không ghi đè thay đổi chưa commit của người dùng)",
            f"2. [Lock] Khóa và claim độc quyền task '{task.id}' (tránh tiến trình khác xử lý trùng)",
            f"3. [Branch] Tạo hoặc xác nhận chuyển sang nhánh riêng '{target_branch}'",
            f"4. [RunRecord] Khởi tạo bản ghi RunRecord trong SQLite cho task '{task.id}'",
            f"5. [Agent] Giao task '{task.id}' ({task.title}) cho AI Coding Agent trong phạm vi: {', '.join(in_scope)}",
            f"6. [DiffGuard] Kiểm tra toàn bộ diff đảm bảo không vi phạm ngoài phạm vi (out-of-scope)",
            f"7. [QualityGate] Chạy kiểm thử nghiệm thu: {', '.join(test_commands)}",
        ]

        mode_prefix = "[DRY-RUN] Dự kiến" if dry_run else "[EXECUTED] Đã claim và khởi tạo"
        message = f"{mode_prefix} xử lý tuần tự 1 task sẵn sàng: {task.id} — {task.title}"

        return NextActionPlan(
            has_next=True,
            dry_run=dry_run,
            claimed=False,
            task_id=task.id,
            title=task.title,
            run_id=None,
            target_branch=target_branch,
            in_scope=in_scope,
            out_of_scope=out_of_scope,
            test_commands=test_commands,
            planned_actions=planned_actions,
            message=message,
        )

    def validate_task_diff(
        self,
        task_id: str,
        base_ref: Optional[str] = "main"
    ) -> DiffScopeResult:
        """Strictly validate changed files against task contract in_scope and out_of_scope.
        
        Raises:
            OutOfScopeDiffError: If diff touches forbidden files or files outside in-scope.
        """
        contract = self.load_task_contract(task_id)
        in_scope = contract.scope.in_scope if contract else ["src/*", "tests/*"]
        out_of_scope = contract.scope.out_of_scope if contract else [".env", ".env.*", "*.pem", "*.key"]

        return self.git_guard.ensure_diff_in_scope(
            in_scope=in_scope,
            out_of_scope=out_of_scope,
            base_ref=base_ref,
            task_id=task_id
        )

    def run_quality_gates(
        self,
        task_id: str,
        run_id: Optional[str] = None,
        timeout: Optional[int] = None,
        record_in_db: bool = True,
    ) -> QualityGateSummary:
        """Execute automated quality gate test commands for a task and record the attempt."""
        contract = self.load_task_contract(task_id)
        if not contract:
            raise OrchestratorError(f"Không tìm thấy task contract cho task '{task_id}'.")
        runner = QualityGateRunner(
            repo_root=self.workspace_dir,
            run_repo=self._get_run_repo(),
            runs_dir=self.workspace_dir / "runs",
        )
        return runner.run_quality_gate(
            task_contract=contract,
            run_id=run_id,
            timeout_per_command=timeout,
            record_in_db=record_in_db,
        )

    def next_task(
        self,
        dry_run: bool = False,
        task_id: Optional[str] = None,
        owner_id: Optional[str] = None,
    ) -> NextActionPlan:
        """Select the next ready task and either preview planned actions (dry-run) or claim it.

        When dry_run=True:
            - Does NOT claim or lock the task.
            - Does NOT write to SQLite or modify any code/files.
            - Does NOT switch branches.
            - Can be executed repeatedly without side effects.

        When dry_run=False:
            - Checks working tree cleanliness (fails safely if dirty, preserving user changes).
            - Atomically locks the task via TaskLockManager.
            - Creates/switches to task branch.
            - Creates a RunRecord in SQLite.
        """
        selected = self.select_next_task(target_task_id=task_id)
        if selected is None:
            return NextActionPlan(
                has_next=False,
                dry_run=dry_run,
                claimed=False,
                message="Không có task nào sẵn sàng (unblocked & unclaimed) để thực thi."
            )

        contract = self.load_task_contract(selected.id)
        plan = self._build_action_plan(selected, contract, dry_run=dry_run)

        if dry_run:
            # Strictly no state mutation, no lock creation, no DB record creation, no branch checkout
            return plan

        # STEP 1: Guard working tree cleanliness BEFORE any lock or branch change
        self.git_guard.ensure_clean_working_tree()

        # STEP 2: Atomically acquire exclusive task lock
        claim = self.lock_manager.claim_task(
            task_id=selected.id,
            owner_id=owner_id,
        )

        try:
            # STEP 3: Ensure task branch exists and checkout safely
            branch_pattern = contract.constraints.branch_pattern if (contract and contract.constraints.branch_pattern) else "task/{id}"
            actual_branch = self.git_guard.ensure_task_branch(selected.id, branch_pattern=branch_pattern)
            plan.target_branch = actual_branch

            # STEP 4: Create SQLite run record
            repo = self._get_run_repo(read_only_if_missing=False)
            assert repo is not None
            max_attempts = contract.constraints.max_attempts if (contract and hasattr(contract.constraints, "max_attempts")) else (
                contract.constraints.max_fix_attempts if contract else 3
            )
            run_record = repo.create_run(
                task_id=selected.id,
                max_attempts=max_attempts,
                metadata={
                    "title": selected.title,
                    "target_branch": actual_branch,
                    "claimed_by": claim.owner_id,
                    "pid": claim.pid,
                }
            )
            plan.claimed = True
            plan.run_id = run_record.run_id
            return plan
        except Exception:
            # Rollback lock if operation failed
            self.lock_manager.release_task(selected.id, owner_id=claim.owner_id)
            raise
