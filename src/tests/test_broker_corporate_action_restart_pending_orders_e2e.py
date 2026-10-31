from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np
import pytest

from src.execution.broker_executor import BrokerExecutor
from src.strategy.weight_interface import PortfolioWeights


def test_restart_with_unresolved_corporate_action_and_pending_order_blocks_execution(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "test-secret")
    state_path = tmp_path / "state" / "broker_state.json"
    monkeypatch.setenv("STMAA_BROKER_STATE_FILE", str(state_path))

    first = BrokerExecutor(paper_trading=True, configured_symbols=["AAPL", "MSFT"])
    first.state["corporate_action_reconciliation"] = {
        "last_activity_id": "spin-missing-symbol",
        "last_reconciled_at": "2026-10-30T12:00:00+00:00",
        "recent_events": {
            "spin-missing-symbol": {
                "id": "spin-missing-symbol",
                "activity_type": "SPIN",
                "symbol": None,
                "activity_sub_type": None,
                "date": "2026-10-30",
                "status": "executed",
            }
        },
        "unresolved_activity_ids": ["spin-missing-symbol"],
        "resolved_activity_symbols": {},
        "manual_resolution_history": [],
        "blocked_symbols": [],
        "pagination_checkpoint_seen": True,
    }
    first._save_state()

    # A new process must restore the unresolved blocker from durable state.
    executor = BrokerExecutor(paper_trading=True, configured_symbols=["AAPL", "MSFT"])
    submitted = []
    price_reads = []
    activity_reads = []

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
            "account_id": "restart-corporate-action-paper",
            "account_data_fresh": True,
            "account_snapshot_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    monkeypatch.setattr(
        executor,
        "get_positions",
        lambda: {"AAPL": {"qty": 1.0, "market_value": 100.0}},
    )
    monkeypatch.setattr(
        executor,
        "_get_open_orders",
        lambda: [
            SimpleNamespace(
                id="pending-msft",
                symbol="MSFT",
                status="accepted",
                qty="2",
                filled_qty="0",
                limit_price="100",
                notional=None,
            )
        ],
    )

    def read_activities(path, params):
        activity_reads.append((path, dict(params)))
        assert path == "/account/activities"
        return [
            {
                "id": "spin-missing-symbol",
                "activity_type": "SPIN",
                "symbol": None,
                "status": "executed",
                "date": "2026-10-30",
            }
        ]

    monkeypatch.setattr(executor.trading_client, "get", read_activities)
    monkeypatch.setattr(
        executor,
        "_get_prices_from_bars",
        lambda tickers: price_reads.append(tickers) or pytest.fail(
            "Market data must not be requested while a corporate action is unresolved"
        ),
    )
    monkeypatch.setattr(
        executor.trading_client,
        "submit_order",
        lambda request: submitted.append(request) or pytest.fail(
            "No broker order may be submitted while a corporate action is unresolved"
        ),
    )

    with pytest.raises(RuntimeError, match="manual reconciliation; refusing trading"):
        executor.execute_weights(
            PortfolioWeights(weights=np.array([0.1]), tickers=["AAPL"])
        )

    assert len(activity_reads) == 1
    assert activity_reads[0][1]["direction"] == "desc"
    assert price_reads == []
    assert submitted == []
    assert executor.state["corporate_action_reconciliation"]["unresolved_activity_ids"] == [
        "spin-missing-symbol"
    ]
    assert executor.state_file.exists()
