"""Unit tests for Task Contract schema, validation, and parsing."""

import json
from pathlib import Path
import pytest

from src.contracts.exceptions import TaskValidationError
from src.contracts.parser import parse_task_dict, parse_task_file, parse_task_json
from src.contracts.task import TaskContract, TaskStatus


@pytest.fixture
def valid_task_data():
    return {
        "id": "ACF-02",
        "objective": "Chuẩn hóa task contract",
        "description": "Xây dựng schema và parser kiểm tra tính hợp lệ của task contract.",
        "scope": {
            "in_scope": [
                "src/contracts/*",
                "tests/test_task_contract.py"
            ],
            "out_of_scope": [
                ".env",
                ".env.*"
            ]
        },
        "constraints": {
            "max_fix_attempts": 3,
            "timeout_seconds": 600,
            "forbidden_commands": ["rm -rf /", "git push -f"]
        },
        "acceptance_criteria": [
            "Có schema task gồm ID, mục tiêu, phạm vi, constraints",
            "Có acceptance criteria và lệnh kiểm thử",
            "Task thiếu tiêu chí bắt buộc bị từ chối"
        ],
        "test_commands": [
            "pytest tests/test_task_contract.py"
        ],
        "dependencies": ["ACF-01"],
        "status": "ready"
    }


def test_parse_valid_task_dict(valid_task_data):
    """Xác minh task hợp lệ được parse thành công và đầy đủ thông tin."""
    task = parse_task_dict(valid_task_data)
    assert isinstance(task, TaskContract)
    assert task.id == "ACF-02"
    assert task.objective == "Chuẩn hóa task contract"
    assert len(task.scope.in_scope) == 2
    assert ".env" in task.scope.out_of_scope
    assert task.constraints.max_fix_attempts == 3
    assert len(task.acceptance_criteria) == 3
    assert task.test_commands == ["pytest tests/test_task_contract.py"]
    assert task.dependencies == ["ACF-01"]
    assert task.status == TaskStatus.READY


def test_parse_sample_task_files():
    """Kiểm tra đọc và parse trực tiếp từ các file task mẫu trong thư mục tasks/."""
    tasks_dir = Path(__file__).resolve().parent.parent / "tasks"
    
    acf_01 = parse_task_file(tasks_dir / "ACF-01.json")
    assert acf_01.id == "ACF-01"
    assert acf_01.status == TaskStatus.COMPLETED

    acf_02 = parse_task_file(tasks_dir / "ACF-02.json")
    assert acf_02.id == "ACF-02"
    assert acf_02.status == TaskStatus.COMPLETED


def test_missing_id_rejected(valid_task_data):
    """Task thiếu ID phải bị từ chối với thông báo lỗi dễ hiểu."""
    del valid_task_data["id"]
    with pytest.raises(TaskValidationError) as exc_info:
        parse_task_dict(valid_task_data)
    
    err = exc_info.value
    assert "Mã task (id)" in str(err)
    assert "Thiếu trường thông tin bắt buộc" in str(err)


def test_empty_or_invalid_id(valid_task_data):
    """Task có ID rỗng hoặc chứa ký tự đặc biệt bị từ chối."""
    valid_task_data["id"] = "   "
    with pytest.raises(TaskValidationError) as exc_info:
        parse_task_dict(valid_task_data)
    assert "không được để trống" in str(exc_info.value)

    valid_task_data["id"] = "ACF 02!@#"
    with pytest.raises(TaskValidationError) as exc_info:
        parse_task_dict(valid_task_data)
    assert "không hợp lệ" in str(exc_info.value)


def test_missing_objective_rejected(valid_task_data):
    """Task thiếu mục tiêu (objective) phải bị từ chối."""
    del valid_task_data["objective"]
    with pytest.raises(TaskValidationError) as exc_info:
        parse_task_dict(valid_task_data)
    assert "objective" in str(exc_info.value)

    valid_task_data["objective"] = "   "
    with pytest.raises(TaskValidationError) as exc_info:
        parse_task_dict(valid_task_data)
    assert "không được để trống" in str(exc_info.value)


def test_missing_scope_rejected(valid_task_data):
    """Task thiếu phạm vi (scope) bị từ chối."""
    del valid_task_data["scope"]
    with pytest.raises(TaskValidationError) as exc_info:
        parse_task_dict(valid_task_data)
    assert "scope" in str(exc_info.value)


def test_empty_in_scope_rejected(valid_task_data):
    """Task có phạm vi in_scope rỗng bị từ chối."""
    valid_task_data["scope"]["in_scope"] = []
    with pytest.raises(TaskValidationError) as exc_info:
        parse_task_dict(valid_task_data)
    assert "in_scope" in str(exc_info.value)


def test_scope_conflict_rejected(valid_task_data):
    """Xung đột giữa in_scope và out_of_scope bị từ chối."""
    valid_task_data["scope"]["in_scope"].append(".env")
    with pytest.raises(TaskValidationError) as exc_info:
        parse_task_dict(valid_task_data)
    assert "Xung đột phạm vi" in str(exc_info.value)


def test_missing_acceptance_criteria_rejected(valid_task_data):
    """Task thiếu acceptance_criteria hoặc danh sách rỗng bị từ chối."""
    del valid_task_data["acceptance_criteria"]
    with pytest.raises(TaskValidationError) as exc_info:
        parse_task_dict(valid_task_data)
    assert "acceptance_criteria" in str(exc_info.value)

    valid_task_data["acceptance_criteria"] = []
    with pytest.raises(TaskValidationError) as exc_info:
        parse_task_dict(valid_task_data)
    assert "không được để trống" in str(exc_info.value)


def test_missing_test_commands_rejected(valid_task_data):
    """Task thiếu test_commands hoặc rỗng bị từ chối."""
    del valid_task_data["test_commands"]
    with pytest.raises(TaskValidationError) as exc_info:
        parse_task_dict(valid_task_data)
    assert "test_commands" in str(exc_info.value)

    valid_task_data["test_commands"] = []
    with pytest.raises(TaskValidationError) as exc_info:
        parse_task_dict(valid_task_data)
    assert "không được để trống" in str(exc_info.value)


def test_invalid_constraints_rejected(valid_task_data):
    """Constraints với max_fix_attempts < 1 hoặc timeout_seconds < 30 bị từ chối."""
    valid_task_data["constraints"]["max_fix_attempts"] = 0
    with pytest.raises(TaskValidationError) as exc_info:
        parse_task_dict(valid_task_data)
    assert "constraints" in str(exc_info.value)


def test_invalid_json_string():
    """Chuỗi JSON sai cú pháp trả về lỗi dễ hiểu."""
    malformed_json = '{"id": "ACF-02", "objective": "Lỗi cú pháp", '
    with pytest.raises(TaskValidationError) as exc_info:
        parse_task_json(malformed_json)
    assert "Lỗi cú pháp JSON" in str(exc_info.value)


def test_nonexistent_file():
    """Đọc file không tồn tại trả về lỗi rõ ràng."""
    with pytest.raises(TaskValidationError) as exc_info:
        parse_task_file("tasks/does_not_exist.json")
    assert "Không tìm thấy file" in str(exc_info.value)
