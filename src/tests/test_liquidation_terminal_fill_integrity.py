"""Fail-closed checks for contradictory terminal emergency-liquidation fills."""

from types import SimpleNamespace

import pytest

from src.execution.broker_executor import BrokerExecutor


@pytest.fixture
def liquidation_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "test-secret")


def test_filled_liquidation_order_with_incomplete_fill_quantity_stays_pending(
    liquidation_env, monkeypatch
):
    executor = BrokerExecutor()
    executor.state["emergency_liquidation_pending"] = {"AAPL": "close-1"}
    executor._save_state()

    class Broker:
        def __init__(self):
            self.position_reads = 0
            self.close_calls = []

        def get_all_positions(self):
            self.position_reads += 1
            # An empty snapshot must not override contradictory order evidence.
            return []

        def get_order_by_id(self, order_id):
            assert order_id == "close-1"
            return SimpleNamespace(
                id=order_id,
                symbol="AAPL",
                status="filled",
                qty="10",
                filled_qty="4",
            )

        def close_position(self, symbol):
            self.close_calls.append(symbol)
            raise AssertionError("No replacement close may be submitted")

    broker = Broker()
    executor.trading_client = broker
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [])

    with pytest.raises(
        RuntimeError,
        match="filled status with incomplete fill quantity",
    ):
        executor._liquidate_all_positions([])

    assert broker.position_reads == 1
    assert broker.close_calls == []
    assert executor.state["emergency_liquidation_pending"] == {"AAPL": "close-1"}
