"""Unit tests for Auto-Pilot Orchestrator Loop and Factory Dashboard (ACF-12)."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from src.autopilot.dashboard import DashboardRenderer
from src.autopilot.engine import AutoPilotEngine
from src.autopilot.models import AutoPilotConfig, AutoPilotResult, DashboardSummary, TaskStepResult
from src.beads.models import BeadsDependency, BeadsTask
from src.correction.models import CorrectionAction, CorrectionDecision
from src.delivery.models import PRStatus, PullRequestPackage
from src.orchestrator.cli import main as cli_main
from src.orchestrator.engine import SequentialOrchestrator
from src.orchestrator.lock import TaskClaim
from src.orchestrator.models import NextActionPlan
from src.quality.models import QualityGateSummary, SingleCommandResult
from src.review.models import ReviewAction, ReviewDecision
from src.storage.models import RunRecord, RunStatus


def test_dashboard_renderer_text_and_json():
    mock_orch = MagicMock()
    mock_orch.git_guard.get_current_branch.return_value = "main"
    mock_orch.git_guard.is_working_tree_clean.return_value = True

    mock_orch.beads.get_ready_tasks.return_value = [
        BeadsTask(id="ACF-TEST-01", title="Test Ready Task", status="open", priority=1)
    ]
    mock_orch.beads.get_blocked_tasks.return_value = [
        BeadsTask(
            id="ACF-TEST-02",
            title="Test Blocked Task",
            status="open",
            dependencies=[BeadsDependency(depends_on_id="ACF-TEST-01", type="blocks")]
        )
    ]
    mock_orch.lock_manager.list_claims.return_value = []

    mock_repo = MagicMock()
    mock_repo.list_runs.return_value = [
        RunRecord(run_id="run-001", task_id="ACF-TEST-01", status=RunStatus.SUCCESS, attempt_count=1, max_attempts=3)
    ]
    mock_orch._get_run_repo.return_value = mock_repo

    renderer = DashboardRenderer(orchestrator=mock_orch)
    summary = renderer.get_summary()

    assert summary.ready_count == 1
    assert summary.blocked_count == 1
    assert summary.total_runs_count == 1
    assert summary.successful_runs_count == 1
    assert summary.working_tree_clean is True

    text = renderer.render_text()
    assert "AI CODING FACTORY — CENTRAL DASHBOARD" in text
    assert "ACF-TEST-01" in text
    assert "ACF-TEST-02" in text

    data = renderer.render_json()
    assert data["ready_count"] == 1
    assert data["git_branch"] == "main"
    assert data["working_tree_clean"] is True


def test_autopilot_single_task_dirty_tree_halts():
    mock_orch = MagicMock()
    mock_orch.git_guard.is_working_tree_clean.return_value = False

    engine = AutoPilotEngine(orchestrator=mock_orch)
    res = engine.run_single_task("ACF-DIRTY")

    assert res.task_id == "ACF-DIRTY"
    assert res.status == "error"
    assert "Working tree đang có thay đổi chưa commit" in (res.error_message or "")


def test_autopilot_single_task_success_lifecycle():
    mock_orch = MagicMock()
    mock_orch.git_guard.is_working_tree_clean.return_value = True

    mock_orch.next_task.return_value = NextActionPlan(
        has_next=True,
        claimed=True,
        dry_run=False,
        task_id="ACF-AUTOPILOT-1",
        title="Test Autopilot Task",
        run_id="run-auto-1",
        target_branch="task/ACF-AUTOPILOT-1",
    )

    mock_handoff = MagicMock()
    mock_handoff.prepare_handoff.return_value = MagicMock()
    mock_handoff.confirm_handoff.return_value = MagicMock()

    mock_orch.run_quality_gates.return_value = QualityGateSummary(
        task_id="ACF-AUTOPILOT-1",
        run_id="run-auto-1",
        passed=True,
        command_results=[SingleCommandResult(command="pytest", exit_code=0, passed=True, duration_seconds=1.0)],
        total_commands=1,
        passed_commands=1,
        failed_commands=0,
        total_duration=1.0,
    )

    mock_orch.deliver_task_pr.return_value = PullRequestPackage(
        task_id="ACF-AUTOPILOT-1",
        run_id="run-auto-1",
        title="Test Autopilot Task",
        commit_sha="abcdef123456",
        branch="task/ACF-AUTOPILOT-1",
        pr_url="https://github.com/org/repo/pull/42",
        status=PRStatus.CREATED,
    )

    mock_orch.review_task.return_value = ReviewDecision(
        action=ReviewAction.APPROVE,
        task_id="ACF-AUTOPILOT-1",
        run_id="run-auto-1",
        merge_commit_sha="123456abcdef",
        task_closed=True,
        merged=True,
        message="Merged and closed",
    )

    engine = AutoPilotEngine(orchestrator=mock_orch, handoff_manager=mock_handoff)
    cfg = AutoPilotConfig(auto_approve_review=True)
    res = engine.run_single_task("ACF-AUTOPILOT-1", config=cfg)

    assert res.task_id == "ACF-AUTOPILOT-1"
    assert res.run_id == "run-auto-1"
    assert res.status == "success"
    assert res.quality_passed is True
    assert res.pr_created is True
    assert res.merged is True
    assert res.closed_in_beads is True


def test_autopilot_self_correction_flow():
    mock_orch = MagicMock()
    mock_orch.git_guard.is_working_tree_clean.return_value = True

    mock_orch.next_task.return_value = NextActionPlan(
        has_next=True,
        claimed=True,
        dry_run=False,
        task_id="ACF-CORRECT-1",
        title="Test Correction Task",
        run_id="run-correct-1",
        target_branch="task/ACF-CORRECT-1",
    )

    # First quality gate fails, second quality gate passes
    fail_summary = QualityGateSummary(
        task_id="ACF-CORRECT-1",
        run_id="run-correct-1",
        passed=False,
        command_results=[SingleCommandResult(command="pytest", exit_code=1, passed=False, duration_seconds=1.0)],
        total_commands=1,
        passed_commands=0,
        failed_commands=1,
        total_duration=1.0,
    )
    pass_summary = QualityGateSummary(
        task_id="ACF-CORRECT-1",
        run_id="run-correct-1",
        passed=True,
        command_results=[SingleCommandResult(command="pytest", exit_code=0, passed=True, duration_seconds=1.0)],
        total_commands=1,
        passed_commands=1,
        failed_commands=0,
        total_duration=1.0,
    )
    mock_orch.run_quality_gates.side_effect = [fail_summary, pass_summary]

    mock_orch.evaluate_correction.return_value = CorrectionDecision(
        run_id="run-correct-1",
        task_id="ACF-CORRECT-1",
        attempt_number=1,
        max_attempts=3,
        remaining_attempts=2,
        action=CorrectionAction.RETRY_FIX,
        passed=False,
        fix_prompt_path="runs/run-correct-1/fix_prompt_attempt_1.md",
        message="Created fix prompt",
    )

    mock_orch.deliver_task_pr.return_value = PullRequestPackage(
        task_id="ACF-CORRECT-1",
        run_id="run-correct-1",
        title="Test Correction Task",
        branch="task/ACF-CORRECT-1",
        status=PRStatus.CREATED,
        pr_url="https://github.com/org/repo/pull/43",
    )
    mock_orch.review_task.return_value = ReviewDecision(
        action=ReviewAction.APPROVE,
        task_id="ACF-CORRECT-1",
        run_id="run-correct-1",
        merged=True,
        task_closed=True,
        message="Merged",
    )

    callback_called = []
    def dummy_callback(task_id, run_id, diag):
        callback_called.append((task_id, run_id))
        return True

    engine = AutoPilotEngine(orchestrator=mock_orch, agent_callback=dummy_callback)
    res = engine.run_single_task("ACF-CORRECT-1")

    assert res.status == "success"
    assert res.attempts == 1
    assert len(callback_called) == 2  # handoff callback + fix callback


def test_autopilot_blocked_when_exhausted():
    mock_orch = MagicMock()
    mock_orch.git_guard.is_working_tree_clean.return_value = True

    mock_orch.next_task.return_value = NextActionPlan(
        has_next=True,
        claimed=True,
        dry_run=False,
        task_id="ACF-BLOCKED-1",
        title="Test Blocked Task",
        run_id="run-blocked-1",
        target_branch="task/ACF-BLOCKED-1",
    )

    mock_orch.run_quality_gates.return_value = QualityGateSummary(
        task_id="ACF-BLOCKED-1",
        run_id="run-blocked-1",
        passed=False,
        command_results=[SingleCommandResult(command="pytest", exit_code=1, passed=False, duration_seconds=1.0)],
        total_commands=1,
        passed_commands=0,
        failed_commands=1,
        total_duration=1.0,
    )

    mock_orch.evaluate_correction.return_value = CorrectionDecision(
        run_id="run-blocked-1",
        task_id="ACF-BLOCKED-1",
        attempt_number=3,
        max_attempts=3,
        remaining_attempts=0,
        action=CorrectionAction.HALT_BLOCKED,
        passed=False,
        diagnostic_report_path="runs/run-blocked-1/blocked_diagnostic.json",
        message="Attempts exhausted",
    )

    engine = AutoPilotEngine(orchestrator=mock_orch)
    res = engine.run_single_task("ACF-BLOCKED-1")

    assert res.status == "blocked"
    assert "Vượt quá số lần sửa tối đa" in (res.error_message or "")
    mock_orch.lock_manager.release_task.assert_called_once_with("ACF-BLOCKED-1", owner_id="autopilot")


def test_autopilot_run_chaining_and_max_tasks():
    mock_orch = MagicMock()
    mock_orch.git_guard.is_working_tree_clean.return_value = True

    # Simulate sequential task selection: Task 1 then Task 2 then None
    task1 = BeadsTask(id="ACF-SEQ-1", title="First Task", status="open")
    task2 = BeadsTask(id="ACF-SEQ-2", title="Second Task", status="open")
    mock_orch.select_next_task.side_effect = [task1, task2, None]

    engine = AutoPilotEngine(orchestrator=mock_orch)

    # Mock run_single_task
    with patch.object(engine, "run_single_task") as mock_step:
        mock_step.side_effect = [
            TaskStepResult(task_id="ACF-SEQ-1", status="success", merged=True, closed_in_beads=True),
            TaskStepResult(task_id="ACF-SEQ-2", status="success", merged=True, closed_in_beads=True),
        ]

        # Test with max_tasks=1
        cfg = AutoPilotConfig(max_tasks=1)
        res = engine.run(config=cfg)
        assert res.total_tasks_processed == 1
        assert res.successful_tasks == ["ACF-SEQ-1"]
        assert res.status == "completed"

    # Reset side effect and test run without limit
    mock_orch.select_next_task.side_effect = [task1, task2, None]
    with patch.object(engine, "run_single_task") as mock_step:
        mock_step.side_effect = [
            TaskStepResult(task_id="ACF-SEQ-1", status="success", merged=True, closed_in_beads=True),
            TaskStepResult(task_id="ACF-SEQ-2", status="success", merged=True, closed_in_beads=True),
        ]
        res_all = engine.run(config=AutoPilotConfig(max_tasks=None))
        assert res_all.total_tasks_processed == 2
        assert res_all.successful_tasks == ["ACF-SEQ-1", "ACF-SEQ-2"]
        assert res_all.status == "completed"


def test_autopilot_dry_run_simulation():
    mock_orch = MagicMock()
    mock_orch.beads.get_ready_tasks.return_value = [
        BeadsTask(id="ACF-DRY-1", title="Dry Run 1", status="open"),
        BeadsTask(id="ACF-DRY-2", title="Dry Run 2", status="open"),
    ]
    mock_orch.load_task_contract.return_value = None

    engine = AutoPilotEngine(orchestrator=mock_orch)
    res = engine.run(config=AutoPilotConfig(dry_run=True, max_tasks=2))

    assert res.status == "dry_run"
    assert res.total_tasks_processed == 2
    assert len(res.task_results) == 2
    assert res.task_results[0].status == "dry_run"


def test_cli_dashboard_and_autopilot(capsys):
    # Test dashboard
    code = cli_main(["dashboard"])
    assert code == 0
    captured = capsys.readouterr()
    assert "AI CODING FACTORY — CENTRAL DASHBOARD" in captured.out

    # Test dashboard --json
    code = cli_main(["dashboard", "--json"])
    assert code == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["factory_name"] == "AI Coding Factory"

    # Test auto-pilot --dry-run
    code = cli_main(["auto-pilot", "--dry-run"])
    assert code == 0
    captured = capsys.readouterr()
    assert "KẾT QUẢ AUTO-PILOT LOOP (DRY_RUN)" in captured.out

    # Test auto-pilot --dry-run --json
    code = cli_main(["auto-pilot", "--dry-run", "--json"])
    assert code == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["status"] == "dry_run"
