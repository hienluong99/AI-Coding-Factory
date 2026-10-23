"""CLI for AI Coding Factory Orchestrator (`factory status`, `factory next`, `factory handoff`)."""

import argparse
import json
import sys
from typing import List, Optional

from src.beads.exceptions import BeadsError, TaskBlockedError
from src.handoff.manager import AntigravityHandoffManager
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
        description="AI Coding Factory — Sequential Orchestrator & Antigravity Handoff CLI"
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

    # factory handoff
    handoff_parser = subparsers.add_parser(
        "handoff",
        help="Chuẩn bị prompt, hiển thị thông tin task/branch/acceptance criteria và bàn giao cho Antigravity"
    )
    handoff_parser.add_argument(
        "--task-id",
        dest="task_id",
        help="Chỉ định mã task cần bàn giao (mặc định tự chọn task ready tiếp theo)"
    )
    handoff_parser.add_argument(
        "--owner",
        dest="owner_id",
        help="Định danh tiến trình/worker thực hiện claim"
    )
    handoff_group = handoff_parser.add_mutually_exclusive_group()
    handoff_group.add_argument(
        "-y", "--yes",
        action="store_true",
        help="Xác nhận đồng ý bàn giao ngay cho Antigravity"
    )
    handoff_group.add_argument(
        "-n", "--no",
        action="store_true",
        help="Từ chối bắt đầu sau khi tạo gói chuẩn bị bàn giao"
    )
    handoff_group.add_argument(
        "--wait",
        action="store_true",
        help="Chỉ tạo gói chuẩn bị và giữ ở trạng thái waiting_for_human để xác nhận sau"
    )
    handoff_parser.add_argument(
        "--notes",
        dest="notes",
        help="Ghi chú của người dùng khi xác nhận bàn giao"
    )
    handoff_parser.add_argument(
        "--json",
        action="store_true",
        help="Xuất kết quả ở định dạng JSON"
    )

    # factory confirm
    confirm_parser = subparsers.add_parser(
        "confirm",
        help="Xác nhận hoặc từ chối một run đang ở trạng thái waiting_for_human"
    )
    confirm_parser.add_argument("run_id", help="Mã run_id đang chờ người dùng xác nhận")
    confirm_group = confirm_parser.add_mutually_exclusive_group(required=True)
    confirm_group.add_argument(
        "-y", "--yes",
        action="store_true",
        help="Phê duyệt bàn giao và bắt đầu thực thi"
    )
    confirm_group.add_argument(
        "-n", "--no",
        action="store_true",
        help="Từ chối bàn giao"
    )
    confirm_parser.add_argument(
        "--notes",
        dest="notes",
        help="Ghi chú xác nhận"
    )
    confirm_parser.add_argument(
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

        elif args.command == "handoff":
            manager = AntigravityHandoffManager(orchestrator=orchestrator)
            output_fn = (lambda _msg: None) if args.json else print

            if args.wait:
                confirm_cb = None
            elif args.yes:
                confirm_cb = lambda _pkt: True
            elif args.no:
                confirm_cb = lambda _pkt: False
            else:
                def _prompt_human(pkt) -> bool:
                    ans = input(
                        f"\n>>> Xác nhận bàn giao task '{pkt.task_id}' (nhánh '{pkt.target_branch}') "
                        "cho Antigravity để bắt đầu? [y/N]: "
                    ).strip().lower()
                    return ans in ("y", "yes")
                confirm_cb = _prompt_human

            run_record, packet = manager.execute_handoff_workflow(
                task_id=args.task_id,
                owner_id=args.owner_id,
                confirm_callback=confirm_cb,
                output_fn=output_fn,
                notes=args.notes,
            )
            if args.json:
                print(json.dumps({
                    "run": run_record.model_dump(),
                    "handoff": packet.model_dump(),
                }, ensure_ascii=False, indent=2))
            return 0

        elif args.command == "confirm":
            manager = AntigravityHandoffManager(orchestrator=orchestrator)
            approved = bool(args.yes)
            run_record, packet = manager.record_confirmation(
                run_id=args.run_id,
                approved=approved,
                notes=args.notes,
            )
            if args.json:
                print(json.dumps({
                    "run": run_record.model_dump(),
                    "handoff": packet.model_dump(),
                }, ensure_ascii=False, indent=2))
            else:
                state_str = "ĐÃ PHÊ DUYỆT & BÀN GIAO (handed_off)" if approved else "ĐÃ TỪ CHỐI (rejected)"
                print(f"[PASS] Run '{run_record.run_id}' (Task: {packet.task_id}): {state_str} | Run status: {run_record.status.value}")
            return 0

        elif args.command == "release":
            released = orchestrator.lock_manager.release_task(args.task_id)
            if released:
                print(f"[PASS] Đã giải phóng khóa cho task '{args.task_id}'.")
                return 0
            else:
                print(f"[INFO] Task '{args.task_id}' hiện không bị khóa.")
                return 0

    except (TaskBlockedError, TaskAlreadyClaimedError, OrchestratorError, BeadsError, ValueError) as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
