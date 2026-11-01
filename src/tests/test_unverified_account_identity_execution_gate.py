"""A fresh financial snapshot without verified account identity cannot authorize allocations."""
from datetime import datetime, timezone

import numpy as np
import pytest

from src.execution.broker_executor import BrokerExecutor
from src.strategy.weight_interface import PortfolioWeights


@pytest.fixture
def identity_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "test-secret")
    monkeypatch.setenv(
        "STMAA_BROKER_STATE_FILE",
        str(tmp_path / "state" / "broker_state.json"),
    )


@pytest.mark.parametrize("account_id", [None, ""], ids=["missing-id", "empty-id"])
def test_fresh_account_without_verified_identity_blocks_before_positions_quotes_or_orders(
    identity_env, monkeypatch, account_id
):
    executor = BrokerExecutor()
    position_reads = []
    quote_reads = []
    submitted = []
    monkeypatch.setattr(
        executor,
        "get_account_status",
        lambda: {
            "cash": 10000.0,
            "portfolio_value": 10000.0,
            "buying_power": 10000.0,
            "equity": 10000.0,
            "account_data_fresh": True,
            "account_id": account_id,
            "account_identity_verified": False,
            "account_snapshot_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [])
    monkeypatch.setattr(
        executor,
        "get_positions",
        lambda: position_reads.append(True) or {},
    )
    monkeypatch.setattr(
        executor,
        "_get_prices_from_bars",
        lambda tickers: quote_reads.append(tickers) or {},
    )
    monkeypatch.setattr(
        executor.trading_client,
        "submit_order",
        lambda request: submitted.append(request),
    )

    with pytest.raises(
        RuntimeError,
        match="Cannot verify current broker account identity",
    ):
        executor.execute_weights(
            PortfolioWeights(weights=np.array([0.1]), tickers=["AAPL"])
        )

    assert position_reads == []
    assert quote_reads == []
    assert submitted == []
    assert executor.state.get("order_submission_unresolved", {}) == {}
