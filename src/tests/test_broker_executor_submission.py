from types import SimpleNamespace

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

    # The guard must run before any account or broker API access.
    with pytest.raises(RuntimeError, match="manual broker reconciliation"):
        executor.execute_weights(SimpleNamespace(tickers=["MSFT"], weights=[0.1]))
