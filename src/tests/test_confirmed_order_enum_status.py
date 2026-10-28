from types import SimpleNamespace
from enum import Enum

import pytest

from src.execution.broker_executor import BrokerExecutor


@pytest.fixture
def alpaca_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "test-secret")





def test_confirmed_order_enum_status_is_normalized_before_reconciliation(alpaca_env, monkeypatch):
    from enum import Enum
    from types import SimpleNamespace

    class BrokerStatus(Enum):
        FILLED = "filled"

    executor = BrokerExecutor()
    executor.state["order_submission_confirmed"] = {
        "client-order-1": {
            "ticker": "AAPL",
            "order_id": "broker-order-1",
            "client_order_id": "client-order-1",
            "status": "accepted",
        }
    }
    monkeypatch.setattr(
        executor.trading_client,
        "get_order_by_id",
        lambda order_id: SimpleNamespace(status=BrokerStatus.FILLED),
    )

    executor._refresh_confirmed_submission_statuses()

    assert executor.state["order_submission_confirmed"]["client-order-1"]["status"] == "filled"
    assert executor._status_text(BrokerStatus.FILLED) == "filled"
