"""Fail-closed E2E tests for persisted terminal orders after restart."""
from types import SimpleNamespace

import numpy as np
import pytest

from src.execution.broker_executor import BrokerExecutor
from src.strategy.weight_interface import PortfolioWeights


@pytest.fixture
def terminal_order_env(monkeypatch, tmp_path):
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
        executor.trading_client,
        "submit_order",
        lambda request: submitted.append(request),
    )
    # Simulate state restored from disk after a process restart: the broker
    # acknowledged a terminal order, but its position effect is not reconciled.
    executor.state["order_submission_confirmed"] = {
        "client-aapl-buy": {
            "ticker": "AAPL",
            "side": "buy",
            "quantity": 10.0,
            "order_id": "broker-order-123",
            "client_order_id": "client-aapl-buy",
            "status": "filled",
            "filled_qty": 10.0,
            "pre_order_position_qty": 0.0,
            "position_reconciled": False,
        }
    }
    monkeypatch.setattr(
        executor.trading_client,
        "get_order_by_id",
        lambda order_id: SimpleNamespace(
            id=order_id,
            symbol="AAPL",
            side="buy",
            qty="10",
            filled_qty="10",
            status="filled",
        ),
    )
    return executor, submitted


def _target_weights():
    return PortfolioWeights(weights=np.array([0.1]), tickers=["AAPL"])


def test_restart_with_terminal_fill_and_lagging_position_snapshot_blocks_new_orders(
    terminal_order_env, monkeypatch
):
    executor, submitted = terminal_order_env
    # Broker says the order filled 10 shares, but the position snapshot still
    # reports only 5. Treating this as reconciled could understate exposure.
    monkeypatch.setattr(
        executor,
        "get_positions",
        lambda: {
            "AAPL": {
                "qty": 5.0,
                "market_value": 500.0,
                "avg_entry_price": 100.0,
                "unrealized_pl": 0.0,
            }
        },
    )

    with pytest.raises(RuntimeError, match="has not reconciled terminal order"):
        executor.execute_weights(_target_weights())

    assert submitted == []


def test_restart_with_terminal_order_missing_pre_order_baseline_blocks(
    terminal_order_env, monkeypatch
):
    executor, submitted = terminal_order_env
    executor.state["order_submission_confirmed"]["client-aapl-buy"].pop(
        "pre_order_position_qty"
    )
    monkeypatch.setattr(executor, "get_positions", lambda: {})

    with pytest.raises(RuntimeError, match="lacks a pre-order position baseline"):
        executor.execute_weights(_target_weights())

    assert submitted == []


def test_restart_with_terminal_order_and_mismatched_broker_identity_blocks(
    terminal_order_env, monkeypatch
):
    executor, submitted = terminal_order_env
    monkeypatch.setattr(
        executor.trading_client,
        "get_order_by_id",
        lambda order_id: SimpleNamespace(
            id=order_id,
            symbol="MSFT",
            side="buy",
            qty="10",
            filled_qty="10",
            status="filled",
        ),
    )
    monkeypatch.setattr(
        executor,
        "get_positions",
        lambda: {
            "AAPL": {
                "qty": 10.0,
                "market_value": 1000.0,
                "avg_entry_price": 100.0,
                "unrealized_pl": 0.0,
            }
        },
    )

    with pytest.raises(RuntimeError, match="identity differs from persisted intent"):
        executor.execute_weights(_target_weights())

    assert submitted == []
