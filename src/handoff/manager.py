"""Workflow manager for handing off tasks to Antigravity with human confirmation."""

import json
from pathlib import Path
from typing import Callable, Optional, Tuple, Union

from src.beads.models import BeadsTask
from src.contracts.task import TaskConstraints, TaskContract, TaskScope
from src.orchestrator.engine import SequentialOrchestrator
from src.storage.models import RunRecord, RunStatus, utc_now_iso

from .models import HandoffPacket
from .prompt_builder import (
    build_antigravity_prompt,
    format_handoff_display,
    gather_handoff_context,
)


class AntigravityHandoffManager:
    """Coordinates prompt generation, display, human confirmation, and state persistence."""

    def __init__(
        self,
        orchestrator: Optional[SequentialOrchestrator] = None,
        runs_dir: Optional[Union[str, Path]] = None,
    ):
        self.orchestrator = orchestrator or SequentialOrchestrator()
        self.workspace_dir = self.orchestrator.workspace_dir
        self.runs_dir = Path(runs_dir).resolve() if runs_dir else (self.workspace_dir / "runs")

    def _resolve_contract(self, beads_task: BeadsTask) -> TaskContract:
        """Load task contract from disk or synthesize a valid contract from BeadsTask metadata."""
        contract = self.orchestrator.load_task_contract(beads_task.id)
        if contract is not None:
            return contract

        deps = [d.target_id for d in beads_task.dependencies if d.target_id]
        return TaskContract(
            id=beads_task.id,
            objective=beads_task.title,
            description=beads_task.description or beads_task.title,
            scope=TaskScope(
                in_scope=["src/*", "tests/*"],
                out_of_scope=[".env", ".env.*", "*.pem", "*.key"],
            ),
            constraints=TaskConstraints(
                max_fix_attempts=3,
                timeout_seconds=600,
                branch_pattern="task/{id}",
            ),
            acceptance_criteria=[
                f"Hoàn thành yêu cầu của task {beads_task.id}: {beads_task.title}",
                "Toàn bộ bài kiểm thử tự động vượt qua (PASS)",
            ],
            test_commands=["pytest"],
            dependencies=deps,
        )

    def prepare_handoff(
        self,
        task_id: Optional[str] = None,
        owner_id: Optional[str] = None,
    ) -> Tuple[RunRecord, HandoffPacket]:
        """Select ready task, generate Antigravity prompt, and persist `waiting_for_human` state."""
        selected = self.orchestrator.select_next_task(target_task_id=task_id)
        if selected is None:
            raise ValueError("Không có task nào sẵn sàng (unblocked & unclaimed) để bàn giao.")

        contract = self._resolve_contract(selected)
        context = gather_handoff_context(self.workspace_dir, contract)

        # Acquire exclusive lock so no other process claims this task during handoff
        claim = self.orchestrator.lock_manager.claim_task(
            task_id=selected.id,
            owner_id=owner_id,
        )

        try:
            repo = self.orchestrator._get_run_repo(read_only_if_missing=False)
            assert repo is not None

            run_id = repo.generate_run_id(selected.id)
            run_folder = self.runs_dir / run_id
            run_folder.mkdir(parents=True, exist_ok=True)

            prompt_text = build_antigravity_prompt(contract, context, run_id=run_id)
            prompt_file = run_folder / "antigravity_prompt.md"
            packet_file = run_folder / "handoff_packet.json"

            prompt_file.write_text(prompt_text, encoding="utf-8")

            packet = HandoffPacket(
                run_id=run_id,
                task_id=contract.id,
                objective=contract.objective,
                description=contract.description or "",
                target_branch=context.target_branch,
                in_scope=contract.scope.in_scope,
                out_of_scope=contract.scope.out_of_scope,
                acceptance_criteria=contract.acceptance_criteria,
                test_commands=contract.test_commands,
                forbidden_commands=contract.constraints.forbidden_commands,
                max_fix_attempts=contract.constraints.max_fix_attempts,
                context=context,
                prompt_text=prompt_text,
                prompt_path=prompt_file.as_posix(),
                packet_path=packet_file.as_posix(),
                status=RunStatus.WAITING_FOR_HUMAN.value,
                confirmed_by_human=None,
                confirmed_at=None,
            )

            packet_file.write_text(
                json.dumps(packet.model_dump(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            run_record = repo.create_run(
                task_id=contract.id,
                run_id=run_id,
                max_attempts=contract.constraints.max_fix_attempts,
                status=RunStatus.WAITING_FOR_HUMAN,
                log_path=(run_folder / "execution.log").as_posix(),
                metadata={
                    "title": contract.objective,
                    "target_branch": context.target_branch,
                    "claimed_by": claim.owner_id,
                    "pid": claim.pid,
                    "handoff": packet.model_dump(),
                },
            )
            return run_record, packet
        except Exception:
            self.orchestrator.lock_manager.release_task(selected.id, owner_id=claim.owner_id)
            raise

    def record_confirmation(
        self,
        run_id: str,
        approved: bool,
        notes: Optional[str] = None,
    ) -> Tuple[RunRecord, HandoffPacket]:
        """Record the human operator's confirmation decision and persist the handoff outcome."""
        repo = self.orchestrator._get_run_repo(read_only_if_missing=False)
        assert repo is not None

        run_record = repo.get_run(run_id)
        if run_record is None:
            raise ValueError(f"Run ID '{run_id}' không tồn tại.")

        handoff_data = run_record.metadata.get("handoff")
        if not handoff_data:
            raise ValueError(f"Run ID '{run_id}' chưa có thông tin chuẩn bị bàn giao (handoff packet).")

        packet = HandoffPacket.model_validate(handoff_data)
        now = utc_now_iso()
        packet.confirmed_by_human = approved
        packet.confirmed_at = now
        packet.handoff_notes = notes

        if approved:
            packet.status = "handed_off"
            next_run_status = RunStatus.RUNNING
        else:
            packet.status = "rejected"
            next_run_status = RunStatus.WAITING_FOR_HUMAN

        if packet.packet_path:
            Path(packet.packet_path).write_text(
                json.dumps(packet.model_dump(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        updated_run = repo.update_run(
            run_id=run_id,
            status=next_run_status,
            metadata_update={
                "handoff": packet.model_dump(),
                "handoff_result": {
                    "status": packet.status,
                    "confirmed_by_human": approved,
                    "confirmed_at": now,
                    "notes": notes,
                    "prompt_path": packet.prompt_path,
                },
            },
        )
        return updated_run, packet

    def execute_handoff_workflow(
        self,
        task_id: Optional[str] = None,
        owner_id: Optional[str] = None,
        confirm_callback: Optional[Callable[[HandoffPacket], bool]] = None,
        output_fn: Callable[[str], None] = print,
        notes: Optional[str] = None,
    ) -> Tuple[RunRecord, HandoffPacket]:
        """Run the full handoff workflow:
        1. Prepare prompt & context from task contract.
        2. Persist `waiting_for_human` state in SQLite.
        3. Display clear summary (task, branch, acceptance criteria).
        4. Ask human for confirmation before starting (if confirm_callback is supplied).
        5. Persist confirmation result and transition state.
        """
        run_record, packet = self.prepare_handoff(task_id=task_id, owner_id=owner_id)
        summary_text = format_handoff_display(packet)
        output_fn(summary_text)

        if confirm_callback is None:
            # Remain in waiting_for_human until human confirms
            output_fn(
                f"[WAITING_FOR_HUMAN] Đã lưu trạng thái 'waiting_for_human' cho run '{run_record.run_id}'. "
                "Đang chờ người dùng xác nhận để bắt đầu."
            )
            return run_record, packet

        approved = bool(confirm_callback(packet))
        updated_run, updated_packet = self.record_confirmation(
            run_id=run_record.run_id,
            approved=approved,
            notes=notes,
        )
        if approved:
            output_fn(
                f"[HANDED_OFF] Người dùng đã xác nhận! Task '{updated_packet.task_id}' đã được bàn giao "
                f"cho Antigravity (Run: {updated_run.run_id}, Status: {updated_run.status.value})."
            )
        else:
            output_fn(
                f"[CANCELLED] Người dùng từ chối bắt đầu. Run '{updated_run.run_id}' giữ trạng thái "
                f"'{updated_run.status.value}' (handoff: {updated_packet.status})."
            )
        return updated_run, updated_packet
