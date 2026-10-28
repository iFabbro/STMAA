"""Aggregate pending-order exposure must block new positions fail-closed."""
from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np

from src.execution.broker_executor import BrokerExecutor
from src.strategy.weight_interface import PortfolioWeights


def test_aggregate_pending_order_exposure_blocks_new_position(
    monkeypatch, tmp_path
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "test-secret")
    monkeypatch.setenv(
        "STMAA_BROKER_STATE_FILE",
        str(tmp_path / "state" / "broker_state.json"),
    )
    executor = BrokerExecutor(paper_trading=True)
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
            "account_id": "aggregate-pending-order-test",
            "account_snapshot_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    monkeypatch.setattr(executor, "get_positions", lambda: {})
    # Each order is individually estimable, but together they reserve $6,000,
    # exceeding the configured 50% gross-exposure limit on $10,000 equity.
    monkeypatch.setattr(
        executor,
        "_get_open_orders",
        lambda: [
            SimpleNamespace(
                id="pending-msft-1",
                symbol="MSFT",
                status="accepted",
                qty="20",
                filled_qty="0",
                limit_price="150",
                notional=None,
            ),
            SimpleNamespace(
                id="pending-nvda-1",
                symbol="NVDA",
                status="accepted",
                qty="20",
                filled_qty="0",
                limit_price="150",
                notional=None,
            ),
        ],
    )
    monkeypatch.setattr(
        executor,
        "_get_prices_from_bars",
        lambda tickers: {"AAPL": 100.0, "MSFT": 150.0, "NVDA": 150.0},
    )
    monkeypatch.setattr(
        executor.trading_client,
        "submit_order",
        lambda request: submitted.append(request),
    )

    result = executor.execute_weights(
        PortfolioWeights(weights=np.array([0.1]), tickers=["AAPL"])
    )

    assert result["orders"] == []
    assert submitted == []
