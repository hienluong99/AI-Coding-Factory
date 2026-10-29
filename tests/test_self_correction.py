"""Unit tests for Self-Correction Engine and Blocked Handling (ACF-09)."""

import json
import os
from pathlib import Path
import pytest

from src.contracts.task import TaskConstraints, TaskContract, TaskScope
from src.correction.diagnostics import (
    diagnose_command_failure,
    extract_related_files,
    extract_root_cause_snippet,
)
from src.correction.engine import SelfCorrectionEngine
from src.correction.models import CorrectionAction, FailureDiagnosis
from src.correction.prompt_builder import (
    build_blocked_diagnostic_report,
    build_fix_prompt,
    format_blocked_markdown_report,
)
from src.quality.models import (
    QualityGateStatus,
    QualityGateSummary,
    SingleCommandResult,
)
from src.storage.models import RunRecord, RunStatus, TestResultRecord
from src.storage.repository import RunRepository
from src.orchestrator.cli import main as cli_main


def test_extract_related_files():
    trace = """
    File "src/quality/runner.py", line 45, in run_command
    File "tests/test_foo.py", line 12, in test_something
    File "C:\\Users\\USER\\AppData\\Local\\lib.py", line 10
    """
    files = extract_related_files(trace)
    assert "src/quality/runner.py" in files
    assert "tests/test_foo.py" in files
    assert "AppData" not in "".join(files)


def test_extract_root_cause_snippet():
    text = """
    def test_example():
    >       assert 1 == 2
    E       AssertionError: assert 1 == 2
    """
    snippet = extract_root_cause_snippet(text)
    assert "AssertionError: assert 1 == 2" in snippet


def test_diagnose_pytest_failure():
    cmd_res = SingleCommandResult(
        command="pytest tests/test_demo.py",
        exit_code=1,
        passed=False,
        summary="1 failed in 0.5s",
        stdout="""
        ============================= FAILURES =============================
        ____________________________ test_demo _____________________________
        >   assert result == "expected"
        E   AssertionError: assert 'actual' == 'expected'
        FAILED tests/test_demo.py::test_demo - AssertionError
        ========================= 1 failed in 0.5s =========================
        """,
        stderr="",
    )
    diag = diagnose_command_failure(cmd_res)
    assert diag.exit_code == 1
    assert "tests/test_demo.py::test_demo" in diag.failed_tests
    assert diag.error_type == "AssertionError"
    assert "AssertionError" in diag.root_cause_snippet


def test_diagnose_timeout_failure():
    cmd_res = SingleCommandResult(
        command="python long_task.py",
        exit_code=124,
        passed=False,
        duration_seconds=5.0,
        summary="TIMEOUT (after 5s)",
        stdout="",
        stderr="",
        timed_out=True,
        error_message="Lệnh kiểm thử vượt quá thời gian tối đa",
    )
    diag = diagnose_command_failure(cmd_res)
    assert diag.error_type == "TimeoutExpired"
    assert "TIMEOUT" in diag.error_summary
    assert "vòng lặp" in diag.suggested_action.lower()


def test_build_fix_prompt():
    contract = TaskContract(
        id="ACF-TEST",
        objective="Fix the bug",
        scope=TaskScope(in_scope=["src/foo.py"], out_of_scope=[".env"]),
        acceptance_criteria=["Tiêu chí A", "Tiêu chí B"],
        test_commands=["pytest tests/test_foo.py"]
    )
    run = RunRecord(
        run_id="run_101",
        task_id="ACF-TEST",
        attempt_count=1,
        max_attempts=3,
    )
    failures = [
        FailureDiagnosis(
            command="pytest tests/test_foo.py",
            exit_code=1,
            error_summary="1 failed",
            error_type="AssertionError",
            failed_tests=["tests/test_foo.py::test_bar"],
            root_cause_snippet="E   AssertionError: False is not True",
            suggested_action="Sửa logic foo.py"
        )
    ]

    prompt = build_fix_prompt(contract, run, failures, target_branch="task/ACF-TEST")
    assert "Self-Correction Attempt #1/3" in prompt
    assert "ACF-TEST" in prompt
    assert "run_101" in prompt
    assert "tests/test_foo.py::test_bar" in prompt
    assert "AssertionError" in prompt
    assert "src/foo.py" in prompt
    assert ".env" in prompt


def test_self_correction_noop_on_passing_quality_gate(tmp_path):
    repo = RunRepository(db_path=tmp_path / "factory.db")
    run = repo.create_run(task_id="ACF-TEST-PASS")

    engine = SelfCorrectionEngine(repo_root=tmp_path, run_repo=repo, runs_dir=tmp_path / "runs")
    summary = QualityGateSummary(
        passed=True,
        status=QualityGateStatus.PASS,
        total_commands=1,
        passed_commands=1,
    )

    decision = engine.evaluate_run(run.run_id, quality_summary=summary)
    assert decision.action == CorrectionAction.NOOP_SUCCESS
    assert decision.passed is True


def test_self_correction_retry_fix_when_under_max_attempts(tmp_path):
    repo = RunRepository(db_path=tmp_path / "factory.db")
    run = repo.create_run(task_id="ACF-RETRY", max_attempts=3)

    # Record 1 failed attempt
    repo.record_attempt(
        run_id=run.run_id,
        status=RunStatus.FAILED,
        test_results=[
            TestResultRecord(
                command="pytest",
                passed=False,
                exit_code=1,
                summary="1 failed",
                stdout="FAILED tests/test_a.py::test_fail - AssertionError",
                stderr=""
            )
        ]
    )

    engine = SelfCorrectionEngine(
        repo_root=tmp_path,
        run_repo=repo,
        runs_dir=tmp_path / "runs",
        tasks_dir=tmp_path / "tasks"
    )

    decision = engine.evaluate_run(run.run_id)
    assert decision.action == CorrectionAction.RETRY_FIX
    assert decision.attempt_number == 1
    assert decision.max_attempts == 3
    assert decision.remaining_attempts == 2
    assert decision.fix_prompt_path is not None

    # Check file exists on disk
    prompt_file = tmp_path / decision.fix_prompt_path
    assert prompt_file.exists()
    content = prompt_file.read_text(encoding="utf-8")
    assert "Self-Correction Attempt #1/3" in content
    assert "tests/test_a.py::test_fail" in content

    # Check metadata in SQLite
    updated = repo.get_run(run.run_id)
    assert updated.metadata["last_fix_prompt"] == decision.fix_prompt_path
    assert updated.metadata["remaining_attempts"] == 2


def test_self_correction_halts_and_blocks_when_max_attempts_reached(tmp_path):
    repo = RunRepository(db_path=tmp_path / "factory.db")
    run = repo.create_run(task_id="ACF-BLOCK", max_attempts=3)

    # Simulate 3 failed attempts to exhaust max_fix_attempts
    for i in range(1, 4):
        repo.record_attempt(
            run_id=run.run_id,
            status=RunStatus.FAILED,
            test_results=[
                TestResultRecord(
                    command="pytest",
                    passed=False,
                    exit_code=1,
                    summary=f"Attempt {i} failed",
                    stdout=f"FAILED tests/test_b.py::test_fail_{i} - ValueError",
                    stderr=""
                )
            ]
        )

    engine = SelfCorrectionEngine(
        repo_root=tmp_path,
        run_repo=repo,
        runs_dir=tmp_path / "runs",
        tasks_dir=tmp_path / "tasks"
    )

    decision = engine.evaluate_run(run.run_id)
    assert decision.action == CorrectionAction.HALT_BLOCKED
    assert decision.attempt_number == 3
    assert decision.remaining_attempts == 0
    assert decision.diagnostic_report_path is not None

    # Verify diagnostic reports written to disk
    json_path = tmp_path / decision.diagnostic_report_path
    assert json_path.exists()
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["final_status"] == "blocked"
    assert data["total_attempts"] == 3
    assert len(data["attempt_history_summary"]) == 3

    md_path = tmp_path / "runs" / run.run_id / "blocked_diagnostic.md"
    assert md_path.exists()
    md_content = md_path.read_text(encoding="utf-8")
    assert "Báo Cáo Chẩn Đoán Sự Cố — Task Blocked" in md_content

    # Verify RunRecord transitioned to BLOCKED in SQLite
    blocked_run = repo.get_run(run.run_id)
    assert blocked_run.status == RunStatus.BLOCKED
    assert "Đạt giới hạn tối đa 3 lần sửa lỗi" in blocked_run.error_message


def test_cli_fix_command(tmp_path, capsys):
    # Setup test repo and run
    db_file = tmp_path / "factory.db"
    repo = RunRepository(db_path=db_file)
    run = repo.create_run(task_id="ACF-CLI-FIX", max_attempts=3)

    repo.record_attempt(
        run_id=run.run_id,
        status=RunStatus.FAILED,
        test_results=[
            TestResultRecord(
                command="pytest",
                passed=False,
                exit_code=1,
                summary="1 failed",
                stdout="FAILED tests/test_c.py::test_bug - TypeError",
                stderr=""
            )
        ]
    )

    # Configure custom factory DB path for CLI
    old_env = os.environ.get("FACTORY_DB_PATH")
    try:
        os.environ["FACTORY_DB_PATH"] = str(db_file)
        exit_code = cli_main(["fix", run.run_id, "--json"])
        assert exit_code == 0
        captured = capsys.readouterr()
        decision_data = json.loads(captured.out)
        assert decision_data["run_id"] == run.run_id
        assert decision_data["action"] == "retry_fix"
        assert decision_data["remaining_attempts"] == 2
    finally:
        if old_env is not None:
            os.environ["FACTORY_DB_PATH"] = old_env
        else:
            os.environ.pop("FACTORY_DB_PATH", None)


def test_run_quality_and_self_correct_end_to_end(tmp_path):
    repo = RunRepository(db_path=tmp_path / "factory.db")
    engine = SelfCorrectionEngine(
        repo_root=tmp_path,
        run_repo=repo,
        runs_dir=tmp_path / "runs",
        tasks_dir=tmp_path / "tasks"
    )

    # Create dummy failing contract
    task_file = tmp_path / "tasks" / "ACF-FAIL-E2E.json"
    task_file.parent.mkdir(parents=True, exist_ok=True)
    task_file.write_text(json.dumps({
        "id": "ACF-FAIL-E2E",
        "objective": "Verify Self Correction Loop",
        "scope": {"in_scope": ["src/*"]},
        "acceptance_criteria": ["Check failure loop"],
        "test_commands": ['python -c "import sys; print(\'Traceback: test failed\'); sys.exit(1)"'],
        "constraints": {"max_fix_attempts": 2}
    }), encoding="utf-8")

    # Attempt 1: Should produce RETRY_FIX
    dec1 = engine.run_quality_and_self_correct(task_id="ACF-FAIL-E2E")
    assert dec1.action == CorrectionAction.RETRY_FIX
    assert dec1.attempt_number == 1
    assert dec1.remaining_attempts == 1
    assert Path(tmp_path / dec1.fix_prompt_path).exists()

    # Attempt 2: Reached max_attempts (2) -> Should HALT_BLOCKED
    dec2 = engine.run_quality_and_self_correct(task_id="ACF-FAIL-E2E", run_id=dec1.run_id)
    assert dec2.action == CorrectionAction.HALT_BLOCKED
    assert dec2.attempt_number == 2
    assert dec2.remaining_attempts == 0
    assert Path(tmp_path / dec2.diagnostic_report_path).exists()

    # Verify SQLite reflects BLOCKED
    run_rec = repo.get_run(dec1.run_id)
    assert run_rec.status == RunStatus.BLOCKED
