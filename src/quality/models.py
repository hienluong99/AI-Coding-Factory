"""Models for automated Quality Gates execution and test command results."""

from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field


def utc_now_iso() -> str:
    """Return current UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()


class QualityGateStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    TIMEOUT = "timeout"
    ERROR = "error"


class SingleCommandResult(BaseModel):
    """Result of an individual test or linter command execution."""
    __test__ = False

    command: str = Field(..., description="Câu lệnh kiểm thử đã thực thi")
    exit_code: int = Field(default=0, description="Mã thoát của tiến trình")
    passed: bool = Field(default=True, description="Trạng thái hoàn thành thành công")
    duration_seconds: float = Field(default=0.0, description="Thời gian thực thi tính bằng giây")
    summary: Optional[str] = Field(default="", description="Tóm tắt kết quả phân tích cú pháp")
    stdout: Optional[str] = Field(default="", description="Standard output log")
    stderr: Optional[str] = Field(default="", description="Standard error log")
    timed_out: bool = Field(default=False, description="Đánh dấu nếu lệnh bị hết hạn thời gian")
    error_message: Optional[str] = Field(default=None, description="Thông điệp lỗi nếu có ngoại lệ")
    executed_at: str = Field(default_factory=utc_now_iso, description="Thời điểm bắt đầu thực thi")


class QualityGateSummary(BaseModel):
    """Aggregated summary of all test commands executed in a Quality Gate run."""
    __test__ = False

    run_id: Optional[str] = Field(default=None, description="Mã run tương ứng trong storage")
    task_id: Optional[str] = Field(default=None, description="Mã task contract")
    attempt_number: Optional[int] = Field(default=None, description="Số thứ tự của attempt (1-based)")
    status: QualityGateStatus = Field(default=QualityGateStatus.PASS, description="Trạng thái tổng hợp")
    passed: bool = Field(default=True, description="True nếu 100% câu lệnh pass")
    total_commands: int = Field(default=0, description="Tổng số câu lệnh kiểm thử")
    passed_commands: int = Field(default=0, description="Số câu lệnh đạt")
    failed_commands: int = Field(default=0, description="Số câu lệnh thất bại hoặc timeout")
    command_results: List[SingleCommandResult] = Field(default_factory=list, description="Chi tiết từng câu lệnh")
    log_path: Optional[str] = Field(default=None, description="Đường dẫn file log của attempt")
    started_at: str = Field(default_factory=utc_now_iso, description="Thời điểm bắt đầu")
    finished_at: Optional[str] = Field(default=None, description="Thời điểm kết thúc")
    total_duration_seconds: float = Field(default=0.0, description="Tổng thời gian thực thi tất cả câu lệnh")
    error_message: Optional[str] = Field(default=None, description="Thông điệp lỗi nếu bị gián đoạn")
