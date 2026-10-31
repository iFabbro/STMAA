import numpy as np
import pytest

from src.execution.broker_executor import BrokerExecutor
from src.strategy.weight_interface import PortfolioWeights


@pytest.mark.parametrize(
    ("activity_symbol", "positions", "expected_state_key", "expected_value"),
    [
        (
            None,
            {"AAPL": {"qty": 1.0, "market_value": 100.0}},
            "unresolved_activity_ids",
            ["spin-missing-symbol"],
        ),
        (
            "SPIN",
            {"SPIN": {"qty": 2.0, "market_value": 200.0}},
            "blocked_symbols",
            ["SPIN"],
        ),
    ],
)
def test_broker_execution_uses_real_corporate_action_reconciler_fail_closed(
    monkeypatch,
    tmp_path,
    activity_symbol,
    positions,
    expected_state_key,
    expected_value,
):
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "test-secret")
    monkeypatch.setenv(
        "STMAA_BROKER_STATE_FILE",
        str(tmp_path / "execution" / "broker_state.json"),
    )

    executor = BrokerExecutor(
        paper_trading=True,
        configured_symbols=["AAPL"],
    )

    # Isolate unrelated order-recovery stages while exercising the production
    # corporate-action reconciler through the real broker-executor boundary.
    monkeypatch.setattr(executor, "_reconcile_unresolved_submissions", lambda: None)
    monkeypatch.setattr(executor, "_refresh_confirmed_submission_statuses", lambda: None)
    monkeypatch.setattr(executor, "_reconcile_terminal_order_positions", lambda: None)
    monkeypatch.setattr(
        executor,
        "get_account_status",
        lambda: {
            "cash": 10000.0,
            "portfolio_value": 10000.0,
            "buying_power": 10000.0,
            "equity": 10000.0,
            "account_data_fresh": True,
        },
    )
    monkeypatch.setattr(executor, "get_positions", lambda: positions)
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [])

    activity = {
        "id": "spin-missing-symbol",
        "activity_type": "SPIN",
        "symbol": activity_symbol,
        "status": "executed",
        "date": "2026-10-30",
    }
    requests = []

    def read_activities(path, params):
        requests.append((path, dict(params)))
        assert path == "/account/activities"
        return [activity]

    monkeypatch.setattr(executor.trading_client, "get", read_activities)
    monkeypatch.setattr(
        executor,
        "_get_prices_from_bars",
        lambda _tickers: pytest.fail(
            "Market data must not be requested after corporate-action reconciliation blocks"
        ),
    )
    monkeypatch.setattr(
        executor.trading_client,
        "submit_order",
        lambda _request: pytest.fail(
            "Broker orders must not be submitted after corporate-action reconciliation blocks"
        ),
    )

    with pytest.raises(RuntimeError, match="(?i)corporate.action"):
        executor.execute_weights(
            PortfolioWeights(weights=np.array([0.5]), tickers=["AAPL"])
        )

    assert len(requests) == 1
    reconciliation = executor.state["corporate_action_reconciliation"]
    assert reconciliation[expected_state_key] == expected_value
    assert executor.state_file.exists()
