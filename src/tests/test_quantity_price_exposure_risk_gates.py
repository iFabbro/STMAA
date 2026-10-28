"""Quantity/quote-based exposure regression matrix for uncertain broker snapshots."""
from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np
import pytest

from src.execution.broker_executor import BrokerExecutor
from src.strategy.weight_interface import PortfolioWeights


@pytest.fixture
def exposure_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "test-secret")
    monkeypatch.setenv(
        "STMAA_BROKER_STATE_FILE",
        str(tmp_path / "state" / "broker_state.json"),
    )


def _account():
    return {
        "cash": 10000.0,
        "portfolio_value": 10000.0,
        "buying_power": 10000.0,
        "equity": 10000.0,
        "account_data_fresh": True,
        "account_id": "quantity-price-risk-test",
    }


def _quote(price):
    return {"price": price, "timestamp": datetime.now(timezone.utc).isoformat()}


def _buy_symbols(submitted):
    return [
        request.symbol
        for request in submitted
        if str(getattr(request.side, "value", request.side)).lower() == "buy"
    ]


def test_quantity_times_fresh_quote_overrides_underreported_market_value(
    exposure_env, monkeypatch
):
    executor = BrokerExecutor()
    submitted = []
    # Broker market_value is deliberately understated: 30 * $200 = $6,000,
    # even though the snapshot claims only $1,000.
    monkeypatch.setattr(executor, "get_account_status", _account)
    monkeypatch.setattr(
        executor,
        "get_positions",
        lambda: {
            "MSFT": {
                "qty": 30.0,
                "market_value": 1000.0,
                "avg_entry_price": 100.0,
                "unrealized_pl": 0.0,
            }
        },
    )
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [])
    monkeypatch.setattr(
        executor.alpaca_feed,
        "get_latest_quotes",
        lambda tickers: {"MSFT": _quote(200.0), "AAPL": _quote(100.0)},
    )
    monkeypatch.setattr(
        executor.trading_client,
        "submit_order",
        lambda request: submitted.append(request),
    )

    executor.execute_weights(
        PortfolioWeights(weights=np.array([0.1]), tickers=["AAPL"])
    )

    assert "AAPL" not in _buy_symbols(submitted)


def test_missing_fresh_quote_for_held_position_blocks_new_exposure(
    exposure_env, monkeypatch
):
    executor = BrokerExecutor()
    submitted = []
    monkeypatch.setattr(executor, "get_account_status", _account)
    monkeypatch.setattr(
        executor,
        "get_positions",
        lambda: {
            "MSFT": {
                "qty": 5.0,
                "market_value": 500.0,
                "avg_entry_price": 100.0,
                "unrealized_pl": 0.0,
            }
        },
    )
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [])
    monkeypatch.setattr(executor.alpaca_feed, "get_latest_quotes", lambda tickers: {})
    monkeypatch.setattr(
        executor.trading_client,
        "submit_order",
        lambda request: submitted.append(request),
    )

    with pytest.raises(RuntimeError, match="Cannot safely calculate exposure"):
        executor.execute_weights(
            PortfolioWeights(weights=np.array([0.1]), tickers=["AAPL"])
        )

    assert submitted == []


def test_partial_fill_with_lagging_position_snapshot_reserves_full_order_exposure(
    exposure_env, monkeypatch
):
    executor = BrokerExecutor()
    submitted = []
    # The position snapshot reflects only the partial fill, while the open-order
    # snapshot still reports the original active order. Reserve the full order
    # conservatively until broker position/order state is reconciled.
    partial_order = SimpleNamespace(
        id="external-partial-msft",
        symbol="MSFT",
        status="partially_filled",
        qty="50",
        filled_qty="20",
        limit_price="100",
        notional="5000",
    )
    monkeypatch.setattr(executor, "get_account_status", _account)
    monkeypatch.setattr(
        executor,
        "get_positions",
        lambda: {
            "MSFT": {
                "qty": 20.0,
                "market_value": 0.0,
                "avg_entry_price": 100.0,
                "unrealized_pl": 0.0,
            }
        },
    )
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [partial_order])
    monkeypatch.setattr(
        executor.alpaca_feed,
        "get_latest_quotes",
        lambda tickers: {"MSFT": _quote(100.0), "AAPL": _quote(100.0)},
    )
    monkeypatch.setattr(
        executor.trading_client,
        "submit_order",
        lambda request: submitted.append(request),
    )

    executor.execute_weights(
        PortfolioWeights(weights=np.array([0.1]), tickers=["AAPL"])
    )

    assert "AAPL" not in _buy_symbols(submitted)


def test_unpriced_external_order_blocks_new_positions(
    exposure_env, monkeypatch
):
    executor = BrokerExecutor()
    submitted = []
    external_order = SimpleNamespace(
        id="manual-order-unknown",
        symbol="XYZ",
        status="accepted",
        qty="10",
        filled_qty="0",
        limit_price=None,
        notional=None,
    )
    monkeypatch.setattr(executor, "get_account_status", _account)
    monkeypatch.setattr(executor, "get_positions", lambda: {})
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [external_order])
    monkeypatch.setattr(
        executor.alpaca_feed,
        "get_latest_quotes",
        lambda tickers: {"AAPL": _quote(100.0)},
    )
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
