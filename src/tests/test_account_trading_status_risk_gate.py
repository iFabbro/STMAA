"""Broker account restrictions must fail closed before any new position is submitted."""
from types import SimpleNamespace

import numpy as np
import pytest

from src.execution.broker_executor import BrokerExecutor
from src.strategy.weight_interface import PortfolioWeights


@pytest.fixture
def broker_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "test-secret")
    monkeypatch.setenv(
        "STMAA_BROKER_STATE_FILE",
        str(tmp_path / "state" / "broker_state.json"),
    )


@pytest.mark.parametrize(
    ("status", "trading_blocked"),
    [
        pytest.param("ACTIVE", True, id="trading-blocked"),
        pytest.param("INACTIVE", False, id="inactive-account"),
        pytest.param("ACTIVE", None, id="unknown-trading-blocked-flag"),
        pytest.param(None, False, id="unknown-account-status"),
    ],
)
def test_restricted_or_unverified_account_blocks_before_any_new_order(
    broker_env, monkeypatch, status, trading_blocked
):
    executor = BrokerExecutor()
    submitted = []
    account_fields = {
        "cash": "10000",
        "portfolio_value": "10000",
        "buying_power": "10000",
        "equity": "10000",
        "daytrade_count": 0,
        "status": status,
        "trading_blocked": trading_blocked,
        "id": "paper-account-1",
    }
    fake_account = SimpleNamespace(**account_fields)
    monkeypatch.setattr(executor.trading_client, "get_account", lambda: fake_account)
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

    assert submitted == []
    assert executor.state["account_data_fresh"] is False
