"""Client for integrating with the Beads (bd) CLI."""

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .exceptions import BeadsCLIError, BeadsError, TaskBlockedError
from .models import BeadsDependency, BeadsTask


class BeadsClient:
    """Interface to communicate with Beads issue tracker via CLI."""

    def __init__(
        self,
        workspace_dir: Optional[Union[str, Path]] = None,
        cli_command: Optional[str] = None
    ):
        self.workspace_dir = Path(workspace_dir or os.getenv("BEADS_WORKSPACE", ".")).resolve()
        self.cli_command = cli_command or os.getenv("BEADS_CLI_PATH", "npx bd")

    def _run_command(self, args: List[str]) -> str:
        """Execute a Beads CLI command in the workspace directory."""
        cmd = f"{self.cli_command} " + " ".join(args)
        
        try:
            result = subprocess.run(
                cmd,
                shell=True,
                cwd=str(self.workspace_dir),
                capture_output=True,
                text=True,
                encoding="utf-8"
            )
        except Exception as e:
            raise BeadsError(f"Không thể thực thi lệnh '{cmd}': {e}") from e

        if result.returncode != 0:
            raise BeadsCLIError(
                command=cmd,
                exit_code=result.returncode,
                stderr=result.stderr.strip()
            )

        return result.stdout.strip()

    def init(
        self,
        prefix: str = "ACF",
        skip_agents: bool = True,
        skip_hooks: bool = True
    ) -> bool:
        """Initialize Beads repository if missing."""
        args = [
            "init",
            "--init-if-missing",
            "--non-interactive",
            f"--prefix {prefix}"
        ]
        if skip_agents:
            args.append("--skip-agents")
        if skip_hooks:
            args.append("--skip-hooks")

        self._run_command(args)
        return True

    def get_ready_tasks(self) -> List[BeadsTask]:
        """Fetch all tasks that are ready for execution (not blocked)."""
        output = self._run_command(["ready", "--json"])
        if not output:
            return []

        try:
            data = json.loads(output)
        except json.JSONDecodeError as e:
            raise BeadsError(f"Phản hồi JSON từ 'bd ready --json' không hợp lệ: {output}") from e

        tasks: List[BeadsTask] = []
        for item in data:
            task = BeadsTask.model_validate(item)
            task.raw = item
            tasks.append(task)
        return tasks

    def get_blocked_tasks(self) -> List[BeadsTask]:
        """Fetch all tasks that are currently blocked by unfinished dependencies."""
        output = self._run_command(["blocked", "--json"])
        if not output:
            return []

        try:
            data = json.loads(output)
        except json.JSONDecodeError as e:
            raise BeadsError(f"Phản hồi JSON từ 'bd blocked --json' không hợp lệ: {output}") from e

        tasks: List[BeadsTask] = []
        for item in data:
            task = BeadsTask.model_validate(item)
            task.raw = item
            tasks.append(task)
        return tasks

    def get_task(self, task_id: str) -> BeadsTask:
        """Retrieve task details including dependencies."""
        output = self._run_command(["show", task_id, "--json"])
        if not output:
            raise BeadsError(f"Task '{task_id}' không tồn tại hoặc không có dữ liệu trả về.")

        try:
            data = json.loads(output)
        except json.JSONDecodeError as e:
            raise BeadsError(f"Phản hồi JSON từ 'bd show {task_id} --json' không hợp lệ: {output}") from e

        # `bd show` returns a list of matching items
        item = data[0] if isinstance(data, list) and data else data
        task = BeadsTask.model_validate(item)
        task.raw = item if isinstance(item, dict) else {}
        return task

    def is_task_ready(self, task_id: str) -> bool:
        """Check if a specific task ID is currently ready to be dispatched."""
        ready_tasks = self.get_ready_tasks()
        return any(t.id == task_id for t in ready_tasks)

    def ensure_task_ready_for_dispatch(self, task_id: str) -> BeadsTask:
        """Validate that a task is strictly ready for dispatch.
        
        Raises:
            TaskBlockedError: If the task is blocked by unfinished dependencies.
        """
        task = self.get_task(task_id)

        # Check blocked list
        blocked_tasks = self.get_blocked_tasks()
        for b in blocked_tasks:
            if b.id == task_id:
                blockers = b.blocked_by or [d.target_id for d in b.dependencies]
                raise TaskBlockedError(task_id=task_id, blocked_by=blockers)

        # Double check against ready list
        ready_tasks = self.get_ready_tasks()
        if not any(r.id == task_id for r in ready_tasks):
            # Not ready, extract dependencies that might be open
            unmet_deps = [
                d.target_id for d in task.dependencies
                if d.status and d.status != "closed"
            ]
            raise TaskBlockedError(task_id=task_id, blocked_by=unmet_deps or ["Unsatisfied dependencies"])

        return task
