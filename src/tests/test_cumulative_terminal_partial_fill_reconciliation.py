"""Cumulative reconciliation of multiple terminal partial fills per symbol."""
import pytest

from src.execution.broker_executor import BrokerExecutor


@pytest.fixture
def executor_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "test-secret")
    monkeypatch.setenv(
        "STMAA_BROKER_STATE_FILE",
        str(tmp_path / "state" / "broker_state.json"),
    )
    executor = BrokerExecutor(paper_trading=True)
    return executor


def _order(*, baseline, filled, timestamp):
    return {
        "ticker": "AAPL",
        "side": "buy",
        "quantity": 10.0,
        "order_id": f"broker-order-{timestamp}",
        "client_order_id": f"client-order-{timestamp}",
        "status": "canceled",
        "filled_qty": filled,
        "pre_order_position_qty": baseline,
        "timestamp": timestamp,
        "position_reconciled": False,
    }


def _set_sequential_orders(executor, second_baseline=12.0):
    executor.state["order_submission_confirmed"] = {
        "client-order-1": _order(
            baseline=10.0, filled=2.0, timestamp="2026-10-28T10:00:00+00:00"
        ),
        "client-order-2": _order(
            baseline=second_baseline,
            filled=3.0,
            timestamp="2026-10-28T10:01:00+00:00",
        ),
    }
    executor._save_state()


def test_reconciles_multiple_sequential_partial_fills_against_final_position(
    executor_env, monkeypatch
):
    executor = executor_env
    _set_sequential_orders(executor)
    position_reads = []
    monkeypatch.setattr(
        executor,
        "get_positions",
        lambda: position_reads.append(True) or {"AAPL": {"qty": 15.0}},
    )

    executor._reconcile_terminal_order_positions()

    orders = executor.state["order_submission_confirmed"]
    assert orders["client-order-1"]["position_reconciled"] is True
    assert orders["client-order-2"]["position_reconciled"] is True
    assert len(position_reads) == 1


def test_stale_final_position_keeps_all_sequential_fills_unreconciled(
    executor_env, monkeypatch
):
    executor = executor_env
    _set_sequential_orders(executor)
    monkeypatch.setattr(
        executor, "get_positions", lambda: {"AAPL": {"qty": 14.0}}
    )

    with pytest.raises(
        RuntimeError, match="has not reconciled terminal order"
    ):
        executor._reconcile_terminal_order_positions()

    orders = executor.state["order_submission_confirmed"]
    assert orders["client-order-1"]["position_reconciled"] is False
    assert orders["client-order-2"]["position_reconciled"] is False


def test_discontinuous_quantity_chain_fails_closed(executor_env, monkeypatch):
    executor = executor_env
    _set_sequential_orders(executor, second_baseline=13.0)
    monkeypatch.setattr(
        executor, "get_positions", lambda: {"AAPL": {"qty": 16.0}}
    )

    with pytest.raises(
        RuntimeError, match="quantity chain is inconsistent"
    ):
        executor._reconcile_terminal_order_positions()

    orders = executor.state["order_submission_confirmed"]
    assert orders["client-order-1"]["position_reconciled"] is False
    assert orders["client-order-2"]["position_reconciled"] is False


def test_multiple_orders_without_timestamp_fail_closed(executor_env, monkeypatch):
    executor = executor_env
    _set_sequential_orders(executor)
    executor.state["order_submission_confirmed"]["client-order-2"].pop("timestamp")
    executor._save_state()
    monkeypatch.setattr(
        executor, "get_positions", lambda: {"AAPL": {"qty": 15.0}}
    )

    with pytest.raises(RuntimeError, match="lack timestamps"):
        executor._reconcile_terminal_order_positions()

    orders = executor.state["order_submission_confirmed"]
    assert orders["client-order-1"]["position_reconciled"] is False
    assert orders["client-order-2"]["position_reconciled"] is False
