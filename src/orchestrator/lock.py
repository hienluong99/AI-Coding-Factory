"""Atomic cross-process task locking mechanism to prevent duplicate task claims."""

import json
import os
import socket
from pathlib import Path
from typing import List, Optional, Union

from src.storage.models import utc_now_iso
from .exceptions import TaskAlreadyClaimedError
from .models import TaskClaim


class TaskLockManager:
    """Manages atomic file-based locks for task claims across multiple processes."""

    def __init__(self, lock_dir: Optional[Union[str, Path]] = None):
        raw_dir = lock_dir or os.getenv("FACTORY_LOCK_DIR", "runs/locks")
        self.lock_dir = Path(raw_dir).resolve()

    def _lock_path_for(self, task_id: str) -> Path:
        safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in task_id)
        return self.lock_dir / f"{safe_id}.lock"

    def get_claim(self, task_id: str) -> Optional[TaskClaim]:
        """Read the current claim for a task if locked (read-only, no side effects)."""
        lock_path = self._lock_path_for(task_id)
        if not lock_path.exists():
            return None

        try:
            content = lock_path.read_text(encoding="utf-8").strip()
            if not content:
                return TaskClaim(
                    task_id=task_id,
                    owner_id="unknown",
                    pid=0,
                    lock_file=str(lock_path)
                )
            data = json.loads(content)
            return TaskClaim.model_validate(data)
        except Exception:
            return TaskClaim(
                task_id=task_id,
                owner_id="locked",
                pid=0,
                lock_file=str(lock_path)
            )

    def is_claimed(self, task_id: str) -> bool:
        """Return True if the task is currently claimed/locked (read-only)."""
        return self._lock_path_for(task_id).exists()

    def list_claims(self) -> List[TaskClaim]:
        """Return all active task claims (read-only)."""
        if not self.lock_dir.exists():
            return []

        claims: List[TaskClaim] = []
        for path in sorted(self.lock_dir.glob("*.lock")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                claims.append(TaskClaim.model_validate(data))
            except Exception:
                task_id = path.stem
                claims.append(TaskClaim(
                    task_id=task_id,
                    owner_id="locked",
                    pid=0,
                    lock_file=str(path)
                ))
        return claims

    def claim_task(
        self,
        task_id: str,
        owner_id: Optional[str] = None,
        pid: Optional[int] = None,
        run_id: Optional[str] = None
    ) -> TaskClaim:
        """Atomically claim a task using OS-level exclusive file creation (O_CREAT | O_EXCL).

        Raises:
            TaskAlreadyClaimedError: If another process has already claimed the task.
        """
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        lock_path = self._lock_path_for(task_id)

        actual_pid = pid if pid is not None else os.getpid()
        actual_owner = owner_id or f"{socket.gethostname()}:{actual_pid}"
        now = utc_now_iso()

        claim = TaskClaim(
            task_id=task_id,
            owner_id=actual_owner,
            pid=actual_pid,
            claimed_at=now,
            run_id=run_id,
            lock_file=str(lock_path)
        )

        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        try:
            fd = os.open(str(lock_path), flags)
        except FileExistsError as e:
            existing = self.get_claim(task_id)
            raise TaskAlreadyClaimedError(
                task_id=task_id,
                owner_id=existing.owner_id if existing else None,
                pid=existing.pid if existing else None,
                claimed_at=existing.claimed_at if existing else None
            ) from e

        try:
            payload = json.dumps(claim.model_dump(), ensure_ascii=False, indent=2)
            os.write(fd, payload.encode("utf-8"))
        finally:
            os.close(fd)

        return claim

    def release_task(self, task_id: str, owner_id: Optional[str] = None) -> bool:
        """Release a lock on a task. If owner_id is provided, verifies ownership first."""
        lock_path = self._lock_path_for(task_id)
        if not lock_path.exists():
            return False

        if owner_id is not None:
            existing = self.get_claim(task_id)
            if existing and existing.owner_id != owner_id:
                raise TaskAlreadyClaimedError(
                    task_id=task_id,
                    owner_id=existing.owner_id,
                    pid=existing.pid,
                    claimed_at=existing.claimed_at
                )

        try:
            lock_path.unlink()
            return True
        except FileNotFoundError:
            return False
