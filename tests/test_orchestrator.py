"""Unit and integration tests for Sequential Orchestrator, CLI, and Task Lock."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from unittest.mock import MagicMock
import pytest

from src.beads.exceptions import TaskBlockedError
from src.beads.models import BeadsTask
from src.orchestrator.cli import main as cli_main
from src.orchestrator.engine import SequentialOrchestrator
from src.orchestrator.exceptions import TaskAlreadyClaimedError
from src.orchestrator.lock import TaskLockManager
from src.storage.repository import RunRepository


@pytest.fixture
def mock_beads():
    client = MagicMock()
    ready_1 = BeadsTask(
        id="ACF-03",
        title="Tích hợp Beads CLI",
        description="Mô tả ACF-03",
        status="open",
        priority=1,
    )
    ready_2 = BeadsTask(
        id="ACF-05",
        title="CLI và Orchestrator tuần tự",
        description="Mô tả ACF-05",
        status="open",
        priority=2,
    )
    blocked_1 = BeadsTask(
        id="ACF-06",
        title="Tự động tạo PR",
        description="Mô tả ACF-06",
        status="open",
        priority=2,
        blocked_by=["ACF-05"],
        blocked_by_count=1,
    )

    client.get_ready_tasks.return_value = [ready_1, ready_2]
    client.get_blocked_tasks.return_value = [blocked_1]

    def _ensure_ready(task_id: str) -> BeadsTask:
        if task_id == "ACF-06":
            raise TaskBlockedError(task_id="ACF-06", blocked_by=["ACF-05"])
        for t in [ready_1, ready_2]:
            if t.id == task_id:
                return t
        raise ValueError(f"Unknown task {task_id}")

    client.ensure_task_ready_for_dispatch.side_effect = _ensure_ready
    return client


@pytest.fixture
def orchestrator_env(tmp_path: Path, mock_beads):
    lock_dir = tmp_path / "runs" / "locks"
    db_path = tmp_path / "runs" / "factory.db"
    tasks_dir = Path(__file__).resolve().parent.parent / "tasks"

    lock_manager = TaskLockManager(lock_dir=lock_dir)
    orch = SequentialOrchestrator(
        workspace_dir=tmp_path,
        beads_client=mock_beads,
        lock_manager=lock_manager,
        tasks_dir=tasks_dir,
        db_path=db_path,
    )
    return orch, lock_manager, db_path


def test_factory_status_snapshot(orchestrator_env):
    """Kiểm tra factory status trả về đúng danh sách ready, blocked, claims và runs."""
    orch, _, _ = orchestrator_env
    status = orch.get_status()

    assert len(status.ready_tasks) == 2
    assert status.ready_tasks[0].id == "ACF-03"
    assert len(status.blocked_tasks) == 1
    assert status.blocked_tasks[0].id == "ACF-06"
    assert len(status.active_claims) == 0
    assert len(status.recent_runs) == 0


def test_selects_only_one_unblocked_task(orchestrator_env):
    """Orchestrator tuần tự chỉ chọn đúng 1 task chưa bị block mỗi lần."""
    orch, _, _ = orchestrator_env

    plan = orch.next_task(dry_run=True)
    assert plan.has_next is True
    assert plan.task_id == "ACF-03"
    assert len(plan.planned_actions) >= 5

    # Nếu cố tình yêu cầu chọn task đang bị block (ACF-06) -> phải bị từ chối
    with pytest.raises(TaskBlockedError):
        orch.next_task(dry_run=True, task_id="ACF-06")


def test_dry_run_multiple_times_never_claims_or_modifies_state(orchestrator_env):
    """Acceptance Criteria: Chạy dry-run nhiều lần không sửa code hoặc claim task."""
    orch, lock_manager, db_path = orchestrator_env

    for _ in range(5):
        plan = orch.next_task(dry_run=True)
        assert plan.has_next is True
        assert plan.dry_run is True
        assert plan.claimed is False
        assert plan.run_id is None
        assert plan.task_id == "ACF-03"
        assert "task/ACF-03" == plan.target_branch

    # Sau 5 lần chạy dry-run:
    # 1. Không có bất kỳ lock file nào được tạo
    assert lock_manager.is_claimed("ACF-03") is False
    assert len(lock_manager.list_claims()) == 0
    assert not lock_manager.lock_dir.exists()

    # 2. Không có bất kỳ bản ghi RunRecord nào hay database file nào bị ghi ra đĩa
    assert not db_path.exists()


def test_lock_prevents_two_processes_claiming_same_task(orchestrator_env, tmp_path: Path, mock_beads):
    """Kiểm tra cơ chế khóa ngăn chặn 2 tiến trình claim cùng một task."""
    orch_1, lock_manager, db_path = orchestrator_env
    tasks_dir = Path(__file__).resolve().parent.parent / "tasks"

    # Khởi tạo Orchestrator thứ 2 đại diện cho tiến trình thứ 2 cùng trỏ vào lock_dir và db_path
    orch_2 = SequentialOrchestrator(
        workspace_dir=tmp_path,
        beads_client=mock_beads,
        lock_manager=TaskLockManager(lock_dir=lock_manager.lock_dir),
        tasks_dir=tasks_dir,
        db_path=db_path,
    )

    # Tiến trình 1 chạy factory next (dry_run=False) -> Claim thành công ACF-03
    plan_1 = orch_1.next_task(dry_run=False, owner_id="worker-process-1")
    assert plan_1.claimed is True
    assert plan_1.task_id == "ACF-03"
    assert plan_1.run_id is not None
    assert lock_manager.is_claimed("ACF-03") is True

    # Tiến trình 2 cố tình claim cùng task ACF-03 -> Bị chặn bởi TaskAlreadyClaimedError
    with pytest.raises(TaskAlreadyClaimedError) as exc_info:
        orch_2.next_task(dry_run=False, task_id="ACF-03", owner_id="worker-process-2")
    assert "ACF-03" in str(exc_info.value)
    assert "worker-process-1" in str(exc_info.value)

    # Nếu tiến trình 2 gọi factory next tự động -> Bỏ qua ACF-03 đang bị khóa và chọn task kế tiếp ACF-05
    plan_2 = orch_2.next_task(dry_run=False, owner_id="worker-process-2")
    assert plan_2.claimed is True
    assert plan_2.task_id == "ACF-05"


def test_atomic_race_condition_across_parallel_workers(tmp_path: Path):
    """Kiểm tra 10 luồng/worker đua nhau claim cùng 1 task đồng thời: chỉ đúng 1 worker thắng."""
    lock_manager = TaskLockManager(lock_dir=tmp_path / "locks")

    def _try_claim(worker_idx: int) -> bool:
        try:
            lock_manager.claim_task("ACF-RACE-01", owner_id=f"worker-{worker_idx}", pid=1000 + worker_idx)
            return True
        except TaskAlreadyClaimedError:
            return False

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(_try_claim, range(10)))

    # Chỉ duy nhất 1 worker được phép claim thành công, 9 worker còn lại nhận TaskAlreadyClaimedError
    assert results.count(True) == 1
    assert results.count(False) == 9


def test_cli_status_and_next_dry_run_commands(monkeypatch, orchestrator_env, capsys):
    """Kiểm tra lệnh CLI `factory status` và `factory next --dry-run --json`."""
    orch, lock_manager, _ = orchestrator_env
    monkeypatch.setattr("src.orchestrator.cli.SequentialOrchestrator", lambda: orch)

    # 1. Chạy factory status --json
    exit_code = cli_main(["status", "--json"])
    assert exit_code == 0
    out_status = json.loads(capsys.readouterr().out)
    assert len(out_status["ready_tasks"]) == 2
    assert len(out_status["blocked_tasks"]) == 1

    # 2. Chạy factory next --dry-run --json nhiều lần qua CLI
    for _ in range(3):
        exit_code = cli_main(["next", "--dry-run", "--json"])
        assert exit_code == 0
        out_next = json.loads(capsys.readouterr().out)
        assert out_next["dry_run"] is True
        assert out_next["claimed"] is False
        assert out_next["task_id"] == "ACF-03"

    # Xác nhận CLI dry-run không tạo khóa
    assert lock_manager.is_claimed("ACF-03") is False
