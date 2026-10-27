"""Fail-closed risk regressions for incomplete market snapshots."""
from datetime import datetime, timedelta, timezone
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
    executor = BrokerExecutor()
    monkeypatch.setattr(executor, "get_account_status", lambda: {
        "cash": 10000.0,
        "portfolio_value": 10000.0,
        "buying_power": 10000.0,
        "equity": 10000.0,
        "account_data_fresh": True,
    })
    monkeypatch.setattr(executor, "get_positions", lambda: {})
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [])
    return executor


@pytest.mark.parametrize("unavailable_kind", ["missing", "stale"])
def test_partial_market_snapshot_blocks_all_new_orders_atomically(
    isolated_executor, monkeypatch, unavailable_kind
):
    """One unavailable target quote must block the whole rebalance before any submit."""
    executor = isolated_executor
    now = datetime.now(timezone.utc)
    quotes = {
        "AAPL": {"price": 100.0, "timestamp": now.isoformat()},
    }
    if unavailable_kind == "stale":
        quotes["MSFT"] = {
            "price": 200.0,
            "timestamp": (now - timedelta(minutes=10)).isoformat(),
        }

    monkeypatch.setattr(
        executor.alpaca_feed,
        "get_latest_quotes",
        lambda tickers: {ticker: quotes[ticker] for ticker in tickers if ticker in quotes},
    )
    submitted = []
    monkeypatch.setattr(
        executor.trading_client,
        "submit_order",
        lambda request: submitted.append(request) or SimpleNamespace(
            id=f"order-{request.symbol}", status="accepted"
        ),
    )

    with pytest.raises(RuntimeError, match="valid prices unavailable for: MSFT"):
        executor.execute_weights(
            PortfolioWeights(
                weights=np.array([0.1, 0.1]),
                tickers=["AAPL", "MSFT"],
            )
        )

    assert submitted == []


def test_unreadable_open_order_snapshot_blocks_before_market_sizing_or_submit(
    isolated_executor, monkeypatch
):
    """A broker open-order read failure must not be treated as an empty order book."""
    executor = isolated_executor
    submitted = []
    market_data_reads = []

    def fail_open_orders():
        raise RuntimeError("simulated broker open-order read failure")

    monkeypatch.setattr(executor, "_get_open_orders", fail_open_orders)
    monkeypatch.setattr(
        executor.alpaca_feed,
        "get_latest_quotes",
        lambda tickers: market_data_reads.append(tickers) or {},
    )
    monkeypatch.setattr(
        executor.trading_client,
        "submit_order",
        lambda request: submitted.append(request),
    )

    with pytest.raises(RuntimeError, match="simulated broker open-order read failure"):
        executor.execute_weights(
            PortfolioWeights(weights=np.array([0.1]), tickers=["AAPL"])
        )

    assert market_data_reads == []
    assert submitted == []
