"""Regression matrix for stale/malformed market data and fail-closed sizing."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pytest

from src.execution.broker_executor import BrokerExecutor
from src.strategy.weight_interface import PortfolioWeights


@pytest.fixture
def executor_env(monkeypatch, tmp_path):
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
        "account_snapshot_at": datetime.now(timezone.utc).isoformat(),
    })
    monkeypatch.setattr(executor, "get_positions", lambda: {})
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [])
    return executor


@pytest.mark.parametrize(
    "bad_quote",
    [
        pytest.param({"price": 100.0}, id="missing-timestamp"),
        pytest.param(
            {"price": 100.0, "timestamp": "2026-10-27T12:00:00"},
            id="timezone-naive-timestamp",
        ),
        pytest.param(
            {
                "price": 100.0,
                "timestamp": (datetime.now(timezone.utc) - timedelta(minutes=6)).isoformat(),
            },
            id="stale-quote",
        ),
        pytest.param(
            {
                "price": 100.0,
                "timestamp": (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat(),
            },
            id="future-quote",
        ),
        pytest.param(
            {"price": float("nan"), "timestamp": datetime.now(timezone.utc).isoformat()},
            id="non-finite-price",
        ),
        pytest.param(
            {"price": 0.0, "timestamp": datetime.now(timezone.utc).isoformat()},
            id="non-positive-price",
        ),
    ],
)
def test_bad_quote_blocks_entire_rebalance_before_any_order(
    executor_env, monkeypatch, bad_quote
):
    executor = executor_env
    now = datetime.now(timezone.utc)
    quotes = {
        "AAPL": {"price": 100.0, "timestamp": now.isoformat()},
        "MSFT": bad_quote,
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
            PortfolioWeights(weights=np.array([0.1, 0.1]), tickers=["AAPL", "MSFT"])
        )

    assert submitted == []


def test_unestimable_pending_order_blocks_even_when_target_quotes_are_fresh(
    executor_env, monkeypatch
):
    executor = executor_env
    unknown_order = SimpleNamespace(
        id="pending-unknown",
        symbol="UNKNOWN",
        status="accepted",
        qty="10",
        filled_qty="0",
        limit_price=None,
        notional=None,
    )
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [unknown_order])
    monkeypatch.setattr(
        executor.alpaca_feed,
        "get_latest_quotes",
        lambda tickers: {
            ticker: {
                "price": 100.0,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            for ticker in tickers
        },
    )
    submitted = []
    monkeypatch.setattr(
        executor.trading_client,
        "submit_order",
        lambda request: submitted.append(request),
    )

    with pytest.raises(RuntimeError, match="Cannot estimate exposure for active order"):
        executor.execute_weights(
            PortfolioWeights(weights=np.array([0.1]), tickers=["AAPL"])
        )

    assert submitted == []
