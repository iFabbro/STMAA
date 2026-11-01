"""Account snapshots without identity must not inherit prior account provenance."""
from types import SimpleNamespace

import pytest

from src.execution.broker_executor import BrokerExecutor


@pytest.fixture
def isolated_executor(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "test-secret")
    monkeypatch.setenv(
        "STMAA_BROKER_STATE_FILE",
        str(tmp_path / "state" / "broker_state.json"),
    )
    return BrokerExecutor(paper_trading=True)


@pytest.mark.parametrize("account_id", [None, ""], ids=["missing-id", "empty-id"])
def test_account_snapshot_without_id_remains_unlinked(
    isolated_executor, monkeypatch, account_id
):
    executor = isolated_executor
    executor.state.update({
        "account_id": "previous-account",
        "metrics_baseline_source": "verified_account",
        "metrics_account_mode": "paper",
        "metrics_account_id": "previous-account",
        "equity_history": [50000.0, 51000.0],
    })
    executor._save_state()
    monkeypatch.setattr(
        executor.trading_client,
        "get_account",
        lambda: SimpleNamespace(
            cash="10000",
            portfolio_value="10000",
            buying_power="10000",
            equity="10000",
            daytrade_count=0,
            status="ACTIVE",
            trading_blocked=False,
            id=account_id,
        ),
    )

    snapshot = executor.get_account_status()

    assert snapshot["account_data_fresh"] is True
    assert snapshot.get("account_id") is None
    assert executor._current_account_identity_verified is False
    assert executor.state["account_id"] == "previous-account"
    assert executor.state["metrics_account_id"] is None
    assert executor.state["equity_history"] == [10000.0]
