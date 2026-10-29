"""Successful multi-symbol terminal reconciliation must survive restart idempotently."""
import json
from types import SimpleNamespace

import pytest

from src.execution.broker_executor import BrokerExecutor


def _terminal_order(symbol, client_order_id, broker_order_id):
    return {
        "ticker": symbol,
        "side": "buy",
        "quantity": 2.0,
        "order_id": broker_order_id,
        "client_order_id": client_order_id,
        "status": "filled",
        "filled_qty": 2.0,
        "pre_order_position_qty": 0.0,
        "position_reconciled": False,
    }


def _mock_order_reads(monkeypatch, executor):
    orders = {
        "broker-order-aapl": ("client-order-aapl", "AAPL"),
        "broker-order-msft": ("client-order-msft", "MSFT"),
    }

    def get_order_by_id(order_id):
        client_order_id, symbol = orders[order_id]
        return SimpleNamespace(
            id=order_id,
            client_order_id=client_order_id,
            symbol=symbol,
            side="buy",
            qty="2",
            filled_qty="2",
            status="filled",
        )

    monkeypatch.setattr(executor.trading_client, "get_order_by_id", get_order_by_id)


@pytest.fixture
def persisted_multi_symbol_state(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "test-secret")
    state_path = tmp_path / "state" / "broker_state.json"
    monkeypatch.setenv("STMAA_BROKER_STATE_FILE", str(state_path))

    initial = BrokerExecutor(paper_trading=True)
    initial.state["order_submission_confirmed"] = {
        "client-order-aapl": _terminal_order(
            "AAPL", "client-order-aapl", "broker-order-aapl"
        ),
        "client-order-msft": _terminal_order(
            "MSFT", "client-order-msft", "broker-order-msft"
        ),
    }
    initial._save_state()
    return state_path


def test_successful_multi_symbol_reconciliation_is_durable_and_idempotent(
    persisted_multi_symbol_state, monkeypatch
):
    state_path = persisted_multi_symbol_state
    executor = BrokerExecutor(paper_trading=True)
    _mock_order_reads(monkeypatch, executor)
    position_reads = []
    monkeypatch.setattr(
        executor,
        "get_positions",
        lambda: position_reads.append(True)
        or {
            "AAPL": {"qty": 2.0, "market_value": 200.0},
            "MSFT": {"qty": 2.0, "market_value": 400.0},
        },
    )
    submitted = []
    monkeypatch.setattr(
        executor.trading_client,
        "submit_order",
        lambda request: submitted.append(request),
    )

    executor._refresh_confirmed_submission_statuses()
    executor._reconcile_terminal_order_positions()

    in_memory = executor.state["order_submission_confirmed"]
    assert all(order["position_reconciled"] is True for order in in_memory.values())
    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    assert all(
        order["position_reconciled"] is True
        for order in persisted["order_submission_confirmed"].values()
    )
    assert len(position_reads) == 1
    assert submitted == []

    # A fresh process must trust only the durable reconciliation checkpoint:
    # terminal reconciled orders should not be re-read or reconciled again.
    after_restart = BrokerExecutor(paper_trading=True)

    def unexpected_order_read(_order_id):
        raise AssertionError("durably reconciled terminal order must not be re-read")

    monkeypatch.setattr(
        after_restart.trading_client, "get_order_by_id", unexpected_order_read
    )
    monkeypatch.setattr(
        after_restart,
        "get_positions",
        lambda: (_ for _ in ()).throw(
            AssertionError("no position snapshot should be needed after durable reconciliation")
        ),
    )
    restarted_submissions = []
    monkeypatch.setattr(
        after_restart.trading_client,
        "submit_order",
        lambda request: restarted_submissions.append(request),
    )

    after_restart._refresh_confirmed_submission_statuses()
    after_restart._reconcile_terminal_order_positions()

    assert all(
        order["position_reconciled"] is True
        for order in after_restart.state["order_submission_confirmed"].values()
    )
    assert restarted_submissions == []
