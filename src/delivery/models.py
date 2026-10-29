"""Models for delivery packaging and Pull Request automation (ACF-10)."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


def utc_now_iso() -> str:
    """Return current UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()


class PRStatus(str, Enum):
    """Status of the Pull Request operation."""
    CREATED = "created"            # Pull Request đã tạo thành công trên GitHub
    LOCAL_BUNDLE = "local_bundle"  # Gói PR đóng gói hoàn chỉnh cục bộ (offline / không có token)
    FAILED = "failed"              # Quá trình tạo PR gặp lỗi


class PullRequestPackage(BaseModel):
    """Structured delivery package and Pull Request metadata."""
    __test__ = False

    task_id: str = Field(..., description="Mã task contract")
    run_id: str = Field(..., description="Mã run tương ứng trong storage")
    title: str = Field(..., description="Tiêu đề commit & Pull Request")
    branch: str = Field(..., description="Nhánh nguồn chứa code task")
    base_branch: str = Field(default="main", description="Nhánh đích cần merge vào")
    commit_sha: Optional[str] = Field(default=None, description="SHA commit của đợt bàn giao")
    commit_message: str = Field(default="", description="Nội dung thông điệp commit")
    pr_body: str = Field(default="", description="Nội dung miêu tả Pull Request")
    pr_url: Optional[str] = Field(default=None, description="Đường dẫn PR trên GitHub nếu có")
    pr_number: Optional[int] = Field(default=None, description="Số thứ tự của Pull Request")
    status: PRStatus = Field(default=PRStatus.LOCAL_BUNDLE, description="Trạng thái phân phối")
    diff_files: List[str] = Field(default_factory=list, description="Danh sách file thay đổi")
    summary_artifact_path: Optional[str] = Field(default=None, description="Đường dẫn file pr_summary.md")
    json_artifact_path: Optional[str] = Field(default=None, description="Đường dẫn file pr_package.json")
    created_at: str = Field(default_factory=utc_now_iso, description="Thời điểm đóng gói")
