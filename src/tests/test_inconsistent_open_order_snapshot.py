"""Reject contradictory broker open-order snapshots before risk sizing or submission."""
from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np
import pytest

from src.execution.broker_executor import BrokerExecutor
from src.strategy.weight_interface import PortfolioWeights


@pytest.fixture
def executor_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "test-secret")
    monkeypatch.setenv(
        "STMAA_BROKER_STATE_FILE",
        str(tmp_path / "state" / "broker_state.json"),
    )
    executor = BrokerExecutor()
    monkeypatch.setattr(executor, "get_account_status", lambda: {
        "cash": 10000.0,
        "portfolio_value": 10000.0,
        "buying_power": 10000.0,
        "equity": 10000.0,
        "account_data_fresh": True,
        "account_snapshot_at": datetime.now(timezone.utc).isoformat(),
    })
    return executor


@pytest.mark.parametrize(
    "order",
    [
        pytest.param(
            SimpleNamespace(id="stale-filled", symbol="AAPL", status="filled"),
            id="terminal-order-in-open-snapshot",
        ),
        pytest.param(
            SimpleNamespace(id="missing-status", symbol="AAPL"),
            id="missing-status",
        ),
        pytest.param(
            SimpleNamespace(id="unknown-status", symbol="AAPL", status="future_broker_state"),
            id="unknown-status",
        ),
        pytest.param(
            SimpleNamespace(id="replaced-order", symbol="AAPL", status="replaced"),
            id="replaced-parent-is-not-active",
        ),
        pytest.param(
            SimpleNamespace(id="calculated-order", symbol="AAPL", status="calculated"),
            id="completed-calculation-is-not-active",
        ),
    ],
)
def test_inconsistent_open_order_snapshot_blocks_rebalance_before_submission(
    executor_env, monkeypatch, order
):
    executor = executor_env
    submitted = []
    positions_reads = []

    monkeypatch.setattr(
        executor.trading_client,
        "get_orders",
        lambda **kwargs: [order],
    )
    monkeypatch.setattr(
        executor.trading_client,
        "get_all_positions",
        lambda: positions_reads.append(True) or [],
    )
    monkeypatch.setattr(
        executor.trading_client,
        "submit_order",
        lambda request: submitted.append(request),
    )

    with pytest.raises(
        RuntimeError,
        match="Unable to retrieve or validate broker open orders",
    ):
        executor.execute_weights(
            PortfolioWeights(weights=np.array([0.1]), tickers=["AAPL"])
        )

    assert submitted == []
    assert positions_reads == []


@pytest.mark.parametrize(
    "snapshot",
    [
        pytest.param(
            [
                SimpleNamespace(
                    id="duplicate-order-id",
                    symbol="AAPL",
                    status="accepted",
                    qty="1",
                    filled_qty="0",
                    limit_price="100",
                ),
                SimpleNamespace(
                    id="duplicate-order-id",
                    symbol="MSFT",
                    status="accepted",
                    qty="1",
                    filled_qty="0",
                    limit_price="100",
                ),
            ],
            id="duplicate-order-identities",
        ),
        pytest.param(
            {"orders": []},
            id="non-list-response-shape",
        ),
    ],
)
def test_duplicate_or_malformed_open_order_snapshot_blocks_before_positions(
    executor_env, monkeypatch, snapshot
):
    executor = executor_env
    submitted = []
    positions_reads = []

    monkeypatch.setattr(
        executor.trading_client,
        "get_orders",
        lambda **kwargs: snapshot,
    )
    monkeypatch.setattr(
        executor.trading_client,
        "get_all_positions",
        lambda: positions_reads.append(True) or [],
    )
    monkeypatch.setattr(
        executor.trading_client,
        "submit_order",
        lambda request: submitted.append(request),
    )

    with pytest.raises(
        RuntimeError,
        match="Unable to retrieve or validate broker open orders",
    ):
        executor.execute_weights(
            PortfolioWeights(weights=np.array([0.1]), tickers=["AAPL"])
        )

    assert submitted == []
    assert positions_reads == []


@pytest.mark.parametrize("status", ["stopped", "done_for_day", "held"])
def test_nonterminal_alpaca_order_statuses_are_reserved_as_active(
    executor_env, monkeypatch, status
):
    executor = executor_env
    order = SimpleNamespace(
        id=f"active-{status}",
        symbol="MSFT",
        status=status,
        qty="10",
        filled_qty="0",
        limit_price="100",
        notional=None,
    )
    monkeypatch.setattr(
        executor.trading_client,
        "get_orders",
        lambda **kwargs: [order],
    )

    open_orders = executor._get_open_orders()

    assert open_orders == [order]
    assert executor._get_reserved_notional(open_orders, prices={}) == 1000.0
