"""Automated Quality Gates Runner for AI Coding Factory."""

import os
import platform
import subprocess
import threading
import time
from pathlib import Path
from typing import List, Optional, TextIO

from src.contracts.task import TaskContract
from src.storage.models import RunStatus
from src.storage.repository import RunRepository

from .models import (
    QualityGateStatus,
    QualityGateSummary,
    SingleCommandResult,
    utc_now_iso,
)
from .parser import parse_command_summary, strip_ansi, to_test_result_record


def _kill_process_tree(proc: subprocess.Popen) -> None:
    """Safely terminate a subprocess and all of its spawned child processes."""
    try:
        if platform.system() == "Windows":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        else:
            proc.kill()
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


class QualityGateRunner:
    """
    Executes automated test and quality gate commands defined in a TaskContract.
    Streams output in real-time to attempt-specific log files, enforces timeouts,
    parses command summaries, and persists results into the SQLite repository.
    """

    def __init__(
        self,
        repo_root: Optional[Path] = None,
        run_repo: Optional[RunRepository] = None,
        runs_dir: Optional[Path] = None,
    ) -> None:
        self.repo_root = Path(repo_root) if repo_root else Path.cwd()
        self.run_repo = run_repo or RunRepository()
        self.runs_dir = Path(runs_dir) if runs_dir else (self.repo_root / "runs")

    def run_single_command(
        self,
        command: str,
        cwd: Optional[Path] = None,
        timeout: int = 300,
        log_file: Optional[TextIO] = None,
        forbidden_commands: Optional[List[str]] = None,
    ) -> SingleCommandResult:
        """
        Execute a single test/linter shell command with real-time output capture.
        """
        started_iso = utc_now_iso()
        start_time = time.time()
        work_dir = cwd or self.repo_root

        # Check safety against forbidden commands
        if forbidden_commands:
            for forbidden in forbidden_commands:
                if forbidden.lower() in command.lower():
                    err_msg = f"Lệnh bị chặn do vi phạm forbidden_commands: '{forbidden}'"
                    if log_file:
                        log_file.write(f"\n[BLOCKED] {err_msg}\n")
                        log_file.flush()
                    return SingleCommandResult(
                        command=command,
                        exit_code=126,
                        passed=False,
                        duration_seconds=0.0,
                        summary="BLOCKED: Forbidden command detected",
                        stdout="",
                        stderr=err_msg,
                        timed_out=False,
                        error_message=err_msg,
                        executed_at=started_iso,
                    )

        stdout_lines: List[str] = []
        stderr_lines: List[str] = []
        log_lock = threading.Lock()

        def stream_pipe(pipe, collector: List[str], prefix: str = "") -> None:
            try:
                for line in iter(pipe.readline, ""):
                    collector.append(line)
                    if log_file:
                        with log_lock:
                            log_file.write(f"{prefix}{line}")
                            log_file.flush()
            except Exception:
                pass
            finally:
                pipe.close()

        timed_out = False
        error_msg: Optional[str] = None
        exit_code = 0

        try:
            # Launch subprocess with pipes for stdout and stderr
            proc = subprocess.Popen(
                command,
                shell=True,
                cwd=str(work_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )

            # Spawn reader threads for concurrent streaming
            t_out = threading.Thread(target=stream_pipe, args=(proc.stdout, stdout_lines, ""))
            t_err = threading.Thread(target=stream_pipe, args=(proc.stderr, stderr_lines, ""))
            t_out.daemon = True
            t_err.daemon = True
            t_out.start()
            t_err.start()

            try:
                proc.wait(timeout=timeout)
                exit_code = proc.returncode
            except subprocess.TimeoutExpired:
                timed_out = True
                _kill_process_tree(proc)
                exit_code = 124  # Standard timeout exit code
                error_msg = f"Lệnh kiểm thử vượt quá thời gian tối đa ({timeout} giây)."
                if log_file:
                    with log_lock:
                        log_file.write(f"\n[TIMEOUT] {error_msg}\n")
                        log_file.flush()

            t_out.join(timeout=2)
            t_err.join(timeout=2)

        except Exception as exc:
            exit_code = 1
            error_msg = f"Ngoại lệ khi thực thi lệnh: {exc}"
            if log_file:
                with log_lock:
                    log_file.write(f"\n[ERROR] {error_msg}\n")
                    log_file.flush()

        duration = round(time.time() - start_time, 2)
        full_stdout = "".join(stdout_lines)
        full_stderr = "".join(stderr_lines)
        passed = (exit_code == 0) and not timed_out

        summary = parse_command_summary(command, full_stdout, full_stderr, exit_code)
        if timed_out:
            summary = f"TIMEOUT (after {timeout}s)"

        return SingleCommandResult(
            command=command,
            exit_code=exit_code,
            passed=passed,
            duration_seconds=duration,
            summary=summary,
            stdout=full_stdout,
            stderr=full_stderr,
            timed_out=timed_out,
            error_message=error_msg,
            executed_at=started_iso,
        )

    def run_quality_gate(
        self,
        task_contract: TaskContract,
        run_id: Optional[str] = None,
        attempt_number: Optional[int] = None,
        timeout_per_command: Optional[int] = None,
        record_in_db: bool = True,
    ) -> QualityGateSummary:
        """
        Execute all test commands specified in a TaskContract and record the attempt.
        """
        started_iso = utc_now_iso()
        overall_start_time = time.time()

        # Determine attempt number and run directory
        resolved_run_id = run_id or f"adhoc_{task_contract.id}_{int(overall_start_time)}"
        resolved_attempt = attempt_number

        if resolved_attempt is None:
            if run_id and self.run_repo:
                try:
                    existing_run = self.run_repo.get_run(run_id, include_attempts=False)
                    if existing_run:
                        resolved_attempt = existing_run.attempt_count + 1
                except Exception:
                    pass
            if resolved_attempt is None:
                resolved_attempt = 1

        # Prepare log directory and file
        run_dir = self.runs_dir / resolved_run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        log_file_path = run_dir / f"attempt_{resolved_attempt}_quality_gate.log"

        timeout = timeout_per_command or task_contract.constraints.timeout_seconds
        forbidden = task_contract.constraints.forbidden_commands

        command_results: List[SingleCommandResult] = []

        with open(log_file_path, "w", encoding="utf-8") as log_f:
            # Write structured header
            log_f.write("=" * 80 + "\n")
            log_f.write("AI CODING FACTORY — AUTOMATED QUALITY GATES LOG\n")
            log_f.write(f"Run ID:         {resolved_run_id}\n")
            log_f.write(f"Task ID:        {task_contract.id}\n")
            log_f.write(f"Objective:      {task_contract.objective}\n")
            log_f.write(f"Attempt:        #{resolved_attempt}\n")
            log_f.write(f"Started At:     {started_iso}\n")
            log_f.write(f"Timeout/cmd:    {timeout}s\n")
            log_f.write(f"Commands Count: {len(task_contract.test_commands)}\n")
            log_f.write("=" * 80 + "\n\n")

            log_f.write("Tiêu chí nghiệm thu (Acceptance Criteria):\n")
            for idx, crit in enumerate(task_contract.acceptance_criteria, 1):
                log_f.write(f"  {idx}. {crit}\n")
            log_f.write("\nDanh sách câu lệnh kiểm thử:\n")
            for idx, cmd in enumerate(task_contract.test_commands, 1):
                log_f.write(f"  {idx}. {cmd}\n")
            log_f.write("\n" + "=" * 80 + "\n\n")
            log_f.flush()

            # Execute commands
            for idx, cmd in enumerate(task_contract.test_commands, 1):
                log_f.write("-" * 80 + "\n")
                log_f.write(f"[CMD {idx}/{len(task_contract.test_commands)}] $ {cmd}\n")
                log_f.write(f"Started: {utc_now_iso()}\n")
                log_f.write("-" * 80 + "\n")
                log_f.flush()

                res = self.run_single_command(
                    command=cmd,
                    cwd=self.repo_root,
                    timeout=timeout,
                    log_file=log_f,
                    forbidden_commands=forbidden,
                )
                command_results.append(res)

                log_f.write("\n" + "-" * 80 + "\n")
                status_str = "PASS" if res.passed else ("TIMEOUT" if res.timed_out else "FAIL")
                log_f.write(
                    f"Result: {status_str} | Exit Code: {res.exit_code} | Duration: {res.duration_seconds}s\n"
                )
                log_f.write(f"Summary: {res.summary}\n")
                log_f.write("=" * 80 + "\n\n")
                log_f.flush()

            # Final Summary calculation
            total_cmds = len(command_results)
            passed_cmds = sum(1 for r in command_results if r.passed)
            failed_cmds = total_cmds - passed_cmds
            all_passed = (failed_cmds == 0) and total_cmds > 0

            overall_status = QualityGateStatus.PASS
            if any(r.timed_out for r in command_results):
                overall_status = QualityGateStatus.TIMEOUT
            elif not all_passed:
                overall_status = QualityGateStatus.FAIL

            finished_iso = utc_now_iso()
            total_duration = round(time.time() - overall_start_time, 2)

            log_f.write("=" * 80 + "\n")
            log_f.write(f"QUALITY GATES FINAL RESULT: {overall_status.value.upper()}\n")
            log_f.write(f"Commands Passed: {passed_cmds}/{total_cmds}\n")
            log_f.write(f"Total Duration:  {total_duration}s\n")
            log_f.write(f"Finished At:     {finished_iso}\n")
            log_f.write("=" * 80 + "\n")
            log_f.flush()

        relative_log_path = str(log_file_path.relative_to(self.repo_root)).replace("\\", "/")

        err_message = None
        if not all_passed:
            failed_details = [
                f"'{r.command}' ({r.summary or 'exit code ' + str(r.exit_code)})"
                for r in command_results
                if not r.passed
            ]
            err_message = f"Quality gate thất bại tại: {'; '.join(failed_details)}"

        summary = QualityGateSummary(
            run_id=resolved_run_id,
            task_id=task_contract.id,
            attempt_number=resolved_attempt,
            status=overall_status,
            passed=all_passed,
            total_commands=total_cmds,
            passed_commands=passed_cmds,
            failed_commands=failed_cmds,
            command_results=command_results,
            log_path=relative_log_path,
            started_at=started_iso,
            finished_at=finished_iso,
            total_duration_seconds=total_duration,
            error_message=err_message,
        )

        # Record attempt to SQLite database if requested and run exists
        if record_in_db and run_id and self.run_repo:
            test_records = [to_test_result_record(r) for r in command_results]
            db_status = RunStatus.SUCCESS if all_passed else RunStatus.FAILED
            try:
                self.run_repo.record_attempt(
                    run_id=run_id,
                    status=db_status,
                    log_path=relative_log_path,
                    test_results=test_records,
                    error_message=err_message,
                    started_at=started_iso,
                    finished_at=finished_iso,
                )
            except Exception as exc:
                # Do not crash the runner if DB record fails, but note error
                if not summary.error_message:
                    summary.error_message = f"Lỗi lưu trữ DB: {exc}"

        return summary
