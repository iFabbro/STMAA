"""Fail-closed regression tests for aggregate and indeterminate pending-order exposure."""
from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np
import pytest

from src.execution.broker_executor import BrokerExecutor
from src.strategy.weight_interface import PortfolioWeights


@pytest.fixture
def pending_exposure_env(monkeypatch, tmp_path):
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
            "buying_power": 10000.0,
            "equity": 10000.0,
            "account_data_fresh": True,
            "account_id": "pending-exposure-fail-closed-test",
            "account_snapshot_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    monkeypatch.setattr(executor, "get_positions", lambda: {})
    monkeypatch.setattr(
        executor,
        "_get_prices_from_bars",
        lambda tickers: {"AAPL": 100.0, "MSFT": 100.0},
    )
    monkeypatch.setattr(
        executor.trading_client,
        "submit_order",
        lambda request: submitted.append(request),
    )
    return executor, submitted


def _weights():
    return PortfolioWeights(weights=np.array([0.1]), tickers=["AAPL"])


def test_estimable_order_does_not_mask_an_unestimable_pending_order(
    pending_exposure_env, monkeypatch
):
    executor, submitted = pending_exposure_env
    monkeypatch.setattr(
        executor,
        "_get_open_orders",
        lambda: [
            SimpleNamespace(
                id="priced-msft",
                symbol="MSFT",
                status="accepted",
                qty="10",
                filled_qty="0",
                limit_price="100",
                notional=None,
            ),
            SimpleNamespace(
                id="unpriced-external",
                symbol="XYZ",
                status="accepted",
                qty="10",
                filled_qty="0",
                limit_price=None,
                notional=None,
            ),
        ],
    )

    with pytest.raises(
        RuntimeError,
        match="Cannot estimate exposure for active order XYZ",
    ):
        executor.execute_weights(_weights())

    assert submitted == []


def test_non_finite_aggregate_pending_order_exposure_blocks_execution(
    pending_exposure_env, monkeypatch
):
    executor, submitted = pending_exposure_env
    monkeypatch.setattr(
        executor,
        "_get_open_orders",
        lambda: [
            SimpleNamespace(
                id="overflowing-notional",
                symbol="XYZ",
                status="accepted",
                qty="1e308",
                filled_qty="0",
                limit_price="2",
                notional=None,
            ),
        ],
    )

    with pytest.raises(
        RuntimeError,
        match="Cannot estimate aggregate exposure from open orders",
    ):
        executor.execute_weights(_weights())

    assert submitted == []
