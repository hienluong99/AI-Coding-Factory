"""Self-Correction Engine for automated test failure analysis and blocked handling."""

import json
import os
from pathlib import Path
from typing import List, Optional, Union

from src.contracts.parser import parse_task_file
from src.contracts.task import TaskConstraints, TaskContract, TaskScope
from src.quality.models import QualityGateStatus, QualityGateSummary
from src.quality.runner import QualityGateRunner
from src.storage.models import RunRecord, RunStatus
from src.storage.repository import RunRepository

from .diagnostics import analyze_failures, diagnose_command_failure
from .models import (
    CorrectionAction,
    CorrectionDecision,
    FailureDiagnosis,
    utc_now_iso,
)
from .prompt_builder import (
    build_blocked_diagnostic_report,
    build_fix_prompt,
    format_blocked_markdown_report,
)


class SelfCorrectionEngine:
    """
    Manages the self-correction lifecycle:
    - Analyzes test failures from Quality Gates.
    - Emits structured fix prompts if attempts < max_attempts.
    - Transitions RunRecord to BLOCKED and creates diagnostics if attempts >= max_attempts.
    """

    def __init__(
        self,
        repo_root: Optional[Union[str, Path]] = None,
        run_repo: Optional[RunRepository] = None,
        runs_dir: Optional[Union[str, Path]] = None,
        tasks_dir: Optional[Union[str, Path]] = None,
    ) -> None:
        self.repo_root = Path(repo_root) if repo_root else Path.cwd()
        self.runs_dir = Path(runs_dir) if runs_dir else (self.repo_root / "runs")
        self.tasks_dir = Path(tasks_dir) if tasks_dir else (self.repo_root / "tasks")
        self.run_repo = run_repo or RunRepository(
            db_path=self.runs_dir / "factory.db"
        )

    def load_contract(self, task_id: str) -> TaskContract:
        """Load TaskContract from disk or synthesize a fallback contract."""
        contract_file = self.tasks_dir / f"{task_id}.json"
        if contract_file.exists():
            return parse_task_file(contract_file)

        return TaskContract(
            id=task_id,
            objective=f"Task {task_id}",
            scope=TaskScope(in_scope=["src/*", "tests/*"]),
            constraints=TaskConstraints(max_fix_attempts=3),
            acceptance_criteria=["Toàn bộ bài kiểm thử tự động vượt qua (PASS)"],
            test_commands=["pytest"],
        )

    def evaluate_run(
        self,
        run_id: str,
        quality_summary: Optional[QualityGateSummary] = None,
    ) -> CorrectionDecision:
        """
        Evaluate a run after a Quality Gate execution.
        Determines whether to retry fixing or halt as BLOCKED.
        """
        run = self.run_repo.get_run(run_id, include_attempts=True)
        if not run:
            raise ValueError(f"Không tìm thấy Run ID '{run_id}' trong cơ sở dữ liệu.")

        contract = self.load_contract(run.task_id)
        run_dir = self.runs_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        target_branch = run.metadata.get("target_branch") or f"task/{run.task_id}"

        # 1. Check if Quality Gate passed
        if quality_summary and quality_summary.passed:
            return CorrectionDecision(
                run_id=run_id,
                task_id=run.task_id,
                attempt_number=run.attempt_count,
                max_attempts=run.max_attempts,
                remaining_attempts=max(0, run.max_attempts - run.attempt_count),
                action=CorrectionAction.NOOP_SUCCESS,
                passed=True,
                failures=[],
                message=f"Quality Gates cho run '{run_id}' đã PASS 100%. Không cần sửa lỗi.",
            )

        # 2. Extract failure diagnoses
        failures: List[FailureDiagnosis] = []
        if quality_summary:
            failures = analyze_failures(quality_summary)
        elif run.attempts:
            # Fallback analysis from latest attempt records
            latest_attempt = run.attempts[-1]
            for test_rec in latest_attempt.test_results:
                if not test_rec.passed:
                    from src.quality.models import SingleCommandResult
                    cmd_res = SingleCommandResult(
                        command=test_rec.command,
                        exit_code=test_rec.exit_code,
                        passed=test_rec.passed,
                        summary=test_rec.summary,
                        stdout=test_rec.stdout or "",
                        stderr=test_rec.stderr or "",
                    )
                    failures.append(diagnose_command_failure(cmd_res))

        # 3. Determine action based on MAX_FIX_ATTEMPTS
        attempt_count = run.attempt_count
        max_attempts = run.max_attempts
        remaining = max(0, max_attempts - attempt_count)

        if attempt_count < max_attempts:
            # ACTION: RETRY_FIX
            fix_prompt_text = build_fix_prompt(
                task_contract=contract,
                run_record=run,
                failures=failures,
                target_branch=target_branch,
            )

            prompt_filename = f"fix_prompt_attempt_{attempt_count}.md"
            prompt_file = run_dir / prompt_filename
            prompt_file.write_text(fix_prompt_text, encoding="utf-8")

            rel_prompt_path = str(prompt_file.relative_to(self.repo_root)).replace("\\", "/")

            # Update run metadata with fix prompt path
            self.run_repo.update_run(
                run_id=run_id,
                metadata_update={
                    "last_fix_prompt": rel_prompt_path,
                    "remaining_attempts": remaining,
                },
            )

            msg = (
                f"[RETRY_FIX] Phát hiện {len(failures)} lỗi kiểm thử (Lần thử #{attempt_count}/{max_attempts}). "
                f"Đã tạo prompt tự sửa lỗi tại '{rel_prompt_path}'. Còn {remaining} lượt sửa."
            )

            return CorrectionDecision(
                run_id=run_id,
                task_id=run.task_id,
                attempt_number=attempt_count,
                max_attempts=max_attempts,
                remaining_attempts=remaining,
                action=CorrectionAction.RETRY_FIX,
                passed=False,
                failures=failures,
                fix_prompt_path=rel_prompt_path,
                message=msg,
            )

        else:
            # ACTION: HALT_BLOCKED
            diag_report = build_blocked_diagnostic_report(
                task_contract=contract,
                run_record=run,
                failures=failures,
            )

            # Write JSON report
            json_file = run_dir / "blocked_diagnostic.json"
            json_file.write_text(
                json.dumps(diag_report.model_dump(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            rel_json_path = str(json_file.relative_to(self.repo_root)).replace("\\", "/")

            # Write Markdown report
            md_content = format_blocked_markdown_report(diag_report, contract)
            md_file = run_dir / "blocked_diagnostic.md"
            md_file.write_text(md_content, encoding="utf-8")
            rel_md_path = str(md_file.relative_to(self.repo_root)).replace("\\", "/")

            # Transition run to BLOCKED in SQLite
            block_err = (
                f"Đạt giới hạn tối đa {max_attempts} lần sửa lỗi. "
                f"Chẩn đoán sự cố: '{rel_md_path}'."
            )
            self.run_repo.update_run(
                run_id=run_id,
                status=RunStatus.BLOCKED,
                error_message=block_err,
                metadata_update={
                    "blocked_diagnostic_json": rel_json_path,
                    "blocked_diagnostic_md": rel_md_path,
                },
            )

            msg = (
                f"[HALT_BLOCKED] Đã đạt giới hạn tối đa {max_attempts} lần thử. "
                f"Run '{run_id}' đã được đánh dấu BLOCKED. "
                f"Báo cáo chẩn đoán sự cố: '{rel_md_path}'."
            )

            return CorrectionDecision(
                run_id=run_id,
                task_id=run.task_id,
                attempt_number=attempt_count,
                max_attempts=max_attempts,
                remaining_attempts=0,
                action=CorrectionAction.HALT_BLOCKED,
                passed=False,
                failures=failures,
                diagnostic_report_path=rel_json_path,
                message=msg,
            )

    def run_quality_and_self_correct(
        self,
        task_id: str,
        run_id: Optional[str] = None,
        timeout: Optional[int] = None,
    ) -> CorrectionDecision:
        """
        Execute Quality Gates and immediately evaluate self-correction logic.
        """
        contract = self.load_contract(task_id)

        # Ensure run exists in SQLite
        actual_run_id = run_id
        if not actual_run_id:
            run = self.run_repo.create_run(
                task_id=task_id,
                max_attempts=contract.constraints.max_fix_attempts,
                metadata={"title": contract.objective},
            )
            actual_run_id = run.run_id

        runner = QualityGateRunner(
            repo_root=self.repo_root,
            run_repo=self.run_repo,
            runs_dir=self.runs_dir,
        )

        quality_summary = runner.run_quality_gate(
            task_contract=contract,
            run_id=actual_run_id,
            timeout_per_command=timeout,
            record_in_db=True,
        )

        return self.evaluate_run(actual_run_id, quality_summary=quality_summary)
