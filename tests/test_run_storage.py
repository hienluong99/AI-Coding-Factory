"""Unit and integration tests for Run records and SQLite persistence."""

import json
import sqlite3
from pathlib import Path
import pytest

from src.storage.migrations import init_and_migrate
from src.storage.models import RunRecord, RunStatus, TestResultRecord
from src.storage.repository import RunRepository


@pytest.fixture
def temp_db(tmp_path: Path):
    db_file = tmp_path / "factory_test.db"
    return db_file


def test_auto_schema_initialization_and_migration(temp_db: Path):
    """Xác minh schema và migrations tự động khởi tạo khi kết nối database mới."""
    assert not temp_db.exists()

    repo = RunRepository(db_path=temp_db)
    assert temp_db.exists()

    # Kiểm tra các bảng đã được tạo
    with sqlite3.connect(str(temp_db)) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = {row[0] for row in cursor.fetchall()}
        assert "schema_migrations" in tables
        assert "run_records" in tables
        assert "run_attempts" in tables

        # Kiểm tra version migration đã áp dụng
        cursor.execute("SELECT version, name FROM schema_migrations;")
        migrations = cursor.fetchall()
        assert len(migrations) >= 1
        assert migrations[0][0] == 1
        assert migrations[0][1] == "initial_run_records_and_attempts"

        # Kiểm tra tính idempotent (chạy lại migration không gây lỗi và không áp dụng thêm)
        new_applied = init_and_migrate(conn)
        assert new_applied == 0


def test_create_and_get_run_record(temp_db: Path):
    """Xác minh lưu run_id, task_id, timestamps, trạng thái và truy xuất chính xác."""
    repo = RunRepository(db_path=temp_db)

    run = repo.create_run(
        task_id="ACF-04",
        max_attempts=3,
        metadata={"agent": "antigravity", "model": "gemini"},
        log_path="runs/test_run.log"
    )

    assert run.run_id.startswith("run_ACF-04_")
    assert run.task_id == "ACF-04"
    assert run.status == RunStatus.PENDING
    assert run.attempt_count == 0
    assert run.max_attempts == 3
    assert run.log_path == "runs/test_run.log"
    assert run.metadata["agent"] == "antigravity"
    assert run.created_at is not None
    assert run.updated_at is not None
    assert run.finished_at is None

    # Lấy lại bản ghi từ database
    fetched = repo.get_run(run.run_id)
    assert fetched is not None
    assert fetched.run_id == run.run_id
    assert fetched.task_id == "ACF-04"
    assert fetched.status == RunStatus.PENDING


def test_record_attempt_and_test_results(temp_db: Path):
    """Xác minh lưu số lần thử, log path và kết quả kiểm thử qua từng lượt."""
    repo = RunRepository(db_path=temp_db)
    run = repo.create_run(task_id="ACF-04")

    # Lần thử 1: Thất bại do kiểm thử lỗi
    test_1 = TestResultRecord(
        command="pytest tests/test_task.py",
        passed=False,
        exit_code=1,
        summary="1 failed, 0 passed",
        stderr="AssertionError in line 45"
    )
    attempt_1 = repo.record_attempt(
        run_id=run.run_id,
        status=RunStatus.RUNNING,
        log_path="runs/attempt_1.log",
        test_results=[test_1],
        error_message="Unit test failed"
    )
    assert attempt_1.attempt_number == 1
    assert attempt_1.status == RunStatus.RUNNING
    assert len(attempt_1.test_results) == 1
    assert attempt_1.test_results[0].passed is False

    # Kiểm tra run_records đã được cập nhật attempt_count = 1
    updated_run = repo.get_run(run.run_id)
    assert updated_run.attempt_count == 1
    assert updated_run.log_path == "runs/attempt_1.log"
    assert len(updated_run.test_results) == 1

    # Lần thử 2: Đạt kiểm thử
    test_2 = TestResultRecord(
        command="pytest tests/test_task.py",
        passed=True,
        exit_code=0,
        summary="1 passed, 0 failed",
        stdout="All tests passed successfully"
    )
    attempt_2 = repo.record_attempt(
        run_id=run.run_id,
        status=RunStatus.SUCCESS,
        log_path="runs/attempt_2.log",
        test_results=[test_2]
    )
    assert attempt_2.attempt_number == 2
    assert attempt_2.status == RunStatus.SUCCESS

    # Kết thúc run với SUCCESS
    final_run = repo.finish_run(
        run_id=run.run_id,
        status=RunStatus.SUCCESS,
        test_results=[test_2]
    )
    assert final_run.status == RunStatus.SUCCESS
    assert final_run.attempt_count == 2
    assert final_run.finished_at is not None
    assert len(final_run.attempts) == 2


def test_list_runs_with_filters(temp_db: Path):
    """Xác minh liệt kê danh sách run records và lọc theo task_id, status."""
    repo = RunRepository(db_path=temp_db)

    run_1 = repo.create_run(task_id="TASK-A")
    run_2 = repo.create_run(task_id="TASK-A")
    run_3 = repo.create_run(task_id="TASK-B")

    repo.finish_run(run_1.run_id, status=RunStatus.SUCCESS)
    repo.finish_run(run_2.run_id, status=RunStatus.FAILED)
    repo.finish_run(run_3.run_id, status=RunStatus.BLOCKED)

    all_runs = repo.list_runs()
    assert len(all_runs) == 3

    task_a_runs = repo.list_runs(task_id="TASK-A")
    assert len(task_a_runs) == 2

    success_runs = repo.list_runs(status=RunStatus.SUCCESS)
    assert len(success_runs) == 1
    assert success_runs[0].run_id == run_1.run_id


def test_factory_restart_history_persistence(temp_db: Path):
    """Acceptance Criteria: Khởi động lại Factory vẫn truy xuất toàn vẹn lịch sử run."""
    # Phase 1: Factory ban đầu tạo và ghi nhận các run
    factory_session_1 = RunRepository(db_path=temp_db)

    run_acf01 = factory_session_1.create_run(task_id="ACF-01", max_attempts=3)
    factory_session_1.finish_run(run_acf01.run_id, status=RunStatus.SUCCESS)

    run_acf02 = factory_session_1.create_run(task_id="ACF-02", max_attempts=3)
    factory_session_1.record_attempt(
        run_id=run_acf02.run_id,
        status=RunStatus.RUNNING,
        log_path="runs/acf02_attempt1.log",
        test_results=[
            TestResultRecord(
                command="pytest tests/test_task_contract.py",
                passed=False,
                exit_code=1,
                summary="2 failed, 11 passed"
            )
        ]
    )
    factory_session_1.record_attempt(
        run_id=run_acf02.run_id,
        status=RunStatus.SUCCESS,
        log_path="runs/acf02_attempt2.log",
        test_results=[
            TestResultRecord(
                command="pytest tests/test_task_contract.py",
                passed=True,
                exit_code=0,
                summary="13 passed, 0 failed"
            )
        ]
    )
    factory_session_1.finish_run(
        run_id=run_acf02.run_id,
        status=RunStatus.SUCCESS,
        test_results=[
            TestResultRecord(
                command="pytest tests/test_task_contract.py",
                passed=True,
                exit_code=0,
                summary="13 passed, 0 failed"
            )
        ]
    )

    run_acf03 = factory_session_1.create_run(task_id="ACF-03", max_attempts=3)
    factory_session_1.finish_run(
        run_id=run_acf03.run_id,
        status=RunStatus.BLOCKED,
        error_message="Dependency task not resolved"
    )

    # Giả lập tắt hoàn toàn Factory 1 (xóa session và giải phóng bộ nhớ)
    del factory_session_1

    # Phase 2: Factory được khởi động lại (tạo instance mới hoàn toàn từ file DB hiện hữu)
    factory_session_2 = RunRepository(db_path=temp_db)

    # Kiểm tra khôi phục lịch sử toàn bộ runs
    all_runs = factory_session_2.list_runs()
    assert len(all_runs) == 3
    run_ids = {r.run_id for r in all_runs}
    assert run_acf01.run_id in run_ids
    assert run_acf02.run_id in run_ids
    assert run_acf03.run_id in run_ids

    # Kiểm tra chi tiết task ACF-02 sau khi khởi động lại
    recovered_acf02 = factory_session_2.get_run(run_acf02.run_id, include_attempts=True)
    assert recovered_acf02 is not None
    assert recovered_acf02.task_id == "ACF-02"
    assert recovered_acf02.status == RunStatus.SUCCESS
    assert recovered_acf02.attempt_count == 2
    assert recovered_acf02.finished_at is not None
    assert len(recovered_acf02.attempts) == 2
    assert recovered_acf02.attempts[0].attempt_number == 1
    assert recovered_acf02.attempts[0].test_results[0].passed is False
    assert recovered_acf02.attempts[1].attempt_number == 2
    assert recovered_acf02.attempts[1].test_results[0].passed is True
    assert recovered_acf02.test_results[0].summary == "13 passed, 0 failed"

    # Kiểm tra task ACF-03 sau khi khởi động lại
    recovered_acf03 = factory_session_2.get_run(run_acf03.run_id)
    assert recovered_acf03 is not None
    assert recovered_acf03.status == RunStatus.BLOCKED
    assert recovered_acf03.error_message == "Dependency task not resolved"
