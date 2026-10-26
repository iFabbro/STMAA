"""Recovery and idempotency regression tests; all broker interactions are mocked."""

import json
from types import SimpleNamespace

import numpy as np
import pytest

from src.execution.broker_executor import BrokerExecutor
from src.strategy.weight_interface import PortfolioWeights


@pytest.fixture
def alpaca_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "test-secret")


class FakeLiquidationClient:
    def __init__(self, positions=None, history=None, close_error=None, open_orders=None):
        self.positions = list(positions or [])
        self.history = list(history or [])
        self.close_error = close_error
        self.open_orders = list(open_orders or [])
        self.close_calls = []
        self.history_reads = 0

    def get_all_positions(self):
        return list(self.positions)

    def get_orders(self, **kwargs):
        self.history_reads += 1
        return list(self.history)

    def close_position(self, symbol):
        self.close_calls.append(symbol)
        if self.close_error:
            raise self.close_error
        return SimpleNamespace(id=f"close-{symbol}", status="accepted")


def test_ambiguous_close_is_not_resubmitted_after_restart(alpaca_env):
    position = SimpleNamespace(symbol="AAPL", qty="2")
    first = BrokerExecutor()
    first_client = FakeLiquidationClient(
        positions=[position],
        close_error=TimeoutError("response lost after request"),
    )
    first.trading_client = first_client
    first._get_open_orders = lambda: []

    with pytest.raises(TimeoutError, match="response lost"):
        first._liquidate_all_positions([])

    persisted = json.loads(first.state_file.read_text(encoding="utf-8"))
    assert persisted["emergency_liquidation_unresolved"]["AAPL"]["requested_qty"] == 2.0
    assert first_client.close_calls == ["AAPL"]

    restarted = BrokerExecutor()
    second_client = FakeLiquidationClient(positions=[position], history=[])
    restarted.trading_client = second_client
    restarted._get_open_orders = lambda: []

    restarted._liquidate_all_positions([])

    assert second_client.history_reads == 1
    assert second_client.close_calls == []
    assert "AAPL" in restarted.state["emergency_liquidation_unresolved"]


def test_active_close_order_with_residual_position_is_not_duplicated(alpaca_env):
    executor = BrokerExecutor()
    executor.state["emergency_liquidation_pending"] = {"AAPL": "close-existing"}
    executor._save_state()
    residual_position = SimpleNamespace(symbol="AAPL", qty="0.75")
    client = FakeLiquidationClient(
        positions=[residual_position],
        open_orders=[SimpleNamespace(id="close-existing", symbol="AAPL")],
    )
    executor.trading_client = client
    executor._get_open_orders = lambda: list(client.open_orders)

    executor._liquidate_all_positions(list(client.open_orders))

    assert client.close_calls == []
    assert executor.state["emergency_liquidation_pending"]["AAPL"] == "close-existing"


def test_unresolved_order_submission_after_restart_blocks_new_orders(alpaca_env):
    executor = BrokerExecutor()
    executor.state["order_submission_unresolved"] = {
        "AAPL": {
            "client_order_id": "client-order-ambiguous",
            "side": "buy",
            "quantity": 1.0,
            "price": 100.0,
            "created_at": "2026-10-26T00:00:00+00:00",
            "reason": "submission_outcome_unconfirmed",
        }
    }
    executor._save_state()

    restarted = BrokerExecutor()
    submitted = []

    class ReadUnavailableClient:
        def get_order_by_client_id(self, client_order_id):
            raise ConnectionError("broker reconciliation unavailable")

        def submit_order(self, request):
            submitted.append(request)
            raise AssertionError("must not submit while the prior outcome is unresolved")

    restarted.trading_client = ReadUnavailableClient()

    with pytest.raises(RuntimeError, match="requires reconciliation or terminal status"):
        restarted.execute_weights(
            PortfolioWeights(weights=np.array([0.1]), tickers=["AAPL"])
        )

    assert submitted == []
    assert "AAPL" in restarted.state["order_submission_unresolved"]
