"""Regression tests for aggregate pending exposure and broker-position overlap."""
from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np

from src.execution.broker_executor import BrokerExecutor
from src.strategy.weight_interface import PortfolioWeights


def _order(order_id, symbol, status, qty, filled_qty, limit_price):
    return SimpleNamespace(
        id=order_id,
        symbol=symbol,
        status=status,
        qty=str(qty),
        filled_qty=str(filled_qty),
        limit_price=str(limit_price),
        notional=None,
    )


def _weights():
    return PortfolioWeights(weights=np.array([0.1]), tickers=["AAPL"])


def test_aggregate_reserve_uses_full_quantity_for_partial_fill():
    executor = BrokerExecutor.__new__(BrokerExecutor)
    orders = [
        _order("partial-msft", "MSFT", "partially_filled", 20, 10, 100),
        _order("accepted-tsla", "TSLA", "accepted", 10, 0, 100),
    ]

    reserved = executor._get_reserved_notional(orders, prices={})

    # Reserve the full requested notional until a later position reconciliation;
    # using only the unfilled remainder would understate exposure during snapshot lag.
    assert reserved == 3000.0


def test_existing_position_plus_aggregate_pending_reserve_blocks_new_position(
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
            "account_id": "aggregate-position-overlap-test",
            "account_snapshot_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    monkeypatch.setattr(
        executor,
        "get_positions",
        lambda: {
            "MSFT": {
                "qty": 20.0,
                "market_value": 2000.0,
                "avg_entry_price": 100.0,
                "unrealized_pl": 0.0,
            }
        },
    )
    monkeypatch.setattr(
        executor,
        "_get_prices_from_bars",
        lambda tickers: {"AAPL": 100.0, "MSFT": 100.0, "TSLA": 100.0},
    )
    monkeypatch.setattr(
        executor,
        "_get_open_orders",
        lambda: [
            _order("partial-msft", "MSFT", "partially_filled", 20, 10, 100),
            _order("accepted-tsla", "TSLA", "accepted", 10, 0, 100),
        ],
    )
    monkeypatch.setattr(
        executor.trading_client,
        "submit_order",
        lambda request: submitted.append(request),
    )

    # Existing MSFT exposure (2,000) plus pending-order reserves (3,000)
    # consumes the 50% portfolio exposure limit on 10,000 equity.
    executor.execute_weights(_weights())

    assert submitted == []
