"""Integrated fail-closed checks: pending orders, stale quotes, and partial position reads."""
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
    executor = BrokerExecutor(paper_trading=True)
    monkeypatch.setattr(executor, "get_account_status", lambda: {
        "cash": 10000.0,
        "portfolio_value": 10000.0,
        "buying_power": 10000.0,
        "equity": 10000.0,
        "account_data_fresh": True,
        "account_id": "test-account",
    })
    return executor


def _target():
    return PortfolioWeights(weights=np.array([0.10]), tickers=["AAPL"])


def test_pending_broker_order_blocks_rebalance_and_sends_zero_orders(
    isolated_executor, monkeypatch
):
    executor = isolated_executor
    submitted = []
    executor.state["order_submission_confirmed"] = {
        "client-order-1": {
            "ticker": "AAPL",
            "side": "buy",
            "quantity": 10.0,
            "order_id": "broker-order-1",
            "client_order_id": "client-order-1",
            "status": "accepted",
        }
    }
    monkeypatch.setattr(
        executor.trading_client,
        "get_order_by_id",
        lambda order_id: SimpleNamespace(
            id=order_id,
            client_order_id="client-order-1",
            status="accepted",
            symbol="AAPL",
            side="buy",
            qty="10.0",
            filled_qty="0",
        ),
    )
    monkeypatch.setattr(
        executor.trading_client,
        "submit_order",
        lambda request: submitted.append(request),
    )

    with pytest.raises(
        RuntimeError,
        match="requires reconciliation or terminal status",
    ):
        executor.execute_weights(_target())

    assert len(submitted) == 0


def test_stale_quote_blocks_rebalance_and_sends_zero_orders(
    isolated_executor, monkeypatch
):
    executor = isolated_executor
    submitted = []
    monkeypatch.setattr(executor, "get_positions", lambda: {})
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [])
    stale_timestamp = (
        datetime.now(timezone.utc) - timedelta(minutes=10)
    ).isoformat()
    monkeypatch.setattr(
        executor.alpaca_feed,
        "get_latest_quotes",
        lambda tickers: {
            "AAPL": {"price": 100.0, "timestamp": stale_timestamp}
        },
    )
    monkeypatch.setattr(
        executor.trading_client,
        "submit_order",
        lambda request: submitted.append(request),
    )

    with pytest.raises(
        RuntimeError,
        match="valid prices unavailable for: AAPL",
    ):
        executor.execute_weights(_target())

    assert len(submitted) == 0


def test_incomplete_position_snapshot_blocks_rebalance_and_sends_zero_orders(
    isolated_executor, monkeypatch
):
    executor = isolated_executor
    submitted = []
    monkeypatch.setattr(
        executor,
        "get_positions",
        lambda: (_ for _ in ()).throw(
            RuntimeError(
                "Unable to retrieve broker positions; refusing to execute trades"
            )
        ),
    )
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [])
    monkeypatch.setattr(
        executor.trading_client,
        "submit_order",
        lambda request: submitted.append(request),
    )

    with pytest.raises(
        RuntimeError,
        match="Unable to retrieve broker positions",
    ):
        executor.execute_weights(_target())

    assert len(submitted) == 0

def test_duplicate_target_tickers_are_rejected_before_account_reads(
    isolated_executor, monkeypatch
):
    executor = isolated_executor
    submitted = []
    monkeypatch.setattr(
        executor,
        "get_account_status",
        lambda: (_ for _ in ()).throw(
            AssertionError("Account reads must not occur for invalid target allocation")
        ),
    )
    monkeypatch.setattr(
        executor.trading_client,
        "submit_order",
        lambda request: submitted.append(request),
    )

    duplicate_target = PortfolioWeights(
        weights=np.array([0.20, 0.30]),
        tickers=["AAPL", " aapl "],
    )
    with pytest.raises(ValueError, match="Target tickers must be unique"):
        executor.execute_weights(duplicate_target)

    assert submitted == []

