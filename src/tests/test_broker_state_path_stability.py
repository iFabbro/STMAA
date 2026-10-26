"""Broker state must survive process restarts regardless of launch directory."""
from pathlib import Path

from src.execution.broker_executor import BrokerExecutor


def test_default_broker_state_path_is_anchored_to_repository_root(
    monkeypatch, tmp_path
):
    monkeypatch.delenv("STMAA_BROKER_STATE_FILE", raising=False)
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "test-secret")
    monkeypatch.chdir(tmp_path)
    # Avoid writing to the production-default path during this path-only assertion.
    monkeypatch.setattr(BrokerExecutor, "_save_state", lambda self: None)

    executor = BrokerExecutor()

    expected = (
        Path(__file__).resolve().parents[2]
        / "logs"
        / "execution"
        / "broker_state.json"
    )
    assert executor.state_file == expected


def test_configured_broker_state_survives_restart_from_different_cwd(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "test-secret")
    state_file = tmp_path / "shared" / "broker_state.json"
    monkeypatch.setenv("STMAA_BROKER_STATE_FILE", str(state_file))

    first_cwd = tmp_path / "first"
    second_cwd = tmp_path / "second"
    first_cwd.mkdir()
    second_cwd.mkdir()
    monkeypatch.chdir(first_cwd)

    first = BrokerExecutor()
    first.state["emergency_liquidation_unresolved"] = {
        "AAPL": {
            "created_at": "2026-10-26T12:00:00+00:00",
            "requested_qty": 2.0,
            "expected_side": "sell",
            "reason": "close_submission_outcome_unconfirmed",
        }
    }
    first._save_state()

    monkeypatch.chdir(second_cwd)
    restarted = BrokerExecutor()

    assert restarted.state_file == state_file
    assert "AAPL" in restarted.state["emergency_liquidation_unresolved"]


def test_relative_state_file_override_is_anchored_to_repository_root(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "test-secret")
    monkeypatch.setenv("STMAA_BROKER_STATE_FILE", "custom-state/broker_state.json")
    monkeypatch.chdir(tmp_path)
    # Path-only test: do not create files in the repository-root default location.
    monkeypatch.setattr(BrokerExecutor, "_save_state", lambda self: None)

    executor = BrokerExecutor()

    expected = (
        Path(__file__).resolve().parents[2]
        / "custom-state"
        / "broker_state.json"
    )
    assert executor.state_file == expected
