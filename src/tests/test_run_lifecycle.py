import os
import subprocess
import sys
import time

import pytest

import hub
import trade
from src.utils import sqlite_logger
from src.utils.sqlite_logger import db_conn, end_run, recover_orphaned_runs, start_run


def test_end_run_persists_terminal_status_and_timestamp(tmp_path):
    database = tmp_path / "stmaa.db"
    run_id = start_run(mode="test", db_path=database)

    end_run(run_id, status="completed", db_path=database)

    with db_conn(database) as conn:
        row = conn.execute(
            "SELECT ended_at, status FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()

    assert row["ended_at"]
    assert row["status"] == "completed"


def test_importing_entrypoints_does_not_start_runs():
    assert trade.RUN_ID is None
    assert hub.RUN_ID is None


def test_trade_main_finalizes_failed_preflight_run(monkeypatch):
    finalized = []
    monkeypatch.setattr(trade, "start_run", lambda **_kwargs: "trade-run")
    monkeypatch.setattr(
        trade, "end_run", lambda run_id, status: finalized.append((run_id, status))
    )
    monkeypatch.setattr(trade, "log_event", lambda *args, **kwargs: None)
    monkeypatch.setenv("STMAA_TRADING_MODE", "live")
    monkeypatch.setenv("ALPACA_API_KEY", "paper-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "paper-secret")

    assert trade.main(["--preflight-only"]) == 1
    assert finalized == [("trade-run", "failed")]
    assert trade.RUN_ID is None


def test_hub_main_finalizes_graceful_shutdown(monkeypatch):
    finalized = []
    monkeypatch.setattr(hub, "start_run", lambda **_kwargs: "hub-run")
    monkeypatch.setattr(
        hub, "end_run", lambda run_id, status: finalized.append((run_id, status))
    )
    monkeypatch.setattr(hub, "log_event", lambda *args, **kwargs: None)

    def stop():
        raise KeyboardInterrupt

    monkeypatch.setattr(hub, "render", stop)
    hub.main()

    assert finalized == [("hub-run", "completed")]
    assert hub.RUN_ID is None


def test_hub_main_marks_unexpected_failure(monkeypatch):
    finalized = []
    monkeypatch.setattr(hub, "start_run", lambda **_kwargs: "hub-run")
    monkeypatch.setattr(
        hub, "end_run", lambda run_id, status: finalized.append((run_id, status))
    )
    monkeypatch.setattr(hub, "log_event", lambda *args, **kwargs: None)

    def fail():
        raise RuntimeError("render failed")

    monkeypatch.setattr(hub, "render", fail)
    with pytest.raises(RuntimeError, match="render failed"):
        hub.main()

    assert finalized == [("hub-run", "failed")]
    assert hub.RUN_ID is None



def test_end_run_rejects_non_terminal_status_without_mutating_run(tmp_path):
    database = tmp_path / "stmaa.db"
    run_id = start_run(mode="test", db_path=database)

    with pytest.raises(ValueError, match="terminal"):
        end_run(run_id, status="running", db_path=database)

    with db_conn(database) as conn:
        row = conn.execute(
            "SELECT ended_at, status FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()

    assert row["ended_at"] is None
    assert row["status"] == "running"


def test_end_run_rejects_unknown_run_id(tmp_path):
    database = tmp_path / "stmaa.db"

    with pytest.raises(ValueError, match="does not exist"):
        end_run("missing-run", status="completed", db_path=database)


def test_end_run_cannot_overwrite_terminal_run_state(tmp_path):
    database = tmp_path / "stmaa.db"
    run_id = start_run(mode="test", db_path=database)
    end_run(run_id, status="completed", db_path=database)

    with db_conn(database) as conn:
        original = conn.execute(
            "SELECT ended_at, status FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()

    with pytest.raises(ValueError, match="already been finalized"):
        end_run(run_id, status="failed", db_path=database)

    with db_conn(database) as conn:
        final = conn.execute(
            "SELECT ended_at, status FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()

    assert final["ended_at"] == original["ended_at"]
    assert final["status"] == "completed"



def _spawn_sleeping_process():
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"]
    )
    deadline = time.monotonic() + 5
    start_ticks = None
    while time.monotonic() < deadline:
        start_ticks = sqlite_logger._read_process_start_ticks(process.pid)
        if start_ticks is not None:
            break
        time.sleep(0.01)
    assert start_ticks is not None
    return process, start_ticks


def _run_row(database, run_id):
    with db_conn(database) as conn:
        return conn.execute(
            "SELECT status, ended_at, owner_pid, owner_start_ticks "
            "FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()


def test_start_run_persists_verifiable_process_identity(tmp_path):
    database = tmp_path / "stmaa.db"
    run_id = start_run(mode="test", db_path=database)

    row = _run_row(database, run_id)

    assert row["owner_pid"] == os.getpid()
    assert row["owner_start_ticks"]
    assert sqlite_logger.process_identity_is_alive(
        row["owner_pid"], row["owner_start_ticks"]
    ) is True


def test_forcibly_terminated_process_run_is_recovered_as_failed(tmp_path, monkeypatch):
    database = tmp_path / "stmaa.db"
    process, start_ticks = _spawn_sleeping_process()
    try:
        monkeypatch.setattr(
            sqlite_logger, "current_process_identity",
            lambda: (process.pid, start_ticks),
        )
        run_id = start_run(mode="test", db_path=database)

        process.kill()
        process.wait(timeout=5)

        assert recover_orphaned_runs(db_path=database) == [run_id]
        row = _run_row(database, run_id)
        assert row["status"] == "failed"
        assert row["ended_at"]
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def test_orphan_recovery_preserves_another_live_process_run(tmp_path, monkeypatch):
    database = tmp_path / "stmaa.db"
    first, first_ticks = _spawn_sleeping_process()
    second, second_ticks = _spawn_sleeping_process()
    try:
        monkeypatch.setattr(
            sqlite_logger, "current_process_identity",
            lambda: (first.pid, first_ticks),
        )
        first_run = start_run(mode="test", db_path=database)

        monkeypatch.setattr(
            sqlite_logger, "current_process_identity",
            lambda: (second.pid, second_ticks),
        )
        second_run = start_run(mode="test", db_path=database)

        first.kill()
        first.wait(timeout=5)

        assert recover_orphaned_runs(db_path=database) == [first_run]
        first_row = _run_row(database, first_run)
        second_row = _run_row(database, second_run)
        assert first_row["status"] == "failed"
        assert first_row["ended_at"]
        assert second_row["status"] == "running"
        assert second_row["ended_at"] is None
        assert second_row["owner_pid"] == second.pid
    finally:
        for process in (first, second):
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)


def test_orphan_recovery_leaves_unverifiable_legacy_runs_untouched(tmp_path):
    database = tmp_path / "stmaa.db"
    run_id = start_run(mode="test", db_path=database)
    with db_conn(database) as conn:
        conn.execute(
            "UPDATE runs SET owner_pid = NULL, owner_start_ticks = NULL "
            "WHERE run_id = ?",
            (run_id,),
        )

    assert recover_orphaned_runs(db_path=database) == []
    row = _run_row(database, run_id)
    assert row["status"] == "running"
    assert row["ended_at"] is None


def test_process_identity_mismatch_detects_pid_reuse(monkeypatch):
    fields = ["S"] + ["0"] * 18 + ["new-start-token"]
    fake_stat = "12345 (test process) " + " ".join(fields)
    monkeypatch.setattr(
        sqlite_logger.Path, "read_text", lambda *_args, **_kwargs: fake_stat
    )

    assert sqlite_logger.process_identity_is_alive(12345, "old-start-token") is False
    assert sqlite_logger.process_identity_is_alive(12345, "new-start-token") is True


def test_init_db_migrates_existing_runs_table(tmp_path):
    import sqlite3

    database = tmp_path / "legacy.db"
    with sqlite3.connect(database) as conn:
        conn.execute(
            """
            CREATE TABLE runs (
                run_id TEXT PRIMARY KEY,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                mode TEXT NOT NULL,
                branch TEXT,
                commit_sha TEXT,
                status TEXT NOT NULL,
                meta TEXT NOT NULL
            )
            """
        )

    sqlite_logger.init_db(database)

    with db_conn(database) as conn:
        columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(runs)").fetchall()
        }
    assert {"owner_pid", "owner_start_ticks"} <= columns
