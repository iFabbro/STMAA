from types import SimpleNamespace

import pytest

from src.utils.paper_soak_production_provider import (
    PAPER_ENDPOINT,
    validate_production_paper_preflight,
)


class FakePreflightExecutor:
    def __init__(self):
        self.paper_trading = True
        self.base_url = PAPER_ENDPOINT
        self.calls = 0
        self.account = {
            "account_data_fresh": True,
            "account_id": "paper-account-test",
            "equity": 100_000.0,
            "cash": 50_000.0,
            "buying_power": 100_000.0,
            "status": "active",
            "trading_blocked": False,
        }

    def get_account_status(self):
        self.calls += 1
        return self.account


def test_production_preflight_requires_fresh_active_paper_account():
    executor = FakePreflightExecutor()
    account = validate_production_paper_preflight(SimpleNamespace(executor=executor))

    assert account is executor.account
    assert executor.calls == 1


def test_production_preflight_rejects_non_paper_endpoint_before_broker_read():
    executor = FakePreflightExecutor()
    executor.base_url = "https://api.alpaca.markets"

    with pytest.raises(RuntimeError, match="endpoint is not Alpaca Paper"):
        validate_production_paper_preflight(SimpleNamespace(executor=executor))

    assert executor.calls == 0


def test_production_preflight_rejects_blocked_account():
    executor = FakePreflightExecutor()
    executor.account["trading_blocked"] = True

    with pytest.raises(RuntimeError, match="not active and unblocked"):
        validate_production_paper_preflight(SimpleNamespace(executor=executor))


def test_production_preflight_rejects_stale_account_snapshot():
    executor = FakePreflightExecutor()
    executor.account["account_data_fresh"] = False

    with pytest.raises(RuntimeError, match="no fresh account snapshot"):
        validate_production_paper_preflight(SimpleNamespace(executor=executor))
