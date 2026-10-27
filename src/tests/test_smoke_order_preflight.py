"""Regression tests for every direct, bounded Paper smoke-order entry point."""
from datetime import datetime, timedelta, timezone

import pytest

from src.execution.smoke_order_preflight import validate_smoke_order_preflight


class FakeExecutor:
    def __init__(self, *, account=None, positions=None, open_orders=None, state=None):
        self.account = account or {
            "account_data_fresh": True,
            "account_id": "paper-account",
            "equity": 10000.0,
        }
        self.positions = positions if positions is not None else {}
        self.open_orders = open_orders if open_orders is not None else []
        self.state = state or {}

    def get_account_status(self):
        return self.account

    def get_positions(self):
        return self.positions

    def _get_open_orders(self):
        return self.open_orders


def fresh_quote(price=100.0):
    return {"price": price, "timestamp": datetime.now(timezone.utc).isoformat()}


def test_smoke_preflight_allows_bounded_order_when_all_risk_inputs_are_verified(monkeypatch):
    monkeypatch.setenv("MAX_PORTFOLIO_EXPOSURE", "0.50")
    executor = FakeExecutor(positions={"AAPL": {"market_value": 1000.0}})
    result = validate_smoke_order_preflight(executor, "SPY", 10.0, fresh_quote())
    assert result["account_id"] == "paper-account"


@pytest.mark.parametrize(
    "account",
    [
        {"account_data_fresh": False, "account_id": "paper-account", "equity": 10000.0},
        {"account_data_fresh": True, "equity": 10000.0},
        {"account_data_fresh": True, "account_id": "paper-account", "equity": float("nan")},
    ],
)
def test_smoke_preflight_blocks_unverified_account(account):
    with pytest.raises(RuntimeError, match="Smoke order blocked"):
        validate_smoke_order_preflight(FakeExecutor(account=account), "SPY", 10.0, fresh_quote())


def test_smoke_preflight_blocks_any_existing_open_order():
    executor = FakeExecutor(open_orders=[object()])
    with pytest.raises(RuntimeError, match="existing open orders"):
        validate_smoke_order_preflight(executor, "SPY", 10.0, fresh_quote())


def test_smoke_preflight_blocks_ambiguous_persisted_submission():
    executor = FakeExecutor(state={"order_submission_unresolved": {"AAPL": {"client_order_id": "unknown"}}})
    with pytest.raises(RuntimeError, match="need reconciliation"):
        validate_smoke_order_preflight(executor, "SPY", 10.0, fresh_quote())


@pytest.mark.parametrize(
    "quote",
    [
        {},
        {"price": 100.0, "timestamp": None},
        {"price": 100.0, "timestamp": datetime.now().isoformat()},
        {"price": 100.0, "timestamp": (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()},
        {"price": float("nan"), "timestamp": datetime.now(timezone.utc).isoformat()},
    ],
)
def test_smoke_preflight_blocks_missing_malformed_or_stale_quote(quote):
    with pytest.raises(RuntimeError, match="market quote"):
        validate_smoke_order_preflight(FakeExecutor(), "SPY", 10.0, quote)


def test_smoke_preflight_blocks_unknown_position_exposure():
    executor = FakeExecutor(positions={"AAPL": {"qty": 2.0}})
    with pytest.raises(RuntimeError, match="exposure is unknown"):
        validate_smoke_order_preflight(executor, "SPY", 10.0, fresh_quote())


def test_smoke_preflight_blocks_order_that_would_breach_exposure_limit(monkeypatch):
    monkeypatch.setenv("MAX_PORTFOLIO_EXPOSURE", "0.50")
    executor = FakeExecutor(positions={"AAPL": {"market_value": 4995.0}})
    with pytest.raises(RuntimeError, match="exceeds exposure limit"):
        validate_smoke_order_preflight(executor, "SPY", 10.0, fresh_quote())


def test_smoke_preflight_blocks_invalid_exposure_limit(monkeypatch):
    monkeypatch.setenv("MAX_PORTFOLIO_EXPOSURE", "nan")
    with pytest.raises(RuntimeError, match="exposure limit is invalid"):
        validate_smoke_order_preflight(FakeExecutor(), "SPY", 10.0, fresh_quote())


@pytest.mark.parametrize(
    "quote",
    [
        {
            "price": 100.0,
            "timestamp": (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat(),
        },
        {
            "price": 100.0,
            "timestamp": "not-a-timestamp",
        },
    ],
)
def test_smoke_preflight_blocks_future_or_malformed_timestamp(quote):
    with pytest.raises(RuntimeError, match="valid timestamped market quote|market quote"):
        validate_smoke_order_preflight(FakeExecutor(), "SPY", 10.0, quote)


@pytest.mark.parametrize(
    "positions",
    [
        {"AAPL": {"market_value": float("nan")}},
        {"AAPL": {"market_value": float("inf")}},
        {"AAPL": {"market_value": "not-a-number"}},
    ],
)
def test_smoke_preflight_blocks_non_finite_or_unparseable_position_exposure(positions):
    executor = FakeExecutor(positions=positions)
    with pytest.raises(RuntimeError, match="exposure is invalid"):
        validate_smoke_order_preflight(executor, "SPY", 10.0, fresh_quote())
