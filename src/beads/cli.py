"""CLI tool for interacting with Beads via Python integration."""

import sys
from .client import BeadsClient
from .exceptions import TaskBlockedError

# Ensure UTF-8 output on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


def main() -> int:
    if len(sys.argv) < 2:
        print("Sử dụng: python -m src.beads.cli <ready|blocked|show <id>|dispatch <id>>")
        return 1

    action = sys.argv[1].lower()
    client = BeadsClient()

    if action == "ready":
        tasks = client.get_ready_tasks()
        print(f"=== DANH SÁCH TASK SẴN SÀNG ({len(tasks)}) ===")
        for t in tasks:
            print(f"- [{t.id}] {t.title} (Trạng thái: {t.status})")
        return 0

    if action == "blocked":
        tasks = client.get_blocked_tasks()
        print(f"=== DANH SÁCH TASK BỊ BLOCK ({len(tasks)}) ===")
        for t in tasks:
            print(f"- [{t.id}] {t.title} (Bị chặn bởi: {', '.join(t.blocked_by)})")
        return 0

    if action == "show" and len(sys.argv) >= 3:
        task_id = sys.argv[2]
        task = client.get_task(task_id)
        print(f"=== CHI TIẾT TASK: {task.id} ===")
        print(f"Tiêu đề: {task.title}")
        print(f"Trạng thái: {task.status}")
        print(f"Mô tả: {task.description}")
        print(f"Số phụ thuộc: {len(task.dependencies)}")
        for dep in task.dependencies:
            print(f"  * Phụ thuộc vào: {dep.target_id} [{dep.status or 'N/A'}]")
        return 0

    if action == "dispatch" and len(sys.argv) >= 3:
        task_id = sys.argv[2]
        print(f"--> Kiểm tra tính sẵn sàng để dispatch task '{task_id}'...")
        try:
            task = client.ensure_task_ready_for_dispatch(task_id)
            print(f" [PASS] Task '{task.id}' hợp lệ và sẵn sàng để giao cho AI Agent!")
            return 0
        except TaskBlockedError as e:
            print(f" [REJECTED] {e}")
            return 1

    print(f"Lệnh không hợp lệ: {action}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
