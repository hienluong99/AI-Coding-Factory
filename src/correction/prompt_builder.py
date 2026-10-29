"""Prompt builder and report generator for Self-Correction and Blocked States."""

import json
from typing import List, Optional
from src.contracts.task import TaskContract
from src.storage.models import RunRecord
from .models import BlockedDiagnosticReport, FailureDiagnosis, utc_now_iso


def build_fix_prompt(
    task_contract: TaskContract,
    run_record: RunRecord,
    failures: List[FailureDiagnosis],
    target_branch: Optional[str] = None,
) -> str:
    """
    Generate an actionable, structured fix prompt for an AI agent
    to resolve specific failures detected by Quality Gates.
    """
    attempt_num = run_record.attempt_count
    max_attempts = run_record.max_attempts
    remaining = max(0, max_attempts - attempt_num)
    branch = target_branch or f"task/{task_contract.id}"

    prompt_lines = [
        f"# Yêu Cầu Tự Sửa Lỗi (Self-Correction Attempt #{attempt_num}/{max_attempts})",
        "",
        f"- **Mã Task:** `{task_contract.id}`",
        f"- **Mã Run:** `{run_record.run_id}`",
        f"- **Mục tiêu:** {task_contract.objective}",
        f"- **Nhánh Git:** `{branch}`",
        f"- **Lượt sửa còn lại:** `{remaining}` (Nếu hết số lượt, Factory sẽ tự động đánh dấu BLOCKED)",
        "",
        "---",
        "",
        "## 1. Chi Tiết Lỗi Kiểm Thử Cần Khắc Phục",
        "",
    ]

    for idx, f in enumerate(failures, 1):
        prompt_lines.append(f"### Lỗi {idx}: Lệnh `{f.command}` (Exit Code: {f.exit_code})")
        prompt_lines.append(f"- **Tóm tắt:** {f.error_summary}")
        if f.error_type:
            prompt_lines.append(f"- **Loại ngoại lệ:** `{f.error_type}`")
        if f.failed_tests:
            prompt_lines.append("- **Test case bị trượt:**")
            for t in f.failed_tests:
                prompt_lines.append(f"  - `{t}`")
        if f.related_files:
            prompt_lines.append("- **File phát hiện liên quan:**")
            for rf in f.related_files:
                prompt_lines.append(f"  - `{rf}`")
        if f.suggested_action:
            prompt_lines.append(f"- **Gợi ý xử lý:** {f.suggested_action}")

        if f.root_cause_snippet:
            prompt_lines.append("\n**Log / Traceback trích xuất:**")
            prompt_lines.append("```text")
            prompt_lines.append(f.root_cause_snippet)
            prompt_lines.append("```")
        prompt_lines.append("\n---\n")

    prompt_lines.extend([
        "## 2. Tiêu Chí Nghiệm Thu (Acceptance Criteria)",
        "",
    ])
    for idx, crit in enumerate(task_contract.acceptance_criteria, 1):
        prompt_lines.append(f"{idx}. {crit}")

    prompt_lines.extend([
        "",
        "---",
        "",
        "## 3. Phạm Vi File & Quy Tắc An Toàn (Scope Rules)",
        "",
        "### Phạm vi ĐƯỢC PHÉP sửa đổi (In-scope):",
    ])
    for inc in task_contract.scope.in_scope:
        prompt_lines.append(f"- `{inc}`")

    prompt_lines.extend([
        "",
        "### Phạm vi TUYỆT ĐỐI CẤM (Out-of-scope):",
    ])
    for outc in task_contract.scope.out_of_scope:
        prompt_lines.append(f"- `{outc}`")

    prompt_lines.extend([
        "",
        "---",
        "",
        "## 4. Hướng Dẫn Hành Động Cho AI Coding Agent",
        "",
        "1. **Phân tích:** Đọc kỹ traceback và logic của test case bị trượt phía trên.",
        "2. **Thực thi:** Sửa lỗi mã nguồn hoặc cập nhật file test trong phạm vi `in_scope`. Không sửa ngoài phạm vi.",
        "3. **Kiểm tra lại:** Sau khi sửa, chạy lại Quality Gate bằng lệnh CLI:",
        f"   ```bash\n   python -m src.factory quality {task_contract.id} --run-id {run_record.run_id}\n   ```",
        "4. Nếu Quality Gate PASS (100%), quy trình sẽ tiếp tục bước đóng gói bàn giao.",
        "",
    ])

    return "\n".join(prompt_lines)


def build_blocked_diagnostic_report(
    task_contract: TaskContract,
    run_record: RunRecord,
    failures: List[FailureDiagnosis],
) -> BlockedDiagnosticReport:
    """Generate structured BlockedDiagnosticReport when max fix attempts are reached."""
    history: List[dict] = []
    for att in run_record.attempts:
        history.append({
            "attempt_number": att.attempt_number,
            "status": att.status.value,
            "started_at": att.started_at,
            "finished_at": att.finished_at,
            "log_path": att.log_path,
            "error_message": att.error_message,
            "test_count": len(att.test_results),
            "passed_test_count": sum(1 for t in att.test_results if t.passed),
        })

    guidance = [
        f"Kiểm tra log chi tiết từng lần thử tại thư mục runs/{run_record.run_id}/.",
        "Đánh giá lại logic của test case: có thể test quá khắt khe hoặc mâu thuẫn với yêu cầu hiện tại.",
        "Nếu lỗi do thiếu dependency môi trường hoặc hệ thống bên ngoài, hãy bổ sung và cập nhật cấu hình.",
        f"Sau khi xử lý thủ công, có thể reset lượt thử hoặc giải phóng khóa bằng: python -m src.factory release {task_contract.id}.",
    ]

    return BlockedDiagnosticReport(
        run_id=run_record.run_id,
        task_id=task_contract.id,
        total_attempts=run_record.attempt_count,
        max_attempts=run_record.max_attempts,
        final_status="blocked",
        generated_at=utc_now_iso(),
        failures=failures,
        operator_guidance=guidance,
        attempt_history_summary=history,
    )


def format_blocked_markdown_report(report: BlockedDiagnosticReport, task_contract: TaskContract) -> str:
    """Format BlockedDiagnosticReport into a clear, readable markdown document."""
    lines = [
        f"# 🚨 Báo Cáo Chẩn Đoán Sự Cố — Task Blocked [{report.task_id}]",
        "",
        f"- **Mã Task:** `{report.task_id}` — {task_contract.objective}",
        f"- **Mã Run:** `{report.run_id}`",
        f"- **Số lần thử:** `{report.total_attempts}/{report.max_attempts}` (Đạt ngưỡng giới hạn tối đa)",
        f"- **Trạng thái:** `BLOCKED`",
        f"- **Thời điểm tạo:** `{report.generated_at}`",
        "",
        "---",
        "",
        "## 1. Lịch Sử Các Lần Thử (Attempt History)",
        "",
        "| Attempt | Trạng thái | Started At | Log Path | Kết quả test |",
        "| :---: | :---: | :---: | :---: | :---: |",
    ]

    for item in report.attempt_history_summary:
        lines.append(
            f"| #{item['attempt_number']} | `{item['status']}` | {item['started_at']} | `{item['log_path'] or 'N/A'}` | {item['passed_test_count']}/{item['test_count']} passed |"
        )

    lines.extend([
        "",
        "---",
        "",
        "## 2. Nguyên Nhân Thất Bại Gần Nhất",
        "",
    ])

    for idx, f in enumerate(report.failures, 1):
        lines.append(f"### Lỗi {idx}: `{f.command}`")
        lines.append(f"- **Tóm tắt:** {f.error_summary}")
        if f.error_type:
            lines.append(f"- **Ngoại lệ:** `{f.error_type}`")
        if f.failed_tests:
            lines.append("- **Test cases:** " + ", ".join(f"`{t}`" for t in f.failed_tests))
        if f.root_cause_snippet:
            lines.append("```text")
            lines.append(f.root_cause_snippet)
            lines.append("```")
        lines.append("")

    lines.extend([
        "---",
        "",
        "## 3. Hướng Dẫn Can Thiệp Dành Cho Kỹ Sư Vận Hành (Human Intervention)",
        "",
    ])
    for g in report.operator_guidance:
        lines.append(f"- {g}")

    lines.append("")
    return "\n".join(lines)
