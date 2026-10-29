"""Fail-closed restart reconciliation for malformed broker execution state."""
from types import SimpleNamespace

import numpy as np
import pytest

from src.execution.broker_executor import BrokerExecutor
from src.strategy.weight_interface import PortfolioWeights


@pytest.fixture
def restart_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "test-secret")
    monkeypatch.setenv(
        "STMAA_BROKER_STATE_FILE",
        str(tmp_path / "state" / "broker_state.json"),
    )


@pytest.mark.parametrize(
    ("field", "value", "remove_field"),
    [
        ("status", None, False),
        ("status", "", False),
        ("status", "unknown", False),
        ("status", None, True),
        ("filled_qty", None, False),
        ("filled_qty", "NaN", False),
        ("filled_qty", "inf", False),
        ("filled_qty", "-1", False),
        ("filled_qty", "11", False),
        ("filled_qty", None, True),
    ],
)
def test_restart_keeps_untrusted_broker_execution_state_unresolved(
    restart_env, monkeypatch, field, value, remove_field
):
    first = BrokerExecutor(paper_trading=True)
    intent = {
        "client_order_id": "expected-client-id",
        "side": "buy",
        "quantity": 10.0,
        "price": 100.0,
        "pre_order_position_qty": 0.0,
        "created_at": "2026-10-29T12:00:00+00:00",
        "reason": "submission_outcome_unconfirmed",
    }
    first.state["order_submission_unresolved"] = {"AAPL": intent}
    first._save_state()

    restarted = BrokerExecutor(paper_trading=True)
    broker_order = {
        "id": "broker-order-123",
        "client_order_id": "expected-client-id",
        "symbol": "AAPL",
        "side": "buy",
        "qty": "10",
        "filled_qty": "0",
        "status": "accepted",
    }
    if remove_field:
        broker_order.pop(field, None)
    else:
        broker_order[field] = value

    submitted = []
    monkeypatch.setattr(
        restarted.trading_client,
        "get_order_by_client_id",
        lambda client_order_id: SimpleNamespace(**broker_order),
    )
    monkeypatch.setattr(
        restarted.trading_client,
        "submit_order",
        lambda request: submitted.append(request),
    )
    monkeypatch.setattr(
        restarted,
        "get_account_status",
        lambda: (_ for _ in ()).throw(
            AssertionError("Must block before account sizing and execution")
        ),
    )

    with pytest.raises(RuntimeError, match="requires reconciliation or terminal status"):
        restarted.execute_weights(
            PortfolioWeights(weights=np.array([0.1]), tickers=["AAPL"])
        )

    assert restarted.state["order_submission_unresolved"]["AAPL"] == intent
    assert restarted.state["order_submission_confirmed"] == {}
    assert submitted == []
