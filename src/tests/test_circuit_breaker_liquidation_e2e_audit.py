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
