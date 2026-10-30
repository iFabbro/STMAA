"""Malformed persisted submission intents must remain fail-closed after restart."""
from copy import deepcopy
from types import SimpleNamespace

import numpy as np
import pytest

from src.execution.broker_executor import BrokerExecutor
from src.strategy.weight_interface import PortfolioWeights


@pytest.mark.parametrize(
    ("intent_overrides", "expected_lookup"),
    [
        ({"client_order_id": ""}, False),
        ({"client_order_id": None}, False),
        ({"quantity": "NaN"}, True),
        ({"quantity": "inf"}, True),
        ({"quantity": -10.0}, True),
        ({"side": "hold"}, True),
    ],
)
def test_malformed_persisted_submission_intent_cannot_resume_execution(
    monkeypatch, tmp_path, intent_overrides, expected_lookup
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "test-secret")
    monkeypatch.setenv(
        "STMAA_BROKER_STATE_FILE",
        str(tmp_path / "state" / "broker_state.json"),
    )

    first = BrokerExecutor(paper_trading=True)
    intent = {
        "client_order_id": "expected-client-id",
        "side": "buy",
        "quantity": 10.0,
        "price": 100.0,
        "pre_order_position_qty": 0.0,
        "created_at": "2026-10-30T12:00:00+00:00",
        "reason": "submission_outcome_unconfirmed",
    }
    intent.update(intent_overrides)
    first.state["order_submission_unresolved"] = {"AAPL": intent}
    first._save_state()

    restarted = BrokerExecutor(paper_trading=True)
    broker_order = SimpleNamespace(
        id="broker-order-123",
        client_order_id="expected-client-id",
        symbol="AAPL",
        side="buy",
        qty="10",
        filled_qty="0",
        status="accepted",
    )
    lookups = []
    submissions = []

    def lookup(client_order_id):
        lookups.append(client_order_id)
        return broker_order

    monkeypatch.setattr(restarted.trading_client, "get_order_by_client_id", lookup)
    monkeypatch.setattr(
        restarted.trading_client,
        "submit_order",
        lambda request: submissions.append(request),
    )
    monkeypatch.setattr(
        restarted,
        "get_account_status",
        lambda: (_ for _ in ()).throw(
            AssertionError("Malformed durable intent must block before account sizing")
        ),
    )

    with pytest.raises(
        RuntimeError,
        match="requires reconciliation or terminal status",
    ):
        restarted.execute_weights(
            PortfolioWeights(weights=np.array([0.1]), tickers=["AAPL"])
        )

    assert bool(lookups) is expected_lookup
    assert submissions == []
    assert restarted.state["order_submission_unresolved"]["AAPL"] == intent
    assert restarted.state["order_submission_confirmed"] == {}
