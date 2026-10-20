"""Command-line interface to validate task contract files."""

import sys
from pathlib import Path

# Ensure UTF-8 output on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from .exceptions import TaskValidationError
from .parser import parse_task_file


def main() -> int:
    if len(sys.argv) < 2:
        print("Sử dụng: python -m src.contracts.cli <đường_dẫn_task_contract.json> ...")
        return 1

    has_errors = False
    for path_str in sys.argv[1:]:
        path = Path(path_str)
        print(f"--> Đang kiểm tra task contract: {path} ...")
        try:
            task = parse_task_file(path)
            print(f" [PASS] Task contract '{task.id}' hợp lệ!")
            print(f"        Mục tiêu: {task.objective}")
            print(f"        Phạm vi in-scope: {', '.join(task.scope.in_scope)}")
            print(f"        Số tiêu chí nghiệm thu: {len(task.acceptance_criteria)}")
            print(f"        Lệnh kiểm thử: {', '.join(task.test_commands)}")
        except TaskValidationError as e:
            print(f" [FAIL] Task contract không hợp lệ:")
            print(str(e))
            has_errors = True
        except Exception as e:
            print(f" [ERROR] Lỗi không xác định: {e}")
            has_errors = True

    return 1 if has_errors else 0


if __name__ == "__main__":
    sys.exit(main())
