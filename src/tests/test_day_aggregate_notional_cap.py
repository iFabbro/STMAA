"""DAY-path regression tests for the shared hard USD 1,000 gross-notional cap."""
from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np
import pytest

from src.execution.broker_executor import BrokerExecutor
from src.strategy.weight_interface import PortfolioWeights


@pytest.fixture
def day_cap_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "test-secret")
    monkeypatch.setenv(
        "STMAA_BROKER_STATE_FILE", str(tmp_path / "state" / "broker_state.json")
    )
    executor = BrokerExecutor(paper_trading=True)
    executor.configured_symbols = ["AAPL", "MSFT"]
    submitted = []

    account = {
        "cash": 10000.0,
        "portfolio_value": 10000.0,
        "buying_power": 10000.0,
        "equity": 10000.0,
        "account_data_fresh": True,
        "account_id": "day-aggregate-cap-test",
        "account_snapshot_at": datetime.now(timezone.utc).isoformat(),
    }
    positions = {}
    open_orders = []

    monkeypatch.setattr(executor, "_reconcile_unresolved_submissions", lambda: None)
    monkeypatch.setattr(executor, "_refresh_confirmed_submission_statuses", lambda: None)
    monkeypatch.setattr(executor, "_reconcile_terminal_order_positions", lambda: None)
    monkeypatch.setattr(executor, "get_account_status", lambda: dict(account))
    monkeypatch.setattr(executor, "get_positions", lambda: dict(positions))
    monkeypatch.setattr(executor, "_get_open_orders", lambda: list(open_orders))
    monkeypatch.setattr(
        executor, "_get_prices_from_bars",
        lambda tickers: {symbol: 100.0 for symbol in tickers},
    )
    monkeypatch.setattr(executor, "reconcile_corporate_actions", lambda *a, **k: None)
    monkeypatch.setattr(executor, "_verify_execution_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(executor, "_verify_quote_before_submission", lambda symbol, price: price)
    monkeypatch.setattr(executor, "_ensure_verified_metrics_baseline", lambda *a, **k: None)
    monkeypatch.setattr(executor, "_record_confirmed_submission", lambda *a, **k: None)
    monkeypatch.setattr(executor, "_save_state", lambda: None)
    monkeypatch.setattr(executor, "_persist_metrics_history", lambda: None)
    monkeypatch.setattr(executor.metrics, "update", lambda *a, **k: None)
    monkeypatch.setattr(executor.metrics, "get_all_metrics", lambda: {})
    monkeypatch.setattr(executor.metrics, "write_metrics_json", lambda *a, **k: None)
    monkeypatch.setattr(executor.trading_client, "submit_order", lambda request: (
        submitted.append(request) or SimpleNamespace(
            id=f"day-test-{len(submitted)}", status="accepted",
        type=request.type, limit_price=getattr(request, "limit_price", None)
        )
    ))

    return executor, positions, open_orders, submitted


def _weights(weight):
    return PortfolioWeights(weights=np.array([weight]), tickers=["AAPL"])


def test_day_buy_is_clipped_to_remaining_aggregate_gross_notional(day_cap_env):
    executor, positions, open_orders, submitted = day_cap_env
    positions["AAPL"] = {
        "qty": 9.0, "market_value": 900.0,
        "avg_entry_price": 100.0, "unrealized_pl": 0.0,
    }

    executor.execute_weights(_weights(0.20))

    assert len(submitted) == 1
    assert str(submitted[0].side.value).lower() == "buy"
    assert str(getattr(submitted[0].type, "value", submitted[0].type)).lower() == "limit"
    assert submitted[0].limit_price == 100.0
    assert float(submitted[0].qty) == pytest.approx(1.0)
    assert float(submitted[0].qty) * 100.0 + 900.0 <= 1000.0


def test_day_combines_positions_and_full_partial_fill_pending_buy_reserve(day_cap_env):
    executor, positions, open_orders, submitted = day_cap_env
    positions["AAPL"] = {
        "qty": 5.0, "market_value": 500.0,
        "avg_entry_price": 100.0, "unrealized_pl": 0.0,
    }
    open_orders.append(SimpleNamespace(
        id="partial-msft", symbol="MSFT", side="buy",
        status="partially_filled", qty="6", filled_qty="3",
        limit_price="100", notional=None,
    ))

    executor.execute_weights(_weights(0.20))

    # $500 marked position + full $600 original partial-fill order reserve
    # already exceeds the shared $1,000 cap; no further BUY may be submitted.
    assert submitted == []


def test_day_sell_remains_allowed_when_existing_gross_exposure_exceeds_cap(day_cap_env):
    executor, positions, open_orders, submitted = day_cap_env
    positions["AAPL"] = {
        "qty": 15.0, "market_value": 1500.0,
        "avg_entry_price": 100.0, "unrealized_pl": 0.0,
    }

    executor.execute_weights(_weights(0.0))

    assert len(submitted) == 1
    assert str(submitted[0].side.value).lower() == "sell"
    assert str(getattr(submitted[0].type, "value", submitted[0].type)).lower() == "market"
    assert float(submitted[0].qty) == pytest.approx(15.0)


def test_day_buy_to_cover_short_remains_allowed_above_gross_cap(day_cap_env):
    executor, positions, open_orders, submitted = day_cap_env
    positions["AAPL"] = {
        "qty": -15.0, "market_value": -1500.0,
        "avg_entry_price": 100.0, "unrealized_pl": 0.0,
    }

    executor.execute_weights(_weights(0.0))

    assert len(submitted) == 1
    assert str(submitted[0].side.value).lower() == "buy"
    assert float(submitted[0].qty) == pytest.approx(15.0)
    assert float(submitted[0].qty) * 100.0 == pytest.approx(1500.0)


def test_day_unknown_pending_order_side_fails_closed(day_cap_env):
    executor, positions, open_orders, submitted = day_cap_env
    open_orders.append(SimpleNamespace(
        id="ambiguous-order", symbol="MSFT", side="unknown",
        status="accepted", qty="1", filled_qty="0",
        limit_price="100", notional=None,
    ))

    with pytest.raises(RuntimeError, match="order side is unknown"):
        executor.execute_weights(_weights(0.20))

    assert submitted == []


def test_day_restart_with_unresolved_submission_blocks_new_exposure(day_cap_env):
    executor, positions, open_orders, submitted = day_cap_env
    executor.state["order_submission_unresolved"] = {
        "MSFT": {"client_order_id": "persisted-ambiguous-day-order"}
    }

    with pytest.raises(RuntimeError, match="requires reconciliation"):
        executor.execute_weights(_weights(0.20))

    assert submitted == []
