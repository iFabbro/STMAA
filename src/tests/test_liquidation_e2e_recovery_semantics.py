"""E2E recovery regressions for Alpaca order-state semantics.

These tests exercise BrokerExecutor with deterministic SDK-shaped responses.
They do not contact Alpaca or submit live orders; the separate Alpaca Paper
read-only workflow validates the real broker API without mutating orders.
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


def position(symbol="AAPL", qty="0.75"):
    return SimpleNamespace(symbol=symbol, qty=qty)


class ReconciliationClient:
    def __init__(self, position_responses, order_status="rejected"):
        self.position_responses = list(position_responses)
        self.order_status = order_status
        self.position_reads = 0
        self.close_calls = []
        self.cancel_calls = []

    def get_all_positions(self):
        self.position_reads += 1
        response = self.position_responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return list(response)

    def get_order_by_id(self, order_id):
        return SimpleNamespace(id=order_id, status=self.order_status)

    def get_orders(self, **kwargs):
        return []

    def cancel_order_by_id(self, order_id):
        self.cancel_calls.append(str(order_id))

    def close_position(self, symbol):
        self.close_calls.append(symbol)
        return SimpleNamespace(id=f"retry-{symbol}", status="accepted")


def executor_with_pending_close():
    executor = BrokerExecutor()
    executor.state["emergency_liquidation_pending"] = {"AAPL": "close-previous"}
    executor._save_state()
    return executor


def test_rejected_or_cancelled_close_with_residual_position_can_retry(executor_env):
    """A terminal close may have partially filled; retry only after fresh position read."""
    executor = executor_with_pending_close()
    client = ReconciliationClient(
        position_responses=[[position()], [position()]],
        order_status="canceled",
    )
    executor.trading_client = client
    executor._get_open_orders = lambda: []

    result = executor._liquidate_all_positions([])

    assert client.position_reads == 2
    assert client.close_calls == ["AAPL"]
    assert result[0]["order_id"] == "retry-AAPL"
    assert executor.state["emergency_liquidation_pending"]["AAPL"] == "retry-AAPL"


def test_failed_fresh_position_read_preserves_pending_and_blocks_retry(executor_env):
    """A stale first snapshot cannot authorize a retry when refresh fails."""
    executor = executor_with_pending_close()
    client = ReconciliationClient(
        position_responses=[
            [position()],
            ConnectionError("fresh positions unavailable"),
        ],
        order_status="rejected",
    )
    executor.trading_client = client
    executor._get_open_orders = lambda: []

    with pytest.raises(ConnectionError, match="fresh positions unavailable"):
        executor._liquidate_all_positions([])

    assert client.position_reads == 2
    assert client.close_calls == []
    persisted = json.loads(executor.state_file.read_text(encoding="utf-8"))
    assert persisted["emergency_liquidation_pending"]["AAPL"] == "close-previous"


def test_replaced_or_unknown_order_outcome_remains_pending(executor_env):
    """Unknown/replaced outcomes must not trigger a duplicate close."""
    executor = executor_with_pending_close()
    client = ReconciliationClient(
        position_responses=[[position()]],
        order_status="replaced",
    )
    executor.trading_client = client
    executor._get_open_orders = lambda: []

    executor._liquidate_all_positions([])

    assert client.close_calls == []
    assert executor.state["emergency_liquidation_pending"]["AAPL"] == "close-previous"
