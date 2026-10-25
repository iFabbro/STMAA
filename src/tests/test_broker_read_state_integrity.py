"""Regression tests for fail-closed broker reads and persisted execution state."""

import json
from types import SimpleNamespace

import numpy as np
import pytest

from src.execution.broker_executor import BrokerExecutor
from src.strategy.weight_interface import PortfolioWeights


@pytest.fixture
def alpaca_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "test-secret")


def test_get_open_orders_fails_closed_on_broker_read_error(alpaca_env):
    executor = BrokerExecutor()

    class FailingClient:
        def get_orders(self, **kwargs):
            raise ConnectionError("broker unavailable")

    executor.trading_client = FailingClient()

    with pytest.raises(
        RuntimeError, match="Unable to retrieve or validate broker open orders"
    ):
        executor._get_open_orders()


@pytest.mark.parametrize(
    "response",
    [
        None,
        {"orders": []},
        [SimpleNamespace(id="order-1", symbol="")],
        [SimpleNamespace(id="", symbol="AAPL")],
        [
            SimpleNamespace(id="same-id", symbol="AAPL"),
            SimpleNamespace(id="same-id", symbol="MSFT"),
        ],
    ],
)
def test_get_open_orders_fails_closed_on_malformed_snapshot(alpaca_env, response):
    executor = BrokerExecutor()

    class FakeClient:
        def get_orders(self, **kwargs):
            return response

    executor.trading_client = FakeClient()

    with pytest.raises(RuntimeError, match="Unable to retrieve or validate broker open orders"):
        executor._get_open_orders()


def test_execute_weights_does_not_submit_when_open_orders_are_unreadable(
    alpaca_env, monkeypatch
):
    executor = BrokerExecutor()
    submitted = []

    monkeypatch.setattr(executor, "get_account_status", lambda: {
        "cash": 10000.0,
        "portfolio_value": 10000.0,
        "buying_power": 10000.0,
        "equity": 10000.0,
        "account_data_fresh": True,
    })
    monkeypatch.setattr(executor, "get_positions", lambda: {})
    monkeypatch.setattr(
        executor.trading_client,
        "get_orders",
        lambda **kwargs: (_ for _ in ()).throw(ConnectionError("broker unavailable")),
    )
    monkeypatch.setattr(
        executor.trading_client,
        "submit_order",
        lambda request: submitted.append(request),
    )

    with pytest.raises(
        RuntimeError, match="Unable to retrieve or validate broker open orders"
    ):
        executor.execute_weights(
            PortfolioWeights(weights=np.array([0.1]), tickers=["AAPL"])
        )

    assert submitted == []


def test_get_positions_fails_closed_on_duplicate_symbol_rows(alpaca_env, monkeypatch):
    executor = BrokerExecutor()
    position = SimpleNamespace(
        symbol="AAPL",
        qty="1",
        market_value="100",
        avg_entry_price="100",
        unrealized_pl="0",
    )
    monkeypatch.setattr(
        executor.trading_client,
        "get_all_positions",
        lambda: [position, position],
    )

    with pytest.raises(RuntimeError, match="Unable to retrieve broker positions"):
        executor.get_positions()


def test_corrupt_submission_state_entries_block_executor_restart(alpaca_env):
    executor = BrokerExecutor()
    executor.state_file.write_text(
        json.dumps({
            "account_mode": "paper",
            "order_submission_unresolved": {"AAPL": "not-an-object"},
        }),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="Unable to load persisted broker state"):
        BrokerExecutor()
