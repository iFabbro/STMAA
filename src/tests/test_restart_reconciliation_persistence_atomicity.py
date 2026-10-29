"""Restart audit: terminal-fill reconciliation must be durable before execution resumes."""
import json
from types import SimpleNamespace

import numpy as np
import pytest

from src.execution.broker_executor import BrokerExecutor
from src.strategy.weight_interface import PortfolioWeights


def test_restart_does_not_accept_terminal_fill_when_reconciliation_persistence_fails(
    monkeypatch, tmp_path
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "test-secret")
    state_path = tmp_path / "state" / "broker_state.json"
    monkeypatch.setenv("STMAA_BROKER_STATE_FILE", str(state_path))

    first = BrokerExecutor(paper_trading=True)
    first.state["order_submission_confirmed"] = {
        "client-order-aapl": {
            "ticker": "AAPL",
            "side": "buy",
            "quantity": 2.0,
            "order_id": "broker-order-aapl",
            "client_order_id": "client-order-aapl",
            "status": "filled",
            "filled_qty": 2.0,
            "pre_order_position_qty": 0.0,
            "position_reconciled": False,
        }
    }
    first._save_state()

    restarted = BrokerExecutor(paper_trading=True)
    submitted = []
    monkeypatch.setattr(
        restarted.trading_client,
        "get_order_by_id",
        lambda order_id: SimpleNamespace(
            id=order_id,
            client_order_id="client-order-aapl",
            symbol="AAPL",
            side="buy",
            qty="2",
            filled_qty="2",
            status="filled",
        ),
    )
    monkeypatch.setattr(
        restarted,
        "get_positions",
        lambda: {
            "AAPL": {
                "qty": 2.0,
                "market_value": 200.0,
                "avg_entry_price": 100.0,
                "unrealized_pl": 0.0,
            }
        },
    )
    monkeypatch.setattr(
        restarted.trading_client,
        "submit_order",
        lambda request: submitted.append(request),
    )

    def fail_persist():
        raise OSError("simulated persistence failure during position reconciliation")

    monkeypatch.setattr(restarted, "_save_state", fail_persist)

    with pytest.raises(OSError, match="persistence failure"):
        restarted.execute_weights(
            PortfolioWeights(weights=np.array([0.1]), tickers=["AAPL"])
        )

    # The in-memory transition is rolled back, and disk still records that the
    # fill has not been reconciled. Execution must stop before any new order.
    stored = restarted.state["order_submission_confirmed"]["client-order-aapl"]
    assert stored["position_reconciled"] is False
    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    assert (
        persisted["order_submission_confirmed"]["client-order-aapl"][
            "position_reconciled"
        ]
        is False
    )
    assert submitted == []
