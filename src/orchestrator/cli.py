"""CLI for AI Coding Factory Orchestrator (`factory status`, `factory next`)."""

import argparse
import json
import sys
from typing import List, Optional

from src.beads.exceptions import BeadsError, TaskBlockedError
from .engine import SequentialOrchestrator
from .exceptions import OrchestratorError, TaskAlreadyClaimedError

# Ensure UTF-8 output on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="factory",
        description="AI Coding Factory — Sequential Orchestrator CLI"
    )
    subparsers = parser.add_subparsers(dest="command")

    # factory status
    status_parser = subparsers.add_parser(
        "status",
        help="Hiển thị trạng thái tổng quan của Factory (ready, blocked, claimed, runs)"
    )
    status_parser.add_argument(
        "--json",
        action="store_true",
        help="Xuất kết quả ở định dạng JSON"
    )

    # factory next
    next_parser = subparsers.add_parser(
        "next",
        help="Chọn tuần tự 1 task sẵn sàng tiếp theo để thực thi hoặc xem trước (--dry-run)"
    )
    next_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Chỉ xem hành động dự kiến, tuyệt đối không sửa code hoặc claim task"
    )
    next_parser.add_argument(
        "--task-id",
        dest="task_id",
        help="Chỉ định cụ thể mã task muốn chọn"
    )
    next_parser.add_argument(
        "--owner",
        dest="owner_id",
        help="Định danh tiến trình/worker thực hiện claim"
    )
    next_parser.add_argument(
        "--json",
        action="store_true",
        help="Xuất kết quả ở định dạng JSON"
    )

    # factory release
    release_parser = subparsers.add_parser(
        "release",
        help="Giải phóng khóa (lock) của một task"
    )
    release_parser.add_argument("task_id", help="Mã task cần giải phóng khóa")

    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 1

    orchestrator = SequentialOrchestrator()

    try:
        if args.command == "status":
            status = orchestrator.get_status()
            if args.json:
                print(json.dumps(status.model_dump(), ensure_ascii=False, indent=2))
                return 0

            claimed_ids = {c.task_id for c in status.active_claims}
            print("=== TRẠNG THÁI AI CODING FACTORY ===")
            print(f"1. Task sẵn sàng (Ready):    {len(status.ready_tasks)}")
            for t in status.ready_tasks:
                lock_tag = " [ĐANG ĐƯỢC CLAIM]" if t.id in claimed_ids else ""
                print(f"   - [{t.id}] {t.title} (status: {t.status}){lock_tag}")

            print(f"2. Task bị chặn (Blocked):   {len(status.blocked_tasks)}")
            for b in status.blocked_tasks:
                blockers = ", ".join(b.blocked_by) if b.blocked_by else "dependencies"
                print(f"   - [{b.id}] {b.title} (blocked by: {blockers})")

            print(f"3. Khóa đang giữ (Claims):   {len(status.active_claims)}")
            for c in status.active_claims:
                print(f"   - [{c.task_id}] owner={c.owner_id} (pid={c.pid}, at={c.claimed_at})")

            print(f"4. Lịch sử Run gần đây:      {len(status.recent_runs)}")
            for r in status.recent_runs[:5]:
                print(f"   - [{r.run_id}] task={r.task_id} | status={r.status.value.upper()} | attempts={r.attempt_count}/{r.max_attempts}")
            return 0

        elif args.command == "next":
            plan = orchestrator.next_task(
                dry_run=args.dry_run,
                task_id=args.task_id,
                owner_id=args.owner_id,
            )
            if args.json:
                print(json.dumps(plan.model_dump(), ensure_ascii=False, indent=2))
                return 0

            print(plan.message)
            if not plan.has_next:
                return 0

            print(f"  - Task ID:       {plan.task_id}")
            print(f"  - Tiêu đề:       {plan.title}")
            print(f"  - Chế độ:        {'DRY-RUN (Không sửa code / Không claim task)' if plan.dry_run else 'CLAIM & DISPATCH'}")
            print(f"  - Đã claim khóa: {'KHÔNG' if not plan.claimed else 'CÓ'}")
            if plan.run_id:
                print(f"  - Run ID:        {plan.run_id}")
            print(f"  - Nhánh dự kiến: {plan.target_branch}")
            print(f"  - Phạm vi sửa:   {', '.join(plan.in_scope)}")
            print("  - Các bước hành động dự kiến:")
            for step in plan.planned_actions:
                print(f"      {step}")
            return 0

        elif args.command == "release":
            released = orchestrator.lock_manager.release_task(args.task_id)
            if released:
                print(f"[PASS] Đã giải phóng khóa cho task '{args.task_id}'.")
                return 0
            else:
                print(f"[INFO] Task '{args.task_id}' hiện không bị khóa.")
                return 0

    except (TaskBlockedError, TaskAlreadyClaimedError, OrchestratorError, BeadsError) as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
