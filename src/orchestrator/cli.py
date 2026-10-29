"""CLI for AI Coding Factory Orchestrator (`factory status`, `factory next`, `factory handoff`, `factory guard`)."""

import argparse
import json
import sys
from typing import List, Optional

from src.beads.exceptions import BeadsError, TaskBlockedError
from src.handoff.manager import AntigravityHandoffManager
from src.vcs.exceptions import DirtyWorkingTreeError, GitGuardError, OutOfScopeDiffError
from .engine import SequentialOrchestrator
from src.delivery.exceptions import DeliveryError
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
        description="AI Coding Factory — Sequential Orchestrator & Git Guard CLI"
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

    # factory guard
    guard_parser = subparsers.add_parser(
        "guard",
        help="Kiểm tra working tree và kiểm tra phạm vi diff (diff scope guard)"
    )
    guard_parser.add_argument(
        "--task-id",
        dest="task_id",
        help="Mã task để kiểm tra diff so với in_scope và out_of_scope"
    )
    guard_parser.add_argument(
        "--base",
        dest="base_ref",
        default="main",
        help="Nhánh hoặc ref gốc để so sánh diff (mặc định: main)"
    )
    guard_parser.add_argument(
        "--json",
        action="store_true",
        help="Xuất kết quả ở định dạng JSON"
    )

    # factory quality / test
    quality_parser = subparsers.add_parser(
        "quality",
        aliases=["test"],
        help="Thực thi Quality Gates (test_commands) tự động, stream log và ghi nhận attempt"
    )
    quality_parser.add_argument("task_id", help="Mã task contract cần kiểm thử (vd: ACF-08)")
    quality_parser.add_argument(
        "--run-id",
        dest="run_id",
        help="Mã Run ID để gắn bản ghi attempt trong SQLite (tùy chọn)"
    )
    quality_parser.add_argument(
        "--timeout",
        dest="timeout",
        type=int,
        default=None,
        help="Giới hạn thời gian tối đa mỗi câu lệnh tính bằng giây"
    )
    quality_parser.add_argument(
        "--no-record",
        dest="no_record",
        action="store_true",
        help="Không ghi nhận kết quả attempt vào SQLite"
    )
    quality_parser.add_argument(
        "--json",
        action="store_true",
        help="Xuất kết quả ở định dạng JSON"
    )

    # factory fix / correct
    fix_parser = subparsers.add_parser(
        "fix",
        aliases=["correct"],
        help="Phân tích lỗi kiểm thử, sinh prompt tự sửa lỗi hoặc đánh dấu BLOCKED"
    )
    fix_parser.add_argument("run_id", help="Mã Run ID cần phân tích lỗi và sửa chữa")
    fix_parser.add_argument(
        "--json",
        action="store_true",
        help="Xuất kết quả ở định dạng JSON"
    )

    # factory pr / delivery
    pr_parser = subparsers.add_parser(
        "pr",
        aliases=["delivery"],
        help="Đóng gói bàn giao, tạo Conventional Commit và Pull Request"
    )
    pr_parser.add_argument("task_id", help="Mã task contract cần đóng gói bàn giao (vd: ACF-10)")
    pr_parser.add_argument(
        "--run-id",
        dest="run_id",
        help="Mã Run ID tương ứng (tùy chọn)"
    )
    pr_parser.add_argument(
        "--base",
        dest="base_branch",
        default="main",
        help="Nhánh đích của PR (mặc định: main)"
    )
    pr_parser.add_argument(
        "--no-push",
        dest="no_push",
        action="store_true",
        help="Không đẩy nhánh lên git remote"
    )
    pr_parser.add_argument(
        "--local",
        dest="local_only",
        action="store_true",
        help="Chỉ tạo gói bundle bàn giao cục bộ, bỏ qua gọi GitHub API"
    )
    pr_parser.add_argument(
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

        elif args.command == "guard":
            # Check working tree
            tree_status = orchestrator.git_guard.get_working_tree_status()
            if not tree_status.is_clean:
                raise DirtyWorkingTreeError(
                    dirty_files=tree_status.file_paths,
                    current_branch=tree_status.current_branch
                )

            diff_result = None
            if args.task_id:
                diff_result = orchestrator.validate_task_diff(args.task_id, base_ref=args.base_ref)

            if args.json:
                print(json.dumps({
                    "working_tree": tree_status.model_dump(),
                    "diff_result": diff_result.model_dump() if diff_result else None
                }, ensure_ascii=False, indent=2))
                return 0

            print(f"[PASS] Working tree sạch sẽ trên nhánh '{tree_status.current_branch}'.")
            if diff_result:
                print(f"[PASS] {diff_result.summary}")
            return 0

        elif args.command in ("quality", "test"):
            if not args.json:
                print(f"--> Bắt đầu chạy Quality Gates cho task '{args.task_id}'...")
            summary = orchestrator.run_quality_gates(
                task_id=args.task_id,
                run_id=args.run_id,
                timeout=args.timeout,
                record_in_db=not args.no_record,
            )
            if args.json:
                print(json.dumps(summary.model_dump(), ensure_ascii=False, indent=2))
                return 0 if summary.passed else 1

            status_tag = "[PASS]" if summary.passed else "[FAIL]"
            print(f"\n{status_tag} Kết quả Quality Gates: {summary.status.value.upper()}")
            print(f"  - Task ID:         {summary.task_id}")
            print(f"  - Run ID:          {summary.run_id}")
            print(f"  - Attempt:         #{summary.attempt_number}")
            print(f"  - Số lệnh passed:  {summary.passed_commands}/{summary.total_commands}")
            print(f"  - Thời lượng:      {summary.total_duration_seconds}s")
            print(f"  - Log file:        {summary.log_path}")

            for idx, cmd_res in enumerate(summary.command_results, 1):
                cmd_tag = "[OK]" if cmd_res.passed else "[FAIL]"
                print(f"    {cmd_tag} [CMD {idx}] {cmd_res.command} -> {cmd_res.summary} ({cmd_res.duration_seconds}s)")

            if not summary.passed:
                if summary.error_message:
                    print(f"\nChi tiết lỗi: {summary.error_message}", file=sys.stderr)
                return 1
            return 0

        elif args.command in ("fix", "correct"):
            decision = orchestrator.evaluate_correction(run_id=args.run_id)
            if args.json:
                print(json.dumps(decision.model_dump(), ensure_ascii=False, indent=2))
                return 0 if decision.passed or decision.action.value == "retry_fix" else 1

            if decision.passed:
                print(f"[PASS] {decision.message}")
                return 0

            if decision.action.value == "retry_fix":
                print(f"[RETRY_FIX] {decision.message}")
                print(f"  - Run ID:           {decision.run_id}")
                print(f"  - Lần thử:          #{decision.attempt_number}/{decision.max_attempts}")
                print(f"  - Số lỗi phát hiện: {len(decision.failures)}")
                print(f"  - Fix prompt file:  {decision.fix_prompt_path}")
                print(f"\n--> Agent hãy đọc file '{decision.fix_prompt_path}' để tiến hành sửa lỗi theo phạm vi in-scope.")
                return 0
            else:
                print(f"[BLOCKED] {decision.message}", file=sys.stderr)
                print(f"  - Run ID:           {decision.run_id}", file=sys.stderr)
                print(f"  - Lần thử:          #{decision.attempt_number}/{decision.max_attempts}", file=sys.stderr)
                print(f"  - Báo cáo sự cố:    {decision.diagnostic_report_path}", file=sys.stderr)
                return 1

        elif args.command in ("pr", "delivery"):
            pkg = orchestrator.deliver_task_pr(
                task_id=args.task_id,
                run_id=args.run_id,
                base_branch=args.base_branch,
                push_to_remote=not args.no_push,
                skip_remote_api=args.local_only,
            )
            if args.json:
                print(json.dumps(pkg.model_dump(), ensure_ascii=False, indent=2))
                return 0

            status_str = "GITHUB PULL REQUEST" if pkg.status.value == "created" else "LOCAL DELIVERY BUNDLE"
            print(f"[PASS] Đóng gói bàn giao thành công ({status_str})!")
            print(f"  - Task ID:        {pkg.task_id}")
            print(f"  - Run ID:         {pkg.run_id}")
            print(f"  - Tiêu đề PR:     {pkg.title}")
            print(f"  - Nhánh:          {pkg.branch} -> {pkg.base_branch}")
            if pkg.commit_sha:
                print(f"  - Commit SHA:     {pkg.commit_sha[:8]}")
            if pkg.pr_url:
                print(f"  - GitHub PR:      {pkg.pr_url}")
            print(f"  - Summary report: {pkg.summary_artifact_path}")
            print(f"  - Package JSON:   {pkg.json_artifact_path}")
            return 0

        elif args.command == "release":
            released = orchestrator.lock_manager.release_task(args.task_id)
            if released:
                print(f"[PASS] Đã giải phóng khóa cho task '{args.task_id}'.")
                return 0
            else:
                print(f"[INFO] Task '{args.task_id}' hiện không bị khóa.")
                return 0

    except (
        DirtyWorkingTreeError,
        OutOfScopeDiffError,
        GitGuardError,
        TaskBlockedError,
        TaskAlreadyClaimedError,
        OrchestratorError,
        DeliveryError,
        BeadsError,
        ValueError
    ) as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
