"""Restart, reconnection, and persisted-state recovery for the Paper soak monitor.

These tests use SQLite and injected providers only. No broker client or order
operation is involved.
"""
import json

import pytest

from src.utils import sqlite_logger
from src.utils.paper_soak_monitor import PaperSoakMonitor
from src.utils.paper_soak_shadow_runner import PaperSoakShadowRunner
from src.utils.paper_soak_stop_controller import (
    PaperSoakHealthSnapshot,
    PaperSoakStopController,
)
from src.utils.sqlite_logger import db_conn, recover_orphaned_runs


def healthy_snapshot(**overrides):
    values = {
        "broker_endpoint_is_paper": True,
        "order_mutation_attempted": False,
        "broker_state_consistent": True,
        "exposure_determinable": True,
        "market_data_valid": True,
        "market_data_fresh": True,
        "persistence_healthy": True,
        "broker_snapshot_complete": True,
        "api_consecutive_failures": 0,
        "heartbeat_age_seconds": 0.0,
    }
    values.update(overrides)
    return PaperSoakHealthSnapshot(**values)


def test_process_restart_recovers_orphaned_session_as_failed_and_blocks_late_writes(
    tmp_path, monkeypatch
):
    database = tmp_path / "paper-soak.db"
    first_process = PaperSoakMonitor(
        db_path=database,
        duration_seconds=3600,
        heartbeat_interval_seconds=1,
    )
    run_id = first_process.start()
    first_process.heartbeat()

    # Model a confirmed-dead owner process. Unknown process identity is not
    # enough evidence to recover an orphan and is covered by sqlite_logger tests.
    monkeypatch.setattr(
        sqlite_logger, "process_identity_is_alive", lambda _pid, _ticks: False
    )
    assert recover_orphaned_runs(db_path=database) == [run_id]

    with db_conn(database) as conn:
        run = conn.execute(
            "SELECT status, ended_at FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
    assert run["status"] == "failed"
    assert run["ended_at"] is not None

    # The previous process object cannot append after recovery has finalized
    # the run. A restarted application must create a new run, not revive it.
    with pytest.raises(ValueError, match="not active"):
        first_process.heartbeat()

    restarted = PaperSoakMonitor(db_path=database, duration_seconds=3600)
    new_run_id = restarted.start()
    assert new_run_id != run_id

    with db_conn(database) as conn:
        old_status = conn.execute(
            "SELECT status FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()["status"]
        new_status = conn.execute(
            "SELECT status FROM runs WHERE run_id = ?", (new_run_id,)
        ).fetchone()["status"]
    assert old_status == "failed"
    assert new_status == "running"


def test_reconnection_failure_terminates_shadow_run_without_retry_or_stale_observation(
    tmp_path,
):
    database = tmp_path / "paper-soak.db"
    monitor = PaperSoakMonitor(db_path=database, duration_seconds=600)
    provider_calls = []

    def disconnected_provider():
        provider_calls.append("read")
        raise ConnectionError("simulated broker read-only connection loss")

    # The runner receives only the provider, never a broker executor/client.
    runner = PaperSoakShadowRunner(monitor, disconnected_provider)

    with pytest.raises(ConnectionError, match="connection loss"):
        runner.run(sleep=lambda _seconds: pytest.fail("must fail closed immediately"))

    with db_conn(database) as conn:
        run = conn.execute(
            "SELECT status FROM runs WHERE run_id = ?", (monitor.run_id,)
        ).fetchone()
        events = conn.execute(
            "SELECT event_type FROM events WHERE run_id = ? ORDER BY id",
            (monitor.run_id,),
        ).fetchall()

    assert run["status"] == "failed"
    assert provider_calls == ["read"]
    event_types = [event["event_type"] for event in events]
    assert "shadow_runner_error" in event_types
    assert "session_failed" in event_types
    assert "shadow_observation" not in event_types


def test_stop_state_is_restored_from_sqlite_after_monitor_object_recreation(tmp_path):
    database = tmp_path / "paper-soak.db"
    original_monitor = PaperSoakMonitor(db_path=database, duration_seconds=600)
    run_id = original_monitor.start()
    original_controller = PaperSoakStopController(original_monitor)

    stopped = original_controller.evaluate(
        healthy_snapshot(exposure_determinable=False)
    )
    assert stopped.stopped is True
    assert stopped.reason == "exposure_not_determinable"

    # Recreate the monitor/controller objects while referencing the same
    # still-active run, as happens when a supervising component is rebuilt
    # within the owning process. The durable stop event remains authoritative.
    recovered_monitor = PaperSoakMonitor(db_path=database, duration_seconds=600)
    recovered_monitor.run_id = run_id
    recovered_controller = PaperSoakStopController(recovered_monitor)

    assert recovered_controller.stopped is True
    resumed = recovered_controller.evaluate(healthy_snapshot())
    assert resumed.stopped is True
    assert resumed.reason == "stop_already_latched"

    with db_conn(database) as conn:
        rows = conn.execute(
            "SELECT payload FROM events WHERE run_id = ? "
            "AND source = 'paper_soak_stop_controller' "
            "AND event_type = 'stop_triggered'",
            (run_id,),
        ).fetchall()
    assert len(rows) == 1
    assert json.loads(rows[0]["payload"])["reason"] == "exposure_not_determinable"
