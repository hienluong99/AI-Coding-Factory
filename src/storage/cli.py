"""CLI tool to inspect and manage Run records in SQLite."""

import argparse
import json
import sys
from .models import RunStatus
from .repository import RunRepository

# Ensure UTF-8 output on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description="AI Coding Factory Run Record CLI")
    subparsers = parser.add_subparsers(dest="command")

    # List command
    list_parser = subparsers.add_parser("list", help="Danh sách các run record")
    list_parser.add_argument("--task-id", dest="task_id", help="Lọc theo task ID")
    list_parser.add_argument("--status", dest="status", help="Lọc theo trạng thái")
    list_parser.add_argument("--limit", dest="limit", type=int, default=20, help="Số lượng tối đa")

    # Show command
    show_parser = subparsers.add_parser("show", help="Xem chi tiết một run record")
    show_parser.add_argument("run_id", help="Mã run_id cần xem")

    # Create command
    create_parser = subparsers.add_parser("create", help="Tạo một run record mới")
    create_parser.add_argument("task_id", help="Mã task_id")
    create_parser.add_argument("--max-attempts", type=int, default=3, help="Số lần thử tối đa")

    args = parser.parse_args()
    repo = RunRepository()

    if args.command == "list":
        status_filter = RunStatus(args.status) if args.status else None
        runs = repo.list_runs(task_id=args.task_id, status=status_filter, limit=args.limit)
        print(f"=== DANH SÁCH RUN RECORDS ({len(runs)}) ===")
        for r in runs:
            print(f"- [{r.run_id}] Task: {r.task_id} | Status: {r.status.value.upper()} | Attempts: {r.attempt_count}/{r.max_attempts} | Created: {r.created_at}")
        return 0

    elif args.command == "show":
        run = repo.get_run(args.run_id)
        if not run:
            print(f"[FAIL] Không tìm thấy run_id: {args.run_id}")
            return 1
        print(f"=== CHI TIẾT RUN: {run.run_id} ===")
        print(f"Task ID:        {run.task_id}")
        print(f"Trạng thái:     {run.status.value.upper()}")
        print(f"Thời gian tạo:  {run.created_at}")
        print(f"Cập nhật cuối:  {run.updated_at}")
        print(f"Hoàn thành lúc: {run.finished_at or 'Chưa hoàn thành'}")
        print(f"Số lần thử:     {run.attempt_count} / {run.max_attempts}")
        print(f"Log path:       {run.log_path or 'N/A'}")
        print(f"Kết quả test:   {len(run.test_results)} lượt kiểm thử")
        for idx, t in enumerate(run.test_results, 1):
            res_str = "PASS" if t.passed else "FAIL"
            print(f"  [{idx}] [{res_str}] Lệnh: {t.command} (Mã: {t.exit_code}) - {t.summary}")
        if run.attempts:
            print(f"Lịch sử các lần thử ({len(run.attempts)}):")
            for a in run.attempts:
                print(f"  * Lần {a.attempt_number}: {a.status.value} ({a.started_at} -> {a.finished_at})")
        return 0

    elif args.command == "create":
        new_run = repo.create_run(task_id=args.task_id, max_attempts=args.max_attempts)
        print(f"[PASS] Đã tạo run record thành công: {new_run.run_id}")
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
