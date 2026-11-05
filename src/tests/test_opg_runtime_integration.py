"""RW-08.4 PR C tests for PaperTradingBot OPG runtime integration."""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pandas as pd

import trade
from src.strategy.weight_interface import PortfolioWeights


ET = ZoneInfo("America/New_York")


def _market_data():
    return pd.DataFrame(
        {"Close_AAPL": [99.0, 100.0]},
        index=pd.DatetimeIndex(["2026-11-02", "2026-11-03"]),
    )


def _safe_monitoring_executor(**overrides):
    values = {
        "get_account_status": Mock(return_value={"account_data_fresh": True}),
        "reconcile_daily_decision_if_required": Mock(),
        "reconcile_broker_state_read_only": Mock(return_value={"safe_for_new_decision": True}),
        "claim_daily_decision": Mock(return_value=True),
        "complete_daily_decision": Mock(),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_opg_daily_cycle_waits_until_submission_window():
    bot = trade.PaperTradingBot.__new__(trade.PaperTradingBot)
    bot.tickers = ["AAPL"]
    bot.executor = _safe_monitoring_executor()

    result = bot.generate_and_execute_opg_daily_cycle(
        _market_data(),
        now=datetime(2026, 11, 3, 18, 59, tzinfo=ET),
    )

    assert result["execution_mode"] == "opg"
    assert result["opg_schedule_state"] == "waiting_for_window"
    assert result["orders"] == []
    bot.executor.reconcile_broker_state_read_only.assert_called_once_with()


def test_opg_daily_cycle_marks_missed_open_without_catch_up():
    bot = trade.PaperTradingBot.__new__(trade.PaperTradingBot)
    bot.tickers = ["AAPL"]
    bot.executor = _safe_monitoring_executor()

    result = bot.generate_and_execute_opg_daily_cycle(
        _market_data(),
        now=datetime(2026, 11, 4, 9, 25, tzinfo=ET),
    )

    assert result["execution_mode"] == "opg"
    assert result["opg_schedule_state"] == "missed_open"
    assert result["orders"] == []


def test_opg_daily_cycle_blocks_when_broker_reconciliation_is_unsafe():
    executor = _safe_monitoring_executor(
        reconcile_broker_state_read_only=Mock(return_value={"safe_for_new_decision": False})
    )
    bot = trade.PaperTradingBot.__new__(trade.PaperTradingBot)
    bot.tickers = ["AAPL"]
    bot.executor = executor
    bot.generate_weights = Mock(side_effect=AssertionError("PPO must not run while broker state is blocked"))

    result = bot.generate_and_execute_opg_daily_cycle(
        _market_data(),
        now=datetime(2026, 11, 3, 19, 0, tzinfo=ET),
    )

    assert result["broker_state_blocked"] is True
    assert result["orders"] == []
    executor.claim_daily_decision.assert_not_called()
    bot.generate_weights.assert_not_called()


def test_opg_daily_cycle_routes_claimed_signal_to_opg_context():
    bot = trade.PaperTradingBot.__new__(trade.PaperTradingBot)
    bot.tickers = ["AAPL"]
    bot.executor = SimpleNamespace(
        get_account_status=Mock(return_value={"account_data_fresh": True}),
        reconcile_daily_decision_if_required=Mock(),
        reconcile_broker_state_read_only=Mock(return_value={"safe_for_new_decision": True}),
        claim_daily_decision=Mock(return_value=True),
        complete_daily_decision=Mock(),
    )
    weights = PortfolioWeights(
        weights=[0.2],
        tickers=["AAPL"],
        timestamp="2026-11-03T00:00:00Z",
    )
    bot.generate_weights = Mock(return_value=weights)
    bot.execute_and_persist_cycle = Mock(return_value={"orders": []})

    result = bot.generate_and_execute_opg_daily_cycle(
        _market_data(),
        now=datetime(2026, 11, 3, 19, 0, tzinfo=ET),
    )

    bot.executor.reconcile_daily_decision_if_required.assert_called_once_with()
    bot.executor.reconcile_broker_state_read_only.assert_called_once_with()
    bot.executor.claim_daily_decision.assert_called_once()
    bot.generate_weights.assert_called_once()
    bot.execute_and_persist_cycle.assert_called_once()
    args, kwargs = bot.execute_and_persist_cycle.call_args
    assert args == (weights,)
    assert kwargs["opg_context"]["target_session"] == "2026-11-04"
    assert kwargs["opg_context"]["signal_bar_at"] == "2026-11-03T00:00:00+00:00"
    assert kwargs["opg_context"]["market_data"] is not None
    bot.executor.complete_daily_decision.assert_called_once_with(
        "2026-11-03T00:00:00+00:00"
    )
    assert result["opg_schedule_state"] == "submission_window"


def test_opg_trade_builds_integer_plan_and_never_calls_day_executor(monkeypatch):
    submitted = {}

    class Executor:
        paper_trading = True

        def get_account_status(self):
            return {
                "account_data_fresh": True,
                "account_data_source": "alpaca_account",
                "account_snapshot_at": "2026-11-03T20:00:00+00:00",
                "equity": 1000.0,
                "portfolio_value": 1000.0,
            }

        def get_positions(self):
            return {"AAPL": {"qty": "0", "market_value": "0"}}

        def execute_opg_plan(self, plan, *, signal_bar_at, target_session):
            submitted["plan"] = plan
            submitted["signal_bar_at"] = signal_bar_at
            submitted["target_session"] = target_session
            return {
                "batch_id": "batch-test",
                "status": "submission_complete",
                "orders": {
                    "AAPL": {
                        "side": "buy",
                        "quantity": 2,
                        "status": "accepted",
                        "order_id": "broker-order-1",
                        "client_order_id": "stmaa-opg-test",
                        "filled_qty": "0",
                        "average_fill_price": None,
                    }
                },
            }

        def execute_weights(self, weights):
            raise AssertionError("OPG mode must never fall back to DAY execution")

    bot = trade.PaperTradingBot.__new__(trade.PaperTradingBot)
    bot.tickers = ["AAPL"]
    bot.pipeline = SimpleNamespace(risk_module=None)
    bot.executor = Executor()
    monkeypatch.setattr(trade, "write_snapshot", lambda *args, **kwargs: None)
    monkeypatch.setattr(trade, "log_event", lambda *args, **kwargs: None)

    weights = PortfolioWeights(
        weights=[0.2],
        tickers=["AAPL"],
        timestamp="2026-11-03T00:00:00Z",
    )
    result = bot.execute_trade(
        weights,
        opg_context={
            "market_data": _market_data(),
            "signal_bar_at": "2026-11-03T00:00:00+00:00",
            "target_session": "2026-11-04",
        },
    )

    plan = submitted["plan"]
    assert plan.assets["AAPL"].target_quantity == 2
    assert plan.assets["AAPL"].delta_quantity == 2
    assert submitted["signal_bar_at"] == "2026-11-03T00:00:00+00:00"
    assert submitted["target_session"] == "2026-11-04"
    assert result["execution_mode"] == "opg"
    assert result["opg_batch"]["batch_id"] == "batch-test"
    assert result["orders"][0]["ticker"] == "AAPL"
    assert result["orders"][0]["time_in_force"] == "opg"
