from contextlib import nullcontext

import pytest

from src.execution.broker_executor import BrokerExecutor


def make_executor(state=None):
    executor = BrokerExecutor.__new__(BrokerExecutor)
    executor.state = {
        "last_execution_time": None,
        "total_orders": 0,
        "order_submission_unresolved": {},
        "order_submission_confirmed": {},
        "emergency_liquidation_pending": {},
        "emergency_liquidation_unresolved": {},
        **(state or {}),
    }
    executor.saved = 0
    executor.execution_lock = lambda: nullcontext()
    executor._save_state = lambda: setattr(executor, "saved", executor.saved + 1)
    executor._reconcile_unresolved_submissions = lambda: None
    executor._refresh_confirmed_submission_statuses = lambda: None
    executor._reconcile_terminal_order_positions = lambda: None
    executor._get_open_orders = lambda: []
    return executor


def test_daily_decision_claim_is_durable_and_one_per_bar():
    executor = make_executor()
    bar = "2026-10-30T04:00:00+00:00"

    assert executor.claim_daily_decision(bar, ["AAPL", "MSFT"]) is True
    assert executor.state["daily_decision"]["status"] == "started"
    assert executor.saved == 1

    with pytest.raises(RuntimeError, match="previous cycle is unresolved"):
        executor.claim_daily_decision(bar, ["AAPL", "MSFT"])

    executor.complete_daily_decision(bar)
    assert executor.state["daily_decision"]["status"] == "completed"
    assert executor.claim_daily_decision("2026-10-30T05:00:00+01:00", ["AAPL", "MSFT"]) is False
    assert executor.claim_daily_decision("2026-11-02T05:00:00+00:00", ["AAPL", "MSFT"]) is True


def test_interrupted_cycle_requires_read_only_reconciliation_and_consumes_bar():
    bar = "2026-10-30T04:00:00+00:00"
    executor = make_executor()
    assert executor.claim_daily_decision(bar, ["AAPL"]) is True

    calls = []
    executor._reconcile_unresolved_submissions = lambda: calls.append("unresolved")
    executor._refresh_confirmed_submission_statuses = lambda: calls.append("confirmed")
    executor._reconcile_terminal_order_positions = lambda: calls.append("positions")
    executor._get_open_orders = lambda: calls.append("open_orders") or []

    assert executor.reconcile_daily_decision_if_required() is True
    assert calls == ["unresolved", "confirmed", "positions", "open_orders"]
    assert executor.state["daily_decision"]["status"] == "reconciled"
    assert executor.state["daily_decision"]["reconciliation"]["open_orders"] == 0
    assert executor.claim_daily_decision(bar, ["AAPL"]) is False
    assert executor.claim_daily_decision("2026-11-02T05:00:00+00:00", ["AAPL"]) is True


def test_repeated_bar_still_runs_read_only_order_maintenance():
    executor = make_executor()
    bar = "2026-10-30T04:00:00+00:00"
    executor.claim_daily_decision(bar, ["AAPL"])
    executor.complete_daily_decision(bar)

    maintenance_calls = []
    executor._reconcile_unresolved_submissions = lambda: maintenance_calls.append("unresolved")
    executor._refresh_confirmed_submission_statuses = lambda: maintenance_calls.append("confirmed")
    executor._reconcile_terminal_order_positions = lambda: maintenance_calls.append("positions")
    executor._get_open_orders = lambda: maintenance_calls.append("open_orders") or []

    assert executor.claim_daily_decision(bar, ["AAPL"]) is False
    assert maintenance_calls == ["unresolved", "confirmed", "positions", "open_orders"]


def test_new_bar_is_blocked_by_active_orders_but_same_bar_remains_skipped():
    executor = make_executor()
    bar = "2026-10-30T04:00:00+00:00"
    executor.claim_daily_decision(bar, ["AAPL"])
    executor.complete_daily_decision(bar)
    executor._get_open_orders = lambda: [object()]

    assert executor.claim_daily_decision(bar, ["AAPL"]) is False
    with pytest.raises(RuntimeError, match="unresolved, active, or open orders"):
        executor.claim_daily_decision("2026-11-02T05:00:00+00:00", ["AAPL"])


def test_interrupted_cycle_stays_blocked_when_open_orders_exist():
    executor = make_executor()
    bar = "2026-10-30T04:00:00+00:00"
    executor.claim_daily_decision(bar, ["AAPL"])
    executor._get_open_orders = lambda: [object()]

    with pytest.raises(RuntimeError, match="reconciliation is not conclusive"):
        executor.reconcile_daily_decision_if_required()

    assert executor.state["daily_decision"]["status"] == "started"
    with pytest.raises(RuntimeError, match="previous cycle is unresolved"):
        executor.claim_daily_decision("2026-11-02T05:00:00+00:00", ["AAPL"])


def test_interrupted_cycle_stays_blocked_when_unresolved_submission_remains():
    executor = make_executor({
        "order_submission_unresolved": {"AAPL": {"client_order_id": "ambiguous"}},
    })
    executor.state["daily_decision"] = {
        "bar_timestamp": "2026-10-30T04:00:00+00:00",
        "status": "started",
    }

    with pytest.raises(RuntimeError, match="reconciliation is not conclusive"):
        executor.reconcile_daily_decision_if_required()

    assert executor.state["daily_decision"]["status"] == "started"


def test_legacy_execution_history_requires_operator_baseline_not_automatic_clear():
    executor = make_executor({"last_execution_time": "2026-10-29T20:00:00+00:00"})
    bar = "2026-10-30T04:00:00+00:00"

    with pytest.raises(RuntimeError, match="legacy broker execution history"):
        executor.claim_daily_decision(bar, ["AAPL"])

    with pytest.raises(RuntimeError, match="cannot be mapped"):
        executor.reconcile_daily_decision_if_required()

    with pytest.raises(RuntimeError, match="previous cycle is unresolved"):
        executor.claim_daily_decision("2026-11-02T05:00:00+00:00", ["AAPL"])


def test_daily_decision_rejects_naive_or_out_of_order_timestamps():
    executor = make_executor()
    with pytest.raises(ValueError, match="timezone-aware"):
        executor.claim_daily_decision("2026-10-30T04:00:00", ["AAPL"])

    executor.claim_daily_decision("2026-11-02T05:00:00+00:00", ["AAPL"])
    executor.complete_daily_decision("2026-11-02T05:00:00+00:00")
    with pytest.raises(RuntimeError, match="older than the last processed"):
        executor.claim_daily_decision("2026-10-30T04:00:00+00:00", ["AAPL"])


def test_daily_decision_marker_survives_executor_restart(tmp_path):
    import logging
    import threading

    def persistent_executor():
        executor = BrokerExecutor.__new__(BrokerExecutor)
        executor.state_file = tmp_path / "broker_state.json"
        executor._persisted_state_revision = -1
        executor._state_persistence_failed = False
        executor._execution_lock_local = threading.local()
        executor.execution_lock_timeout_seconds = 1.0
        executor.logger = logging.getLogger("daily-decision-test")
        executor.state = {
            "state_schema_version": 1,
            "state_revision": 0,
            "account_mode": "paper",
            "last_execution_time": None,
            "total_orders": 0,
            "emergency_liquidation_pending": {},
            "emergency_liquidation_unresolved": {},
            "order_submission_unresolved": {},
            "order_submission_confirmed": {},
        }
        if executor.state_file.exists():
            executor._load_state()
        else:
            executor._save_state()
        executor._reconcile_unresolved_submissions = lambda: None
        executor._refresh_confirmed_submission_statuses = lambda: None
        executor._reconcile_terminal_order_positions = lambda: None
        executor._get_open_orders = lambda: []
        return executor

    bar = "2026-10-30T04:00:00+00:00"
    first_process = persistent_executor()
    assert first_process.claim_daily_decision(bar, ["AAPL"]) is True
    first_process.complete_daily_decision(bar)

    restarted_process = persistent_executor()
    assert restarted_process.state["daily_decision"]["status"] == "completed"
    assert restarted_process.claim_daily_decision(bar, ["AAPL"]) is False
    assert restarted_process.claim_daily_decision("2026-11-02T05:00:00+00:00", ["AAPL"]) is True
