"""Tests for Beads CLI integration and task dependency dispatching."""

import json
from unittest.mock import MagicMock, patch
import pytest

from src.beads.client import BeadsClient
from src.beads.exceptions import BeadsCLIError, BeadsError, TaskBlockedError
from src.beads.models import BeadsTask


@pytest.fixture
def mock_client():
    return BeadsClient(workspace_dir=".", cli_command="mock_bd")


def test_get_ready_tasks_mocked(mock_client):
    """Kiểm tra parse danh sách ready tasks từ JSON output."""
    sample_json = json.dumps([
        {
            "id": "ACF-03",
            "title": "Tích hợp Beads CLI",
            "description": "Mô tả",
            "status": "open",
            "priority": 2,
            "issue_type": "task",
            "dependencies": [
                {"issue_id": "ACF-03", "depends_on_id": "ACF-01", "type": "blocks", "status": "closed"}
            ]
        }
    ])

    with patch.object(mock_client, "_run_command", return_value=sample_json):
        tasks = mock_client.get_ready_tasks()
        assert len(tasks) == 1
        assert tasks[0].id == "ACF-03"
        assert tasks[0].title == "Tích hợp Beads CLI"
        assert tasks[0].status == "open"
        assert not tasks[0].is_blocked


def test_get_blocked_tasks_mocked(mock_client):
    """Kiểm tra parse danh sách blocked tasks từ JSON output."""
    sample_json = json.dumps([
        {
            "id": "ACF-04",
            "title": "Tự động hóa quality gates",
            "description": "Mô tả",
            "status": "open",
            "priority": 2,
            "blocked_by": ["ACF-03"],
            "blocked_by_count": 1
        }
    ])

    with patch.object(mock_client, "_run_command", return_value=sample_json):
        tasks = mock_client.get_blocked_tasks()
        assert len(tasks) == 1
        assert tasks[0].id == "ACF-04"
        assert tasks[0].blocked_by == ["ACF-03"]
        assert tasks[0].is_blocked is True


def test_ensure_task_ready_dispatches_ready_task(mock_client):
    """Task trong trạng thái ready được phép dispatch thành công."""
    ready_task_data = {
        "id": "ACF-03",
        "title": "Tích hợp Beads CLI",
        "status": "open",
        "dependencies": []
    }

    with patch.object(mock_client, "get_task", return_value=BeadsTask.model_validate(ready_task_data)), \
         patch.object(mock_client, "get_blocked_tasks", return_value=[]), \
         patch.object(mock_client, "get_ready_tasks", return_value=[BeadsTask.model_validate(ready_task_data)]):
        
        task = mock_client.ensure_task_ready_for_dispatch("ACF-03")
        assert task.id == "ACF-03"


def test_ensure_task_ready_rejects_blocked_task(mock_client):
    """Task bị block tuyệt đối không được tự triển khai và phải ném TaskBlockedError."""
    blocked_task_data = {
        "id": "ACF-04",
        "title": "Tự động hóa quality gates",
        "status": "open",
        "blocked_by": ["ACF-03"],
        "blocked_by_count": 1
    }

    with patch.object(mock_client, "get_task", return_value=BeadsTask.model_validate(blocked_task_data)), \
         patch.object(mock_client, "get_blocked_tasks", return_value=[BeadsTask.model_validate(blocked_task_data)]):
        
        with pytest.raises(TaskBlockedError) as exc_info:
            mock_client.ensure_task_ready_for_dispatch("ACF-04")

        err_msg = str(exc_info.value)
        assert "ACF-04" in err_msg
        assert "ACF-03" in err_msg
        assert "BLOCKED" in err_msg


def test_invalid_json_handling(mock_client):
    """Xử lý lỗi định dạng JSON trả về từ CLI."""
    with patch.object(mock_client, "_run_command", return_value="invalid json string"):
        with pytest.raises(BeadsError) as exc_info:
            mock_client.get_ready_tasks()
        assert "không hợp lệ" in str(exc_info.value)


def test_cli_error_handling(mock_client):
    """Xử lý ngoại lệ khi lệnh CLI thất bại (non-zero return code)."""
    with patch("subprocess.run") as mock_sub:
        mock_sub.return_value = MagicMock(returncode=1, stderr="Unknown flag --invalid", stdout="")
        with pytest.raises(BeadsCLIError) as exc_info:
            mock_client._run_command(["invalid"])
        assert exc_info.value.exit_code == 1
        assert "Unknown flag" in str(exc_info.value)


# ==========================================
# Integration tests with real local Beads DB
# ==========================================

def test_live_beads_ready_and_blocked():
    """Kiểm tra trực tiếp với database Beads thực tế đã khởi tạo trong repository."""
    client = BeadsClient()
    
    # ACF-03 phải nằm trong danh sách ready
    ready_tasks = client.get_ready_tasks()
    ready_ids = [t.id for t in ready_tasks]
    assert "ACF-03" in ready_ids

    # ACF-04 phải nằm trong danh sách blocked
    blocked_tasks = client.get_blocked_tasks()
    blocked_ids = [t.id for t in blocked_tasks]
    assert "ACF-04" in blocked_ids

    # Dispatch ACF-03 thành công
    dispatched = client.ensure_task_ready_for_dispatch("ACF-03")
    assert dispatched.id == "ACF-03"

    # Dispatch ACF-04 bị từ chối
    with pytest.raises(TaskBlockedError) as exc_info:
        client.ensure_task_ready_for_dispatch("ACF-04")
    assert "ACF-03" in str(exc_info.value)
