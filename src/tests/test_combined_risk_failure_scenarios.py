"""Combined fail-closed execution scenarios: pending orders, stale data and restart."""
from datetime import datetime, timedelta, timezone
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


def _fresh_account():
    return {
        "cash": 10000.0,
        "portfolio_value": 10000.0,
        "buying_power": 10000.0,
        "equity": 10000.0,
        "account_id": "paper-account-a",
        "account_data_fresh": True,
        "account_snapshot_at": datetime.now(timezone.utc).isoformat(),
    }


def _stale_quotes(*tickers):
    old = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    return {
        ticker: {"price": 100.0, "change_pct": 0.0, "timestamp": old}
        for ticker in tickers
    }


def test_pending_order_unknown_exposure_and_stale_quotes_block_all_new_orders(
    executor_env, monkeypatch
):
    executor = BrokerExecutor()
    submitted = []
    monkeypatch.setattr(executor, "get_account_status", _fresh_account)
    monkeypatch.setattr(executor, "get_positions", lambda: {})
    monkeypatch.setattr(
        executor,
        "_get_open_orders",
        lambda: [
            SimpleNamespace(
                id="pending-msft",
                symbol="MSFT",
                status="accepted",
                qty="10",
                filled_qty="0",
                limit_price=None,
                notional=None,
            )
        ],
    )
    monkeypatch.setattr(
        executor.alpaca_feed,
        "get_latest_quotes",
        lambda tickers: _stale_quotes(*tickers),
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
    assert executor.state.get("order_submission_unresolved", {}) == {}


def test_known_pending_order_does_not_allow_other_symbol_when_quotes_are_stale(
    executor_env, monkeypatch
):
    executor = BrokerExecutor()
    submitted = []
    monkeypatch.setattr(executor, "get_account_status", _fresh_account)
    monkeypatch.setattr(executor, "get_positions", lambda: {})
    monkeypatch.setattr(
        executor,
        "_get_open_orders",
        lambda: [
            SimpleNamespace(
                id="pending-msft",
                symbol="MSFT",
                status="accepted",
                qty="1",
                filled_qty="0",
                limit_price="100",
                notional=None,
            )
        ],
    )
    monkeypatch.setattr(
        executor.alpaca_feed,
        "get_latest_quotes",
        lambda tickers: _stale_quotes(*tickers),
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


def test_restart_with_unresolved_submission_and_broker_read_error_stops_before_market_data(
    executor_env, monkeypatch
):
    first = BrokerExecutor()
    first.state["order_submission_unresolved"] = {
        "AAPL": {
            "client_order_id": "uncertain-client-id",
            "side": "buy",
            "quantity": 10.0,
            "price": 100.0,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "reason": "submission_outcome_unconfirmed",
        }
    }
    first._save_state()

    restarted = BrokerExecutor()
    submitted = []
    account_reads = []
    quote_reads = []

    class UnavailableBroker:
        def get_order_by_client_id(self, client_order_id):
            raise TimeoutError("simulated broker read timeout")

        def submit_order(self, request):
            submitted.append(request)

    restarted.trading_client = UnavailableBroker()
    monkeypatch.setattr(
        restarted, "get_account_status", lambda: account_reads.append(True)
    )
    monkeypatch.setattr(
        restarted.alpaca_feed,
        "get_latest_quotes",
        lambda tickers: quote_reads.append(tickers) or {},
    )

    with pytest.raises(RuntimeError, match="requires reconciliation or terminal status"):
        restarted.execute_weights(
            PortfolioWeights(weights=np.array([0.1]), tickers=["AAPL"])
        )

    assert submitted == []
    assert account_reads == []
    assert quote_reads == []
    assert "AAPL" in restarted.state["order_submission_unresolved"]


def test_incomplete_position_snapshot_blocks_before_order_submission(
    executor_env, monkeypatch
):
    executor = BrokerExecutor()
    submitted = []
    monkeypatch.setattr(executor, "get_account_status", _fresh_account)
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [])
    monkeypatch.setattr(
        executor.trading_client,
        "get_all_positions",
        lambda: [
            SimpleNamespace(
                symbol="AAPL",
                qty="2",
                # market_value deliberately missing
                avg_entry_price="100",
                unrealized_pl="0",
            )
        ],
    )
    monkeypatch.setattr(
        executor.trading_client,
        "submit_order",
        lambda request: submitted.append(request),
    )

    with pytest.raises(RuntimeError, match="Unable to retrieve broker positions"):
        executor.execute_weights(
            PortfolioWeights(weights=np.array([0.1]), tickers=["AAPL"])
        )

    assert submitted == []


def test_non_liquidating_circuit_breaker_cannot_bypass_new_order_block(
    executor_env, monkeypatch
):
    executor = BrokerExecutor()
    submitted = []
    position_reads = []
    quote_reads = []
    monkeypatch.setattr(executor, "get_account_status", _fresh_account)
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [])
    monkeypatch.setattr(
        executor,
        "get_positions",
        lambda: position_reads.append(True) or {},
    )
    monkeypatch.setattr(
        executor.alpaca_feed,
        "get_latest_quotes",
        lambda tickers: quote_reads.append(tickers) or _stale_quotes(*tickers),
    )
    monkeypatch.setattr(
        executor.trading_client,
        "submit_order",
        lambda request: submitted.append(request),
    )

    result = executor.execute_weights(
        PortfolioWeights(
            weights=np.array([0.1]),
            tickers=["AAPL"],
            metadata={
                "circuit_breaker_triggered": True,
                "liquidate_on_circuit_breaker": False,
            },
        )
    )

    assert result["orders"] == []
    assert submitted == []
    assert position_reads == []
    assert quote_reads == []
