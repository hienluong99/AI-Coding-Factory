"""Parser and validator for Task Contracts."""

import json
from pathlib import Path
from typing import Any, Dict, List, Union
from pydantic import ValidationError

from .exceptions import TaskValidationError
from .task import TaskContract


FIELD_LABELS = {
    "id": "Mã task (id)",
    "objective": "Mục tiêu task (objective)",
    "scope": "Phạm vi thực thi (scope)",
    "scope.in_scope": "Phạm vi file cho phép sửa (scope.in_scope)",
    "scope.out_of_scope": "Phạm vi file cấm sửa (scope.out_of_scope)",
    "constraints": "Ràng buộc an toàn (constraints)",
    "acceptance_criteria": "Tiêu chí nghiệm thu (acceptance_criteria)",
    "test_commands": "Lệnh kiểm thử (test_commands)",
    "dependencies": "Danh sách phụ thuộc (dependencies)",
}


def _format_error_message(err: Dict[str, Any]) -> str:
    loc = ".".join(str(item) for item in err.get("loc", []))
    msg = err.get("msg", "")
    err_type = err.get("type", "")
    field_label = FIELD_LABELS.get(loc, loc)

    if err_type == "missing":
        return f"Thiếu trường thông tin bắt buộc: {field_label}."
    
    # Clean up Pydantic prefixes if any
    if msg.startswith("Value error, "):
        msg = msg[len("Value error, "):]

    return f"Lỗi tại '{field_label}': {msg}"


def parse_task_dict(data: Dict[str, Any]) -> TaskContract:
    """Parse a task dictionary into a validated TaskContract.
    
    Raises:
        TaskValidationError: If validation fails with user-friendly error messages.
    """
    if not isinstance(data, dict):
        raise TaskValidationError(
            message="Dữ liệu task phải là một JSON Object (Dictionary).",
            errors=["Định dạng dữ liệu không phải là đối tượng hợp lệ."]
        )

    task_id = str(data.get("id")) if data.get("id") else None

    try:
        return TaskContract.model_validate(data)
    except ValidationError as e:
        formatted_errors: List[str] = []
        for error in e.errors():
            formatted_errors.append(_format_error_message(error))

        summary = f"Task contract '{task_id or 'UNKNOWN'}' không hợp lệ do thiếu hoặc sai tiêu chí bắt buộc."
        raise TaskValidationError(
            message=summary,
            errors=formatted_errors,
            task_id=task_id
        ) from e


def parse_task_json(json_str: str) -> TaskContract:
    """Parse a raw JSON string into a validated TaskContract."""
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        raise TaskValidationError(
            message=f"Lỗi cú pháp JSON: Không thể giải mã nội dung ({e.msg} tại dòng {e.lineno}, cột {e.colno}).",
            errors=[f"Cú pháp JSON không hợp lệ: {e}"]
        ) from e

    return parse_task_dict(data)


def parse_task_file(file_path: Union[str, Path]) -> TaskContract:
    """Read a task contract file (JSON) and return a validated TaskContract."""
    path = Path(file_path)
    if not path.exists():
        raise TaskValidationError(
            message=f"Không tìm thấy file task contract tại đường dẫn: {path}",
            errors=[f"Đường dẫn file không tồn tại: {path}"]
        )

    try:
        content = path.read_text(encoding="utf-8")
    except Exception as e:
        raise TaskValidationError(
            message=f"Không thể đọc file: {path} ({e})",
            errors=[str(e)]
        ) from e

    return parse_task_json(content)
