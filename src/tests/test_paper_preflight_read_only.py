from __future__ import annotations

import json

import pytest

from src.utils.paper_preflight import (
    _order_buy_reserve,
    _reconcile_state,
    run_preflight,
)


class FakeAccount:
    id = "paper-account-1"
    status = "ACTIVE"
    currency = "USD"
    equity = "100000"
    cash = "100000"
    buying_power = "400000"
    trading_blocked = False
    account_blocked = False
    trade_suspended_by_user = False


class FakeClient:
    _base_url = "https://paper-api.alpaca.markets"

    def __init__(self, positions=None, orders=None):
        self.positions = [] if positions is None else positions
        self.orders = [] if orders is None else orders
        self.calls = []

    def get_account(self):
        self.calls.append("GET account")
        return FakeAccount()

    def get_all_positions(self):
        self.calls.append("GET positions")
        return self.positions

    def get_orders(self, *, filter):
        self.calls.append("GET open orders")
        return self.orders


def _state():
    return {
        "state_schema_version": 2,
        "account_mode": "paper",
        "account_id": "paper-account-1",
        "order_submission_unresolved": {},
        "order_submission_confirmed": {},
        "opg_batches": {},
        "emergency_liquidation_pending": {},
        "emergency_liquidation_unresolved": {},
    }


def test_zero_write_preflight_passes_with_empty_consistent_paper_account(tmp_path):
    state_path = tmp_path / "logs" / "execution" / "broker_state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps(_state()), encoding="utf-8")
    before = (state_path.read_bytes(), state_path.stat().st_mtime_ns)
    client = FakeClient()

    report = run_preflight(tmp_path, client, env={
        "STMAA_TRADING_MODE": "paper",
        "ALPACA_BASE_URL": "https://paper-api.alpaca.markets",
        "ALPACA_API_KEY": "key",
        "ALPACA_API_SECRET": "secret",
    })

    assert report["overall"] == "PASS"
    assert report["checks_passed"] == 5
    assert report["read_only"] is True
    assert report["broker_mutations"] == report["local_writes"] == 0
    assert client.calls == ["GET account", "GET positions", "GET open orders"]
    assert (state_path.read_bytes(), state_path.stat().st_mtime_ns) == before


def test_preflight_blocks_when_position_plus_buy_reserve_exceeds_cap(tmp_path):
    state_path = tmp_path / "logs" / "execution" / "broker_state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps(_state()), encoding="utf-8")
    position = type("Position", (), {"symbol": "AAPL", "qty": "2", "market_value": "900"})()
    order = type("Order", (), {
        "id": "order-1", "client_order_id": "client-1", "status": "new",
        "side": "buy", "qty": "2", "limit_price": "100",
    })()
    report = run_preflight(tmp_path, FakeClient([position], [order]), env={
        "STMAA_TRADING_MODE": "paper", "ALPACA_BASE_URL": "https://paper-api.alpaca.markets",
        "ALPACA_API_KEY": "key", "ALPACA_API_SECRET": "secret",
    })
    assert report["overall"] == "BLOCKED"
    assert report["checks"][3]["status"] == "BLOCKED"


def test_market_buy_without_known_notional_fails_closed():
    order = type("Order", (), {"side": "buy", "qty": "1", "limit_price": None})()
    with pytest.raises(ValueError, match="no verifiable notional"):
        _order_buy_reserve(order)


def test_reconciliation_blocks_unresolved_and_unfinished_opg():
    state = _state()
    state["order_submission_unresolved"] = {"AAPL": {"client_order_id": "pending"}}
    with pytest.raises(ValueError, match="unresolved"):
        _reconcile_state(state, "paper-account-1", [])
    state = _state()
    state["opg_batches"] = {"batch-1": {"status": "unresolved", "orders": {}}}
    with pytest.raises(ValueError, match="not completed"):
        _reconcile_state(state, "paper-account-1", [])


def test_reconciliation_blocks_account_mismatch_and_untracked_broker_order():
    state = _state()
    with pytest.raises(ValueError, match="account ID"):
        _reconcile_state(state, "different-account", [])
    state = _state()
    order = type("Order", (), {
        "id": "broker-order", "client_order_id": "broker-client",
    })()
    with pytest.raises(ValueError, match="no matching persisted"):
        _reconcile_state(state, "paper-account-1", [order])


def test_confirmed_submission_requires_terminal_position_reconciliation():
    state = _state()
    state["order_submission_confirmed"] = {
        "client-1": {
            "order_id": "order-1", "client_order_id": "client-1",
            "status": "filled", "position_reconciled": False,
        }
    }
    with pytest.raises(ValueError, match="position-reconciled"):
        _reconcile_state(state, "paper-account-1", [])


def test_short_position_is_counted_as_absolute_gross_exposure(tmp_path):
    state_path = tmp_path / "logs" / "execution" / "broker_state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps(_state()), encoding="utf-8")
    short_position = type("Position", (), {
        "symbol": "TSLA", "qty": "-2", "market_value": "-600",
    })()
    report = run_preflight(tmp_path, FakeClient([short_position]), env={
        "STMAA_TRADING_MODE": "paper", "ALPACA_BASE_URL": "https://paper-api.alpaca.markets",
        "ALPACA_API_KEY": "key", "ALPACA_API_SECRET": "secret",
    })
    assert report["exposure"]["position_gross_usd"] == 600
    assert report["overall"] == "PASS"
