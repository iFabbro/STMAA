"""Fail-closed integration matrix for uncertain broker exposure and market data."""
from types import SimpleNamespace

import numpy as np
import pytest

from src.execution.broker_executor import BrokerExecutor
from src.strategy.weight_interface import PortfolioWeights


@pytest.fixture
def risk_audit_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "test-secret")
    monkeypatch.setenv(
        "STMAA_BROKER_STATE_FILE",
        str(tmp_path / "state" / "broker_state.json"),
    )


def _fresh_account():
    return {
        "cash": 10000.0,
        "portfolio_value": 10000.0,
        "buying_power": 10000.0,
        "equity": 10000.0,
        "account_data_fresh": True,
    }


def test_position_read_failure_blocks_all_new_orders(risk_audit_env, monkeypatch):
    executor = BrokerExecutor()
    submitted = []
    monkeypatch.setattr(executor, "get_account_status", _fresh_account)
    monkeypatch.setattr(
        executor,
        "get_positions",
        lambda: (_ for _ in ()).throw(
            RuntimeError("Unable to retrieve broker positions; refusing to execute trades")
        ),
    )
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [])
    monkeypatch.setattr(
        executor.trading_client,
        "submit_order",
        lambda request: submitted.append(request),
    )

    with pytest.raises(RuntimeError, match="Unable to retrieve broker positions"):
        executor.execute_weights(
            PortfolioWeights(weights=np.array([0.1, 0.1]), tickers=["AAPL", "MSFT"])
        )

    assert submitted == []


def test_open_order_snapshot_failure_blocks_all_new_orders(risk_audit_env, monkeypatch):
    executor = BrokerExecutor()
    submitted = []
    monkeypatch.setattr(executor, "get_account_status", _fresh_account)
    monkeypatch.setattr(executor, "get_positions", lambda: {})
    monkeypatch.setattr(
        executor,
        "_get_open_orders",
        lambda: (_ for _ in ()).throw(
            RuntimeError("Unable to retrieve or validate broker open orders; refusing execution")
        ),
    )
    monkeypatch.setattr(
        executor.trading_client,
        "submit_order",
        lambda request: submitted.append(request),
    )

    with pytest.raises(RuntimeError, match="Unable to retrieve or validate broker open orders"):
        executor.execute_weights(
            PortfolioWeights(weights=np.array([0.1]), tickers=["AAPL"])
        )

    assert submitted == []


@pytest.mark.parametrize(
    "order",
    [
        SimpleNamespace(
            status="accepted", qty="10", filled_qty="0",
            limit_price=None, notional=None, symbol="UNKNOWN",
        ),
        SimpleNamespace(
            status="accepted", qty="10", filled_qty="0",
            limit_price="nan", notional=None, symbol="AAPL",
        ),
        SimpleNamespace(
            status="partially_filled", qty="10", filled_qty="10",
            limit_price="100", notional="1000", symbol="AAPL",
        ),
    ],
)
def test_unestimable_pending_order_exposure_blocks_new_positions(
    risk_audit_env, monkeypatch, order
):
    executor = BrokerExecutor()
    submitted = []
    monkeypatch.setattr(executor, "get_account_status", _fresh_account)
    monkeypatch.setattr(executor, "get_positions", lambda: {})
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [order])
    monkeypatch.setattr(
        executor, "_get_prices_from_bars", lambda tickers: {"AAPL": 100.0, "MSFT": 100.0}
    )
    monkeypatch.setattr(
        executor.trading_client,
        "submit_order",
        lambda request: submitted.append(request),
    )

    with pytest.raises(RuntimeError, match="Cannot estimate exposure for active order"):
        executor.execute_weights(
            PortfolioWeights(weights=np.array([0.1]), tickers=["MSFT"])
        )

    assert submitted == []


def test_market_data_provider_exception_blocks_new_positions(risk_audit_env, monkeypatch):
    executor = BrokerExecutor()
    submitted = []
    monkeypatch.setattr(executor, "get_account_status", _fresh_account)
    monkeypatch.setattr(executor, "get_positions", lambda: {})
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [])
    monkeypatch.setattr(
        executor.alpaca_feed,
        "get_latest_quotes",
        lambda tickers: (_ for _ in ()).throw(TimeoutError("quote service timeout")),
    )
    monkeypatch.setattr(
        executor.trading_client,
        "submit_order",
        lambda request: submitted.append(request),
    )

    with pytest.raises(RuntimeError, match="valid prices unavailable for: AAPL"):
        executor.execute_weights(
            PortfolioWeights(weights=np.array([0.1]), tickers=["AAPL"])
        )

    assert submitted == []
