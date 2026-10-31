"""Broker account snapshots without identity must never authorize execution."""
from types import SimpleNamespace

import numpy as np
import pytest

from src.execution.broker_executor import BrokerExecutor
from src.strategy.weight_interface import PortfolioWeights


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
def test_account_snapshot_without_identity_blocks_new_orders(
    isolated_executor, monkeypatch, account_id
):
    executor = isolated_executor
    submitted = []
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
    monkeypatch.setattr(
        executor.trading_client,
        "submit_order",
        lambda request: submitted.append(request),
    )

    with pytest.raises(
        RuntimeError,
        match="Cannot verify current broker account equity",
    ):
        executor.execute_weights(
            PortfolioWeights(weights=np.array([0.1]), tickers=["AAPL"])
        )

    assert executor.state["account_data_fresh"] is False
    assert submitted == []


def test_missing_current_account_id_does_not_reuse_persisted_identity(
    isolated_executor, monkeypatch
):
    executor = isolated_executor
    executor.state["account_id"] = "previous-account"
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
            id=None,
        ),
    )

    snapshot = executor.get_account_status()

    assert snapshot["account_data_fresh"] is False
    assert snapshot["account_id"] is None
    assert executor.state["account_id"] == "previous-account"
    assert executor.state["account_data_fresh"] is False
