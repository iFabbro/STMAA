"""Read-only mock regressions for liquidation cancellation, partial closes, and restart recovery.

Every broker interaction is simulated; these tests never contact Alpaca.
"""
import json
from types import SimpleNamespace

import pytest

from src.execution.broker_executor import BrokerExecutor


@pytest.fixture
def audit_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "test-secret")


def position(symbol, qty):
    return SimpleNamespace(symbol=symbol, qty=str(qty))


class RecoveryClient:
    def __init__(self, snapshots, orders=None, order_statuses=None):
        self.snapshots = list(snapshots)
        self.open_orders = list(orders or [])
        self.order_statuses = dict(order_statuses or {})
        self.close_calls = []
        self.cancel_calls = []
        self.position_reads = 0

    def get_all_positions(self):
        self.position_reads += 1
        if not self.snapshots:
            raise AssertionError("unexpected extra positions read")
        return self.snapshots.pop(0)

    def get_orders(self, **kwargs):
        return list(self.open_orders)

    def cancel_order_by_id(self, order_id):
        self.cancel_calls.append(str(order_id))

    def get_order_by_id(self, order_id):
        status = self.order_statuses.get(str(order_id))
        if status is None:
            raise LookupError(f"unknown order {order_id}")
        return SimpleNamespace(id=str(order_id), status=status)

    def close_position(self, symbol):
        self.close_calls.append(symbol)
        return SimpleNamespace(id=f"new-close-{symbol}", status="accepted")


def test_cancel_ack_does_not_allow_close_while_order_remains_open(audit_env):
    executor = BrokerExecutor()
    unrelated = SimpleNamespace(id="open-1", symbol="AAPL")
    client = RecoveryClient(
        snapshots=[[position("AAPL", 2)]],
        orders=[unrelated],
    )
    executor.trading_client = client
    executor._get_open_orders = lambda: [unrelated]

    with pytest.raises(RuntimeError, match="confirm unrelated open orders are cancelled"):
        executor._liquidate_all_positions([unrelated])

    assert client.cancel_calls == ["open-1"]
    assert client.close_calls == []
    assert client.position_reads == 0


def test_cancelled_close_with_partial_fill_retries_only_refreshed_residual(audit_env):
    executor = BrokerExecutor()
    executor.state["emergency_liquidation_pending"] = {"AAPL": "old-close"}
    executor._save_state()
    client = RecoveryClient(
        snapshots=[
            [position("AAPL", 2)],  # snapshot before terminal-order reconciliation
            [position("AAPL", 1)],  # refreshed after canceled order; partial fill remains
        ],
        order_statuses={"old-close": "canceled"},
    )
    executor.trading_client = client
    executor._get_open_orders = lambda: []

    liquidations = executor._liquidate_all_positions([])

    assert client.position_reads == 2
    assert client.close_calls == ["AAPL"]
    assert liquidations[0]["quantity"] == pytest.approx(1.0)
    persisted = json.loads(executor.state_file.read_text(encoding="utf-8"))
    assert persisted["emergency_liquidation_pending"]["AAPL"] == "new-close-AAPL"
    assert persisted.get("emergency_liquidation_unresolved", {}) == {}


def test_restart_with_still_open_close_order_does_not_submit_duplicate(audit_env):
    executor = BrokerExecutor()
    executor.state["emergency_liquidation_pending"] = {"AAPL": "existing-close"}
    executor._save_state()

    restored = BrokerExecutor()
    existing = SimpleNamespace(id="existing-close", symbol="AAPL")
    client = RecoveryClient(
        snapshots=[[position("AAPL", 2)]],
        orders=[existing],
    )
    restored.trading_client = client
    restored._get_open_orders = lambda: [existing]

    result = restored._liquidate_all_positions([existing])

    assert result == []
    assert client.close_calls == []
    persisted = json.loads(restored.state_file.read_text(encoding="utf-8"))
    assert persisted["emergency_liquidation_pending"]["AAPL"] == "existing-close"
