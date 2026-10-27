"""Risk-gate combination regressions: never open exposure with unreliable inputs."""
from datetime import datetime, timedelta, timezone
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


def _active_order(
    *,
    symbol="MSFT",
    qty="2",
    filled_qty="0",
    limit_price="100",
    notional=None,
    status="accepted",
):
    return SimpleNamespace(
        id=f"pending-{symbol}",
        symbol=symbol,
        status=status,
        qty=qty,
        filled_qty=filled_qty,
        limit_price=limit_price,
        notional=notional,
    )


def _fresh_account():
    return {
        "cash": 10000.0,
        "portfolio_value": 10000.0,
        "buying_power": 10000.0,
        "equity": 10000.0,
        "account_data_fresh": True,
        "account_id": "test-account",
    }


def _target_weights():
    return PortfolioWeights(weights=np.array([0.1]), tickers=["AAPL"])


def test_pending_order_plus_stale_target_quote_blocks_every_new_order(
    risk_audit_env, monkeypatch
):
    executor = BrokerExecutor()
    submitted = []
    stale = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    monkeypatch.setattr(executor, "get_account_status", _fresh_account)
    monkeypatch.setattr(executor, "get_positions", lambda: {})
    monkeypatch.setattr(
        executor, "_get_open_orders",
        lambda: [_active_order(limit_price="100")],
    )
    monkeypatch.setattr(
        executor.alpaca_feed,
        "get_latest_quotes",
        lambda tickers: {
            ticker: {"price": 100.0, "timestamp": stale}
            for ticker in tickers
        },
    )
    monkeypatch.setattr(
        executor.trading_client,
        "submit_order",
        lambda request: submitted.append(request),
    )

    with pytest.raises(RuntimeError, match="valid prices unavailable for: AAPL"):
        executor.execute_weights(_target_weights())

    assert submitted == []


def test_unpriceable_pending_order_plus_missing_target_quote_fails_closed(
    risk_audit_env, monkeypatch
):
    executor = BrokerExecutor()
    submitted = []
    monkeypatch.setattr(executor, "get_account_status", _fresh_account)
    monkeypatch.setattr(executor, "get_positions", lambda: {})
    monkeypatch.setattr(
        executor, "_get_open_orders",
        lambda: [_active_order(limit_price=None, notional=None)],
    )
    monkeypatch.setattr(executor, "_get_prices_from_bars", lambda tickers: {})
    monkeypatch.setattr(
        executor.trading_client,
        "submit_order",
        lambda request: submitted.append(request),
    )

    with pytest.raises(RuntimeError, match="Cannot estimate exposure for active order"):
        executor.execute_weights(_target_weights())

    assert submitted == []


def test_incomplete_account_snapshot_with_pending_order_blocks_before_pricing(
    risk_audit_env, monkeypatch
):
    executor = BrokerExecutor()
    submitted = []
    reads = []
    monkeypatch.setattr(
        executor,
        "get_account_status",
        lambda: {
            "cash": None,
            "portfolio_value": None,
            "buying_power": None,
            "equity": None,
            "account_data_fresh": False,
        },
    )
    monkeypatch.setattr(
        executor, "_get_open_orders",
        lambda: reads.append("open_orders") or [_active_order()],
    )
    monkeypatch.setattr(
        executor,
        "_get_prices_from_bars",
        lambda tickers: (_ for _ in ()).throw(
            AssertionError("Pricing must not run on an incomplete account snapshot")
        ),
    )
    monkeypatch.setattr(
        executor.trading_client,
        "submit_order",
        lambda request: submitted.append(request),
    )

    with pytest.raises(RuntimeError, match="Cannot verify current broker account equity"):
        executor.execute_weights(_target_weights())

    assert reads == []
    assert submitted == []


def test_pending_order_plus_nonfinite_target_price_blocks_every_new_order(
    risk_audit_env, monkeypatch
):
    executor = BrokerExecutor()
    submitted = []
    monkeypatch.setattr(executor, "get_account_status", _fresh_account)
    monkeypatch.setattr(executor, "get_positions", lambda: {})
    monkeypatch.setattr(
        executor, "_get_open_orders",
        lambda: [_active_order(limit_price="100")],
    )
    monkeypatch.setattr(
        executor, "_get_prices_from_bars",
        lambda tickers: {"AAPL": float("nan"), "MSFT": 100.0},
    )
    monkeypatch.setattr(
        executor.trading_client,
        "submit_order",
        lambda request: submitted.append(request),
    )

    with pytest.raises(RuntimeError, match="valid prices unavailable for: AAPL"):
        executor.execute_weights(_target_weights())

    assert submitted == []


def test_broker_positions_snapshot_failure_with_pending_order_blocks_execution(
    risk_audit_env, monkeypatch
):
    executor = BrokerExecutor()
    submitted = []
    monkeypatch.setattr(executor, "get_account_status", _fresh_account)
    monkeypatch.setattr(
        executor,
        "_get_open_orders",
        lambda: [_active_order(limit_price="100")],
    )
    monkeypatch.setattr(
        executor,
        "get_positions",
        lambda: (_ for _ in ()).throw(
            RuntimeError("Unable to retrieve broker positions")
        ),
    )
    monkeypatch.setattr(
        executor.trading_client,
        "submit_order",
        lambda request: submitted.append(request),
    )

    with pytest.raises(RuntimeError, match="Unable to retrieve broker positions"):
        executor.execute_weights(_target_weights())

    assert submitted == []
