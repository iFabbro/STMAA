"""Regression coverage for terminal broker orders and delayed position propagation."""
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


class DelayedBroker:
    def __init__(self):
        self.positions = []
        self.submitted = []
        self.account_reads = 0

    def get_order_by_id(self, order_id):
        assert order_id == "broker-order-1"
        return SimpleNamespace(
            id=order_id,
            symbol="AAPL",
            side="buy",
            status="filled",
            qty="1",
            filled_qty="1",
        )

    def get_all_positions(self):
        return list(self.positions)

    def submit_order(self, request):
        self.submitted.append(request)
        raise AssertionError("Must not submit while the terminal order is unreconciled")


def _confirmed_order():
    return {
        "ticker": "AAPL",
        "side": "buy",
        "quantity": 1.0,
        "order_id": "broker-order-1",
        "client_order_id": "client-order-1",
        "status": "accepted",
        "price": 100.0,
        "pre_order_position_qty": 0.0,
    }


def test_filled_order_with_delayed_position_snapshot_blocks_new_submission(
    executor_env, monkeypatch
):
    executor = BrokerExecutor()
    broker = DelayedBroker()
    executor.trading_client = broker
    executor.state["order_submission_confirmed"] = {
        "client-order-1": _confirmed_order()
    }
    executor._save_state()
    monkeypatch.setattr(
        executor,
        "get_account_status",
        lambda: broker.account_reads.__class__(0),
    )

    with pytest.raises(
        RuntimeError,
        match="position snapshot has not reconciled terminal order",
    ):
        executor.execute_weights(
            PortfolioWeights(weights=np.array([0.01]), tickers=["AAPL"])
        )

    assert broker.submitted == []
    assert broker.account_reads == 0
    assert executor.state["order_submission_confirmed"]["client-order-1"].get(
        "position_reconciled"
    ) is not True


def test_terminal_order_unblocks_only_after_position_snapshot_catches_up(
    executor_env
):
    executor = BrokerExecutor()
    broker = DelayedBroker()
    executor.trading_client = broker
    executor.state["order_submission_confirmed"] = {
        "client-order-1": _confirmed_order()
    }
    executor._save_state()

    executor._refresh_confirmed_submission_statuses()
    with pytest.raises(
        RuntimeError,
        match="position snapshot has not reconciled terminal order",
    ):
        executor._reconcile_terminal_order_positions()

    broker.positions = [
        SimpleNamespace(
            symbol="AAPL",
            qty="1",
            market_value="100",
            avg_entry_price="100",
            unrealized_pl="0",
        )
    ]
    executor._refresh_confirmed_submission_statuses()
    executor._reconcile_terminal_order_positions()

    confirmed = executor.state["order_submission_confirmed"]["client-order-1"]
    assert confirmed["status"] == "filled"
    assert confirmed["filled_qty"] == 1.0
    assert confirmed["position_reconciled"] is True
