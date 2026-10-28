"""Fail-closed checks for broker positions whose exposure cannot be trusted."""
from types import SimpleNamespace

import numpy as np
import pytest

from src.execution.broker_executor import BrokerExecutor
from src.strategy.weight_interface import PortfolioWeights


@pytest.fixture
def exposure_audit_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "test-secret")
    monkeypatch.setenv(
        "STMAA_BROKER_STATE_FILE",
        str(tmp_path / "state" / "broker_state.json"),
    )


@pytest.mark.parametrize(
    "position",
    [
        pytest.param(
            SimpleNamespace(
                symbol="AAPL",
                qty="10",
                market_value="0",
                avg_entry_price="100",
                unrealized_pl="0",
            ),
            id="nonzero-quantity-zero-market-value",
        ),
        pytest.param(
            SimpleNamespace(
                symbol="AAPL",
                qty="0",
                market_value="1000",
                avg_entry_price="100",
                unrealized_pl="0",
            ),
            id="zero-quantity-nonzero-market-value",
        ),
        pytest.param(
            SimpleNamespace(
                symbol="AAPL",
                qty="10",
                market_value="1000",
                avg_entry_price="0",
                unrealized_pl="0",
            ),
            id="open-position-nonpositive-entry-price",
        ),
    ],
)
def test_inconsistent_position_exposure_blocks_before_any_new_order(
    exposure_audit_env, monkeypatch, position
):
    executor = BrokerExecutor()
    submitted = []

    monkeypatch.setattr(
        executor,
        "get_account_status",
        lambda: {
            "cash": 10000.0,
            "portfolio_value": 10000.0,
            "buying_power": 10000.0,
            "equity": 10000.0,
            "account_id": "paper-account-a",
            "account_data_fresh": True,
        },
    )
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [])
    monkeypatch.setattr(
        executor.trading_client,
        "get_all_positions",
        lambda: [position],
    )
    monkeypatch.setattr(
        executor.alpaca_feed,
        "get_latest_quotes",
        lambda tickers: {
            ticker: {
                "price": 100.0,
                "timestamp": "2026-10-28T18:00:00+00:00",
            }
            for ticker in tickers
        },
    )
    monkeypatch.setattr(
        executor.trading_client,
        "submit_order",
        lambda request: submitted.append(request),
    )

    with pytest.raises(
        RuntimeError,
        match="Unable to retrieve broker positions; refusing to execute trades",
    ):
        executor.execute_weights(
            PortfolioWeights(weights=np.array([0.1]), tickers=["MSFT"])
        )

    assert submitted == []
