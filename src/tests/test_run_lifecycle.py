import pytest

import hub
import trade
from src.utils.sqlite_logger import db_conn, end_run, start_run


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
