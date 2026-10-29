"""End-to-end regression checks for restart recovery and fail-closed risk gates."""
from types import SimpleNamespace

import numpy as np
import pytest

from src.execution.broker_executor import BrokerExecutor
from src.strategy.weight_interface import PortfolioWeights


@pytest.fixture
def audit_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "test-secret")
    monkeypatch.setenv(
        "STMAA_BROKER_STATE_FILE",
        str(tmp_path / "state" / "broker_state.json"),
    )


def test_restart_reconciles_ambiguous_submission_and_blocks_new_orders(audit_env):
    first = BrokerExecutor()
    first.state["order_submission_unresolved"] = {
        "AAPL": {
            "client_order_id": "durable-client-id",
            "side": "buy",
            "quantity": 2.0,
            "price": 100.0,
            "created_at": "2026-10-26T12:00:00+00:00",
            "reason": "submission_outcome_unconfirmed",
        }
    }
    first._save_state()

    restarted = BrokerExecutor()
    submitted = []

    class ReconciliationClient:
        def get_order_by_client_id(self, client_order_id):
            assert client_order_id == "durable-client-id"
            return SimpleNamespace(
                id="broker-order-1",
                client_order_id="durable-client-id",
                status="accepted",
                symbol="AAPL",
                side="buy",
                qty="2.0",
            )

        def get_order_by_id(self, order_id):
            assert order_id == "broker-order-1"
            return SimpleNamespace(
                id="broker-order-1",
                client_order_id="durable-client-id",
                status="accepted",
                symbol="AAPL",
                side="buy",
                qty="2.0",
                filled_qty="0",
            )

        def submit_order(self, request):
            submitted.append(request)
            raise AssertionError("A new order must not be submitted while prior order is active")

    restarted.trading_client = ReconciliationClient()

    with pytest.raises(RuntimeError, match="requires reconciliation or terminal status"):
        restarted.execute_weights(
            PortfolioWeights(weights=np.array([0.1]), tickers=["AAPL"])
        )

    assert submitted == []
    assert restarted.state["order_submission_unresolved"] == {}
    assert restarted.state["order_submission_confirmed"]["durable-client-id"]["order_id"] == "broker-order-1"


def test_incomplete_account_snapshot_blocks_execution_before_submission(audit_env, monkeypatch):
    executor = BrokerExecutor()
    submitted = []

    monkeypatch.setattr(
        executor,
        "get_account_status",
        lambda: {
            "account_data_fresh": False,
            "equity": None,
            "portfolio_value": None,
            "buying_power": None,
        },
    )
    monkeypatch.setattr(
        executor.trading_client,
        "submit_order",
        lambda request: submitted.append(request),
    )

    with pytest.raises(RuntimeError, match="Cannot verify current broker account equity"):
        executor.execute_weights(
            PortfolioWeights(weights=np.array([0.1]), tickers=["AAPL"])
        )

    assert submitted == []


def test_nonterminal_order_identity_mismatch_blocks_reconciliation(audit_env):
    executor = BrokerExecutor()
    executor.state["order_submission_confirmed"] = {
        "persisted-client-id": {
            "ticker": "AAPL",
            "side": "buy",
            "quantity": 2.0,
            "order_id": "persisted-broker-id",
            "client_order_id": "persisted-client-id",
            "status": "accepted",
            "filled_qty": 0.0,
            "pre_order_position_qty": 0.0,
        }
    }
    executor._save_state()

    class MismatchedClient:
        def get_order_by_id(self, order_id):
            assert order_id == "persisted-broker-id"
            return SimpleNamespace(
                id="different-broker-id",
                client_order_id="persisted-client-id",
                status="accepted",
                symbol="AAPL",
                side="buy",
                qty="2.0",
                filled_qty="0",
            )

    executor.trading_client = MismatchedClient()

    with pytest.raises(RuntimeError, match="Non-terminal broker order identity differs"):
        executor._refresh_confirmed_submission_statuses()

    stored = executor.state["order_submission_confirmed"]["persisted-client-id"]
    assert stored["order_id"] == "persisted-broker-id"
    assert stored["status"] == "accepted"
    assert stored.get("position_reconciled") is not True

def test_nonterminal_overfilled_order_blocks_reconciliation(audit_env):
    executor = BrokerExecutor()
    executor.state["order_submission_confirmed"] = {
        "persisted-client-id": {
            "ticker": "AAPL",
            "side": "buy",
            "quantity": 2.0,
            "order_id": "persisted-broker-id",
            "client_order_id": "persisted-client-id",
            "status": "accepted",
            "filled_qty": 0.0,
            "pre_order_position_qty": 0.0,
        }
    }
    executor._save_state()

    class OverfilledClient:
        def get_order_by_id(self, order_id):
            assert order_id == "persisted-broker-id"
            return SimpleNamespace(
                id="persisted-broker-id",
                client_order_id="persisted-client-id",
                status="accepted",
                symbol="AAPL",
                side="buy",
                qty="2.0",
                filled_qty="3.0",
            )

    executor.trading_client = OverfilledClient()

    with pytest.raises(
        RuntimeError,
        match="Broker order reports filled quantity greater than requested quantity",
    ):
        executor._refresh_confirmed_submission_statuses()

    stored = executor.state["order_submission_confirmed"]["persisted-client-id"]
    assert stored["status"] == "accepted"
    assert stored["filled_qty"] == 0.0
    assert stored.get("position_reconciled") is not True

