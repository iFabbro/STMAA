"""End-to-end circuit-breaker liquidation and post-execution metrics regressions.

All broker interactions are mocked. These tests never submit, cancel, or close
orders against Alpaca.
"""
import json
from pathlib import Path
import numpy as np
import pytest

from src.execution.broker_executor import BrokerExecutor
from src.utils.metrics import PortfolioMetrics
from src.strategy.weight_interface import PortfolioWeights


@pytest.fixture
def executor_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "test-secret")


def account_snapshot(equity, *, fresh=True):
    if not fresh:
        return {
            "cash": None,
            "portfolio_value": None,
            "buying_power": None,
            "equity": None,
            "account_data_fresh": False,
            "account_snapshot_at": None,
            "account_data_source": None,
        }
    return {
        "cash": equity,
        "portfolio_value": equity,
        "buying_power": equity,
        "equity": equity,
        "account_data_fresh": True,
        "account_snapshot_at": f"2026-10-26T12:0{int(equity % 10)}:00+00:00",
        "account_data_source": "alpaca_account",
    }


def liquidation_weights():
    return PortfolioWeights(
        weights=np.array([0.0]),
        tickers=["AAPL"],
        metadata={
            "circuit_breaker_triggered": True,
            "liquidate_on_circuit_breaker": True,
        },
    )


def test_circuit_breaker_liquidation_records_orders_and_post_liquidation_metrics(
    executor_env, monkeypatch
):
    executor = BrokerExecutor(initial_capital=500000.0)
    snapshots = iter([account_snapshot(10000.0), account_snapshot(9800.0)])
    monkeypatch.setattr(executor, "get_account_status", lambda: next(snapshots))
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [])
    liquidation_calls = []
    monkeypatch.setattr(
        executor,
        "_liquidate_all_positions",
        lambda open_orders: (
            liquidation_calls.append(list(open_orders))
            or [{
                "ticker": "AAPL",
                "order_id": "mock-close-1",
                "status": "accepted",
            }]
        ),
    )
    monkeypatch.setattr(executor, "get_positions", lambda: {})

    result = executor.execute_weights(liquidation_weights())

    assert liquidation_calls == [[]]
    assert result["circuit_breaker_triggered"] is True
    assert result["liquidation_requested"] is True
    assert result["orders"][0]["order_id"] == "mock-close-1"
    assert result["pre_execution_equity"] == pytest.approx(10000.0)
    assert result["equity"] == pytest.approx(9800.0)
    assert result["metrics_equity"] == pytest.approx(9800.0)
    assert result["account_data_fresh"] is True
    assert executor.state["total_orders"] == 1
    assert executor.state["equity_history"] == pytest.approx([10000.0, 9800.0])


def test_circuit_breaker_liquidation_keeps_metrics_unconfirmed_when_post_account_is_stale(
    executor_env, monkeypatch
):
    executor = BrokerExecutor(initial_capital=500000.0)
    snapshots = iter([account_snapshot(10000.0), account_snapshot(0.0, fresh=False)])
    monkeypatch.setattr(executor, "get_account_status", lambda: next(snapshots))
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [])
    liquidation_calls = []
    monkeypatch.setattr(
        executor,
        "_liquidate_all_positions",
        lambda open_orders: (
            liquidation_calls.append(list(open_orders))
            or [{
                "ticker": "AAPL",
                "order_id": "mock-close-1",
                "status": "accepted",
            }]
        ),
    )
    monkeypatch.setattr(
        executor,
        "get_positions",
        lambda: pytest.fail("Positions must not be read when post-liquidation equity is stale"),
    )

    result = executor.execute_weights(liquidation_weights())

    assert liquidation_calls == [[]]
    assert result["orders"][0]["order_id"] == "mock-close-1"
    assert result["account_data_fresh"] is False
    assert result["equity"] is None
    assert result["metrics_equity"] is None
    assert "metrics_refresh_error" in result
    assert executor.state["equity_history"] == pytest.approx([10000.0])

def test_circuit_breaker_liquidation_returns_order_outcome_when_metrics_export_fails(
    executor_env, monkeypatch
):
    executor = BrokerExecutor(initial_capital=500000.0)
    snapshots = iter([account_snapshot(10000.0), account_snapshot(9800.0)])
    monkeypatch.setattr(executor, "get_account_status", lambda: next(snapshots))
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [])
    monkeypatch.setattr(
        executor,
        "_liquidate_all_positions",
        lambda open_orders: [{
            "ticker": "AAPL",
            "order_id": "mock-close-1",
            "status": "accepted",
        }],
    )
    monkeypatch.setattr(executor, "get_positions", lambda: {})
    monkeypatch.setattr(
        PortfolioMetrics,
        "write_metrics_json",
        lambda self, *args, **kwargs: (_ for _ in ()).throw(OSError("metrics disk unavailable")),
    )

    result = executor.execute_weights(liquidation_weights())

    assert result["orders"][0]["order_id"] == "mock-close-1"
    assert result["circuit_breaker_triggered"] is True
    assert result["liquidation_requested"] is True
    assert result["metrics_equity"] == pytest.approx(9800.0)
    assert result["metrics_export_error"] == "metrics disk unavailable"
    assert result["equity"] == pytest.approx(9800.0)


def test_circuit_breaker_metrics_persistence_failure_rolls_back_uncommitted_metrics(
    executor_env, monkeypatch
):
    executor = BrokerExecutor(initial_capital=500000.0)
    snapshots = iter([account_snapshot(10000.0), account_snapshot(9800.0)])

    def mocked_account_status():
        snapshot = next(snapshots)
        executor.state["account_id"] = "mock-account"
        executor._current_account_identity_verified = True
        return {**snapshot, "account_id": "mock-account"}

    monkeypatch.setattr(executor, "get_account_status", mocked_account_status)
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [])
    monkeypatch.setattr(
        executor,
        "_liquidate_all_positions",
        lambda open_orders: [{
            "ticker": "AAPL",
            "order_id": "mock-close-1",
            "status": "accepted",
        }],
    )
    monkeypatch.setattr(executor, "get_positions", lambda: {})
    original_save_state = executor._save_state

    def fail_only_post_metrics_persist():
        if executor.state.get("equity_history") == [10000.0, 9800.0]:
            raise OSError("simulated state disk failure")
        original_save_state()

    monkeypatch.setattr(executor, "_save_state", fail_only_post_metrics_persist)

    result = executor.execute_weights(liquidation_weights())

    assert result["orders"][0]["order_id"] == "mock-close-1"
    assert result["metrics_equity"] is None
    assert "metrics_refresh_error" in result
    assert result["metrics_refresh_error"] == "simulated state disk failure"
    assert executor.state["equity_history"] == pytest.approx([10000.0])
    persisted_state = json.loads(executor.state_file.read_text(encoding="utf-8"))
    assert persisted_state["equity_history"] == pytest.approx([10000.0])
    exported_metrics = json.loads(Path("logs/metrics.json").read_text(encoding="utf-8"))
    assert exported_metrics["total_return"] == pytest.approx(0.0)
    assert exported_metrics["max_drawdown"] == pytest.approx(0.0)

    # A fresh process must recover only the durable pre-failure history.
    restarted_executor = BrokerExecutor(initial_capital=500000.0)
    assert restarted_executor.state["account_id"] == "mock-account"
    assert restarted_executor.state["equity_history"] == pytest.approx([10000.0])
    assert restarted_executor.metrics.equity_curve == pytest.approx([10000.0])


from types import SimpleNamespace


def test_partial_fill_after_canceled_pending_close_retries_only_residual_quantity(
    executor_env, monkeypatch
):
    executor = BrokerExecutor(initial_capital=500000.0)
    executor.state["emergency_liquidation_pending"] = {"AAPL": "close-1"}

    class MockBroker:
        def __init__(self):
            self.position_reads = 0
            self.close_calls = []

        def get_all_positions(self):
            self.position_reads += 1
            # The first read can be stale; the second read after terminal-order
            # reconciliation shows the residual. A third read verifies the
            # post-close refresh used to discover newly appearing positions.
            qty = 10 if self.position_reads == 1 else 4
            return [SimpleNamespace(symbol="AAPL", qty=str(qty))]

        def get_order_by_id(self, order_id):
            assert order_id == "close-1"
            return SimpleNamespace(id=order_id, symbol="AAPL", status="canceled")

        def close_position(self, symbol):
            self.close_calls.append(symbol)
            return SimpleNamespace(id="close-2", status="accepted")

    broker = MockBroker()
    executor.trading_client = broker
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [])

    result = executor._liquidate_all_positions([])

    assert broker.position_reads == 3
    assert broker.close_calls == ["AAPL"]
    assert result[0]["order_id"] == "close-2"
    assert result[0]["quantity"] == pytest.approx(4.0)
    assert executor.state["emergency_liquidation_pending"] == {"AAPL": "close-2"}
    assert executor.state["emergency_liquidation_unresolved"] == {}


def test_restart_with_unmatched_ambiguous_close_intent_fails_closed(
    executor_env, monkeypatch
):
    executor = BrokerExecutor(initial_capital=500000.0)
    executor.state["emergency_liquidation_unresolved"] = {
        "AAPL": {
            "created_at": "2026-10-26T12:00:00+00:00",
            "requested_qty": 5.0,
            "expected_side": "sell",
            "reason": "close_submission_outcome_unconfirmed",
        }
    }

    class MockBroker:
        def __init__(self):
            self.close_calls = []

        def get_all_positions(self):
            return [SimpleNamespace(symbol="AAPL", qty="5")]

        def get_orders(self, **kwargs):
            return []

        def close_position(self, symbol):
            self.close_calls.append(symbol)
            return SimpleNamespace(id="unexpected-close", status="accepted")

    broker = MockBroker()
    executor.trading_client = broker
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [])

    executor._liquidate_all_positions([])

    assert broker.close_calls == []
    assert "AAPL" in executor.state["emergency_liquidation_unresolved"]
    assert executor.state.get("emergency_liquidation_pending", {}) == {}


def test_close_accepted_but_pending_state_save_fails_restart_stays_fail_closed(
    executor_env, monkeypatch
):
    executor = BrokerExecutor(initial_capital=500000.0)
    position = SimpleNamespace(symbol="AAPL", qty="10")
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [])
    monkeypatch.setattr(executor.trading_client, "get_all_positions", lambda: [position])
    monkeypatch.setattr(executor.trading_client, "get_orders", lambda **kwargs: [])
    close_calls = []
    monkeypatch.setattr(
        executor.trading_client,
        "close_position",
        lambda symbol: (
            close_calls.append(symbol)
            or SimpleNamespace(id="mock-close-accepted", status="accepted")
        ),
    )
    original_save_state = executor._save_state

    def fail_pending_commit():
        if executor.state.get("emergency_liquidation_pending", {}).get("AAPL"):
            raise OSError("simulated pending-state disk failure")
        original_save_state()

    monkeypatch.setattr(executor, "_save_state", fail_pending_commit)

    with pytest.raises(OSError, match="simulated pending-state disk failure"):
        executor._liquidate_all_positions([])

    # Atomic persistence must leave the durable pre-request intent available.
    persisted_state = json.loads(executor.state_file.read_text(encoding="utf-8"))
    assert "AAPL" in persisted_state["emergency_liquidation_unresolved"]
    assert "AAPL" not in persisted_state.get("emergency_liquidation_pending", {})
    assert close_calls == ["AAPL"]

    # Simulate a process restart from the last durable state. No unique broker
    # history match is available, so the unresolved intent must block resubmission.
    restarted = BrokerExecutor(initial_capital=500000.0)
    monkeypatch.setattr(restarted, "_get_open_orders", lambda: [])
    monkeypatch.setattr(restarted.trading_client, "get_all_positions", lambda: [position])
    monkeypatch.setattr(restarted.trading_client, "get_orders", lambda **kwargs: [])
    monkeypatch.setattr(
        restarted.trading_client,
        "close_position",
        lambda symbol: pytest.fail("restart must not submit a duplicate close"),
    )

    assert restarted._liquidate_all_positions([]) == []
    assert "AAPL" in restarted.state["emergency_liquidation_unresolved"]
    assert close_calls == ["AAPL"]


def test_liquidation_bookkeeping_save_failure_rolls_back_memory_counters(
    executor_env, monkeypatch
):
    executor = BrokerExecutor(initial_capital=500000.0)
    snapshots = iter([account_snapshot(10000.0)])
    monkeypatch.setattr(executor, "get_account_status", lambda: next(snapshots))
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [])
    original_save_state = executor._save_state

    def persist_liquidation_order(open_orders):
        executor.state["emergency_liquidation_pending"] = {"AAPL": "mock-close-1"}
        original_save_state()
        return [{"ticker": "AAPL", "order_id": "mock-close-1", "status": "accepted"}]

    monkeypatch.setattr(executor, "_liquidate_all_positions", persist_liquidation_order)

    def fail_bookkeeping_commit():
        if executor.state.get("total_orders", 0) > 0:
            raise OSError("simulated bookkeeping disk failure")
        original_save_state()

    monkeypatch.setattr(executor, "_save_state", fail_bookkeeping_commit)

    with pytest.raises(OSError, match="simulated bookkeeping disk failure"):
        executor.execute_weights(liquidation_weights())

    persisted_state = json.loads(executor.state_file.read_text(encoding="utf-8"))
    assert persisted_state["total_orders"] == 0
    assert persisted_state["last_execution_time"] is None
    assert persisted_state["emergency_liquidation_pending"]["AAPL"] == "mock-close-1"
    assert executor.state["total_orders"] == 0
    assert executor.state["last_execution_time"] is None

def test_pending_liquidation_order_id_must_match_broker_order_symbol(
    executor_env, monkeypatch
):
    executor = BrokerExecutor(initial_capital=500000.0)
    executor.state["emergency_liquidation_pending"] = {"AAPL": "close-msft"}
    mismatched_order = SimpleNamespace(id="close-msft", symbol="MSFT")
    cancel_calls = []
    close_calls = []
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [mismatched_order])
    monkeypatch.setattr(
        executor.trading_client,
        "cancel_order_by_id",
        lambda order_id: cancel_calls.append(order_id),
    )
    monkeypatch.setattr(
        executor.trading_client,
        "get_all_positions",
        lambda: [SimpleNamespace(symbol="AAPL", qty="10")],
    )
    monkeypatch.setattr(
        executor.trading_client,
        "close_position",
        lambda symbol: close_calls.append(symbol),
    )

    with pytest.raises(
        RuntimeError, match="pending liquidation order ID does not match broker order symbol"
    ):
        executor._liquidate_all_positions([mismatched_order])

    # A mismatched persisted ID must neither suppress the AAPL close nor cause
    # cancellation of an order that may belong to a different position.
    assert cancel_calls == []
    assert close_calls == []

def test_liquidation_aborts_when_unrelated_order_cancellation_fails(
    executor_env, monkeypatch
):
    executor = BrokerExecutor(initial_capital=500000.0)
    unrelated_order = SimpleNamespace(id="unrelated-order", symbol="MSFT")
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [unrelated_order])
    cancel_calls = []

    def fail_cancel(order_id):
        cancel_calls.append(order_id)
        raise OSError("simulated broker cancellation failure")

    monkeypatch.setattr(executor.trading_client, "cancel_order_by_id", fail_cancel)
    monkeypatch.setattr(
        executor.trading_client,
        "get_all_positions",
        lambda: pytest.fail("positions must not be acted on after cancellation failure"),
    )
    monkeypatch.setattr(
        executor.trading_client,
        "close_position",
        lambda symbol: pytest.fail("no close may be submitted after cancellation failure"),
    )

    with pytest.raises(OSError, match="simulated broker cancellation failure"):
        executor._liquidate_all_positions([unrelated_order])

    assert cancel_calls == ["unrelated-order"]
    assert executor.state.get("emergency_liquidation_pending", {}) == {}
    assert executor.state.get("emergency_liquidation_unresolved", {}) == {}


def test_ambiguous_close_exception_survives_restart_without_duplicate_close(
    executor_env, monkeypatch
):
    executor = BrokerExecutor(initial_capital=500000.0)
    position = SimpleNamespace(symbol="AAPL", qty="10")
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [])
    monkeypatch.setattr(executor.trading_client, "get_all_positions", lambda: [position])
    monkeypatch.setattr(executor.trading_client, "get_orders", lambda **kwargs: [])
    close_calls = []

    def ambiguous_close(symbol):
        close_calls.append(symbol)
        raise TimeoutError("broker response lost after close submission")

    monkeypatch.setattr(executor.trading_client, "close_position", ambiguous_close)

    with pytest.raises(TimeoutError, match="broker response lost after close submission"):
        executor._liquidate_all_positions([])

    durable = json.loads(executor.state_file.read_text(encoding="utf-8"))
    assert "AAPL" in durable["emergency_liquidation_unresolved"]
    assert durable["emergency_liquidation_unresolved"]["AAPL"]["reason"] == (
        "close_submission_outcome_unconfirmed"
    )
    assert close_calls == ["AAPL"]

    restarted = BrokerExecutor(initial_capital=500000.0)
    monkeypatch.setattr(restarted, "_get_open_orders", lambda: [])
    monkeypatch.setattr(restarted.trading_client, "get_all_positions", lambda: [position])
    monkeypatch.setattr(restarted.trading_client, "get_orders", lambda **kwargs: [])
    monkeypatch.setattr(
        restarted.trading_client,
        "close_position",
        lambda symbol: pytest.fail("restart must not duplicate an ambiguous close"),
    )

    assert restarted._liquidate_all_positions([]) == []
    assert "AAPL" in restarted.state["emergency_liquidation_unresolved"]
    assert close_calls == ["AAPL"]


def test_terminal_pending_order_id_must_match_broker_order_symbol(
    executor_env, monkeypatch
):
    executor = BrokerExecutor(initial_capital=500000.0)
    executor.state["emergency_liquidation_pending"] = {"AAPL": "terminal-msft"}
    executor._save_state()
    close_calls = []
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [])
    monkeypatch.setattr(
        executor.trading_client,
        "get_all_positions",
        lambda: [SimpleNamespace(symbol="AAPL", qty="10")],
    )
    monkeypatch.setattr(
        executor.trading_client,
        "get_order_by_id",
        lambda order_id: SimpleNamespace(
            id=order_id, symbol="MSFT", status="canceled"
        ),
    )
    monkeypatch.setattr(
        executor.trading_client,
        "close_position",
        lambda symbol: close_calls.append(symbol),
    )

    with pytest.raises(
        RuntimeError,
        match="Pending liquidation order ID does not match broker order symbol",
    ):
        executor._liquidate_all_positions([])

    assert close_calls == []
    assert executor.state["emergency_liquidation_pending"] == {
        "AAPL": "terminal-msft"
    }
    persisted = json.loads(executor.state_file.read_text(encoding="utf-8"))
    assert persisted["emergency_liquidation_pending"] == {"AAPL": "terminal-msft"}
