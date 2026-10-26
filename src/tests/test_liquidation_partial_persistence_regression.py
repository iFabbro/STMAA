"""Regression tests for partial liquidation, cancellation failures, and state persistence.

All broker interactions are simulated; no live broker operations are performed.
"""
import json
from types import SimpleNamespace

import pytest

from src.execution.broker_executor import BrokerExecutor


@pytest.fixture
def executor_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "test-secret")


class LiquidationClient:
    def __init__(self, positions=None, open_orders=None, close_errors=None, cancel_error=None):
        self.positions = list(positions or [])
        self.open_orders = list(open_orders or [])
        self.close_errors = dict(close_errors or {})
        self.cancel_error = cancel_error
        self.close_calls = []
        self.cancel_calls = []

    def get_all_positions(self):
        return list(self.positions)

    def cancel_order_by_id(self, order_id):
        self.cancel_calls.append(str(order_id))
        if self.cancel_error:
            raise self.cancel_error

    def get_order_by_id(self, order_id):
        raise LookupError(f"no recorded order {order_id}")

    def get_orders(self, **kwargs):
        return list(self.open_orders)

    def close_position(self, symbol):
        self.close_calls.append(symbol)
        error = self.close_errors.get(symbol)
        if error:
            raise error
        return SimpleNamespace(id=f"close-{symbol}", status="accepted")


def _position(symbol, qty):
    return SimpleNamespace(symbol=symbol, qty=str(qty))


def test_multiple_positions_partial_liquidation_persists_each_outcome(executor_env):
    executor = BrokerExecutor()
    client = LiquidationClient(
        positions=[_position("AAPL", 2), _position("MSFT", 3)],
        close_errors={"MSFT": TimeoutError("response lost")},
    )
    executor.trading_client = client
    executor._get_open_orders = lambda: []

    with pytest.raises(TimeoutError, match="response lost"):
        executor._liquidate_all_positions([])

    persisted = json.loads(executor.state_file.read_text(encoding="utf-8"))
    assert persisted["emergency_liquidation_pending"]["AAPL"] == "close-AAPL"
    assert persisted["emergency_liquidation_unresolved"]["MSFT"]["requested_qty"] == 3.0
    assert client.close_calls == ["AAPL", "MSFT"]


@pytest.mark.parametrize(
    "positions",
    [
        [_position("AAPL", 2), SimpleNamespace(symbol="", qty="1")],
        [_position("AAPL", 2), _position("AAPL", 3)],
        [_position("AAPL", 2), _position("MSFT", "nan")],
    ],
)
def test_malformed_positions_snapshot_blocks_all_new_closes(executor_env, positions):
    """Validate the entire broker snapshot before acting on any valid-looking row."""
    executor = BrokerExecutor()
    client = LiquidationClient(positions=positions)
    executor.trading_client = client
    executor._get_open_orders = lambda: []

    with pytest.raises(RuntimeError, match="Cannot safely liquidate"):
        executor._liquidate_all_positions([])

    assert client.close_calls == []
    persisted = json.loads(executor.state_file.read_text(encoding="utf-8"))
    assert persisted.get("emergency_liquidation_pending", {}) == {}
    assert persisted.get("emergency_liquidation_unresolved", {}) == {}


def test_cancel_failure_aborts_before_any_position_close(executor_env):
    executor = BrokerExecutor()
    client = LiquidationClient(
        positions=[_position("AAPL", 2)],
        cancel_error=ConnectionError("cancel outcome unavailable"),
    )
    executor.trading_client = client
    executor._get_open_orders = lambda: []

    with pytest.raises(ConnectionError, match="cancel outcome unavailable"):
        executor._liquidate_all_positions([SimpleNamespace(id="unrelated-order", symbol="TSLA")])

    assert client.cancel_calls == ["unrelated-order"]
    assert client.close_calls == []


def test_failure_to_persist_close_intent_prevents_broker_close(executor_env, monkeypatch):
    executor = BrokerExecutor()
    client = LiquidationClient(positions=[_position("AAPL", 2)])
    executor.trading_client = client
    executor._get_open_orders = lambda: []
    monkeypatch.setattr(executor, "_save_state", lambda: (_ for _ in ()).throw(OSError("disk full")))

    with pytest.raises(OSError, match="disk full"):
        executor._liquidate_all_positions([])

    assert client.close_calls == []
    assert "AAPL" in executor.state["emergency_liquidation_unresolved"]


def test_failure_to_persist_close_ack_keeps_durable_intent_and_blocks_retry(executor_env, monkeypatch):
    executor = BrokerExecutor()
    client = LiquidationClient(positions=[_position("AAPL", 2)])
    executor.trading_client = client
    executor._get_open_orders = lambda: []
    original_save = executor._save_state
    saves = 0

    def fail_second_save():
        nonlocal saves
        saves += 1
        if saves == 2:
            raise OSError("disk full after broker acknowledgement")
        original_save()

    monkeypatch.setattr(executor, "_save_state", fail_second_save)

    with pytest.raises(OSError, match="disk full after broker acknowledgement"):
        executor._liquidate_all_positions([])

    persisted = json.loads(executor.state_file.read_text(encoding="utf-8"))
    assert "AAPL" in persisted["emergency_liquidation_unresolved"]
    assert client.close_calls == ["AAPL"]

    restarted = BrokerExecutor()
    retry_client = LiquidationClient(positions=[_position("AAPL", 2)])
    restarted.trading_client = retry_client
    restarted._get_open_orders = lambda: []

    restarted._liquidate_all_positions([])

    assert retry_client.close_calls == []
    assert "AAPL" in restarted.state["emergency_liquidation_unresolved"]
