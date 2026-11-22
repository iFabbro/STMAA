"""Bounded retry and fail-closed behavior for Alpaca account snapshots."""
import requests
from types import SimpleNamespace

import pytest

from src.data.alpaca_feed import (
    MAX_TRANSIENT_API_ATTEMPTS,
    TRANSIENT_API_BACKOFF_SECONDS,
)
from src.execution.broker_executor import BrokerExecutor


@pytest.fixture
def broker_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "test-secret")
    monkeypatch.setenv(
        "STMAA_BROKER_STATE_FILE",
        str(tmp_path / "state" / "broker_state.json"),
    )


def _valid_account():
    return SimpleNamespace(
        cash="10000",
        portfolio_value="10000",
        buying_power="10000",
        equity="10000",
        daytrade_count=0,
        status="ACTIVE",
        trading_blocked=False,
        id="paper-account-1",
    )


@pytest.mark.parametrize(
    "transport_error",
    [
        requests.exceptions.ConnectionError("remote disconnected"),
        requests.exceptions.Timeout("request timed out"),
    ],
    ids=["connection-error", "timeout"],
)
def test_account_snapshot_recovers_after_transient_transport_failure(
    broker_env, monkeypatch, transport_error
):
    executor = BrokerExecutor()
    responses = iter([transport_error, _valid_account()])
    calls = []
    delays = []

    def get_account():
        calls.append(1)
        result = next(responses)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(executor.trading_client, "get_account", get_account)
    monkeypatch.setattr(
        "src.execution.broker_executor.time.sleep",
        delays.append,
    )
    monkeypatch.setattr(
        "src.execution.broker_executor.random.uniform",
        lambda _low, high: high,
    )

    snapshot = executor.get_account_status()

    assert len(calls) == 2
    assert delays == [TRANSIENT_API_BACKOFF_SECONDS]
    assert snapshot["account_data_fresh"] is True
    assert snapshot["equity"] == 10000.0
    assert snapshot["account_id"] == "paper-account-1"


def test_account_snapshot_exhaustion_returns_no_cached_or_stale_equity(
    broker_env, monkeypatch
):
    executor = BrokerExecutor()
    calls = []
    delays = []

    def get_account():
        calls.append(1)
        raise requests.exceptions.ConnectionError("remote disconnected")

    monkeypatch.setattr(executor.trading_client, "get_account", get_account)
    monkeypatch.setattr(
        "src.execution.broker_executor.time.sleep",
        delays.append,
    )
    monkeypatch.setattr(
        "src.execution.broker_executor.random.uniform",
        lambda _low, high: high,
    )

    snapshot = executor.get_account_status()

    assert len(calls) == MAX_TRANSIENT_API_ATTEMPTS
    assert len(delays) == MAX_TRANSIENT_API_ATTEMPTS - 1
    assert delays == [
        min(TRANSIENT_API_BACKOFF_SECONDS * (2**index), 8.0)
        for index in range(MAX_TRANSIENT_API_ATTEMPTS - 1)
    ]
    assert snapshot["account_data_fresh"] is False
    assert snapshot["equity"] is None
    assert snapshot["portfolio_value"] is None
    assert executor.state["account_data_fresh"] is False


def test_account_snapshot_does_not_retry_non_transport_validation_failure(
    broker_env, monkeypatch
):
    executor = BrokerExecutor()
    calls = []

    def get_account():
        calls.append(1)
        raise ValueError("invalid broker account response")

    monkeypatch.setattr(executor.trading_client, "get_account", get_account)
    monkeypatch.setattr(
        "src.execution.broker_executor.time.sleep",
        lambda _delay: pytest.fail("non-transport errors must not be retried"),
    )

    snapshot = executor.get_account_status()

    assert len(calls) == 1
    assert snapshot["account_data_fresh"] is False
    assert snapshot["equity"] is None
