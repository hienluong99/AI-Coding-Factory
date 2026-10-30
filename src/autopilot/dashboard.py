"""Factory Dashboard for monitoring DAG progress, active locks, and run records."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.orchestrator.engine import SequentialOrchestrator
from src.storage.models import RunStatus

from .models import DashboardSummary


class DashboardRenderer:
    """Collects and renders real-time operational status for AI Coding Factory."""

    def __init__(self, orchestrator: SequentialOrchestrator):
        self.orchestrator = orchestrator

    def get_summary(self) -> DashboardSummary:
        """Compile a complete DashboardSummary model."""
        git_guard = self.orchestrator.git_guard
        beads = self.orchestrator.beads
        lock_mgr = self.orchestrator.lock_manager
        run_repo = self.orchestrator._get_run_repo(read_only_if_missing=True)

        current_branch = git_guard.get_current_branch()
        is_clean = git_guard.is_working_tree_clean()

        ready_tasks = beads.get_ready_tasks()
        blocked_tasks = beads.get_blocked_tasks()
        active_claims = lock_mgr.list_claims()

        recent_runs = []
        total_runs = 0
        success_runs = 0
        blocked_runs = 0
        pending_runs = 0

        if run_repo:
            all_runs = run_repo.list_runs(limit=50)
            total_runs = len(all_runs)
            for r in all_runs:
                if r.status == RunStatus.SUCCESS:
                    success_runs += 1
                elif r.status == RunStatus.BLOCKED:
                    blocked_runs += 1
                elif r.status in (RunStatus.PENDING, RunStatus.WAITING_FOR_HUMAN, RunStatus.RUNNING):
                    pending_runs += 1
            recent_runs = all_runs[:10]

        return DashboardSummary(
            factory_name="AI Coding Factory",
            timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            git_branch=current_branch,
            working_tree_clean=is_clean,
            ready_count=len(ready_tasks),
            blocked_count=len(blocked_tasks),
            active_claims_count=len(active_claims),
            total_runs_count=total_runs,
            successful_runs_count=success_runs,
            blocked_runs_count=blocked_runs,
            pending_runs_count=pending_runs,
            ready_tasks=[t.model_dump() for t in ready_tasks],
            blocked_tasks=[t.model_dump() for t in blocked_tasks],
            active_claims=[c.model_dump() for c in active_claims],
            recent_runs=[r.model_dump() for r in recent_runs],
        )

    def render_text(self) -> str:
        """Render a formatted, user-friendly ASCII dashboard for terminal output."""
        summary = self.get_summary()
        tree_status = "CLEAN (Sạch)" if summary.working_tree_clean else "DIRTY (Chưa commit)"

        lines = [
            "=" * 78,
            f"🏭 AI CODING FACTORY — CENTRAL DASHBOARD",
            "=" * 78,
            f"  Nhánh VCS:     {summary.git_branch:<24} | Working Tree: {tree_status}",
            f"  Thời gian:     {summary.timestamp}",
            "-" * 78,
            "📊 TIẾN ĐỘ ĐỒ THỊ DAG & RUN METRICS:",
            f"  • Task sẵn sàng (Ready):   {summary.ready_count}",
            f"  • Task bị chặn (Blocked):  {summary.blocked_count}",
            f"  • Khóa đang giữ (Locks):   {summary.active_claims_count}",
            f"  • Tổng số lượt chạy:       {summary.total_runs_count} (✅ {summary.successful_runs_count} Đạt | ⛔ {summary.blocked_runs_count} Blocked | ⏳ {summary.pending_runs_count} Đang xử lý)",
            "-" * 78,
        ]

        # Section: Ready Tasks
        lines.append("📋 DANH SÁCH TASK SẴN SÀNG (READY):")
        if summary.ready_tasks:
            for t in summary.ready_tasks:
                lines.append(f"  • [{t['id']}] {t.get('title', 'N/A')} (priority: {t.get('priority', '-')})")
        else:
            lines.append("  (Không có task nào sẵn sàng)")

        lines.append("-" * 78)

        # Section: Blocked Tasks
        lines.append("🚫 DANH SÁCH TASK BỊ CHẶN (BLOCKED):")
        if summary.blocked_tasks:
            for t in summary.blocked_tasks:
                deps = ", ".join(d.get("depends_on_id", "") for d in t.get("dependencies", []))
                dep_info = f" (chặn bởi: {deps})" if deps else ""
                lines.append(f"  • [{t['id']}] {t.get('title', 'N/A')}{dep_info}")
        else:
            lines.append("  (Không có task nào bị chặn)")

        lines.append("-" * 78)

        # Section: Active Claims
        if summary.active_claims:
            lines.append("🔒 KHÓA TASK ĐANG THỰC THI (ACTIVE CLAIMS):")
            for c in summary.active_claims:
                lines.append(f"  • Task: {c['task_id']} | Chủ sở hữu: {c.get('owner_id')} | PID: {c.get('pid')}")
            lines.append("-" * 78)

        # Section: Recent Runs
        lines.append("🕒 LỊCH SỬ CÁC LẦN CHẠY GẦN ĐÂY:")
        if summary.recent_runs:
            for r in summary.recent_runs:
                status_icon = "✅" if r["status"] == "success" else ("⛔" if r["status"] == "blocked" else "⏳")
                lines.append(
                    f"  • {status_icon} [{r['task_id']}] Run {r['run_id'][:12]}... | Trạng thái: {r['status']:<10} | Lần thử: {r['attempt_count']}/{r['max_attempts']}"
                )
        else:
            lines.append("  (Chưa có lịch sử chạy nào)")

        lines.append("=" * 78)
        return "\n".join(lines)

    def render_json(self) -> Dict[str, Any]:
        """Return the dictionary representation for JSON CLI outputs."""
        return self.get_summary().model_dump()
