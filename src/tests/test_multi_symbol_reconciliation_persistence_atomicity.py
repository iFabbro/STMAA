"""Multi-symbol terminal-fill reconciliation must persist atomically across restart."""
import json
from types import SimpleNamespace

import numpy as np
import pytest

from src.execution.broker_executor import BrokerExecutor
from src.strategy.weight_interface import PortfolioWeights


def _terminal_order(symbol, client_order_id, broker_order_id):
    return {
        "ticker": symbol,
        "side": "buy",
        "quantity": 2.0,
        "order_id": broker_order_id,
        "client_order_id": client_order_id,
        "status": "filled",
        "filled_qty": 2.0,
        "pre_order_position_qty": 0.0,
        "position_reconciled": False,
    }


def _mock_broker_order_reads(monkeypatch, executor):
    orders = {
        "broker-order-aapl": ("client-order-aapl", "AAPL"),
        "broker-order-msft": ("client-order-msft", "MSFT"),
    }

    def get_order_by_id(order_id):
        client_order_id, symbol = orders[order_id]
        return SimpleNamespace(
            id=order_id,
            client_order_id=client_order_id,
            symbol=symbol,
            side="buy",
            qty="2",
            filled_qty="2",
            status="filled",
        )

    monkeypatch.setattr(
        executor.trading_client, "get_order_by_id", get_order_by_id
    )


def test_multi_symbol_reconciliation_persistence_failure_rolls_back_all_orders(
    monkeypatch, tmp_path
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "test-secret")
    state_path = tmp_path / "state" / "broker_state.json"
    monkeypatch.setenv("STMAA_BROKER_STATE_FILE", str(state_path))

    initial = BrokerExecutor(paper_trading=True)
    initial.state["order_submission_confirmed"] = {
        "client-order-aapl": _terminal_order(
            "AAPL", "client-order-aapl", "broker-order-aapl"
        ),
        "client-order-msft": _terminal_order(
            "MSFT", "client-order-msft", "broker-order-msft"
        ),
    }
    initial._save_state()

    restarted = BrokerExecutor(paper_trading=True)
    _mock_broker_order_reads(monkeypatch, restarted)
    monkeypatch.setattr(
        restarted,
        "get_positions",
        lambda: {
            "AAPL": {"qty": 2.0, "market_value": 200.0},
            "MSFT": {"qty": 2.0, "market_value": 400.0},
        },
    )
    submitted = []
    monkeypatch.setattr(
        restarted.trading_client,
        "submit_order",
        lambda request: submitted.append(request),
    )

    def fail_persist():
        raise OSError("simulated multi-symbol reconciliation persistence failure")

    monkeypatch.setattr(restarted, "_save_state", fail_persist)

    with pytest.raises(OSError, match="multi-symbol reconciliation persistence failure"):
        restarted.execute_weights(
            PortfolioWeights(
                weights=np.array([0.1, 0.1]), tickers=["AAPL", "MSFT"]
            )
        )

    for order in restarted.state["order_submission_confirmed"].values():
        assert order["position_reconciled"] is False
    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    for order in persisted["order_submission_confirmed"].values():
        assert order["position_reconciled"] is False
    assert submitted == []

    # A fresh process must reload both orders as unreconciled and fail closed
    # if it cannot obtain a fresh position snapshot.
    after_failure_restart = BrokerExecutor(paper_trading=True)
    _mock_broker_order_reads(monkeypatch, after_failure_restart)

    def unavailable_positions():
        raise ConnectionError("position snapshot unavailable after restart")

    monkeypatch.setattr(
        after_failure_restart, "get_positions", unavailable_positions
    )
    restarted_submissions = []
    monkeypatch.setattr(
        after_failure_restart.trading_client,
        "submit_order",
        lambda request: restarted_submissions.append(request),
    )

    with pytest.raises(ConnectionError, match="position snapshot unavailable"):
        after_failure_restart.execute_weights(
            PortfolioWeights(
                weights=np.array([0.1, 0.1]), tickers=["AAPL", "MSFT"]
            )
        )

    for order in after_failure_restart.state["order_submission_confirmed"].values():
        assert order["position_reconciled"] is False
    assert restarted_submissions == []
