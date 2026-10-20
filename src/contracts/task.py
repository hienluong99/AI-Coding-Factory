"""Task Contract models and validation schema for AI Coding Factory."""

from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field, field_validator, model_validator


class TaskStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    BLOCKED = "blocked"


class TaskScope(BaseModel):
    """Scope definition defining boundaries for agent file operations."""
    in_scope: List[str] = Field(
        ...,
        description="List of files, directories, or modules the agent is authorized to modify."
    )
    out_of_scope: List[str] = Field(
        default_factory=lambda: [
            ".env",
            ".env.*",
            "*.pem",
            "*.key",
            ".git/*",
        ],
        description="List of protected files, directories, or systems the agent must NEVER modify."
    )

    @field_validator("in_scope")
    @classmethod
    def validate_in_scope_not_empty(cls, v: List[str]) -> List[str]:
        cleaned = [item.strip() for item in v if isinstance(item, str) and item.strip()]
        if not cleaned:
            raise ValueError("Phạm vi 'in_scope' không được để trống và phải có ít nhất 1 đường dẫn hợp lệ.")
        return cleaned

    @field_validator("out_of_scope")
    @classmethod
    def clean_out_of_scope(cls, v: List[str]) -> List[str]:
        return [item.strip() for item in v if isinstance(item, str) and item.strip()]


class TaskConstraints(BaseModel):
    """Execution constraints and safety boundaries."""
    max_fix_attempts: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Số lượt tối đa agent được phép tự động sửa lỗi (1 đến 10)."
    )
    timeout_seconds: int = Field(
        default=600,
        ge=30,
        description="Thời gian tối đa thực thi task tính bằng giây."
    )
    forbidden_commands: List[str] = Field(
        default_factory=lambda: [
            "rm -rf /",
            "git push -f",
            "git reset --hard",
            "del /s /q",
            "format",
        ],
        description="Danh sách các câu lệnh cấm agent thực thi."
    )
    branch_pattern: Optional[str] = Field(
        default="task/{id}",
        description="Quy chuẩn đặt tên branch cho task."
    )


class TaskContract(BaseModel):
    """Comprehensive Task Contract schema for AI Coding Factory."""
    id: str = Field(
        ...,
        description="Mã định danh duy nhất của task (ví dụ: ACF-02)."
    )
    objective: str = Field(
        ...,
        description="Mục tiêu cốt lõi của task cần đạt được."
    )
    description: Optional[str] = Field(
        default="",
        description="Mô tả chi tiết bối cảnh và yêu cầu kỹ thuật."
    )
    scope: TaskScope = Field(
        ...,
        description="Phạm vi file và giới hạn chỉnh sửa của agent."
    )
    constraints: TaskConstraints = Field(
        default_factory=TaskConstraints,
        description="Các ràng buộc an toàn và giới hạn tài nguyên."
    )
    acceptance_criteria: List[str] = Field(
        ...,
        description="Danh sách các tiêu chí nghiệm thu bắt buộc."
    )
    test_commands: List[str] = Field(
        ...,
        description="Danh sách các câu lệnh kiểm thử tự động để xác minh tiêu chí nghiệm thu."
    )
    dependencies: List[str] = Field(
        default_factory=list,
        description="Danh sách các task ID phụ thuộc phải hoàn thành trước task này."
    )
    status: TaskStatus = Field(
        default=TaskStatus.PENDING,
        description="Trạng thái hiện tại của task trong pipeline."
    )

    @field_validator("id")
    @classmethod
    def validate_task_id(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("Mã 'id' của task không được để trống.")
        # Only allow alphanumeric, dash, underscore
        if not all(c.isalnum() or c in "-_" for c in stripped):
            raise ValueError(f"Mã task '{stripped}' không hợp lệ. Chỉ được chứa chữ cái, số, dấu gạch ngang (-) và gạch dưới (_).")
        return stripped

    @field_validator("objective")
    @classmethod
    def validate_objective(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("Mục tiêu 'objective' không được để trống.")
        return stripped

    @field_validator("acceptance_criteria")
    @classmethod
    def validate_acceptance_criteria(cls, v: List[str]) -> List[str]:
        cleaned = [item.strip() for item in v if isinstance(item, str) and item.strip()]
        if not cleaned:
            raise ValueError("Danh sách tiêu chí nghiệm thu 'acceptance_criteria' không được để trống và phải có ít nhất 1 tiêu chí cụ thể.")
        return cleaned

    @field_validator("test_commands")
    @classmethod
    def validate_test_commands(cls, v: List[str]) -> List[str]:
        cleaned = [item.strip() for item in v if isinstance(item, str) and item.strip()]
        if not cleaned:
            raise ValueError("Danh sách lệnh kiểm thử 'test_commands' không được để trống và phải có ít nhất 1 câu lệnh hợp lệ.")
        return cleaned

    @model_validator(mode="after")
    def validate_cross_fields(self) -> "TaskContract":
        # Validate that in_scope does not contain out_of_scope items
        in_scope_set = set(self.scope.in_scope)
        out_of_scope_set = set(self.scope.out_of_scope)
        overlap = in_scope_set.intersection(out_of_scope_set)
        if overlap:
            raise ValueError(
                f"Xung đột phạm vi: Các mục sau vừa nằm trong 'in_scope' vừa nằm trong 'out_of_scope': {list(overlap)}"
            )
        return self
