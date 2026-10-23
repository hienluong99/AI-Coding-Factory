"""Prompt generator and display formatter for Antigravity handoff."""

from pathlib import Path
from typing import Dict, List, Optional, Union

from src.contracts.task import TaskContract
from .models import HandoffContext, HandoffPacket


def gather_handoff_context(
    workspace_dir: Union[str, Path],
    contract: TaskContract,
    current_branch: str = "main",
    dependencies_status: Optional[Dict[str, str]] = None,
) -> HandoffContext:
    """Collect relevant workspace context for a task contract."""
    ws = Path(workspace_dir).resolve()
    branch_pattern = contract.constraints.branch_pattern or "task/{id}"
    target_branch = branch_pattern.replace("{id}", contract.id)

    existing_files: List[str] = []
    for pattern in contract.scope.in_scope:
        try:
            for matched in ws.glob(pattern):
                if matched.is_file():
                    rel = matched.relative_to(ws).as_posix()
                    if rel not in existing_files:
                        existing_files.append(rel)
        except Exception:
            continue

    deps_map: Dict[str, str] = {}
    if dependencies_status:
        deps_map.update(dependencies_status)
    else:
        for dep_id in contract.dependencies:
            deps_map[dep_id] = "completed"

    agents_md = "AGENTS.md" if (ws / "AGENTS.md").exists() else "(Chưa có AGENTS.md)"

    return HandoffContext(
        workspace_dir=ws.as_posix(),
        current_branch=current_branch,
        target_branch=target_branch,
        agents_md_path=agents_md,
        existing_in_scope_files=sorted(existing_files),
        dependencies_status=deps_map,
    )


def build_antigravity_prompt(
    contract: TaskContract,
    context: HandoffContext,
    run_id: str,
) -> str:
    """Generate a structured Markdown prompt from TaskContract and HandoffContext."""
    in_scope_lines = "\n".join(f"- `{item}`" for item in contract.scope.in_scope)
    out_of_scope_lines = "\n".join(f"- `{item}`" for item in contract.scope.out_of_scope)
    criteria_lines = "\n".join(f"- [ ] {item}" for item in contract.acceptance_criteria)
    test_cmd_lines = "\n".join(f"- `{cmd}`" for cmd in contract.test_commands)
    forbidden_lines = "\n".join(f"- `{cmd}`" for cmd in contract.constraints.forbidden_commands)

    if context.dependencies_status:
        deps_str = ", ".join(f"{k} ({v})" for k, v in context.dependencies_status.items())
    else:
        deps_str = "Không có"

    if context.existing_in_scope_files:
        existing_files_section = "\n".join(f"- `{f}`" for f in context.existing_in_scope_files)
    else:
        existing_files_section = "- (Chưa có file hiện hữu — tạo mới theo phạm vi in-scope)"

    description_text = contract.description.strip() or contract.objective

    return f"""# [AI CODING FACTORY — BÀN GIAO TASK CHO ANTIGRAVITY]

## 1. Thông Tin Định Danh & Nhánh Làm Việc
- **Task ID:** `{contract.id}`
- **Run ID:** `{run_id}`
- **Mục tiêu (Objective):** {contract.objective}
- **Nhánh làm việc (Target Branch):** `{context.target_branch}`
- **Phụ thuộc (Dependencies):** {deps_str}
- **Tài liệu quy tắc bắt buộc:** `{context.agents_md_path}`

## 2. Mô Tả Yêu Cầu & Bối Cảnh (Context)
{description_text}

### Các file hiện hữu trong phạm vi (`in_scope`):
{existing_files_section}

## 3. Phạm Vi File (File Scope)
### Được phép tạo/sửa (`in_scope`):
{in_scope_lines}

### Tuyệt đối cấm can thiệp (`out_of_scope`):
{out_of_scope_lines}

## 4. Ràng Buộc An Toàn (Constraints)
- **Số lần tự sửa lỗi tối đa (`max_fix_attempts`):** {contract.constraints.max_fix_attempts}
- **Thời gian thực thi tối đa (`timeout_seconds`):** {contract.constraints.timeout_seconds}s
- **Các lệnh bị cấm tuyệt đối:**
{forbidden_lines}

## 5. Tiêu Chí Nghiệm Thu (Acceptance Criteria)
{criteria_lines}

## 6. Lệnh Kiểm Thử Bắt Buộc (Quality Gates)
{test_cmd_lines}

## 7. Hướng Dẫn Thực Thi Dành Cho Antigravity
1. Kiểm tra và chuyển sang nhánh `{context.target_branch}` trước khi chỉnh sửa mã nguồn.
2. Triển khai mã nguồn và unit tests строго trong phạm vi `in_scope`.
3. Chạy toàn bộ lệnh kiểm thử ở Mục 6 cho đến khi PASS 100% (tối đa {contract.constraints.max_fix_attempts} lần thử sửa lỗi).
4. Báo cáo kết quả hoàn thành theo mẫu quy định trong `{context.agents_md_path}`.
"""


def format_handoff_display(packet: HandoffPacket) -> str:
    """Format a clear human-readable summary of the task, branch, and acceptance criteria."""
    criteria_formatted = "\n".join(
        f"  {idx}. {crit}" for idx, crit in enumerate(packet.acceptance_criteria, 1)
    )
    tests_formatted = ", ".join(packet.test_commands)
    in_scope_formatted = ", ".join(packet.in_scope)

    lines = [
        "================================================================",
        f"  BÀN GIAO TASK CHO ANTIGRAVITY (HANDOFF WORKFLOW)",
        "================================================================",
        f"  Task:              [{packet.task_id}] {packet.objective}",
        f"  Run ID:            {packet.run_id}",
        f"  Branch làm việc:   {packet.target_branch}",
        f"  Trạng thái Run:    {packet.status}",
        f"  Phạm vi (Scope):   {in_scope_formatted}",
        f"  Lệnh kiểm thử:     {tests_formatted}",
        "----------------------------------------------------------------",
        "  Tiêu chí nghiệm thu (Acceptance Criteria):",
        criteria_formatted,
        "----------------------------------------------------------------",
    ]
    if packet.prompt_path:
        lines.append(f"  File Prompt bàn giao: {packet.prompt_path}")
    if packet.packet_path:
        lines.append(f"  File Handoff Packet:  {packet.packet_path}")
    lines.append("================================================================")
    return "\n".join(lines)
