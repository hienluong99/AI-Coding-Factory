"""Output parsers for Quality Gate command results."""

import re
from typing import Optional
from src.storage.models import TestResultRecord
from .models import SingleCommandResult

ANSI_REGEX = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")

# Common test framework patterns
PYTEST_SUMMARY_REGEX = re.compile(
    r"=+\s+([0-9]+\s+(?:passed|failed|skipped|error|xfailed|xpassed).+?)\s+=+",
    re.IGNORECASE
)
PYTEST_SHORT_REGEX = re.compile(
    r"([0-9]+\s+(?:passed|failed|error|skipped)[^,\n]*(?:,\s*[0-9]+\s+[a-z]+)*\s+in\s+[0-9\.]+s?)",
    re.IGNORECASE
)
JEST_TESTS_REGEX = re.compile(r"Tests:\s+([^\n]+)", re.IGNORECASE)


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from terminal output."""
    if not text:
        return ""
    return ANSI_REGEX.sub("", text)


def parse_command_summary(command: str, stdout: str, stderr: str, exit_code: int) -> str:
    """
    Extract a concise, human-readable summary from command output.
    Supports Pytest, Linters (Ruff/Flake8/Black), Jest/NPM, and standard exit codes.
    """
    clean_stdout = strip_ansi(stdout)
    clean_stderr = strip_ansi(stderr)
    combined = f"{clean_stdout}\n{clean_stderr}".strip()

    # 1. Pytest detection
    if "pytest" in command.lower() or "tests" in command.lower():
        # Match lines like: "= 44 passed in 19.65s ="
        match = PYTEST_SUMMARY_REGEX.search(clean_stdout)
        if match:
            return match.group(1).strip()

        match_short = PYTEST_SHORT_REGEX.search(clean_stdout)
        if match_short:
            return match_short.group(1).strip()

        if "no tests ran" in clean_stdout.lower():
            return "No tests ran"

    # 2. Jest / NPM test detection
    jest_match = JEST_TESTS_REGEX.search(clean_stdout)
    if jest_match:
        return f"Jest: {jest_match.group(1).strip()}"

    # 3. Linter detections
    if "ruff" in command.lower() or "flake8" in command.lower() or "black" in command.lower():
        if exit_code == 0:
            if "all checks passed" in clean_stdout.lower():
                return "All checks passed"
            if "reformatted" in clean_stdout.lower():
                return clean_stdout.strip().splitlines()[-1]
            return "Linter check passed (0 errors)"
        else:
            # Look for error count
            err_lines = [line.strip() for line in clean_stdout.splitlines() if line.strip()]
            if err_lines:
                return f"Linter violations found ({len(err_lines)} lines)"

    # 4. Fallback based on exit code and last non-empty line
    if exit_code == 0:
        return "PASS (Exit code 0)"
    else:
        last_err = ""
        for line in reversed(clean_stderr.splitlines() or clean_stdout.splitlines()):
            if line.strip():
                last_err = line.strip()
                break
        if last_err and len(last_err) <= 80:
            return f"FAIL (Exit code {exit_code}): {last_err}"
        return f"FAIL (Exit code {exit_code})"


def to_test_result_record(command_result: SingleCommandResult) -> TestResultRecord:
    """Convert a SingleCommandResult into a persistent TestResultRecord for SQLite."""
    return TestResultRecord(
        command=command_result.command,
        passed=command_result.passed,
        exit_code=command_result.exit_code,
        summary=command_result.summary or "",
        stdout=command_result.stdout or "",
        stderr=command_result.stderr or "",
        executed_at=command_result.executed_at,
    )
