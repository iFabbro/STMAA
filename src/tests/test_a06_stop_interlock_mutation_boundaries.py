"""Direct mock regressions for the production Paper stop interlock boundaries."""

import threading
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from src.execution import broker_executor as broker_executor_module
from src.execution.broker_executor import BrokerExecutor
from src.strategy.weight_interface import PortfolioWeights


@pytest.fixture
def stopped_executor_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "test-secret")
    monkeypatch.setenv("STMAA_CONFIGURED_SYMBOLS", "AAPL")
    monkeypatch.setenv(
        "STMAA_BROKER_STATE_FILE", str(tmp_path / "broker_state.json")
    )

    class FakeTradingClient:
        def __init__(self, **kwargs):
            self._base_url = broker_executor_module.PAPER_API_BASE_URL
            self.submit_order = Mock()
            self.cancel_order_by_id = Mock()
            self.close_position = Mock()
            self.get_all_positions = Mock(return_value=[])

    monkeypatch.setattr(
        broker_executor_module, "TradingClient", FakeTradingClient
    )
    monkeypatch.setattr(
        broker_executor_module, "AlpacaFeed", lambda **kwargs: object()
    )

    stop_event = threading.Event()
    executor = BrokerExecutor(
        initial_capital=10_000.0,
        configured_symbols=["AAPL"],
        stop_event=stop_event,
    )
    executor.state["account_id"] = "paper-account"
    executor._current_account_identity_verified = True
    executor.get_account_status = Mock(return_value={
        "cash": 10_000.0,
        "portfolio_value": 10_000.0,
        "buying_power": 10_000.0,
        "equity": 10_000.0,
        "account_data_fresh": True,
        "account_data_source": "alpaca_account",
        "account_identity_verified": True,
        "account_id": "paper-account",
        "account_snapshot_at": "2026-11-25T15:00:00+00:00",
    })
    executor.get_positions = Mock(return_value={})
    executor._get_open_orders = Mock(return_value=[])
    executor._get_prices_from_bars = Mock(return_value={"AAPL": 100.0})
    executor.reconcile_corporate_actions = Mock(return_value={
        "new_activity_count": 0,
        "blocked_symbols": [],
    })
    executor._reconcile_unresolved_submissions = Mock()
    executor._refresh_confirmed_submission_statuses = Mock()
    executor._reconcile_terminal_order_positions = Mock()
    executor._verify_execution_snapshot = Mock()

    def stop_during_final_quote_check(ticker, expected_price):
        stop_event.set()
        return expected_price

    executor._verify_quote_before_submission = stop_during_final_quote_check
    return executor


def test_day_stop_latched_during_execution_preflight_prevents_sdk_submission(
    stopped_executor_env,
):
    executor = stopped_executor_env

    with pytest.raises(RuntimeError, match="shutdown stop signal is active"):
        executor.execute_weights(
            PortfolioWeights(
                weights=np.array([0.20]),
                tickers=["AAPL"],
            )
        )

    executor.trading_client.submit_order.assert_not_called()
    assert executor.stop_event.is_set()
    assert executor.state["order_submission_unresolved"] == {}


def test_stop_latched_before_liquidation_prevents_open_order_cancellation(
    stopped_executor_env,
):
    executor = stopped_executor_env
    executor.stop_event.set()

    with pytest.raises(RuntimeError, match="shutdown stop signal is active"):
        executor._liquidate_all_positions([
            SimpleNamespace(id="open-order-1", symbol="AAPL")
        ])

    executor.trading_client.cancel_order_by_id.assert_not_called()
    executor.trading_client.close_position.assert_not_called()


def test_stop_latched_before_emergency_close_prevents_close_and_clears_unsubmitted_intent(
    stopped_executor_env,
):
    executor = stopped_executor_env
    executor.stop_event.set()
    executor.trading_client.get_all_positions.return_value = [
        SimpleNamespace(symbol="AAPL", qty="2")
    ]

    with pytest.raises(RuntimeError, match="shutdown stop signal is active"):
        executor._liquidate_all_positions([])

    executor.trading_client.close_position.assert_not_called()
    assert executor.state["emergency_liquidation_unresolved"] == {}


def test_failed_close_intent_cleanup_restores_fail_closed_state(
    stopped_executor_env,
):
    executor = stopped_executor_env
    executor.stop_event.set()
    executor.trading_client.get_all_positions.return_value = [
        SimpleNamespace(symbol="AAPL", qty="2")
    ]
    original_save_state = executor._save_state
    save_calls = 0

    def fail_cleanup_save():
        nonlocal save_calls
        save_calls += 1
        if save_calls == 2:
            raise OSError("simulated cleanup persistence failure")
        original_save_state()

    executor._save_state = fail_cleanup_save

    with pytest.raises(RuntimeError, match="could not be durably cleared"):
        executor._liquidate_all_positions([])

    executor.trading_client.close_position.assert_not_called()
    assert "AAPL" in executor.state["emergency_liquidation_unresolved"]
