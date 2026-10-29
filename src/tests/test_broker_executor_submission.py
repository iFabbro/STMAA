from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.execution.broker_executor import BrokerExecutor


def test_total_orders_counts_only_broker_acknowledged_orders():
    orders = [
        {"order_id": "accepted-1", "status": "accepted"},
        {"order_id": "filled-2", "status": "filled"},
        {"order_id": None, "status": "failed"},
        {"order_id": None, "status": "unresolved"},
        {"order_id": "rejected-3", "status": "rejected"},
    ]

    assert BrokerExecutor._count_submitted_orders(orders) == 3


def test_execute_weights_fails_closed_when_submission_outcome_is_unresolved():
    executor = BrokerExecutor.__new__(BrokerExecutor)
    executor.state = {
        "order_submission_unresolved": {
            "AAPL": {
                "client_order_id": "client-order-123",
                "reason": "submission_outcome_unconfirmed",
            }
        }
    }
    executor.trading_client = SimpleNamespace(
        get_order_by_client_id=Mock(side_effect=RuntimeError("broker unavailable"))
    )

    # The guard must run before target execution or any new order submission.
    with pytest.raises(RuntimeError, match="refusing further orders"):
        executor.execute_weights(SimpleNamespace(tickers=["MSFT"], weights=[0.1]))


def test_acknowledged_order_is_persisted_atomically_with_intent_removal():
    executor = BrokerExecutor.__new__(BrokerExecutor)
    executor.state = {
        "order_submission_unresolved": {
            "AAPL": {
                "client_order_id": "client-order-123",
                "side": "buy",
                "quantity": 2,
                "reason": "submission_outcome_unconfirmed",
            }
        }
    }
    snapshots = []
    executor._save_state = lambda: snapshots.append(deepcopy(executor.state))

    executor._record_confirmed_submission(
        "AAPL",
        {
            "ticker": "AAPL",
            "client_order_id": "client-order-123",
            "order_id": "broker-order-456",
            "status": "accepted",
            "quantity": 2,
        },
    )

    assert len(snapshots) == 1
    persisted = snapshots[0]
    assert "AAPL" not in persisted["order_submission_unresolved"]
    assert persisted["order_submission_confirmed"]["client-order-123"]["order_id"] == "broker-order-456"


def test_restart_waits_for_broker_visibility_then_reconciles_without_resubmission():
    executor = BrokerExecutor.__new__(BrokerExecutor)
    intent = {
        "client_order_id": "client-order-123",
        "side": "buy",
        "quantity": 2,
        "price": 100,
        "created_at": "2026-10-25T08:00:00+00:00",
        "reason": "submission_outcome_unconfirmed",
    }
    executor.state = {"order_submission_unresolved": {"AAPL": intent}}
    broker_order = SimpleNamespace(
        id="broker-order-456",
        client_order_id="client-order-123",
        symbol="AAPL",
        side="buy",
        qty="2",
        filled_qty="0",
        status="accepted",
    )
    executor.trading_client = SimpleNamespace(
        get_order_by_client_id=Mock(
            side_effect=[RuntimeError("not visible yet"), broker_order]
        ),
        get_order_by_id=Mock(return_value=broker_order),
        submit_order=Mock(),
    )
    executor.logger = Mock()
    persisted = []
    executor._save_state = lambda: persisted.append(deepcopy(executor.state))

    # First restart: the broker lookup is not yet consistent, so execution stays blocked.
    with pytest.raises(RuntimeError, match="refusing further orders"):
        executor.execute_weights(SimpleNamespace(tickers=["AAPL"], weights=[0.5]))
    assert executor.state["order_submission_unresolved"]["AAPL"]["client_order_id"] == "client-order-123"
    assert executor.trading_client.submit_order.call_count == 0

    # Next restart/read: find the original order by client ID and archive its broker ID.
    with pytest.raises(RuntimeError, match="refusing further orders"):
        executor.execute_weights(SimpleNamespace(tickers=["AAPL"], weights=[0.5]))

    assert executor.state["order_submission_unresolved"] == {}
    confirmed = executor.state["order_submission_confirmed"]["client-order-123"]
    assert confirmed["order_id"] == "broker-order-456"
    assert confirmed["reconciled"] is True
    assert executor.trading_client.submit_order.call_count == 0
    assert persisted[-1]["order_submission_confirmed"]["client-order-123"]["order_id"] == "broker-order-456"


def test_failed_confirmation_persistence_restores_durable_unresolved_intent():
    executor = BrokerExecutor.__new__(BrokerExecutor)
    original_intent = {
        "client_order_id": "client-order-123",
        "side": "buy",
        "quantity": 2,
        "reason": "submission_outcome_unconfirmed",
    }
    executor.state = {"order_submission_unresolved": {"AAPL": original_intent}}

    def fail_save():
        raise OSError("disk full")

    executor._save_state = fail_save

    with pytest.raises(OSError, match="disk full"):
        executor._record_confirmed_submission(
            "AAPL",
            {
                "ticker": "AAPL",
                "client_order_id": "client-order-123",
                "order_id": "broker-order-456",
                "status": "accepted",
            },
        )

    assert executor.state["order_submission_unresolved"]["AAPL"] == original_intent
    assert "order_submission_confirmed" not in executor.state


def test_confirmed_order_status_is_refreshed_from_broker_and_persisted():
    executor = BrokerExecutor.__new__(BrokerExecutor)
    executor.state = {
        "order_submission_unresolved": {},
        "order_submission_confirmed": {
            "client-order-123": {
                "ticker": "AAPL",
                "client_order_id": "client-order-123",
                "order_id": "broker-order-456",
                "status": "accepted",
                "side": "buy",
                "quantity": 2,
                "pre_order_position_qty": 0,
            }
        },
    }
    executor.trading_client = SimpleNamespace(
        get_order_by_id=Mock(
            return_value=SimpleNamespace(
                id="broker-order-456",
                client_order_id="client-order-123",
                symbol="AAPL",
                side="buy",
                qty="2",
                filled_qty="2",
                status="filled",
            )
        )
    )
    persisted = []
    executor._save_state = lambda: persisted.append(deepcopy(executor.state))

    executor._refresh_confirmed_submission_statuses()

    executor.trading_client.get_order_by_id.assert_called_once_with("broker-order-456")
    assert executor.state["order_submission_confirmed"]["client-order-123"]["status"] == "filled"
    assert persisted[-1]["order_submission_confirmed"]["client-order-123"]["status"] == "filled"


def test_confirmed_order_status_read_failure_keeps_fail_closed_state():
    executor = BrokerExecutor.__new__(BrokerExecutor)
    executor.state = {
        "order_submission_confirmed": {
            "client-order-123": {
                "ticker": "AAPL",
                "client_order_id": "client-order-123",
                "order_id": "broker-order-456",
                "status": "accepted",
            }
        }
    }
    executor.trading_client = SimpleNamespace(
        get_order_by_id=Mock(side_effect=RuntimeError("broker unavailable"))
    )
    executor.logger = Mock()
    executor._save_state = Mock()

    executor._refresh_confirmed_submission_statuses()

    assert executor.state["order_submission_confirmed"]["client-order-123"]["status"] == "accepted"
    executor._save_state.assert_not_called()
    executor.logger.warning.assert_called_once()



@pytest.mark.parametrize(
    "field, value",
    [
        ("id", "different-broker-order"),
        ("client_order_id", "different-client-order"),
    ],
)
def test_terminal_confirmed_order_identity_mismatch_blocks_reconciliation(
    field, value
):
    """A terminal lookup must match both persisted broker and client order IDs."""
    executor = BrokerExecutor.__new__(BrokerExecutor)
    stored = {
        "ticker": "AAPL",
        "client_order_id": "client-order-123",
        "order_id": "broker-order-456",
        "status": "accepted",
        "side": "buy",
        "quantity": 2,
        "pre_order_position_qty": 0,
    }
    executor.state = {
        "order_submission_confirmed": {"client-order-123": deepcopy(stored)}
    }
    broker_order = {
        "id": "broker-order-456",
        "client_order_id": "client-order-123",
        "symbol": "AAPL",
        "side": "buy",
        "qty": "2",
        "filled_qty": "2",
        "status": "filled",
    }
    broker_order[field] = value
    executor.trading_client = SimpleNamespace(
        get_order_by_id=Mock(return_value=SimpleNamespace(**broker_order))
    )
    executor._save_state = Mock()

    with pytest.raises(RuntimeError, match="identity differs"):
        executor._refresh_confirmed_submission_statuses()

    assert executor.state["order_submission_confirmed"]["client-order-123"] == stored
    executor._save_state.assert_not_called()
