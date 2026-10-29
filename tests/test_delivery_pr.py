from src.vcs.git_guard import GitGuard
from src.vcs.models import DiffScopeResult
"""Unit tests for Delivery packaging and Pull Request automation (ACF-10)."""

import json
import os
import subprocess
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from src.contracts.task import TaskConstraints, TaskContract, TaskScope, TaskStatus
from src.delivery.exceptions import DeliveryError, QualityGateNotPassedError
from src.delivery.formatter import (
    build_conventional_commit,
    build_pr_body,
    derive_scope,
)
from src.delivery.github_client import (
    GitHubAPIError,
    GitHubClient,
    parse_github_remote,
)
from src.delivery.models import PRStatus, PullRequestPackage
from src.delivery.pr_manager import PullRequestManager
from src.orchestrator.cli import main as cli_main
from src.quality.models import (
    QualityGateStatus,
    QualityGateSummary,
    SingleCommandResult,
)
from src.storage.models import RunRecord, RunStatus, TestResultRecord
from src.storage.repository import RunRepository


def test_derive_scope():
    contract = TaskContract(
        id="ACF-TEST",
        objective="Implement Delivery",
        scope=TaskScope(in_scope=["src/delivery/*", "tests/*"]),
        acceptance_criteria=["Done"],
        test_commands=["pytest"]
    )
    assert derive_scope(contract) == "delivery"


def test_build_conventional_commit():
    contract = TaskContract(
        id="ACF-10",
        objective="Automated PR Generation",
        scope=TaskScope(in_scope=["src/delivery/*"]),
        acceptance_criteria=["Tiêu chí 1", "Tiêu chí 2"],
        test_commands=["pytest"]
    )
    msg = build_conventional_commit(contract)
    assert msg.startswith("feat(delivery): automated pr generation (Ref: ACF-10)")
    assert "Tiêu chí 1" in msg
    assert "Tiêu chí 2" in msg
    assert "src/delivery/*" in msg


def test_build_pr_body():
    contract = TaskContract(
        id="ACF-10",
        objective="Automated PR Generation",
        scope=TaskScope(in_scope=["src/delivery/*"]),
        acceptance_criteria=["Tiêu chí 1"],
        test_commands=["pytest"]
    )
    q_summary = QualityGateSummary(
        passed=True,
        total_commands=1,
        passed_commands=1,
        total_duration_seconds=1.2,
        command_results=[
            SingleCommandResult(
                command="pytest",
                exit_code=0,
                passed=True,
                duration_seconds=1.2,
                summary="5 passed",
            )
        ]
    )
    run = RunRecord(run_id="run_test", task_id="ACF-10", attempt_count=1)

    body = build_pr_body(
        task_contract=contract,
        quality_summary=q_summary,
        diff_files=["src/delivery/models.py"],
        run_record=run,
        commit_sha="abcdef123456",
    )
    assert "[ACF-10] Báo Cáo Nghiệm Thu & Đề Xuất Merge" in body
    assert "run_test" in body
    assert "abcdef12" in body
    assert "src/delivery/models.py" in body
    assert "1/1 PASS" in body
    assert "[x] Tiêu chí 1" in body
    assert "Self-Check Checklist" in body


def test_parse_github_remote():
    # Embedded credentials
    owner, repo, token = parse_github_remote("https://user:ghp_secret123@github.com/myorg/myrepo.git")
    assert owner == "myorg"
    assert repo == "myrepo"
    assert token == "ghp_secret123"

    # Standard HTTPS
    owner2, repo2, token2 = parse_github_remote("https://github.com/hienluong99/AI-Coding-Factory.git")
    assert owner2 == "hienluong99"
    assert repo2 == "AI-Coding-Factory"
    assert token2 is None

    # SSH
    owner3, repo3, token3 = parse_github_remote("git@github.com:hienluong99/AI-Coding-Factory.git")
    assert owner3 == "hienluong99"
    assert repo3 == "AI-Coding-Factory"
    assert token3 is None


def test_github_client_create_pr_success():
    client = GitHubClient(token="mock_token")

    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps({
        "html_url": "https://github.com/myorg/myrepo/pull/42",
        "number": 42,
        "id": 123456,
        "state": "open"
    }).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        res = client.create_pull_request(
            owner="myorg",
            repo="myrepo",
            title="feat: my test pr",
            head="task/ACF-10",
            base="main",
            body="my pr body",
        )
        assert res["url"] == "https://github.com/myorg/myrepo/pull/42"
        assert res["number"] == 42
        assert res["created"] is True


def test_github_client_missing_token_raises():
    client = GitHubClient(token=None)
    with pytest.raises(GitHubAPIError, match="Không có GitHub Token"):
        client.create_pull_request(
            owner="myorg",
            repo="myrepo",
            title="feat: my test pr",
            head="task/ACF-10",
        )


def test_pr_manager_rejects_when_quality_gate_failed(tmp_path):
    repo = RunRepository(db_path=tmp_path / "factory.db")
    run = repo.create_run(task_id="ACF-TEST-FAIL", max_attempts=3)

    # Record failed attempt
    repo.record_attempt(
        run_id=run.run_id,
        status=RunStatus.FAILED,
        test_results=[
            TestResultRecord(command="pytest", passed=False, exit_code=1)
        ]
    )

    task_file = tmp_path / "tasks" / "ACF-TEST-FAIL.json"
    task_file.parent.mkdir(parents=True, exist_ok=True)
    task_file.write_text(json.dumps({
        "id": "ACF-TEST-FAIL",
        "objective": "Must pass tests",
        "scope": {"in_scope": ["src/*"]},
        "acceptance_criteria": ["All pass"],
        "test_commands": ["pytest"]
    }), encoding="utf-8")

    mgr = PullRequestManager(
        repo_root=tmp_path,
        run_repo=repo,
        runs_dir=tmp_path / "runs",
        tasks_dir=tmp_path / "tasks"
    )

    with pytest.raises(QualityGateNotPassedError, match="Quality Gate chưa đạt"):
        mgr.package_and_deliver(task_id="ACF-TEST-FAIL", run_id=run.run_id)


def test_pr_manager_successful_local_bundle(tmp_path):
    repo = RunRepository(db_path=tmp_path / "factory.db")
    run = repo.create_run(task_id="ACF-TEST-PASS", max_attempts=3)

    # Record passed attempt
    repo.record_attempt(
        run_id=run.run_id,
        status=RunStatus.SUCCESS,
        test_results=[
            TestResultRecord(command="pytest", passed=True, exit_code=0, summary="10 passed")
        ]
    )

    task_file = tmp_path / "tasks" / "ACF-TEST-PASS.json"
    task_file.parent.mkdir(parents=True, exist_ok=True)
    task_file.write_text(json.dumps({
        "id": "ACF-TEST-PASS",
        "objective": "Successful PR Packaging",
        "scope": {"in_scope": ["src/delivery/*"]},
        "acceptance_criteria": ["Pass criteria"],
        "test_commands": ["pytest"],
        "status": "completed"
    }), encoding="utf-8")

    mgr = PullRequestManager(
        repo_root=tmp_path,
        run_repo=repo,
        runs_dir=tmp_path / "runs",
        tasks_dir=tmp_path / "tasks"
    )

    # Deliver with push_to_remote=False and skip_remote_api=True (local bundle mode)
    with patch.object(PullRequestManager, "_commit_uncommitted_changes", return_value="mock_sha_1234"):
        package = mgr.package_and_deliver(
            task_id="ACF-TEST-PASS",
            run_id=run.run_id,
            push_to_remote=False,
            skip_remote_api=True
        )

    assert package.status == PRStatus.LOCAL_BUNDLE
    assert package.task_id == "ACF-TEST-PASS"
    assert "feat(delivery): successful pr packaging" in package.title
    assert package.summary_artifact_path is not None
    assert package.json_artifact_path is not None

    # Verify summary and json files written on disk
    summary_path = tmp_path / package.summary_artifact_path
    assert summary_path.exists()
    assert "[ACF-TEST-PASS]" in summary_path.read_text(encoding="utf-8")

    json_path = tmp_path / package.json_artifact_path
    assert json_path.exists()
    saved_data = json.loads(json_path.read_text(encoding="utf-8"))
    assert saved_data["task_id"] == "ACF-TEST-PASS"
    assert saved_data["status"] == "local_bundle"

    # Verify SQLite updated
    updated_run = repo.get_run(run.run_id)
    assert updated_run.status == RunStatus.SUCCESS
    assert "pr" in updated_run.metadata
    assert updated_run.metadata["pr"]["task_id"] == "ACF-TEST-PASS"


def test_cli_pr_command(tmp_path, capsys):
    db_file = tmp_path / "factory.db"
    repo = RunRepository(db_path=db_file)
    run = repo.create_run(task_id="ACF-CLI-PR", max_attempts=3)

    repo.record_attempt(
        run_id=run.run_id,
        status=RunStatus.SUCCESS,
        test_results=[
            TestResultRecord(command="pytest", passed=True, exit_code=0)
        ]
    )

    task_file = Path("tasks") / "ACF-CLI-PR.json"
    task_file.write_text(json.dumps({
        "id": "ACF-CLI-PR",
        "objective": "CLI Delivery Test",
        "scope": {"in_scope": ["src/delivery/*"]},
        "acceptance_criteria": ["Criteria A"],
        "test_commands": ["pytest"],
        "status": "completed"
    }), encoding="utf-8")

    old_env = os.environ.get("FACTORY_DB_PATH")
    try:
        os.environ["FACTORY_DB_PATH"] = str(db_file)
        with patch.object(
            GitGuard,
            "ensure_diff_in_scope",
            return_value=DiffScopeResult(is_valid=True, checked_files=["src/delivery/models.py"])
        ), patch.object(PullRequestManager, "_commit_uncommitted_changes", return_value="mock_cli_sha"):
            exit_code = cli_main(["pr", "ACF-CLI-PR", "--run-id", run.run_id, "--local", "--no-push", "--json"])
        assert exit_code == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["task_id"] == "ACF-CLI-PR"
        assert data["status"] == "local_bundle"
    finally:
        if task_file.exists():
            task_file.unlink()
        if old_env is not None:
            os.environ["FACTORY_DB_PATH"] = old_env
        else:
            os.environ.pop("FACTORY_DB_PATH", None)
