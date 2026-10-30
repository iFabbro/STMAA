"""Persistence fault-injection and concurrent-writer regressions."""
import errno
import json
import multiprocessing
import os
import stat

import pytest

import src.execution.broker_executor as broker_executor_module
from src.execution.broker_executor import BrokerExecutor


def _concurrent_state_writer(state_file, barrier, result_queue, marker):
    os.environ["STMAA_BROKER_STATE_FILE"] = state_file
    os.environ["ALPACA_API_KEY"] = "test-key"
    os.environ["ALPACA_API_SECRET"] = "test-secret"
    executor = BrokerExecutor()
    barrier.wait(timeout=15)
    executor.state["concurrency_marker"] = marker
    try:
        executor._save_state()
        result_queue.put(("saved", marker))
    except Exception as exc:  # expected for the stale writer
        result_queue.put(("failed", marker, str(exc)))


@pytest.fixture
def persistence_executor(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "test-secret")
    monkeypatch.setenv("STMAA_BROKER_STATE_FILE", str(tmp_path / "state.json"))
    return BrokerExecutor()


def test_interrupted_json_write_preserves_previous_state_and_fails_closed(
    persistence_executor, monkeypatch
):
    executor = persistence_executor
    original = executor.state_file.read_bytes()
    executor.state["total_orders"] = 999

    def interrupted_dump(payload, file_handle):
        file_handle.write('{"partial":')
        raise OSError("simulated interrupted write")

    monkeypatch.setattr(broker_executor_module.json, "dump", interrupted_dump)

    with pytest.raises(OSError, match="interrupted write"):
        executor._save_state()

    assert executor.state_file.read_bytes() == original
    assert not list(executor.state_file.parent.glob(f".{executor.state_file.name}.*.tmp"))
    with pytest.raises(RuntimeError, match="persistence previously failed"):
        executor._save_state()


def test_disk_full_during_fsync_preserves_previous_state(
    persistence_executor, monkeypatch
):
    executor = persistence_executor
    original = executor.state_file.read_bytes()
    executor.state["total_orders"] = 999

    def disk_full(_fd):
        raise OSError(errno.ENOSPC, "simulated disk full")

    monkeypatch.setattr(broker_executor_module.os, "fsync", disk_full)

    with pytest.raises(OSError) as exc_info:
        executor._save_state()

    assert exc_info.value.errno == errno.ENOSPC
    assert executor.state_file.read_bytes() == original
    assert not list(executor.state_file.parent.glob(f".{executor.state_file.name}.*.tmp"))



def test_state_replace_fsyncs_parent_directory_after_file_fsync(
    persistence_executor, monkeypatch
):
    executor = persistence_executor
    real_fsync = broker_executor_module.os.fsync
    synced_modes = []

    def record_fsync(fd):
        mode = os.fstat(fd).st_mode
        synced_modes.append("directory" if stat.S_ISDIR(mode) else "file")
        return real_fsync(fd)

    monkeypatch.setattr(broker_executor_module.os, "fsync", record_fsync)
    executor.state["total_orders"] = 7

    executor._save_state()

    assert synced_modes == ["file", "directory"]
    persisted = json.loads(executor.state_file.read_text(encoding="utf-8"))
    assert persisted["total_orders"] == 7
    assert persisted["state_revision"] == executor._persisted_state_revision


def test_parent_directory_fsync_failure_fails_closed_after_replace(
    persistence_executor, monkeypatch
):
    executor = persistence_executor
    real_fsync = broker_executor_module.os.fsync
    original_revision = executor._persisted_state_revision
    executor.state["total_orders"] = 11

    def fail_directory_fsync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError(errno.EIO, "simulated directory fsync failure")
        return real_fsync(fd)

    monkeypatch.setattr(broker_executor_module.os, "fsync", fail_directory_fsync)

    with pytest.raises(OSError, match="directory fsync failure"):
        executor._save_state()

    # os.replace already happened, so the disk may contain the new revision;
    # the executor must nevertheless refuse to continue on an ambiguous commit.
    persisted = json.loads(executor.state_file.read_text(encoding="utf-8"))
    assert persisted["total_orders"] == 11
    assert persisted["state_revision"] == original_revision + 1
    assert executor._persisted_state_revision == original_revision
    assert executor._state_persistence_failed is True
    with pytest.raises(RuntimeError, match="persistence previously failed"):
        executor._save_state()

def test_permission_error_creating_temp_file_preserves_previous_state(
    persistence_executor, monkeypatch
):
    executor = persistence_executor
    original = executor.state_file.read_bytes()

    def permission_denied(**_kwargs):
        raise PermissionError("simulated state directory permission error")

    monkeypatch.setattr(
        broker_executor_module.tempfile, "NamedTemporaryFile", permission_denied
    )

    with pytest.raises(PermissionError, match="permission error"):
        executor._save_state()

    assert executor.state_file.read_bytes() == original


def test_concurrent_processes_cannot_overwrite_newer_broker_state(
    persistence_executor,
):
    executor = persistence_executor
    state_file = str(executor.state_file)
    context = multiprocessing.get_context("fork")
    barrier = context.Barrier(2)
    result_queue = context.Queue()
    workers = [
        context.Process(
            target=_concurrent_state_writer,
            args=(state_file, barrier, result_queue, marker),
        )
        for marker in ("writer-A", "writer-B")
    ]

    for worker in workers:
        worker.start()
    results = [result_queue.get(timeout=20) for _ in workers]
    for worker in workers:
        worker.join(timeout=20)
        assert worker.exitcode == 0

    assert sum(result[0] == "saved" for result in results) == 1
    failures = [result for result in results if result[0] == "failed"]
    assert len(failures) == 1
    assert "changed in another process" in failures[0][2]

    persisted = json.loads(executor.state_file.read_text(encoding="utf-8"))
    assert persisted["concurrency_marker"] in {"writer-A", "writer-B"}
