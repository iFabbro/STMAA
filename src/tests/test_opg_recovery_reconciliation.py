"""Read-only restart recovery and OPG fill/position reconciliation tests."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.execution.broker_executor import BrokerExecutor


def _executor(batch, broker_order, positions=None):
    executor = BrokerExecutor.__new__(BrokerExecutor)
    executor.state = {
        "account_mode": "paper",
        "account_id": "paper-account-1",
        "opg_batches": {"batch-1": batch},
    }
    executor.paper_trading = True
    executor._current_account_identity_verified = True
    executor._save_state = Mock()
    executor.trading_client = SimpleNamespace(
        get_order_by_client_id=Mock(return_value=broker_order),
        get_order_by_id=Mock(return_value=broker_order),
        submit_order=Mock(side_effect=AssertionError("recovery must never submit")),
    )
    executor.get_positions = Mock(return_value=positions or {})
    return executor


def _batch(status="submission_pending", *, order_status="submission_pending", order_id=None):
    return {
        "account_id": "paper-account-1",
        "account_mode": "paper",
        "status": status,
        "orders": {
            "AAPL": {
                "symbol": "AAPL", "side": "buy", "quantity": 2,
                "requested_quantity": 2, "pre_order_position_qty": 3,
                "client_order_id": "stable-aapl", "order_id": order_id,
                "order_type": "market", "time_in_force": "opg",
                "status": order_status, "filled_qty": 0,
            }
        },
    }


def _order(*, status="filled", filled="2", client_id="stable-aapl", order_id="broker-1", side="buy"):
    return SimpleNamespace(
        id=order_id, client_order_id=client_id, symbol="AAPL", side=side,
        qty="2", type="market", time_in_force="opg", status=status,
        filled_qty=filled, filled_avg_price="100" if str(filled) not in {"0", "0.0"} else None,
    )


def test_filled_opg_order_completes_only_after_fresh_position_match():
    executor = _executor(_batch(), _order(), {"AAPL": {"qty": 5}})
    report = executor._recover_opg_batches()
    record = executor.state["opg_batches"]["batch-1"]
    assert report["batches_completed"] == 1
    assert record["status"] == "completed"
    assert record["orders"]["AAPL"]["filled_qty"] == 2
    assert record["orders"]["AAPL"]["position_reconciled"] is True
    executor.trading_client.submit_order.assert_not_called()
    executor.get_positions.assert_called_once()


def test_canceled_partially_filled_order_reconciles_actual_fill_without_residual_retry():
    executor = _executor(_batch(), _order(status="canceled", filled="1"), {"AAPL": {"qty": 4}})
    executor._recover_opg_batches()
    record = executor.state["opg_batches"]["batch-1"]
    assert record["status"] == "completed"
    assert record["orders"]["AAPL"]["status"] == "canceled"
    assert record["orders"]["AAPL"]["filled_qty"] == 1
    assert record["orders"]["AAPL"]["position_reconciled"] is True
    executor.trading_client.submit_order.assert_not_called()


def test_active_partial_fill_remains_blocking_and_does_not_read_positions_or_retry():
    executor = _executor(_batch(), _order(status="partially_filled", filled="1"), {"AAPL": {"qty": 4}})
    report = executor._recover_opg_batches()
    record = executor.state["opg_batches"]["batch-1"]
    assert report["batches_blocked"] == 1
    assert record["status"] == "reconciliation_pending"
    assert record["orders"]["AAPL"]["filled_qty"] == 1
    executor.get_positions.assert_not_called()
    executor.trading_client.submit_order.assert_not_called()


def test_terminal_fill_position_mismatch_persists_unresolved_block():
    executor = _executor(_batch(), _order(), {"AAPL": {"qty": 4}})
    with pytest.raises(RuntimeError, match="position reconciliation unresolved"):
        executor._recover_opg_batches()
    record = executor.state["opg_batches"]["batch-1"]
    assert record["status"] == "unresolved"
    assert "expected 5" in record["blocking_reason"]
    executor.trading_client.submit_order.assert_not_called()


def test_order_not_submitted_before_restart_is_skipped_without_catch_up():
    batch = _batch(order_status="not_submitted")
    executor = _executor(batch, _order())
    executor._recover_opg_batches()
    record = executor.state["opg_batches"]["batch-1"]
    assert record["status"] == "unresolved"
    assert record["orders"]["AAPL"]["status"] == "skipped_after_restart"
    executor.trading_client.get_order_by_client_id.assert_not_called()
    executor.trading_client.submit_order.assert_not_called()
    executor.get_positions.assert_not_called()


def test_account_identity_mismatch_blocks_opg_recovery_before_broker_order_lookup():
    executor = _executor(_batch(), _order())
    executor.state["account_id"] = "different-account"
    with pytest.raises(RuntimeError, match="account identity"):
        executor._recover_opg_batches()
    executor.trading_client.get_order_by_client_id.assert_not_called()


def test_unknown_broker_status_is_persisted_as_unresolved():
    executor = _executor(_batch(), _order(status="mystery"))
    with pytest.raises(RuntimeError, match="status is unknown"):
        executor._recover_opg_batches()
    assert executor.state["opg_batches"]["batch-1"]["status"] == "unresolved"
    executor.trading_client.submit_order.assert_not_called()


def test_terminal_sell_fill_reconciles_reduced_position():
    batch = _batch()
    batch["orders"]["AAPL"].update({"side": "sell", "pre_order_position_qty": 5})
    executor = _executor(batch, _order(side="sell"), {"AAPL": {"qty": 3}})
    executor._recover_opg_batches()
    record = executor.state["opg_batches"]["batch-1"]
    assert record["status"] == "completed"
    assert record["orders"]["AAPL"]["side"] == "sell"
    assert record["orders"]["AAPL"]["position_reconciled"] is True


def test_fractional_broker_fill_quantity_fails_closed():
    executor = _executor(_batch(), _order(filled="1.5"), {"AAPL": {"qty": 4.5}})
    with pytest.raises(RuntimeError, match="fill quantity is invalid"):
        executor._recover_opg_batches()
    assert executor.state["opg_batches"]["batch-1"]["status"] == "unresolved"
    executor.trading_client.submit_order.assert_not_called()


def test_known_broker_order_id_mismatch_fails_closed():
    executor = _executor(_batch(order_id="expected-order-id"), _order(order_id="different-order-id"))
    with pytest.raises(RuntimeError, match="order ID changed"):
        executor._recover_opg_batches()
    assert executor.state["opg_batches"]["batch-1"]["status"] == "unresolved"
    executor.trading_client.get_order_by_id.assert_called_once_with("expected-order-id")
    executor.trading_client.submit_order.assert_not_called()
