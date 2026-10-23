"""Tests for Antigravity task handoff, prompt generation, human confirmation, and persistence."""

import json
from pathlib import Path
from unittest.mock import MagicMock
import pytest

from src.beads.models import BeadsTask
from src.contracts.parser import parse_task_file
from src.handoff.manager import AntigravityHandoffManager
from src.handoff.prompt_builder import (
    build_antigravity_prompt,
    format_handoff_display,
    gather_handoff_context,
)
from src.orchestrator.cli import main as cli_main
from src.orchestrator.engine import SequentialOrchestrator
from src.orchestrator.lock import TaskLockManager
from src.storage.models import RunStatus


@pytest.fixture
def handoff_env(tmp_path: Path):
    workspace_root = Path(__file__).resolve().parent.parent
    tasks_dir = workspace_root / "tasks"
    runs_dir = tmp_path / "runs"
    lock_dir = runs_dir / "locks"
    db_path = runs_dir / "factory.db"

    # Tạo AGENTS.md mẫu trong tmp_path để test context discovery
    (tmp_path / "AGENTS.md").write_text("# AGENTS.md rules", encoding="utf-8")

    mock_beads = MagicMock()
    ready_task = BeadsTask(
        id="ACF-06",
        title="Bàn giao cho Antigravity",
        description="Sinh prompt và bàn giao có xác nhận",
        status="open",
        priority=1,
    )
    mock_beads.get_ready_tasks.return_value = [ready_task]
    mock_beads.get_blocked_tasks.return_value = []
    mock_beads.ensure_task_ready_for_dispatch.return_value = ready_task

    orchestrator = SequentialOrchestrator(
        workspace_dir=tmp_path,
        beads_client=mock_beads,
        lock_manager=TaskLockManager(lock_dir=lock_dir),
        tasks_dir=tasks_dir,
        db_path=db_path,
    )
    manager = AntigravityHandoffManager(
        orchestrator=orchestrator,
        runs_dir=runs_dir,
    )
    return manager, orchestrator


def test_prompt_generation_from_contract_and_context(tmp_path: Path):
    """Kiểm tra sinh prompt đầy đủ từ task contract và context liên quan."""
    workspace_root = Path(__file__).resolve().parent.parent
    contract = parse_task_file(workspace_root / "tasks" / "ACF-06.json")

    context = gather_handoff_context(workspace_root, contract)
    prompt = build_antigravity_prompt(contract, context, run_id="run_ACF-06_test")

    assert "ACF-06" in prompt
    assert "run_ACF-06_test" in prompt
    assert "Bàn giao cho Antigravity" in prompt
    assert "task/ACF-06" in prompt
    assert "AGENTS.md" in prompt
    assert "ACF-05" in prompt
    for crit in contract.acceptance_criteria:
        assert crit in prompt
    for cmd in contract.test_commands:
        assert cmd in prompt


def test_display_clearly_shows_task_branch_and_acceptance_criteria(handoff_env):
    """Kiểm tra hiển thị rõ task, branch và danh sách tiêu chí nghiệm thu."""
    manager, _ = handoff_env
    _, packet = manager.prepare_handoff(task_id="ACF-06")

    display_text = format_handoff_display(packet)
    assert "[ACF-06] Bàn giao cho Antigravity" in display_text
    assert "task/ACF-06" in display_text
    assert "waiting_for_human" in display_text
    for crit in packet.acceptance_criteria:
        assert crit in display_text


def test_prepare_handoff_saves_waiting_for_human_state(handoff_env):
    """Kiểm tra lưu trạng thái waiting_for_human và file prompt trước khi người dùng xác nhận."""
    manager, orchestrator = handoff_env
    output_lines = []

    run_record, packet = manager.execute_handoff_workflow(
        task_id="ACF-06",
        confirm_callback=None,  # Chưa xác nhận ngay -> giữ ở trạng thái waiting_for_human
        output_fn=output_lines.append,
    )

    assert run_record.status == RunStatus.WAITING_FOR_HUMAN
    assert packet.status == "waiting_for_human"
    assert packet.confirmed_by_human is None
    assert Path(packet.prompt_path).exists()
    assert Path(packet.packet_path).exists()

    # Truy xuất lại từ SQLite để chắc chắn trạng thái waiting_for_human đã được lưu bền vững
    repo = orchestrator._get_run_repo()
    stored_run = repo.get_run(run_record.run_id)
    assert stored_run is not None
    assert stored_run.status == RunStatus.WAITING_FOR_HUMAN
    assert stored_run.metadata["handoff"]["status"] == "waiting_for_human"


def test_human_confirmation_before_start_and_persists_result(handoff_env):
    """Kiểm tra yêu cầu người dùng xác nhận trước khi bắt đầu và lưu kết quả bàn giao."""
    manager, orchestrator = handoff_env
    observed_status_during_prompt = []
    output_lines = []

    def _confirm_callback(pkt):
        # Tại thời điểm hỏi người dùng xác nhận, bản ghi trong DB phải đang ở trạng thái waiting_for_human
        repo = orchestrator._get_run_repo()
        current_in_db = repo.get_run(pkt.run_id)
        observed_status_during_prompt.append(current_in_db.status)
        return True

    updated_run, updated_packet = manager.execute_handoff_workflow(
        task_id="ACF-06",
        confirm_callback=_confirm_callback,
        output_fn=output_lines.append,
        notes="Confirmed by human operator",
    )

    # 1. Trước khi người dùng bấm xác nhận, trạng thái trong DB là WAITING_FOR_HUMAN
    assert observed_status_during_prompt == [RunStatus.WAITING_FOR_HUMAN]

    # 2. Sau khi người dùng xác nhận đồng ý, trạng thái chuyển sang RUNNING và lưu kết quả handed_off
    assert updated_run.status == RunStatus.RUNNING
    assert updated_packet.status == "handed_off"
    assert updated_packet.confirmed_by_human is True
    assert updated_packet.confirmed_at is not None

    # 3. Kiểm tra dữ liệu lưu trong SQLite
    repo = orchestrator._get_run_repo()
    persisted = repo.get_run(updated_run.run_id)
    assert persisted.status == RunStatus.RUNNING
    assert persisted.metadata["handoff_result"]["status"] == "handed_off"
    assert persisted.metadata["handoff_result"]["confirmed_by_human"] is True
    assert persisted.metadata["handoff_result"]["notes"] == "Confirmed by human operator"


def test_human_rejection_keeps_waiting_for_human_and_records_rejection(handoff_env):
    """Kiểm tra khi người dùng từ chối bắt đầu: không chạy task và lưu kết quả rejected."""
    manager, orchestrator = handoff_env

    updated_run, updated_packet = manager.execute_handoff_workflow(
        task_id="ACF-06",
        confirm_callback=lambda _pkt: False,
        output_fn=lambda _msg: None,
        notes="Need scope adjustment first",
    )

    assert updated_run.status == RunStatus.WAITING_FOR_HUMAN
    assert updated_packet.status == "rejected"
    assert updated_packet.confirmed_by_human is False

    repo = orchestrator._get_run_repo()
    persisted = repo.get_run(updated_run.run_id)
    assert persisted.status == RunStatus.WAITING_FOR_HUMAN
    assert persisted.metadata["handoff_result"]["status"] == "rejected"
    assert persisted.metadata["handoff_result"]["confirmed_by_human"] is False


def test_cli_handoff_wait_and_confirm_commands(monkeypatch, handoff_env, capsys):
    """Kiểm tra workflow qua CLI: `factory handoff --wait --json` và `factory confirm <run_id> --yes --json`."""
    _, orchestrator = handoff_env
    monkeypatch.setattr("src.orchestrator.cli.SequentialOrchestrator", lambda: orchestrator)

    # Bước 1: Chuẩn bị handoff và dừng ở trạng thái waiting_for_human
    exit_code_1 = cli_main(["handoff", "--task-id", "ACF-06", "--wait", "--json"])
    assert exit_code_1 == 0
    payload_1 = json.loads(capsys.readouterr().out)
    run_id = payload_1["run"]["run_id"]
    assert payload_1["run"]["status"] == "waiting_for_human"
    assert payload_1["handoff"]["status"] == "waiting_for_human"

    # Bước 2: Người dùng xác nhận qua lệnh `factory confirm <run_id> --yes`
    exit_code_2 = cli_main(["confirm", run_id, "--yes", "--notes", "LGTM", "--json"])
    assert exit_code_2 == 0
    payload_2 = json.loads(capsys.readouterr().out)
    assert payload_2["run"]["status"] == "running"
    assert payload_2["handoff"]["status"] == "handed_off"
    assert payload_2["handoff"]["confirmed_by_human"] is True
