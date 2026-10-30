"""Auto-Pilot Orchestrator Loop for autonomous end-to-end task execution in AI Coding Factory."""

import os
import time
from pathlib import Path
from typing import Any, Callable, List, Optional, Union

from src.handoff.manager import AntigravityHandoffManager
from src.orchestrator.engine import SequentialOrchestrator
from src.orchestrator.exceptions import OrchestratorError, TaskAlreadyClaimedError
from src.storage.models import RunStatus

from .models import AutoPilotConfig, AutoPilotResult, TaskStepResult


class AutoPilotEngine:
    """Coordinates fully automated or supervised sequential execution of tasks in the DAG."""

    def __init__(
        self,
        workspace_dir: Optional[Union[str, Path]] = None,
        orchestrator: Optional[SequentialOrchestrator] = None,
        handoff_manager: Optional[AntigravityHandoffManager] = None,
        agent_callback: Optional[Callable[[str, str, Any], bool]] = None,
    ):
        self.workspace_dir = Path(workspace_dir or os.getenv("BEADS_WORKSPACE", ".")).resolve()
        self.orchestrator = orchestrator or SequentialOrchestrator(workspace_dir=self.workspace_dir)
        self.handoff_manager = handoff_manager or AntigravityHandoffManager(
            orchestrator=self.orchestrator
        )
        self.agent_callback = agent_callback

    def run(self, config: Optional[AutoPilotConfig] = None) -> AutoPilotResult:
        """Run the Auto-Pilot loop over ready tasks until exhaustion or limit reached."""
        cfg = config or AutoPilotConfig()
        start_time = time.time()

        # Handle Dry-Run Mode
        if cfg.dry_run:
            return self._run_dry_run(cfg, start_time)

        task_results: List[TaskStepResult] = []
        successful_tasks: List[str] = []
        blocked_tasks: List[str] = []
        pending_review_tasks: List[str] = []
        skipped_tasks: List[str] = []
        total_processed = 0

        # Main Auto-Pilot execution loop
        while True:
            if cfg.max_tasks is not None and total_processed >= cfg.max_tasks:
                break

            # 1. Determine next candidate task
            next_task_id: Optional[str] = None
            if cfg.target_task_ids:
                if total_processed < len(cfg.target_task_ids):
                    next_task_id = cfg.target_task_ids[total_processed]
                else:
                    break
            else:
                candidate = self.orchestrator.select_next_task()
                if candidate is None:
                    break
                next_task_id = candidate.id

            total_processed += 1

            # 2. Execute single task
            step_result = self.run_single_task(next_task_id, cfg)
            task_results.append(step_result)

            if step_result.status == "success":
                successful_tasks.append(step_result.task_id)
            elif step_result.status == "blocked":
                blocked_tasks.append(step_result.task_id)
                if cfg.stop_on_blocked:
                    return AutoPilotResult(
                        total_tasks_processed=total_processed,
                        successful_tasks=successful_tasks,
                        blocked_tasks=blocked_tasks,
                        pending_review_tasks=pending_review_tasks,
                        skipped_tasks=skipped_tasks,
                        task_results=task_results,
                        duration_seconds=round(time.time() - start_time, 2),
                        status="stopped_on_blocked",
                        message=f"Auto-Pilot dừng lại an toàn do task '{step_result.task_id}' bị BLOCKED.",
                    )
            elif step_result.status == "waiting_for_review":
                pending_review_tasks.append(step_result.task_id)
            elif step_result.status == "skipped":
                skipped_tasks.append(step_result.task_id)

        duration = round(time.time() - start_time, 2)
        overall_status = "completed" if total_processed > 0 else "no_ready_tasks"
        msg = (
            f"Auto-Pilot hoàn thành {len(successful_tasks)}/{total_processed} task thành công."
            if total_processed > 0
            else "Không có task nào sẵn sàng để thực thi."
        )

        return AutoPilotResult(
            total_tasks_processed=total_processed,
            successful_tasks=successful_tasks,
            blocked_tasks=blocked_tasks,
            pending_review_tasks=pending_review_tasks,
            skipped_tasks=skipped_tasks,
            task_results=task_results,
            duration_seconds=duration,
            status=overall_status,
            message=msg,
        )

    def _run_dry_run(self, cfg: AutoPilotConfig, start_time: float) -> AutoPilotResult:
        """Simulate execution sequence without mutating git, beads, or databases."""
        task_results: List[TaskStepResult] = []
        ready_tasks = self.orchestrator.beads.get_ready_tasks()
        limit = cfg.max_tasks or len(ready_tasks)

        for task in ready_tasks[:limit]:
            contract = self.orchestrator.load_task_contract(task.id)
            branch = contract.constraints.branch_pattern.replace("{id}", task.id) if (contract and contract.constraints.branch_pattern) else f"task/{task.id}"
            task_results.append(
                TaskStepResult(
                    task_id=task.id,
                    title=task.title,
                    branch=branch,
                    status="dry_run",
                    attempts=0,
                    quality_passed=True,
                    pr_created=True,
                    merged=cfg.auto_approve_review,
                    closed_in_beads=cfg.auto_approve_review,
                )
            )

        return AutoPilotResult(
            total_tasks_processed=len(task_results),
            successful_tasks=[t.task_id for t in task_results],
            task_results=task_results,
            duration_seconds=round(time.time() - start_time, 2),
            status="dry_run",
            message=f"[DRY-RUN] Đã mô phỏng tuần tự {len(task_results)} task sẵn sàng trong DAG.",
        )

    def run_single_task(self, task_id: str, config: Optional[AutoPilotConfig] = None) -> TaskStepResult:
        """Run the full lifecycle for a single task end-to-end."""
        cfg = config or AutoPilotConfig()
        start = time.time()
        step = TaskStepResult(task_id=task_id)

        try:
            # 1. Check Working Tree Cleanliness
            if not self.orchestrator.git_guard.is_working_tree_clean():
                step.status = "error"
                step.error_message = "Working tree đang có thay đổi chưa commit. Factory dừng an toàn."
                step.duration_seconds = round(time.time() - start, 2)
                return step

            # 2. Select & Claim Task (creates lock, branch, and SQLite RunRecord)
            plan = self.orchestrator.next_task(dry_run=False, task_id=task_id, owner_id="autopilot")
            step.run_id = plan.run_id
            step.title = plan.title
            step.branch = plan.target_branch

            # 3. Handoff & Confirmation
            handoff_pack = None
            if self.handoff_manager:
                try:
                    try:
                        handoff_pack = self.handoff_manager.prepare_handoff(task_id=task_id, run_id=step.run_id)
                    except TypeError:
                        handoff_pack = self.handoff_manager.prepare_handoff(task_id=task_id)
                except Exception:
                    handoff_pack = None

                if cfg.auto_approve_handoff:
                    try:
                        self.handoff_manager.confirm_handoff(
                            run_id=step.run_id,
                            approved=True,
                            notes="Auto-Pilot automated dispatch"
                        )
                    except Exception:
                        pass

            # 4. Optional Agent Callback for Code Implementation (if registered)
            if self.agent_callback and step.run_id:
                try:
                    self.agent_callback(task_id, step.run_id, handoff_pack)
                except Exception as cb_err:
                    step.error_message = f"Agent callback error: {cb_err}"

            # 5. Quality Gate Execution
            summary = self.orchestrator.run_quality_gates(
                task_id=task_id,
                run_id=step.run_id,
                timeout=cfg.timeout_per_command,
                record_in_db=True
            )
            step.attempts = getattr(summary, "attempt_number", None) or len(getattr(summary, "command_results", [])) or 1
            step.quality_passed = getattr(summary, "passed", False)

            # 6. Self-Correction Loop if Quality Gate Failed
            while not step.quality_passed:
                from src.correction.models import CorrectionAction, CorrectionDecision
                decision: CorrectionDecision = self.orchestrator.evaluate_correction(run_id=step.run_id, quality_summary=summary)
                step.attempts = getattr(decision, "attempt_number", 1)

                if getattr(decision, "action", None) == CorrectionAction.HALT_BLOCKED or getattr(decision, "remaining_attempts", 1) <= 0:
                    # Release lock and record blocked state
                    self.orchestrator.lock_manager.release_task(task_id, owner_id="autopilot")
                    step.status = "blocked"
                    step.error_message = f"Vượt quá số lần sửa tối đa. Task bị BLOCKED: {decision.message}"
                    step.duration_seconds = round(time.time() - start, 2)
                    return step

                # If an agent callback is registered, trigger fix
                if self.agent_callback and step.run_id:
                    self.agent_callback(task_id, step.run_id, decision)

                # Re-run Quality Gate
                summary = self.orchestrator.run_quality_gates(
                    task_id=task_id,
                    run_id=step.run_id,
                    timeout=cfg.timeout_per_command,
                    record_in_db=True
                )
                step.quality_passed = getattr(summary, "passed", False)
                if not self.agent_callback and not step.quality_passed:
                    # Avoid endless loop if no callback exists to modify code
                    # Let correction engine increment until exhausted
                    continue

            # 7. Delivery & Pull Request Packaging
            pr_res = self.orchestrator.deliver_task_pr(
                task_id=task_id,
                run_id=step.run_id,
                base_branch=cfg.base_branch,
                push_to_remote=cfg.push_to_remote,
                skip_remote_api=cfg.skip_remote_api
            )
            step.pr_created = bool(getattr(pr_res, "pr_url", None) or getattr(pr_res, "commit_sha", None) or str(getattr(pr_res, "status", "")) != "failed")
            step.pr_url = getattr(pr_res, "pr_url", None)

            # 8. Review & Auto-Merge
            if cfg.auto_approve_review:
                review_res = self.orchestrator.review_task(
                    task_id=task_id,
                    approve=True,
                    reviewer="autopilot",
                    base_branch=cfg.base_branch
                )
                step.merged = review_res.merged
                step.closed_in_beads = review_res.task_closed
                step.status = "success"
            else:
                step.status = "waiting_for_review"

        except Exception as e:
            step.status = "error"
            step.error_message = str(e)
            # Ensure lock is released on unexpected failure
            try:
                self.orchestrator.lock_manager.release_task(task_id, owner_id="autopilot")
            except Exception:
                pass

        step.duration_seconds = round(time.time() - start, 2)
        return step
