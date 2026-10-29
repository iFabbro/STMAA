"""Residual exposure audit: partial fills, buying power and fail-closed risk gates."""
from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np
import pytest

from src.execution.broker_executor import BrokerExecutor, RiskEngine
from src.strategy.weight_interface import PortfolioWeights


@pytest.fixture
def audit_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "test-secret")
    monkeypatch.setenv(
        "STMAA_BROKER_STATE_FILE",
        str(tmp_path / "state" / "broker_state.json"),
    )
    executor = BrokerExecutor(paper_trading=True)
    submitted = []
    monkeypatch.setattr(
        executor,
        "get_account_status",
        lambda: {
            "cash": 10000.0,
            "portfolio_value": 10000.0,
            "buying_power": 2500.0,
            "equity": 10000.0,
            "account_data_fresh": True,
            "account_id": "partial-fill-risk-audit",
            "account_snapshot_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    monkeypatch.setattr(executor, "get_positions", lambda: {})
    monkeypatch.setattr(
        executor,
        "_get_prices_from_bars",
        lambda tickers: {ticker: 100.0 for ticker in tickers},
    )
    monkeypatch.setattr(
        executor.trading_client,
        "submit_order",
        lambda request: submitted.append(request),
    )
    return executor, submitted


def _weights(ticker="AAPL", weight=0.1):
    return PortfolioWeights(weights=np.array([weight]), tickers=[ticker])


def _order(
    order_id="pending-msft",
    symbol="MSFT",
    status="accepted",
    qty="10",
    filled_qty="0",
    limit_price="100",
    notional=None,
):
    return SimpleNamespace(
        id=order_id,
        symbol=symbol,
        status=status,
        qty=qty,
        filled_qty=filled_qty,
        limit_price=limit_price,
        notional=notional,
    )


def test_partial_fill_with_position_snapshot_already_reflecting_fill_is_conservative():
    """Full-order reserve avoids undercounting when snapshot timing is uncertain."""
    executor = BrokerExecutor.__new__(BrokerExecutor)
    order = _order(
        order_id="partial-msft",
        status="partially_filled",
        qty="50",
        filled_qty="20",
        limit_price="100",
        notional="5000",
    )

    reserved = executor._get_reserved_notional([order], prices={"MSFT": 100.0})
    positions = {"MSFT": {"qty": 20.0, "market_value": 2000.0}}

    # This deliberately overestimates exposure if the position snapshot already
    # includes the fill. Until order/position snapshots can be correlated safely,
    # conservative over-reservation must not permit a new opening order.
    assert reserved == 5000.0
    engine = RiskEngine(max_position_pct=0.1, max_exposure_pct=0.5)
    allowed, reason = engine.allow(
        "AAPL",
        target_notional=1000.0,
        portfolio_value=10000.0,
        positions=positions,
        reserved_notional=reserved,
    )
    assert allowed == 0.0
    assert reason == "risk_block"


def test_pending_reserve_above_buying_power_blocks_new_submission(audit_env, monkeypatch):
    executor, submitted = audit_env
    # Individually valid orders whose combined $3,000 reserve exceeds buying power.
    monkeypatch.setattr(
        executor,
        "_get_open_orders",
        lambda: [
            _order(order_id="msft-1", qty="15", limit_price="100"),
            _order(order_id="nvda-1", symbol="NVDA", qty="15", limit_price="100"),
        ],
    )

    result = executor.execute_weights(_weights())

    assert result["orders"] == []
    assert submitted == []


@pytest.mark.parametrize(
    "order",
    [
        pytest.param(_order(qty="nan"), id="nan-quantity"),
        pytest.param(_order(qty="inf"), id="infinite-quantity"),
        pytest.param(_order(qty="10", limit_price="nan"), id="nan-limit-price"),
        pytest.param(_order(qty="10", limit_price="inf"), id="infinite-limit-price"),
        pytest.param(_order(qty="10", filled_qty="11"), id="filled-exceeds-quantity"),
        pytest.param(
            _order(status="partially_filled", qty="10", filled_qty="10"),
            id="nonterminal-order-with-no-remainder",
        ),
    ],
)
def test_malformed_pending_order_exposure_fails_closed(audit_env, order):
    executor, _ = audit_env
    with pytest.raises(RuntimeError, match="Cannot estimate exposure"):
        executor._estimate_order_notional(order, prices={"MSFT": 100.0})


def test_non_finite_position_exposure_is_rejected_by_risk_engine():
    engine = RiskEngine()
    with pytest.raises(ValueError, match="Position exposure must be finite"):
        engine.allow(
            "AAPL",
            target_notional=1000.0,
            portfolio_value=10000.0,
            positions={"MSFT": {"market_value": float("nan")}},
            reserved_notional=0.0,
        )


def test_incomplete_open_order_snapshot_blocks_before_submission(audit_env, monkeypatch):
    executor, submitted = audit_env
    monkeypatch.setattr(executor, "_get_open_orders", lambda: None)

    with pytest.raises((RuntimeError, TypeError, ValueError)):
        executor.execute_weights(_weights())

    assert submitted == []


def test_unavailable_position_snapshot_blocks_before_submission(audit_env, monkeypatch):
    executor, submitted = audit_env

    def fail_positions():
        raise RuntimeError("broker positions snapshot unavailable")

    monkeypatch.setattr(executor, "get_positions", fail_positions)
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [])

    with pytest.raises(RuntimeError):
        executor.execute_weights(_weights())

    assert submitted == []
