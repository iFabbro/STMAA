"""Restart safety checks for persisted broker order intents (read-only broker mocks)."""
from types import SimpleNamespace

import numpy as np
import pytest

from src.execution.broker_executor import BrokerExecutor
from src.strategy.weight_interface import PortfolioWeights


def _new_executor(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "test-secret")
    monkeypatch.setenv(
        "STMAA_BROKER_STATE_FILE",
        str(tmp_path / "state" / "broker_state.json"),
    )
    return BrokerExecutor(paper_trading=True)


def _target():
    return PortfolioWeights(weights=np.array([0.10]), tickers=["AAPL"])


def test_restart_keeps_ambiguous_submission_blocked_when_read_only_lookup_fails(
    monkeypatch, tmp_path
):
    first = _new_executor(monkeypatch, tmp_path)
    first.state["order_submission_unresolved"] = {
        "AAPL": {
            "client_order_id": "client-order-ambiguous",
            "side": "buy",
            "quantity": 10.0,
            "price": 100.0,
            "pre_order_position_qty": 0.0,
            "created_at": "2026-10-29T12:00:00+00:00",
            "reason": "submission_outcome_unconfirmed",
        }
    }
    first._save_state()

    restarted = _new_executor(monkeypatch, tmp_path)
    lookups = []
    submitted = []

    def unavailable_read(client_order_id):
        lookups.append(client_order_id)
        raise RuntimeError("simulated read-only broker lookup outage")

    monkeypatch.setattr(
        restarted.trading_client, "get_order_by_client_id", unavailable_read
    )
    monkeypatch.setattr(
        restarted.trading_client,
        "submit_order",
        lambda request: submitted.append(request),
    )
    monkeypatch.setattr(
        restarted,
        "get_account_status",
        lambda: (_ for _ in ()).throw(
            AssertionError("Must block before account/execution workflow")
        ),
    )

    with pytest.raises(
        RuntimeError,
        match="requires reconciliation or terminal status",
    ):
        restarted.execute_weights(_target())

    assert lookups == ["client-order-ambiguous"]
    assert "AAPL" in restarted.state["order_submission_unresolved"]
    assert submitted == []


def test_restart_keeps_partially_filled_order_reserved_until_terminal_reconciliation(
    monkeypatch, tmp_path
):
    first = _new_executor(monkeypatch, tmp_path)
    first.state["order_submission_confirmed"] = {
        "client-order-partial": {
            "ticker": "AAPL",
            "side": "buy",
            "quantity": 10.0,
            "order_id": "broker-order-partial",
            "client_order_id": "client-order-partial",
            "status": "partially_filled",
            "timestamp": "2026-10-29T12:00:00+00:00",
            "price": 100.0,
            "pre_order_position_qty": 0.0,
        }
    }
    first._save_state()

    restarted = _new_executor(monkeypatch, tmp_path)
    submitted = []
    read_order = SimpleNamespace(
        id="broker-order-partial",
        client_order_id="client-order-partial",
        symbol="AAPL",
        side="buy",
        qty="10",
        filled_qty="4",
        status="partially_filled",
    )
    monkeypatch.setattr(
        restarted.trading_client,
        "get_order_by_id",
        lambda order_id: read_order,
    )
    monkeypatch.setattr(
        restarted.trading_client,
        "submit_order",
        lambda request: submitted.append(request),
    )
    monkeypatch.setattr(
        restarted,
        "get_account_status",
        lambda: (_ for _ in ()).throw(
            AssertionError("Must block before account/execution workflow")
        ),
    )

    with pytest.raises(
        RuntimeError,
        match="requires reconciliation or terminal status",
    ):
        restarted.execute_weights(_target())

    persisted = restarted.state["order_submission_confirmed"]["client-order-partial"]
    assert persisted["status"] == "partially_filled"
    assert persisted["filled_qty"] == 4.0
    assert submitted == []


def test_restart_fails_closed_when_persisted_broker_state_is_corrupt(
    monkeypatch, tmp_path
):
    state_path = tmp_path / "state" / "broker_state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text("{not-valid-json", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "test-secret")
    monkeypatch.setenv("STMAA_BROKER_STATE_FILE", str(state_path))

    with pytest.raises(
        RuntimeError,
        match="Unable to load persisted broker state",
    ):
        BrokerExecutor(paper_trading=True)
