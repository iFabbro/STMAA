"""Combined fail-closed tests for restart state, pending orders, and stale prices."""
import json
from types import SimpleNamespace

import numpy as np
import pytest

from src.execution.broker_executor import BrokerExecutor
from src.strategy.weight_interface import PortfolioWeights


@pytest.fixture
def combined_audit_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "test-secret")
    monkeypatch.setenv(
        "STMAA_BROKER_STATE_FILE",
        str(tmp_path / "state" / "broker_state.json"),
    )


def _fresh_account():
    return {
        "cash": 9000.0,
        "portfolio_value": 10000.0,
        "buying_power": 9000.0,
        "equity": 10000.0,
        "account_data_fresh": True,
        "account_snapshot_at": "2026-10-28T08:00:00+00:00",
    }


def test_restart_fails_closed_when_safety_state_maps_are_missing(combined_audit_env):
    first = BrokerExecutor()
    saved = dict(first.state)
    saved.pop("order_submission_unresolved")
    saved.pop("emergency_liquidation_pending")
    first.state_file.write_text(json.dumps(saved), encoding="utf-8")
    original = first.state_file.read_text(encoding="utf-8")

    with pytest.raises(
        RuntimeError,
        match="Unable to load persisted broker state",
    ):
        BrokerExecutor()

    assert first.state_file.read_text(encoding="utf-8") == original


def test_pending_order_plus_stale_quote_blocks_all_new_orders(
    combined_audit_env, monkeypatch
):
    executor = BrokerExecutor()
    submitted = []
    pending = SimpleNamespace(
        id="pending-msft",
        symbol="MSFT",
        status="accepted",
        qty="2",
        filled_qty="0",
        limit_price="100",
        notional=None,
    )

    monkeypatch.setattr(executor, "get_account_status", lambda: _fresh_account())
    monkeypatch.setattr(executor, "get_positions", lambda: {})
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [pending])
    # Empty quote map models missing or stale data; no daily-bar fallback is allowed.
    monkeypatch.setattr(executor, "_get_prices_from_bars", lambda tickers: {})
    monkeypatch.setattr(
        executor.trading_client,
        "submit_order",
        lambda request: submitted.append(request),
    )

    with pytest.raises(RuntimeError, match="valid prices unavailable"):
        executor.execute_weights(
            PortfolioWeights(weights=np.array([0.1]), tickers=["AAPL"])
        )

    assert submitted == []


def test_uncertain_pending_order_exposure_blocks_even_if_target_quote_is_available(
    combined_audit_env, monkeypatch
):
    executor = BrokerExecutor()
    submitted = []
    unknown_exposure = SimpleNamespace(
        id="pending-unknown",
        symbol="UNKNOWN",
        status="accepted",
        qty="10",
        filled_qty="0",
        limit_price=None,
        notional=None,
    )

    monkeypatch.setattr(executor, "get_account_status", lambda: _fresh_account())
    monkeypatch.setattr(executor, "get_positions", lambda: {})
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [unknown_exposure])
    monkeypatch.setattr(
        executor, "_get_prices_from_bars",
        lambda tickers: {ticker: 100.0 for ticker in tickers},
    )
    monkeypatch.setattr(
        executor.trading_client,
        "submit_order",
        lambda request: submitted.append(request),
    )

    with pytest.raises(RuntimeError, match="Cannot estimate exposure for active order"):
        executor.execute_weights(
            PortfolioWeights(weights=np.array([0.1]), tickers=["AAPL"])
        )

    assert submitted == []


def test_restart_with_unreconciled_submission_blocks_before_market_data_or_order_paths(
    combined_audit_env, monkeypatch
):
    first = BrokerExecutor()
    first.state["order_submission_unresolved"] = {
        "AAPL": {
            "client_order_id": "ambiguous-client-id",
            "side": "buy",
            "quantity": 2.0,
            "price": 100.0,
            "created_at": "2026-10-28T08:00:00+00:00",
            "reason": "submission_outcome_unconfirmed",
        }
    }
    first._save_state()

    restarted = BrokerExecutor()
    submitted = []
    market_data_reads = []
    restarted.trading_client = SimpleNamespace(
        get_order_by_client_id=lambda client_order_id: (_ for _ in ()).throw(
            RuntimeError("broker order lookup unavailable")
        ),
        submit_order=lambda request: submitted.append(request),
    )
    monkeypatch.setattr(
        restarted,
        "_get_prices_from_bars",
        lambda tickers: market_data_reads.append(tickers) or {ticker: 100.0 for ticker in tickers},
    )

    with pytest.raises(RuntimeError, match="requires reconciliation or terminal status"):
        restarted.execute_weights(
            PortfolioWeights(weights=np.array([0.1]), tickers=["AAPL"])
        )

    assert submitted == []
    assert market_data_reads == []
    assert "AAPL" in restarted.state["order_submission_unresolved"]


def test_non_liquidating_circuit_breaker_has_no_alternate_new_order_path(
    combined_audit_env, monkeypatch
):
    executor = BrokerExecutor()
    submitted = []
    monkeypatch.setattr(executor, "get_account_status", lambda: _fresh_account())
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [])
    monkeypatch.setattr(
        executor.trading_client,
        "submit_order",
        lambda request: submitted.append(request),
    )

    result = executor.execute_weights(
        PortfolioWeights(
            weights=np.array([0.2]),
            tickers=["AAPL"],
            metadata={
                "circuit_breaker_triggered": True,
                "liquidate_on_circuit_breaker": False,
            },
        )
    )

    assert result["orders"] == []
    assert result["circuit_breaker_triggered"] is True
    assert submitted == []
