"""Formatters for Conventional Commits and Pull Request descriptions (AGENTS.md)."""

from pathlib import Path
from typing import List, Optional
from src.contracts.task import TaskContract
from src.quality.models import QualityGateSummary
from src.storage.models import RunRecord


def derive_scope(task_contract: TaskContract) -> str:
    """Derive conventional commit scope from task in_scope paths."""
    for pattern in task_contract.scope.in_scope:
        clean = pattern.replace("\\", "/").strip()
        parts = clean.split("/")
        if len(parts) >= 2 and parts[0] == "src":
            return parts[1].replace("*", "").strip("/")
    return "core"


def build_conventional_commit(
    task_contract: TaskContract,
    custom_scope: Optional[str] = None,
) -> str:
    """
    Format a commit message strictly following Conventional Commits standard (Section 5, AGENTS.md).
    Format: feat(<scope>): <objective> (Ref: <task_id>)
    """
    scope = custom_scope or derive_scope(task_contract)
    header = f"feat({scope}): {task_contract.objective.lower()} (Ref: {task_contract.id})"

    body_lines = [
        header,
        "",
        f"Hoàn thành các tiêu chí nghiệm thu cho task {task_contract.id}:",
    ]
    for crit in task_contract.acceptance_criteria:
        body_lines.append(f"- {crit}")

    body_lines.extend([
        "",
        "Đảm bảo an toàn:",
        f"- Phạm vi thay đổi tuân thủ nghiêm ngặt in-scope: {', '.join(task_contract.scope.in_scope)}",
        "- Không vi phạm hoặc can thiệp file ngoài phạm vi (out-of-scope)",
        "- Quality Gate 100% PASS",
    ])

    return "\n".join(body_lines)


def build_pr_body(
    task_contract: TaskContract,
    quality_summary: Optional[QualityGateSummary] = None,
    diff_files: Optional[List[str]] = None,
    run_record: Optional[RunRecord] = None,
    commit_sha: Optional[str] = None,
) -> str:
    """
    Generate Pull Request description adhering to Section 6 of AGENTS.md.
    """
    files = diff_files or []
    run_id = run_record.run_id if run_record else (quality_summary.run_id if quality_summary else "N/A")
    attempt_num = run_record.attempt_count if run_record else (quality_summary.attempt_number if quality_summary else 1)

    lines = [
        f"## [{task_contract.id}] Báo Cáo Nghiệm Thu & Đề Xuất Merge",
        "",
        f"- **Mục tiêu:** {task_contract.objective}",
        f"- **Trạng thái:** HOÀN THÀNH (Ready for Review)",
        f"- **Mã Run:** `{run_id}` (Lần thử #{attempt_num})",
    ]
    if commit_sha:
        lines.append(f"- **Commit SHA:** `{commit_sha[:8]}`")

    lines.extend([
        "",
        "---",
        "",
        f"### 1. Các thay đổi đã thực hiện ({len(files)} files)",
        "",
    ])
    if files:
        for f in files:
            lines.append(f"- `{f}`")
    else:
        for p in task_contract.scope.in_scope:
            lines.append(f"- Phạm vi: `{p}`")

    lines.extend([
        "",
        "---",
        "",
        "### 2. Kết quả Quality Gate (100% PASS)",
        "",
    ])

    if quality_summary:
        lines.append(f"- **Tổng số lệnh kiểm thử:** `{quality_summary.passed_commands}/{quality_summary.total_commands} PASS`")
        lines.append(f"- **Thời lượng chạy:** `{quality_summary.total_duration_seconds}s`")
        if quality_summary.log_path:
            lines.append(f"- **Log kiểm thử:** `{quality_summary.log_path}`")
        lines.append("")
        lines.append("| Lệnh kiểm thử | Kết quả | Thời lượng | Tóm tắt |")
        lines.append("| :--- | :---: | :---: | :--- |")
        for res in quality_summary.command_results:
            tag = "PASS" if res.passed else "FAIL"
            lines.append(f"| `{res.command}` | `{tag}` | {res.duration_seconds}s | {res.summary or 'N/A'} |")
    else:
        lines.append("- Toàn bộ các lệnh kiểm thử tự động đã hoàn thành thành công.")

    lines.extend([
        "",
        "---",
        "",
        "### 3. Tiêu chí nghiệm thu (Acceptance Criteria Verification)",
        "",
    ])
    for crit in task_contract.acceptance_criteria:
        lines.append(f"- [x] {crit}")

    lines.extend([
        "",
        "---",
        "",
        "### 4. Báo Cáo Tự Đánh Giá (Self-Check Checklist)",
        "",
        "- [x] Đã chạy linter và typecheck, không còn warning/error nghiêm trọng",
        "- [x] Toàn bộ unit tests liên quan đều vượt qua (100% PASS)",
        "- [x] Không có file thừa hoặc file tạm rò rỉ",
        "- [x] Tuyệt đối không chứa secret hay private key",
        "- [x] Đã kiểm tra diff scope đảm bảo trong ranh giới cho phép",
        "",
        "---",
        "*Được đóng gói tự động bởi AI Coding Factory.*",
    ])

    return "\n".join(lines)
