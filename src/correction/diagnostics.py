"""Diagnostics analyzer for extracting root causes and failure details from test outputs."""

import re
from typing import List, Set
from src.quality.models import QualityGateSummary, SingleCommandResult
from src.quality.parser import strip_ansi
from .models import FailureDiagnosis

PYTEST_FAILED_LINE_REGEX = re.compile(
    r"(?:FAILED|ERROR)\s+([a-zA-Z0-9_\-\.\/\\]+::[a-zA-Z0-9_\-\.\[\]]+)",
    re.IGNORECASE
)
EXCEPTION_REGEX = re.compile(
    r"\b([A-Z][a-zA-Z0-9_]*(?:Error|Exception))\b"
)
FILE_LINE_REGEX = re.compile(
    r'(?:File "([^"]+)", line \d+|([a-zA-Z0-9_\-\.\/\\]+\.py):\d+)'
)


def extract_related_files(text: str) -> List[str]:
    """Extract distinct source/test file paths mentioned in traceback or error log."""
    matches: Set[str] = set()
    for match in FILE_LINE_REGEX.finditer(text):
        f1, f2 = match.groups()
        found = f1 or f2
        if found:
            # Normalize path
            norm = found.replace("\\", "/").strip()
            # Exclude standard library or site-packages paths
            if "site-packages" not in norm and "lib/python" not in norm and "AppData" not in norm:
                matches.add(norm)
    return sorted(list(matches))


def extract_root_cause_snippet(text: str, max_lines: int = 15) -> str:
    """Extract the most relevant error lines or traceback from output."""
    clean_lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    if not clean_lines:
        return ""

    # Priority 1: Pytest error lines starting with 'E   ' or 'E '
    pytest_errs = [line for line in clean_lines if line.startswith("E ") or line.startswith("E   ")]
    if pytest_errs:
        return "\n".join(pytest_errs[:max_lines])

    # Priority 2: Look for 'Traceback (most recent call last):' section
    tb_indices = [i for i, line in enumerate(clean_lines) if "Traceback (most recent call last)" in line]
    if tb_indices:
        last_tb_idx = tb_indices[-1]
        snippet_lines = clean_lines[last_tb_idx:last_tb_idx + max_lines]
        return "\n".join(snippet_lines)

    # Priority 3: Last N lines
    return "\n".join(clean_lines[-max_lines:])


def diagnose_command_failure(cmd_res: SingleCommandResult) -> FailureDiagnosis:
    """Analyze a single failed command and return structured FailureDiagnosis."""
    raw_text = f"{cmd_res.stdout}\n{cmd_res.stderr}"
    clean_text = strip_ansi(raw_text)

    # 1. Timeout special case
    if cmd_res.timed_out:
        return FailureDiagnosis(
            command=cmd_res.command,
            exit_code=cmd_res.exit_code,
            error_summary=cmd_res.summary or "Command timed out",
            error_type="TimeoutExpired",
            failed_tests=[],
            root_cause_snippet=cmd_res.error_message or f"Execution exceeded {cmd_res.duration_seconds}s.",
            related_files=[],
            suggested_action="Kiểm tra vòng lặp vô hạn, deadlock hoặc tối ưu tốc độ thực thi của lệnh kiểm thử.",
        )

    # 2. Forbidden command special case
    if cmd_res.exit_code == 126 and "forbidden" in clean_text.lower():
        return FailureDiagnosis(
            command=cmd_res.command,
            exit_code=cmd_res.exit_code,
            error_summary="Forbidden command blocked by safety policy",
            error_type="ForbiddenCommandError",
            failed_tests=[],
            root_cause_snippet=cmd_res.stderr or "Command matched forbidden_commands pattern.",
            related_files=[],
            suggested_action="Xóa hoặc thay thế câu lệnh nguy hiểm theo quy định an toàn tại AGENTS.md.",
        )

    # 3. Pytest failure extraction
    failed_tests = list(dict.fromkeys(PYTEST_FAILED_LINE_REGEX.findall(clean_text)))

    # 4. Error type extraction
    error_types = EXCEPTION_REGEX.findall(clean_text)
    detected_error_type = error_types[-1] if error_types else None

    # 5. Related files
    related_files = extract_related_files(clean_text)

    # 6. Root cause snippet
    snippet = extract_root_cause_snippet(clean_text)

    # 7. Suggested action
    suggested = "Kiểm tra mã nguồn và điều chỉnh logic để thỏa mãn test case bị trượt."
    if detected_error_type == "AssertionError":
        suggested = "Giá trị trả về hoặc trạng thái thực tế không khớp với mong đợi trong assertion."
    elif detected_error_type in ("ModuleNotFoundError", "ImportError"):
        suggested = "Kiểm tra lại import path hoặc các module/file cần tạo mới."
    elif detected_error_type == "TypeError":
        suggested = "Kiểm tra kiểu dữ liệu của tham số và giá trị trả về."

    return FailureDiagnosis(
        command=cmd_res.command,
        exit_code=cmd_res.exit_code,
        error_summary=cmd_res.summary or f"Command failed with exit code {cmd_res.exit_code}",
        error_type=detected_error_type,
        failed_tests=failed_tests,
        root_cause_snippet=snippet,
        related_files=related_files,
        suggested_action=suggested,
    )


def analyze_failures(summary: QualityGateSummary) -> List[FailureDiagnosis]:
    """Diagnose all failing commands from a QualityGateSummary."""
    diagnoses: List[FailureDiagnosis] = []
    for cmd_res in summary.command_results:
        if not cmd_res.passed:
            diagnoses.append(diagnose_command_failure(cmd_res))
    return diagnoses
