"""Integrated fail-closed checks: pending orders, stale quotes, and partial position reads."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pytest

from src.execution.broker_executor import BrokerExecutor
from src.strategy.weight_interface import PortfolioWeights


@pytest.fixture
def isolated_executor(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "test-secret")
    monkeypatch.setenv(
        "STMAA_BROKER_STATE_FILE",
        str(tmp_path / "state" / "broker_state.json"),
    )
    executor = BrokerExecutor(paper_trading=True)
    monkeypatch.setattr(executor, "get_account_status", lambda: {
        "cash": 10000.0,
        "portfolio_value": 10000.0,
        "buying_power": 10000.0,
        "equity": 10000.0,
        "account_data_fresh": True,
        "account_id": "test-account",
    })
    return executor


def _target():
    return PortfolioWeights(weights=np.array([0.10]), tickers=["AAPL"])


def test_broker_position_symbols_are_canonicalized_and_collisions_rejected(
    isolated_executor, monkeypatch
):
    executor = isolated_executor
    position = SimpleNamespace(
        symbol=" aapl ",
        qty="2",
        market_value="200",
        avg_entry_price="90",
        unrealized_pl="20",
    )
    monkeypatch.setattr(
        executor.trading_client, "get_all_positions", lambda: [position]
    )

    assert executor.get_positions() == {
        "AAPL": {
            "qty": 2.0,
            "market_value": 200.0,
            "avg_entry_price": 90.0,
            "unrealized_pl": 20.0,
        }
    }

    duplicate = SimpleNamespace(
        symbol="aapl",
        qty="1",
        market_value="100",
        avg_entry_price="90",
        unrealized_pl="10",
    )
    monkeypatch.setattr(
        executor.trading_client,
        "get_all_positions",
        lambda: [position, duplicate],
    )
    with pytest.raises(
        RuntimeError,
        match="Unable to retrieve broker positions",
    ):
        executor.get_positions()


def test_pending_broker_order_blocks_rebalance_and_sends_zero_orders(
    isolated_executor, monkeypatch
):
    executor = isolated_executor
    submitted = []
    executor.state["order_submission_confirmed"] = {
        "client-order-1": {
            "ticker": "AAPL",
            "side": "buy",
            "quantity": 10.0,
            "order_id": "broker-order-1",
            "client_order_id": "client-order-1",
            "status": "accepted",
        }
    }
    monkeypatch.setattr(
        executor.trading_client,
        "get_order_by_id",
        lambda order_id: SimpleNamespace(
            id=order_id,
            client_order_id="client-order-1",
            status="accepted",
            symbol="AAPL",
            side="buy",
            qty="10.0",
            filled_qty="0",
        ),
    )
    monkeypatch.setattr(
        executor.trading_client,
        "submit_order",
        lambda request: submitted.append(request),
    )

    with pytest.raises(
        RuntimeError,
        match="requires reconciliation or terminal status",
    ):
        executor.execute_weights(_target())

    assert len(submitted) == 0


def test_unpriceable_pending_order_blocks_rebalance_and_sends_zero_orders(
    isolated_executor, monkeypatch
):
    executor = isolated_executor
    submitted = []
    unpriceable_order = SimpleNamespace(
        id="pending-order-1",
        symbol="AAPL",
        status="accepted",
        qty=None,
        filled_qty="0",
        notional=None,
        limit_price=None,
    )
    monkeypatch.setattr(executor, "get_positions", lambda: {})
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [unpriceable_order])
    monkeypatch.setattr(
        executor, "_get_prices_from_bars", lambda tickers: {"AAPL": 100.0}
    )
    monkeypatch.setattr(
        executor.trading_client,
        "submit_order",
        lambda request: submitted.append(request),
    )

    with pytest.raises(
        RuntimeError,
        match="Cannot estimate exposure for active order",
    ):
        executor.execute_weights(_target())

    assert submitted == []


def test_pending_market_order_without_price_blocks_rebalance_and_sends_zero_orders(
    isolated_executor, monkeypatch
):
    executor = isolated_executor
    submitted = []
    pending_market_order = SimpleNamespace(
        id="pending-market-order-1",
        symbol="AAPL",
        status="accepted",
        qty="10",
        filled_qty="0",
        notional=None,
        limit_price=None,
    )
    monkeypatch.setattr(executor, "get_positions", lambda: {})
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [pending_market_order])
    monkeypatch.setattr(executor, "_get_prices_from_bars", lambda tickers: {})
    monkeypatch.setattr(
        executor.trading_client,
        "submit_order",
        lambda request: submitted.append(request),
    )

    with pytest.raises(
        RuntimeError,
        match="Cannot estimate exposure for active order",
    ):
        executor.execute_weights(_target())

    assert submitted == []


def test_stale_quote_blocks_rebalance_and_sends_zero_orders(
    isolated_executor, monkeypatch
):
    executor = isolated_executor
    submitted = []
    monkeypatch.setattr(executor, "get_positions", lambda: {})
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [])
    stale_timestamp = (
        datetime.now(timezone.utc) - timedelta(minutes=10)
    ).isoformat()
    monkeypatch.setattr(
        executor.alpaca_feed,
        "get_latest_quotes",
        lambda tickers: {
            "AAPL": {"price": 100.0, "timestamp": stale_timestamp}
        },
    )
    monkeypatch.setattr(
        executor.trading_client,
        "submit_order",
        lambda request: submitted.append(request),
    )

    with pytest.raises(
        RuntimeError,
        match="valid prices unavailable for: AAPL",
    ):
        executor.execute_weights(_target())

    assert len(submitted) == 0


def test_incomplete_position_snapshot_blocks_rebalance_and_sends_zero_orders(
    isolated_executor, monkeypatch
):
    executor = isolated_executor
    submitted = []
    monkeypatch.setattr(
        executor,
        "get_positions",
        lambda: (_ for _ in ()).throw(
            RuntimeError(
                "Unable to retrieve broker positions; refusing to execute trades"
            )
        ),
    )
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [])
    monkeypatch.setattr(
        executor.trading_client,
        "submit_order",
        lambda request: submitted.append(request),
    )

    with pytest.raises(
        RuntimeError,
        match="Unable to retrieve broker positions",
    ):
        executor.execute_weights(_target())

    assert len(submitted) == 0

def test_target_tickers_are_canonicalized_before_account_reads(
    isolated_executor, monkeypatch
):
    executor = isolated_executor
    submitted = []
    target = PortfolioWeights(weights=np.array([0.10]), tickers=[" aapl "])

    def stop_after_canonicalization():
        assert target.tickers == ["AAPL"]
        raise RuntimeError("stop after canonicalization")

    monkeypatch.setattr(executor, "get_account_status", stop_after_canonicalization)
    monkeypatch.setattr(
        executor.trading_client,
        "submit_order",
        lambda request: submitted.append(request),
    )

    with pytest.raises(RuntimeError, match="stop after canonicalization"):
        executor.execute_weights(target)

    assert submitted == []


def test_duplicate_target_tickers_are_rejected_before_account_reads(
    isolated_executor, monkeypatch
):
    executor = isolated_executor
    submitted = []
    monkeypatch.setattr(
        executor,
        "get_account_status",
        lambda: (_ for _ in ()).throw(
            AssertionError("Account reads must not occur for invalid target allocation")
        ),
    )
    monkeypatch.setattr(
        executor.trading_client,
        "submit_order",
        lambda request: submitted.append(request),
    )

    duplicate_target = PortfolioWeights(
        weights=np.array([0.20, 0.30]),
        tickers=["AAPL", " aapl "],
    )
    with pytest.raises(ValueError, match="Target tickers must be unique"):
        executor.execute_weights(duplicate_target)

    assert submitted == []



def test_noncanonical_pending_order_symbol_prevents_duplicate_rebalance_order(
    isolated_executor, monkeypatch
):
    executor = isolated_executor
    submitted = []
    pending_order = SimpleNamespace(
        id="pending-order-symbol-normalization",
        symbol=" aapl ",
        status="accepted",
        qty="1",
        filled_qty="0",
        notional=None,
        limit_price="100",
    )
    monkeypatch.setattr(executor, "get_positions", lambda: {})
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [pending_order])
    monkeypatch.setattr(
        executor, "_get_prices_from_bars", lambda tickers: {"AAPL": 100.0}
    )
    monkeypatch.setattr(
        executor.trading_client,
        "submit_order",
        lambda request: submitted.append(request),
    )

    result = executor.execute_weights(_target())

    assert result["orders"] == []
    assert submitted == []


def test_partially_filled_pending_order_reserves_full_requested_exposure(
    isolated_executor
):
    executor = isolated_executor
    partially_filled_order = SimpleNamespace(
        id="partially-filled-order-1",
        symbol="AAPL",
        status="partially_filled",
        qty="10",
        filled_qty="4",
        notional=None,
        limit_price="100",
    )

    # Position snapshots can lag fills; reserve the full requested order amount
    # until terminal status and a separate position reconciliation confirm it.
    assert executor._estimate_order_notional(partially_filled_order) == 1000.0
