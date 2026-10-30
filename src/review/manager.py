"""Review Manager for human approval, auto-merge, and task closing (ACF-11)."""

import json
import os
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple, Union

from src.beads.client import BeadsClient
from src.contracts.parser import parse_task_file
from src.contracts.task import TaskContract, TaskStatus
from src.correction.models import FailureDiagnosis
from src.correction.prompt_builder import build_fix_prompt
from src.orchestrator.lock import TaskLockManager
from src.storage.models import RunRecord, RunStatus
from src.storage.repository import RunRepository

from .models import ReviewAction, ReviewDecision, ReviewSummary, utc_now_iso


class ReviewManager:
    """
    Coordinates Human-in-the-loop review workflow:
    - Provides structured summaries of PRs, diffs, and test results.
    - Automates branch merging, lock releasing, and Beads task closing upon approval.
    - Routes reviewer feedback into the Self-Correction engine on change requests.
    """

    def __init__(
        self,
        repo_root: Optional[Union[str, Path]] = None,
        run_repo: Optional[RunRepository] = None,
        beads_client: Optional[BeadsClient] = None,
        lock_manager: Optional[TaskLockManager] = None,
        runs_dir: Optional[Union[str, Path]] = None,
        tasks_dir: Optional[Union[str, Path]] = None,
    ) -> None:
        self.repo_root = Path(repo_root) if repo_root else Path.cwd()
        self.runs_dir = Path(runs_dir) if runs_dir else (self.repo_root / "runs")
        self.tasks_dir = Path(tasks_dir) if tasks_dir else (self.repo_root / "tasks")
        self.run_repo = run_repo or RunRepository(db_path=self.runs_dir / "factory.db")
        self.beads = beads_client or BeadsClient(workspace_dir=self.repo_root)
        self.lock_manager = lock_manager or TaskLockManager(lock_dir=self.runs_dir / "locks")

    def _resolve_run_and_contract(self, task_id_or_run_id: str) -> Tuple[RunRecord, TaskContract]:
        """Resolve RunRecord and TaskContract from either run_id or task_id."""
        run = self.run_repo.get_run(task_id_or_run_id, include_attempts=True)
        task_id = ""

        if run:
            task_id = run.task_id
        else:
            # Try finding latest run for this task_id
            task_id = task_id_or_run_id
            runs = self.run_repo.list_runs(task_id=task_id, limit=1)
            if runs:
                run = self.run_repo.get_run(runs[0].run_id, include_attempts=True)

        if not run:
            # Create a placeholder run record if none exists yet
            run = self.run_repo.create_run(task_id=task_id)

        contract_file = self.tasks_dir / f"{task_id}.json"
        if not contract_file.exists():
            raise ValueError(f"Không tìm thấy file contract cho task '{task_id}' tại {contract_file}.")

        contract = parse_task_file(contract_file)
        return run, contract

    def get_review_summary(self, task_id_or_run_id: str) -> ReviewSummary:
        """Fetch and aggregate review data for human inspection."""
        run, contract = self._resolve_run_and_contract(task_id_or_run_id)

        pr_meta = run.metadata.get("pr", {})
        branch = pr_meta.get("branch") or run.metadata.get("target_branch") or f"task/{contract.id}"
        base_branch = pr_meta.get("base_branch") or "main"
        commit_sha = pr_meta.get("commit_sha")
        pr_url = pr_meta.get("pr_url")
        pr_number = pr_meta.get("pr_number")
        pr_status = pr_meta.get("status")
        diff_files = pr_meta.get("diff_files", [])

        # Quality gate verification
        quality_passed = False
        test_summary = "Chưa có kiểm thử"
        log_path = run.log_path

        if run.attempts:
            latest = run.attempts[-1]
            quality_passed = (latest.status == RunStatus.SUCCESS)
            if latest.test_results:
                passed_cnt = sum(1 for t in latest.test_results if t.passed)
                test_summary = f"{passed_cnt}/{len(latest.test_results)} passed"
            log_path = latest.log_path or log_path

        remaining = max(0, run.max_attempts - run.attempt_count)

        return ReviewSummary(
            task_id=contract.id,
            task_title=contract.objective,
            run_id=run.run_id,
            run_status=run.status.value,
            branch=branch,
            base_branch=base_branch,
            commit_sha=commit_sha,
            pr_url=pr_url,
            pr_number=pr_number,
            pr_status=pr_status,
            diff_files=diff_files,
            quality_gate_passed=quality_passed,
            test_summary=test_summary,
            log_path=log_path,
            acceptance_criteria=contract.acceptance_criteria,
            in_scope=contract.scope.in_scope,
            attempt_count=run.attempt_count,
            max_attempts=run.max_attempts,
            remaining_attempts=remaining,
        )

    def _execute_git_merge(self, branch: str, base_branch: str, task_contract: TaskContract) -> str:
        """Merge task branch into base_branch with merge commit."""
        # 1. Checkout base branch
        subprocess.run(
            ["git", "checkout", base_branch],
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
            check=True,
        )

        # 2. Perform merge
        merge_msg = f"merge: complete {task_contract.id} ({task_contract.objective.lower()})"
        res = subprocess.run(
            ["git", "merge", "--no-ff", branch, "-m", merge_msg],
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode != 0:
            raise RuntimeError(f"Lỗi khi merge nhánh '{branch}' vào '{base_branch}': {res.stderr}")

        # 3. Get merge commit SHA
        sha_res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
            check=True,
        )
        return sha_res.stdout.strip()

    def approve(
        self,
        task_id_or_run_id: str,
        reviewer: str = "human",
        base_branch: str = "main",
    ) -> ReviewDecision:
        """
        Approve task delivery:
        - Merge task branch into base branch.
        - Close task in Beads issue tracker.
        - Release task lock.
        - Update RunRecord and contract status.
        """
        run, contract = self._resolve_run_and_contract(task_id_or_run_id)

        # 1. Check Quality Gate requirement
        summary = self.get_review_summary(task_id_or_run_id)
        if not summary.quality_gate_passed and contract.status != TaskStatus.COMPLETED:
            raise ValueError(
                f"Không thể phê duyệt task '{contract.id}': Quality Gate chưa đạt (100% PASS). "
                "Vui lòng chạy kiểm thử trước khi phê duyệt."
            )

        branch = summary.branch

        # 2. Merge git branch
        merge_sha = self._execute_git_merge(branch, base_branch, contract)

        # 3. Close task in Beads
        task_closed = self.beads.close_task(
            contract.id,
            reason=f"Phê duyệt & merge bởi {reviewer} (commit {merge_sha[:8]})",
        )

        # 4. Release task lock
        lock_released = self.lock_manager.release_task(contract.id)

        # 5. Update contract file on disk
        contract.status = TaskStatus.COMPLETED
        contract_file = self.tasks_dir / f"{contract.id}.json"
        contract_file.write_text(
            json.dumps(contract.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # 6. Update SQLite RunRecord
        now_iso = utc_now_iso()
        self.run_repo.update_run(
            run_id=run.run_id,
            status=RunStatus.SUCCESS,
            metadata_update={
                "review": {
                    "action": "approve",
                    "reviewer": reviewer,
                    "merged_at": now_iso,
                    "merge_commit_sha": merge_sha,
                    "base_branch": base_branch,
                    "task_closed": task_closed,
                    "lock_released": lock_released,
                }
            },
        )

        msg = (
            f"Phê duyệt thành công task '{contract.id}'! "
            f"Đã merge nhánh '{branch}' vào '{base_branch}' ({merge_sha[:8]}), "
            f"đóng task Beads ({'OK' if task_closed else 'Bỏ qua'}), "
            f"và giải phóng khóa độc quyền."
        )

        return ReviewDecision(
            task_id=contract.id,
            run_id=run.run_id,
            action=ReviewAction.APPROVE,
            reviewer=reviewer,
            merged=True,
            merge_commit_sha=merge_sha,
            task_closed=task_closed,
            lock_released=lock_released,
            message=msg,
        )

    def request_changes(
        self,
        task_id_or_run_id: str,
        feedback: str,
        reviewer: str = "human",
    ) -> ReviewDecision:
        """
        Request changes with reviewer feedback:
        - Injects feedback into self-correction engine to build a new fix prompt.
        - Transitions to BLOCKED if max attempts reached.
        """
        if not feedback or not feedback.strip():
            raise ValueError("Phải cung cấp nội dung phản hồi (feedback) khi yêu cầu sửa đổi.")

        run, contract = self._resolve_run_and_contract(task_id_or_run_id)
        attempt_count = run.attempt_count
        max_attempts = run.max_attempts
        remaining = max(0, max_attempts - attempt_count)

        run_dir = self.runs_dir / run.run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        if attempt_count < max_attempts:
            # Create synthetic failure diagnosis from feedback
            diag = FailureDiagnosis(
                command="human_review",
                exit_code=1,
                error_summary=f"Reviewer yêu cầu sửa: {feedback.strip()}",
                error_type="HumanReviewChangeRequest",
                root_cause_snippet=f"Phản hồi từ Reviewer ({reviewer}):\n{feedback.strip()}",
                suggested_action="Xem xét phản hồi của reviewer, điều chỉnh mã nguồn và kiểm thử lại.",
            )

            prompt_content = build_fix_prompt(
                task_contract=contract,
                run_record=run,
                failures=[diag],
                target_branch=f"task/{contract.id}",
            )

            prompt_file = run_dir / f"fix_prompt_attempt_{attempt_count}_review.md"
            prompt_file.write_text(prompt_content, encoding="utf-8")
            rel_path = str(prompt_file.relative_to(self.repo_root)).replace("\\", "/")

            self.run_repo.update_run(
                run_id=run.run_id,
                status=RunStatus.WAITING_FOR_HUMAN,
                metadata_update={
                    "last_feedback": feedback.strip(),
                    "last_fix_prompt": rel_path,
                    "reviewer": reviewer,
                },
            )

            msg = (
                f"Đã ghi nhận yêu cầu sửa đổi từ {reviewer}. "
                f"Prompt hướng dẫn sửa lỗi: '{rel_path}'. Còn {remaining} lượt sửa."
            )

            return ReviewDecision(
                task_id=contract.id,
                run_id=run.run_id,
                action=ReviewAction.REQUEST_CHANGES,
                reviewer=reviewer,
                feedback=feedback.strip(),
                next_fix_prompt_path=rel_path,
                message=msg,
            )
        else:
            # Exhausted fix attempts
            self.run_repo.update_run(
                run_id=run.run_id,
                status=RunStatus.BLOCKED,
                error_message=f"Reviewer yêu cầu sửa nhưng đã đạt ngưỡng tối đa {max_attempts} lần thử.",
                metadata_update={"last_feedback": feedback.strip()},
            )
            msg = f"Đã đạt ngưỡng tối đa {max_attempts} lần thử. Task '{contract.id}' chuyển sang trạng thái BLOCKED."

            return ReviewDecision(
                task_id=contract.id,
                run_id=run.run_id,
                action=ReviewAction.REQUEST_CHANGES,
                reviewer=reviewer,
                feedback=feedback.strip(),
                message=msg,
            )
