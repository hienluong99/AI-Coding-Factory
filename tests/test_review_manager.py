"""Unit tests for Human-in-the-loop review, auto-merge, and task closing (ACF-11)."""

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from src.beads.client import BeadsClient
from src.contracts.task import TaskConstraints, TaskContract, TaskScope, TaskStatus
from src.orchestrator.cli import main as cli_main
from src.orchestrator.lock import TaskLockManager
from src.review.manager import ReviewManager
from src.review.models import ReviewAction, ReviewDecision, ReviewSummary
from src.storage.models import RunRecord, RunStatus, TestResultRecord
from src.storage.repository import RunRepository


def test_beads_client_close_task():
    client = BeadsClient()
    with patch.object(client, "_run_command", return_value="Closed ACF-01") as mock_run:
        res = client.close_task("ACF-01", reason="Finished work")
        assert res is True
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert "close" in args
        assert "ACF-01" in args


def test_review_manager_get_summary(tmp_path):
    repo = RunRepository(db_path=tmp_path / "factory.db")
    run = repo.create_run(task_id="ACF-REV-01", max_attempts=3)
    repo.record_attempt(
        run_id=run.run_id,
        status=RunStatus.SUCCESS,
        test_results=[
            TestResultRecord(command="pytest", passed=True, exit_code=0, summary="8 passed")
        ]
    )

    task_file = tmp_path / "tasks" / "ACF-REV-01.json"
    task_file.parent.mkdir(parents=True, exist_ok=True)
    task_file.write_text(json.dumps({
        "id": "ACF-REV-01",
        "objective": "Review test task",
        "scope": {"in_scope": ["src/review/*"]},
        "acceptance_criteria": ["Criteria 1", "Criteria 2"],
        "test_commands": ["pytest"],
        "status": "completed"
    }), encoding="utf-8")

    mgr = ReviewManager(
        repo_root=tmp_path,
        run_repo=repo,
        runs_dir=tmp_path / "runs",
        tasks_dir=tmp_path / "tasks"
    )

    summary = mgr.get_review_summary("ACF-REV-01")
    assert summary.task_id == "ACF-REV-01"
    assert summary.task_title == "Review test task"
    assert summary.quality_gate_passed is True
    assert "1/1 passed" in summary.test_summary
    assert len(summary.acceptance_criteria) == 2


def test_review_manager_approve_rejects_if_quality_gate_failed(tmp_path):
    repo = RunRepository(db_path=tmp_path / "factory.db")
    run = repo.create_run(task_id="ACF-REV-FAIL", max_attempts=3)
    repo.record_attempt(
        run_id=run.run_id,
        status=RunStatus.FAILED,
        test_results=[
            TestResultRecord(command="pytest", passed=False, exit_code=1)
        ]
    )

    task_file = tmp_path / "tasks" / "ACF-REV-FAIL.json"
    task_file.parent.mkdir(parents=True, exist_ok=True)
    task_file.write_text(json.dumps({
        "id": "ACF-REV-FAIL",
        "objective": "Failing task",
        "scope": {"in_scope": ["src/*"]},
        "acceptance_criteria": ["All pass"],
        "test_commands": ["pytest"],
        "status": "in_progress"
    }), encoding="utf-8")

    mgr = ReviewManager(
        repo_root=tmp_path,
        run_repo=repo,
        runs_dir=tmp_path / "runs",
        tasks_dir=tmp_path / "tasks"
    )

    with pytest.raises(ValueError, match="Quality Gate chưa đạt"):
        mgr.approve("ACF-REV-FAIL")


def test_review_manager_approve_success_and_merge(tmp_path):
    repo = RunRepository(db_path=tmp_path / "factory.db")
    run = repo.create_run(task_id="ACF-REV-PASS", max_attempts=3)
    repo.record_attempt(
        run_id=run.run_id,
        status=RunStatus.SUCCESS,
        test_results=[
            TestResultRecord(command="pytest", passed=True, exit_code=0)
        ]
    )

    task_file = tmp_path / "tasks" / "ACF-REV-PASS.json"
    task_file.parent.mkdir(parents=True, exist_ok=True)
    task_file.write_text(json.dumps({
        "id": "ACF-REV-PASS",
        "objective": "Pass task",
        "scope": {"in_scope": ["src/review/*"]},
        "acceptance_criteria": ["Criteria 1"],
        "test_commands": ["pytest"],
        "status": "completed"
    }), encoding="utf-8")

    lock_mgr = TaskLockManager(lock_dir=tmp_path / "runs" / "locks")
    lock_mgr.claim_task("ACF-REV-PASS", owner_id="worker_1")
    assert lock_mgr.is_claimed("ACF-REV-PASS") is True

    beads = BeadsClient()
    mgr = ReviewManager(
        repo_root=tmp_path,
        run_repo=repo,
        beads_client=beads,
        lock_manager=lock_mgr,
        runs_dir=tmp_path / "runs",
        tasks_dir=tmp_path / "tasks"
    )

    with patch.object(mgr, "_execute_git_merge", return_value="merge_sha_abcdef123"), \
         patch.object(beads, "close_task", return_value=True):
        decision = mgr.approve("ACF-REV-PASS", reviewer="alice")

    assert decision.action == ReviewAction.APPROVE
    assert decision.merged is True
    assert decision.merge_commit_sha == "merge_sha_abcdef123"
    assert decision.task_closed is True
    assert decision.lock_released is True
    assert lock_mgr.is_claimed("ACF-REV-PASS") is False

    # Verify SQLite updated
    updated_run = repo.get_run(run.run_id)
    assert updated_run.status == RunStatus.SUCCESS
    assert updated_run.metadata["review"]["action"] == "approve"
    assert updated_run.metadata["review"]["reviewer"] == "alice"


def test_review_manager_request_changes_creates_fix_prompt(tmp_path):
    repo = RunRepository(db_path=tmp_path / "factory.db")
    run = repo.create_run(task_id="ACF-REQ-CHANGE", max_attempts=3)
    repo.record_attempt(
        run_id=run.run_id,
        status=RunStatus.SUCCESS,
        test_results=[
            TestResultRecord(command="pytest", passed=True, exit_code=0)
        ]
    )

    task_file = tmp_path / "tasks" / "ACF-REQ-CHANGE.json"
    task_file.parent.mkdir(parents=True, exist_ok=True)
    task_file.write_text(json.dumps({
        "id": "ACF-REQ-CHANGE",
        "objective": "Change requested task",
        "scope": {"in_scope": ["src/review/*"]},
        "acceptance_criteria": ["Criteria 1"],
        "test_commands": ["pytest"],
        "status": "in_progress"
    }), encoding="utf-8")

    mgr = ReviewManager(
        repo_root=tmp_path,
        run_repo=repo,
        runs_dir=tmp_path / "runs",
        tasks_dir=tmp_path / "tasks"
    )

    decision = mgr.request_changes(
        "ACF-REQ-CHANGE",
        feedback="Vui lòng viết thêm docstring và bổ sung test case",
        reviewer="bob"
    )

    assert decision.action == ReviewAction.REQUEST_CHANGES
    assert decision.reviewer == "bob"
    assert decision.next_fix_prompt_path is not None
    assert "docstring" in decision.feedback

    # Check prompt file created on disk
    prompt_file = tmp_path / decision.next_fix_prompt_path
    assert prompt_file.exists()
    content = prompt_file.read_text(encoding="utf-8")
    assert "Reviewer yêu cầu sửa" in content
    assert "Vui lòng viết thêm docstring" in content


def test_review_manager_request_changes_blocks_when_max_attempts_exceeded(tmp_path):
    repo = RunRepository(db_path=tmp_path / "factory.db")
    run = repo.create_run(task_id="ACF-EXHAUST", max_attempts=2)

    # Simulate 2 attempts
    for _ in range(2):
        repo.record_attempt(run_id=run.run_id, status=RunStatus.FAILED)

    task_file = tmp_path / "tasks" / "ACF-EXHAUST.json"
    task_file.parent.mkdir(parents=True, exist_ok=True)
    task_file.write_text(json.dumps({
        "id": "ACF-EXHAUST",
        "objective": "Exhausted attempts",
        "scope": {"in_scope": ["src/*"]},
        "acceptance_criteria": ["Done"],
        "test_commands": ["pytest"]
    }), encoding="utf-8")

    mgr = ReviewManager(
        repo_root=tmp_path,
        run_repo=repo,
        runs_dir=tmp_path / "runs",
        tasks_dir=tmp_path / "tasks"
    )

    decision = mgr.request_changes("ACF-EXHAUST", feedback="Need changes")
    assert decision.action == ReviewAction.REQUEST_CHANGES
    assert "BLOCKED" in decision.message

    updated = repo.get_run(run.run_id)
    assert updated.status == RunStatus.BLOCKED


def test_cli_review_commands(tmp_path, capsys):
    db_file = tmp_path / "factory.db"
    repo = RunRepository(db_path=db_file)
    run = repo.create_run(task_id="ACF-CLI-REV", max_attempts=3)
    repo.record_attempt(
        run_id=run.run_id,
        status=RunStatus.SUCCESS,
        test_results=[
            TestResultRecord(command="pytest", passed=True, exit_code=0)
        ]
    )

    task_file = Path("tasks") / "ACF-CLI-REV.json"
    task_file.write_text(json.dumps({
        "id": "ACF-CLI-REV",
        "objective": "CLI Review Test",
        "scope": {"in_scope": ["src/review/*"]},
        "acceptance_criteria": ["Criteria A"],
        "test_commands": ["pytest"],
        "status": "completed"
    }), encoding="utf-8")

    old_env = os.environ.get("FACTORY_DB_PATH")
    try:
        os.environ["FACTORY_DB_PATH"] = str(db_file)

        # 1. View summary CLI
        code1 = cli_main(["review", "ACF-CLI-REV", "--json"])
        assert code1 == 0
        captured1 = capsys.readouterr()
        data1 = json.loads(captured1.out)
        assert data1["task_id"] == "ACF-CLI-REV"
        assert data1["quality_gate_passed"] is True

        # 2. Approve CLI with mocked git merge
        with patch.object(ReviewManager, "_execute_git_merge", return_value="mock_merge_sha"), \
             patch.object(BeadsClient, "close_task", return_value=True):
            code2 = cli_main(["review", "ACF-CLI-REV", "--approve", "--json"])
            assert code2 == 0
            captured2 = capsys.readouterr()
            data2 = json.loads(captured2.out)
            assert data2["action"] == "approve"
            assert data2["merged"] is True

    finally:
        if task_file.exists():
            task_file.unlink()
        if old_env is not None:
            os.environ["FACTORY_DB_PATH"] = old_env
        else:
            os.environ.pop("FACTORY_DB_PATH", None)
