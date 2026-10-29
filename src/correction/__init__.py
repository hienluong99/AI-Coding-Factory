"""Self-Correction and Blocked handling module (ACF-09)."""

from .diagnostics import analyze_failures, diagnose_command_failure
from .engine import SelfCorrectionEngine
from .models import (
    BlockedDiagnosticReport,
    CorrectionAction,
    CorrectionDecision,
    FailureDiagnosis,
)
from .prompt_builder import (
    build_blocked_diagnostic_report,
    build_fix_prompt,
    format_blocked_markdown_report,
)

__all__ = [
    "BlockedDiagnosticReport",
    "CorrectionAction",
    "CorrectionDecision",
    "FailureDiagnosis",
    "SelfCorrectionEngine",
    "analyze_failures",
    "build_blocked_diagnostic_report",
    "build_fix_prompt",
    "diagnose_command_failure",
    "format_blocked_markdown_report",
]
