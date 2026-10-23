"""Tests for Git working tree protection, task branch isolation, and diff scope guard."""

import os
from pathlib import Path
import subprocess
from unittest.mock import MagicMock
import pytest

from src.beads.models import BeadsTask
from src.handoff.manager import AntigravityHandoffManager
from src.orchestrator.cli import main as cli_main
from src.orchestrator.engine import SequentialOrchestrator
from src.orchestrator.lock import TaskLockManager
from src.vcs.exceptions import DirtyWorkingTreeError, OutOfScopeDiffError
from src.vcs.git_guard import GitGuard


@pytest.fixture
def git_repo(tmp_path: Path):
    """Set up an isolated Git repository in tmp_path with an initial commit."""
    def _run(*args):
        subprocess.run(["git"] + list(args), cwd=str(tmp_path), check=True, capture_output=True, text=True)

    _run("init", "-b", "main")
    _run("config", "user.name", "Test User")
    _run("config", "user.email", "test@user.local")

    # Initial file and commit
    (tmp_path / "README.md").write_text("# Initial", encoding="utf-8")
    _run("add", "README.md")
    _run("commit", "-m", "chore: initial commit")

    guard = GitGuard(repo_dir=tmp_path)
    return tmp_path, guard


def test_clean_working_tree_passes(git_repo):
    """Xác minh working tree sạch sẽ vượt qua kiểm tra an toàn."""
    _, guard = git_repo
    assert guard.is_working_tree_clean() is True
    status = guard.get_working_tree_status()
    assert status.is_clean is True
    assert len(status.dirty_files) == 0
    # Không ném ngoại lệ
    guard.ensure_clean_working_tree()


def test_dirty_working_tree_halts_factory_and_preserves_user_work(git_repo):
    """Acceptance Criteria: Working tree bẩn khiến Factory dừng an toàn và KHÔNG ghi đè code người dùng."""
    repo_dir, guard = git_repo

    # Người dùng đang có file đang sửa chưa commit
    user_file = repo_dir / "my_work.py"
    user_content = "def precious_user_algorithm(): return 42\n"
    user_file.write_text(user_content, encoding="utf-8")

    # 1. Kiểm tra trạng thái: Working tree bị bẩn (Dirty)
    assert guard.is_working_tree_clean() is False
    status = guard.get_working_tree_status()
    assert status.is_clean is False
    assert "my_work.py" in status.file_paths

    # 2. ensure_clean_working_tree phải ném DirtyWorkingTreeError
    with pytest.raises(DirtyWorkingTreeError) as exc_info:
        guard.ensure_clean_working_tree()
    assert "my_work.py" in str(exc_info.value)
    assert "Factory dừng an toàn" in str(exc_info.value)

    # 3. ensure_task_branch KHÔNG được phép chuyển nhánh hoặc ghi đè, dừng an toàn ngay lập tức
    with pytest.raises(DirtyWorkingTreeError):
        guard.ensure_task_branch(task_id="ACF-07")

    # 4. Xác nhận file người dùng được bảo toàn 100%, không bị mất hoặc ghi đè
    assert user_file.exists()
    assert user_file.read_text(encoding="utf-8") == user_content


def test_ensure_task_branch_creation_and_switch(git_repo):
    """Xác minh tạo hoặc chuyển sang branch riêng cho task một cách an toàn."""
    _, guard = git_repo

    # Tạo branch mới cho ACF-07
    branch = guard.ensure_task_branch("ACF-07")
    assert branch == "task/ACF-07"
    assert guard.get_current_branch() == "task/ACF-07"

    # Chuyển về main
    guard._run_git(["checkout", "main"])
    assert guard.get_current_branch() == "main"

    # Gọi lại ensure_task_branch -> checkout lại branch đã tồn tại
    branch_again = guard.ensure_task_branch("ACF-07")
    assert branch_again == "task/ACF-07"
    assert guard.get_current_branch() == "task/ACF-07"


def test_diff_scope_validation_in_scope_passes(git_repo):
    """Diff chứa các file nằm hoàn toàn trong in_scope được chấp thuận."""
    _, guard = git_repo

    in_scope = ["src/vcs/*", "tests/test_git_guard.py", "tasks/ACF-07.json"]
    out_of_scope = [".env", ".env.*", "*.pem", "*.key"]

    changed_files = [
        "src/vcs/git_guard.py",
        "src/vcs/models.py",
        "tests/test_git_guard.py"
    ]

    result = guard.validate_diff_scope(
        changed_files=changed_files,
        in_scope=in_scope,
        out_of_scope=out_of_scope,
        task_id="ACF-07"
    )

    assert result.is_valid is True
    assert len(result.in_scope_matches) == 3
    assert len(result.out_of_scope_violations) == 0
    assert len(result.unauthorized_files) == 0


def test_diff_scope_validation_out_of_scope_halts_safely(git_repo):
    """Acceptance Criteria: Diff ngoài phạm vi hoặc chạm file cấm khiến Factory dừng an toàn."""
    _, guard = git_repo

    in_scope = ["src/vcs/*", "tests/*"]
    out_of_scope = [".env", ".env.*", "*.pem", "*.key", ".git/*"]

    # Trường hợp 1: Chạm vào file cấm (.env)
    forbidden_diff = ["src/vcs/git_guard.py", ".env"]
    result_1 = guard.validate_diff_scope(
        changed_files=forbidden_diff,
        in_scope=in_scope,
        out_of_scope=out_of_scope,
        task_id="ACF-07"
    )
    assert result_1.is_valid is False
    assert ".env" in result_1.out_of_scope_violations

    # ensure_diff_in_scope ném OutOfScopeDiffError
    with pytest.raises(OutOfScopeDiffError) as exc_info:
        if not result_1.is_valid:
            raise OutOfScopeDiffError(
                unauthorized_files=result_1.unauthorized_files,
                out_of_scope_violations=result_1.out_of_scope_violations,
                task_id="ACF-07"
            )
    assert "chạm vào file cấm can thiệp" in str(exc_info.value).lower()

    # Trường hợp 2: Chạm vào file nằm ngoài in_scope (vd: config/production.yaml)
    outside_diff = ["src/vcs/git_guard.py", "config/production.yaml"]
    result_2 = guard.validate_diff_scope(
        changed_files=outside_diff,
        in_scope=in_scope,
        out_of_scope=out_of_scope,
        task_id="ACF-07"
    )
    assert result_2.is_valid is False
    assert "config/production.yaml" in result_2.unauthorized_files


def test_orchestrator_halts_when_working_tree_is_dirty(git_repo):
    """Orchestrator dừng an toàn và không thực hiện task next khi working tree có thay đổi chưa commit."""
    repo_dir, guard = git_repo

    mock_beads = MagicMock()
    ready_task = BeadsTask(id="ACF-07", title="Git Guard", status="open", priority=1)
    mock_beads.get_ready_tasks.return_value = [ready_task]
    mock_beads.get_blocked_tasks.return_value = []
    mock_beads.ensure_task_ready_for_dispatch.return_value = ready_task

    orch = SequentialOrchestrator(
        workspace_dir=repo_dir,
        beads_client=mock_beads,
        lock_manager=TaskLockManager(lock_dir=repo_dir / "runs" / "locks"),
        git_guard=guard,
        tasks_dir=repo_dir / "tasks",
        db_path=repo_dir / "runs" / "factory.db"
    )

    # 1. Chạy dry-run: Vẫn xem được action plan bình thường mà không bị chặn
    plan = orch.next_task(dry_run=True)
    assert plan.has_next is True

    # 2. Tạo một file chưa commit trong working tree
    (repo_dir / "uncommitted_user_file.txt").write_text("user data", encoding="utf-8")

    # 3. Khi chạy thật (dry_run=False): Orchestrator phải dừng an toàn bằng DirtyWorkingTreeError
    with pytest.raises(DirtyWorkingTreeError):
        orch.next_task(dry_run=False)

    # Khóa và database run record không được tạo
    assert not (repo_dir / "runs" / "factory.db").exists()
    assert orch.lock_manager.is_claimed("ACF-07") is False


def test_cli_guard_command(monkeypatch, git_repo, capsys):
    """Kiểm tra lệnh CLI `factory guard`."""
    repo_dir, guard = git_repo

    mock_orch = MagicMock()
    mock_orch.git_guard = guard
    monkeypatch.setattr("src.orchestrator.cli.SequentialOrchestrator", lambda: mock_orch)

    # 1. Working tree sạch -> factory guard PASS
    code = cli_main(["guard"])
    assert code == 0
    assert "[PASS] Working tree sạch sẽ" in capsys.readouterr().out

    # 2. Tạo file bẩn -> factory guard dừng an toàn và báo lỗi
    (repo_dir / "dirty_user_file.txt").write_text("data", encoding="utf-8")
    code = cli_main(["guard"])
    assert code == 1
    err_out = capsys.readouterr().err
    assert "Factory dừng an toàn" in err_out
    assert "dirty_user_file.txt" in err_out
