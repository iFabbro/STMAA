"""End-to-end fail-closed checks for malformed or implausibly timed quotes."""
from datetime import datetime, timezone
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
    monkeypatch.setattr(
        executor,
        "get_account_status",
        lambda: {
            "cash": 10000.0,
            "portfolio_value": 10000.0,
            "buying_power": 10000.0,
            "equity": 10000.0,
            "account_data_fresh": True,
            "account_id": "invalid-quote-test",
        },
    )
    monkeypatch.setattr(executor, "get_positions", lambda: {})
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [])
    return executor


@pytest.mark.parametrize(
    "quote",
    [
        pytest.param(
            {"price": 100.0, "timestamp": "2026-10-28T18:00:00"},
            id="naive-timestamp",
        ),
        pytest.param(
            {"price": 100.0, "timestamp": "not-a-timestamp"},
            id="malformed-timestamp",
        ),
        pytest.param(
            {"price": 100.0, "timestamp": "2099-01-01T00:00:00+00:00"},
            id="implausibly-future-timestamp",
        ),
        pytest.param(
            {"price": float("nan"), "timestamp": datetime.now(timezone.utc).isoformat()},
            id="non-finite-price",
        ),
        pytest.param(
            {"price": 100.0, "timestamp": None},
            id="missing-timestamp",
        ),
    ],
)
def test_invalid_quote_snapshot_blocks_target_execution_without_submission(
    isolated_executor, monkeypatch, quote
):
    executor = isolated_executor
    submitted = []
    monkeypatch.setattr(
        executor.alpaca_feed,
        "get_latest_quotes",
        lambda tickers: {"AAPL": quote},
    )
    monkeypatch.setattr(
        executor.trading_client,
        "submit_order",
        lambda request: submitted.append(request),
    )

    with pytest.raises(RuntimeError):
        executor.execute_weights(
            PortfolioWeights(weights=np.array([0.10]), tickers=["AAPL"])
        )

    assert submitted == []
