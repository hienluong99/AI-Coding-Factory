"""Unit tests for Automated Quality Gates Runner (ACF-08)."""

import json
import os
import sys
import tempfile
from pathlib import Path
import pytest

from src.contracts.task import TaskConstraints, TaskContract, TaskScope, TaskStatus
from src.quality.models import QualityGateStatus, SingleCommandResult
from src.quality.parser import (
    parse_command_summary,
    strip_ansi,
    to_test_result_record,
)
from src.quality.runner import QualityGateRunner
from src.storage.models import RunStatus
from src.storage.repository import RunRepository
from src.orchestrator.cli import main as cli_main


def test_strip_ansi():
    raw = "\x1b[32m44 passed\x1b[0m, \x1b[31m1 failed\x1b[0m"
    clean = strip_ansi(raw)
    assert clean == "44 passed, 1 failed"


def test_parse_command_summary_pytest():
    stdout_pass = "============================= 44 passed in 19.65s ============================="
    summary = parse_command_summary("pytest tests/", stdout_pass, "", 0)
    assert "44 passed in 19.65s" in summary

    stdout_fail = "========================= 2 failed, 42 passed in 10.50s ========================="
    summary_fail = parse_command_summary("pytest", stdout_fail, "", 1)
    assert "2 failed, 42 passed in 10.50s" in summary_fail

    stdout_none = "collected 0 items\n=============== no tests ran in 0.01s ==============="
    summary_none = parse_command_summary("pytest tests/empty", stdout_none, "", 5)
    assert "no tests ran" in summary_none.lower()


def test_parse_command_summary_linters_and_fallback():
    # Ruff pass
    summary = parse_command_summary("ruff check .", "All checks passed!\n", "", 0)
    assert "All checks passed" in summary

    # Exit code fallback
    summary_ok = parse_command_summary("echo hello", "hello\n", "", 0)
    assert summary_ok == "PASS (Exit code 0)"

    summary_err = parse_command_summary("mycmd", "", "Error: module not found", 1)
    assert "Error: module not found" in summary_err


def test_to_test_result_record():
    cmd_res = SingleCommandResult(
        command="pytest tests/",
        exit_code=0,
        passed=True,
        duration_seconds=1.5,
        summary="10 passed in 1.5s",
        stdout="ok",
        stderr="",
    )
    record = to_test_result_record(cmd_res)
    assert record.command == "pytest tests/"
    assert record.passed is True
    assert record.exit_code == 0
    assert record.summary == "10 passed in 1.5s"


def test_runner_run_single_command_success(tmp_path):
    runner = QualityGateRunner(repo_root=tmp_path)
    res = runner.run_single_command('python -c "print(\'hello quality\')"', cwd=tmp_path)
    assert res.passed is True
    assert res.exit_code == 0
    assert "hello quality" in res.stdout
    assert res.timed_out is False
    assert res.duration_seconds >= 0.0


def test_runner_run_single_command_failure(tmp_path):
    runner = QualityGateRunner(repo_root=tmp_path)
    res = runner.run_single_command('python -c "import sys; sys.exit(3)"', cwd=tmp_path)
    assert res.passed is False
    assert res.exit_code == 3
    assert res.timed_out is False


def test_runner_forbidden_command_blocked(tmp_path):
    runner = QualityGateRunner(repo_root=tmp_path)
    res = runner.run_single_command(
        'rm -rf /',
        cwd=tmp_path,
        forbidden_commands=["rm -rf /", "git push -f"]
    )
    assert res.passed is False
    assert res.exit_code == 126
    assert "BLOCKED" in res.summary
    assert "forbidden_commands" in res.stderr


def test_runner_timeout(tmp_path):
    runner = QualityGateRunner(repo_root=tmp_path)
    # Command that sleeps for 5 seconds with a 1 second timeout
    res = runner.run_single_command(
        'python -c "import time; time.sleep(5)"',
        cwd=tmp_path,
        timeout=1
    )
    assert res.passed is False
    assert res.timed_out is True
    assert res.exit_code == 124
    assert "TIMEOUT" in res.summary


def test_runner_run_quality_gate_all_pass_and_streaming_log(tmp_path):
    log_dir = tmp_path / "runs"
    runner = QualityGateRunner(repo_root=tmp_path, runs_dir=log_dir)

    contract = TaskContract(
        id="ACF-TEST",
        objective="Verify Quality Gates",
        scope=TaskScope(in_scope=["src/*"]),
        acceptance_criteria=["Tiêu chí 1", "Tiêu chí 2"],
        test_commands=[
            'python -c "print(\'Step 1 passed\')"',
            'python -c "print(\'Step 2 passed\')"',
        ]
    )

    summary = runner.run_quality_gate(
        task_contract=contract,
        run_id="run_test_01",
        attempt_number=1,
        record_in_db=False,
    )

    assert summary.passed is True
    assert summary.status == QualityGateStatus.PASS
    assert summary.total_commands == 2
    assert summary.passed_commands == 2
    assert summary.failed_commands == 0
    assert summary.log_path is not None

    # Verify log file was written and streamed
    log_file = tmp_path / summary.log_path
    assert log_file.exists()
    content = log_file.read_text(encoding="utf-8")
    assert "AI CODING FACTORY — AUTOMATED QUALITY GATES LOG" in content
    assert "Run ID:         run_test_01" in content
    assert "Task ID:        ACF-TEST" in content
    assert "Tiêu chí 1" in content
    assert "Step 1 passed" in content
    assert "Step 2 passed" in content
    assert "QUALITY GATES FINAL RESULT: PASS" in content


def test_runner_run_quality_gate_with_sqlite_record(tmp_path):
    db_file = tmp_path / "factory.db"
    run_repo = RunRepository(db_path=db_file)
    run_dir = tmp_path / "runs"

    # Create run first
    run = run_repo.create_run(task_id="ACF-TEST-DB")
    assert run.attempt_count == 0

    runner = QualityGateRunner(repo_root=tmp_path, run_repo=run_repo, runs_dir=run_dir)

    contract = TaskContract(
        id="ACF-TEST-DB",
        objective="Quality Gate DB persistence",
        scope=TaskScope(in_scope=["src/*"]),
        acceptance_criteria=["Criterion 1"],
        test_commands=['python -c "print(\'Passed test in DB\')"']
    )

    summary = runner.run_quality_gate(
        task_contract=contract,
        run_id=run.run_id,
        record_in_db=True,
    )

    assert summary.passed is True
    assert summary.attempt_number == 1

    # Reload run from SQLite and verify attempt
    updated_run = run_repo.get_run(run.run_id, include_attempts=True)
    assert updated_run is not None
    assert updated_run.attempt_count == 1
    assert updated_run.status == RunStatus.SUCCESS
    assert len(updated_run.attempts) == 1
    assert updated_run.attempts[0].attempt_number == 1
    assert updated_run.attempts[0].status == RunStatus.SUCCESS
    assert len(updated_run.attempts[0].test_results) == 1
    assert updated_run.attempts[0].test_results[0].passed is True
    assert "Passed test in DB" in updated_run.attempts[0].test_results[0].stdout


def test_runner_run_quality_gate_failing_command(tmp_path):
    log_dir = tmp_path / "runs"
    runner = QualityGateRunner(repo_root=tmp_path, runs_dir=log_dir)

    contract = TaskContract(
        id="ACF-FAIL",
        objective="Failing Quality Gate",
        scope=TaskScope(in_scope=["src/*"]),
        acceptance_criteria=["Criterion 1"],
        test_commands=[
            'python -c "import sys; sys.exit(1)"'
        ]
    )

    summary = runner.run_quality_gate(
        task_contract=contract,
        run_id="run_fail_01",
        record_in_db=False,
    )

    assert summary.passed is False
    assert summary.status == QualityGateStatus.FAIL
    assert summary.passed_commands == 0
    assert summary.failed_commands == 1
    assert "Quality gate thất bại tại" in summary.error_message


def test_cli_quality_gates(capsys):
    # Run CLI quality check on ACF-01 contract with --no-record and --json
    exit_code = cli_main(["quality", "ACF-01", "--no-record", "--json"])
    captured = capsys.readouterr()
    assert exit_code == 0
    data = json.loads(captured.out)
    assert data["task_id"] == "ACF-01"
    assert data["status"] == "pass"
    assert data["passed"] is True
