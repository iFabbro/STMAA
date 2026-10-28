"""Remaining risk-matrix combinations for pending orders and incomplete market data."""
from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np
import pytest

from src.execution.broker_executor import BrokerExecutor
from src.strategy.weight_interface import PortfolioWeights


@pytest.fixture
def risk_matrix_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "test-secret")
    monkeypatch.setenv(
        "STMAA_BROKER_STATE_FILE",
        str(tmp_path / "state" / "broker_state.json"),
    )


def _account(*, fresh=True):
    return {
        "cash": 10000.0,
        "portfolio_value": 10000.0,
        "buying_power": 10000.0,
        "equity": 10000.0,
        "account_id": "risk-matrix-paper-account",
        "account_data_fresh": fresh,
        "account_snapshot_at": datetime.now(timezone.utc).isoformat(),
    }


def _order(*, symbol, limit_price=None, notional=None):
    return SimpleNamespace(
        id=f"pending-{symbol}",
        symbol=symbol,
        status="accepted",
        qty="2",
        filled_qty="0",
        limit_price=limit_price,
        notional=notional,
    )


def test_unknown_pending_order_exposure_blocks_even_with_fresh_target_quote(
    risk_matrix_env, monkeypatch
):
    executor = BrokerExecutor()
    submitted = []
    now = datetime.now(timezone.utc).isoformat()
    monkeypatch.setattr(executor, "get_account_status", _account)
    monkeypatch.setattr(executor, "get_positions", lambda: {})
    monkeypatch.setattr(
        executor, "_get_open_orders",
        lambda: [_order(symbol="UNKNOWN", limit_price=None, notional=None)],
    )
    monkeypatch.setattr(
        executor.alpaca_feed,
        "get_latest_quotes",
        lambda tickers: {
            ticker: {"price": 100.0, "timestamp": now}
            for ticker in tickers
        },
    )
    monkeypatch.setattr(
        executor.trading_client, "submit_order",
        lambda request: submitted.append(request),
    )

    with pytest.raises(RuntimeError, match="Cannot estimate exposure for active order"):
        executor.execute_weights(
            PortfolioWeights(weights=np.array([0.1]), tickers=["AAPL"])
        )

    assert submitted == []


def test_priced_pending_order_and_missing_target_quote_block_all_submissions(
    risk_matrix_env, monkeypatch
):
    executor = BrokerExecutor()
    submitted = []
    monkeypatch.setattr(executor, "get_account_status", _account)
    monkeypatch.setattr(executor, "get_positions", lambda: {})
    monkeypatch.setattr(
        executor, "_get_open_orders",
        lambda: [_order(symbol="MSFT", limit_price="100")],
    )
    # Missing target quote, rather than a stale quote.
    monkeypatch.setattr(executor.alpaca_feed, "get_latest_quotes", lambda tickers: {})
    monkeypatch.setattr(
        executor.trading_client, "submit_order",
        lambda request: submitted.append(request),
    )

    with pytest.raises(RuntimeError, match="valid prices unavailable for: AAPL"):
        executor.execute_weights(
            PortfolioWeights(weights=np.array([0.1]), tickers=["AAPL"])
        )

    assert submitted == []


def test_stale_account_with_unpriced_pending_order_blocks_before_market_data(
    risk_matrix_env, monkeypatch
):
    executor = BrokerExecutor()
    quote_reads = []
    submitted = []
    monkeypatch.setattr(executor, "get_account_status", lambda: _account(fresh=False))
    monkeypatch.setattr(executor, "get_positions", lambda: {})
    monkeypatch.setattr(
        executor, "_get_open_orders",
        lambda: [_order(symbol="UNKNOWN", limit_price=None, notional=None)],
    )
    monkeypatch.setattr(
        executor.alpaca_feed,
        "get_latest_quotes",
        lambda tickers: quote_reads.append(tickers) or {},
    )
    monkeypatch.setattr(
        executor.trading_client, "submit_order",
        lambda request: submitted.append(request),
    )

    with pytest.raises(RuntimeError, match="Cannot verify current broker account equity"):
        executor.execute_weights(
            PortfolioWeights(weights=np.array([0.1]), tickers=["AAPL"])
        )

    assert quote_reads == []
    assert submitted == []
