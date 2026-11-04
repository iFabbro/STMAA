"""Executable RW-08.3 contract tests.

These strict xfails are intentional in PR A: PR B/C implement the API and
remove the markers as each contract becomes executable. All broker effects
are mocked; this module must never contact Alpaca or submit real orders.
"""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


PENDING_IMPLEMENTATION = pytest.mark.xfail(
    strict=True,
    reason="RW-08.3 contract is staged test-first; implementation lands in PR B/C",
)


def _opg_api():
    from src.execution.opg_execution import (
        build_opg_batch_id,
        build_opg_client_order_id,
        build_opg_order_request,
    )

    return build_opg_batch_id, build_opg_client_order_id, build_opg_order_request


def test_opg_order_request_uses_market_opg():
    from alpaca.trading.enums import OrderSide, TimeInForce

    _, _, build_order = _opg_api()
    request = build_order("AAPL", 3, "stmaa-opg-0123456789abcdef")

    assert request.symbol == "AAPL"
    assert request.qty == 3
    assert request.side is OrderSide.BUY
    assert request.time_in_force is TimeInForce.OPG
    assert request.client_order_id == "stmaa-opg-0123456789abcdef"


@pytest.mark.parametrize("quantity", [0, -1, 1.5, True, False, "1.5"])
def test_opg_order_rejects_non_positive_or_fractional_quantity(quantity):
    _, _, build_order = _opg_api()
    with pytest.raises((TypeError, ValueError)):
        build_order("AAPL", quantity, "stmaa-opg-0123456789abcdef")


def test_opg_client_order_id_is_deterministic_and_bounded():
    _, build_client_id, _ = _opg_api()
    first = build_client_id("a" * 64, "AAPL")
    second = build_client_id("a" * 64, "AAPL")

    assert first == second
    assert len(first) <= 48
    assert first != build_client_id("a" * 64, "MSFT")


def test_opg_batch_is_unique_per_signal_identity():
    build_batch_id, _, _ = _opg_api()
    identity = {
        "account_id": "paper-account-1",
        "signal_bar_at": "2026-11-03T21:00:00Z",
        "target_session": "2026-11-04",
        "model_contract_version": 7,
        "execution_sizing_contract": "opg_moo_integer_floor_v1",
    }

    batch_id = build_batch_id(**identity)
    assert batch_id == build_batch_id(**identity)
    assert batch_id != build_batch_id(**{**identity, "target_session": "2026-11-05"})
    assert batch_id != build_batch_id(**{**identity, "account_id": "paper-account-2"})


def test_opg_batch_record_contains_immutable_sizing_snapshot():
    from src.execution.opg_sizing import build_opg_sizing_plan
    from src.execution.opg_execution import build_opg_batch_record

    plan = build_opg_sizing_plan(
        equity=1000,
        target_weights={"AAPL": 0.2},
        close_prices={"AAPL": 100},
        current_positions={"AAPL": 0},
    )
    record = build_opg_batch_record(
        account_id="paper-account-1",
        signal_bar_at="2026-11-03T21:00:00Z",
        target_session="2026-11-04",
        plan=plan,
    )

    assert record["plan"] == plan.to_dict()
    assert record["account_mode"] == "paper"
    assert record["orders"]["AAPL"]["quantity"] == 2
    json.dumps(record)


def test_opg_state_v1_migration_preserves_existing_orders_and_safety_maps(tmp_path, monkeypatch):
    from src.execution.broker_executor import BrokerExecutor

    state_path = tmp_path / "broker_state.json"
    legacy = {
        "state_schema_version": 1,
        "state_revision": 7,
        "account_mode": "paper",
        "account_id": "paper-account-1",
        "order_submission_unresolved": {"AAPL": {"client_order_id": "day-existing"}},
        "order_submission_confirmed": {"old-day-id": {"order_id": "broker-old"}},
        "emergency_liquidation_pending": {},
        "emergency_liquidation_unresolved": {},
        "corporate_action_reconciliation": {"activity-1": {"status": "pending"}},
    }
    state_path.write_text(json.dumps(legacy), encoding="utf-8")
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "test-secret")
    monkeypatch.setenv("STMAA_BROKER_STATE_FILE", str(state_path))

    executor = BrokerExecutor(paper_trading=True)
    migrated = json.loads(state_path.read_text(encoding="utf-8"))

    assert migrated["state_schema_version"] == 2
    assert migrated["state_revision"] > legacy["state_revision"]
    assert migrated["opg_batches"] == {}
    for key in (
        "order_submission_unresolved",
        "order_submission_confirmed",
        "emergency_liquidation_pending",
        "emergency_liquidation_unresolved",
        "corporate_action_reconciliation",
    ):
        assert migrated[key] == legacy[key]
    assert executor.state["opg_batches"] == {}


def test_opg_executor_rejects_non_paper_mode_without_submission():
    from src.execution.broker_executor import BrokerExecutor

    executor = BrokerExecutor.__new__(BrokerExecutor)
    executor.state = {"account_mode": "live", "opg_batches": {}}
    executor.trading_client = SimpleNamespace(
        submit_order=Mock(side_effect=AssertionError("must not submit"))
    )

    with pytest.raises(RuntimeError, match="Paper"):
        executor.execute_opg_plan(
            object(),
            signal_bar_at="2026-11-03T21:00:00Z",
            target_session="2026-11-04",
        )
    executor.trading_client.submit_order.assert_not_called()


def test_opg_batch_is_persisted_before_first_submission(monkeypatch):
    from src.execution.broker_executor import BrokerExecutor

    executor = BrokerExecutor.__new__(BrokerExecutor)
    executor.state = {"account_mode": "paper", "account_id": "paper-account-1", "opg_batches": {}}
    events = []
    executor._save_state = lambda: events.append("persist")

    class BrokerMock:
        def submit_order(self, request):
            events.append("submit")
            return SimpleNamespace(
                id="broker-1", status="accepted", client_order_id=request.client_order_id,
                symbol=request.symbol, side=request.side, qty=str(request.qty),
                type="market", time_in_force="opg", filled_qty="0",
            )

    executor.trading_client = BrokerMock()
    from src.execution.opg_sizing import build_opg_sizing_plan

    plan = build_opg_sizing_plan(
        equity=1000, target_weights={"AAPL": 0.2},
        close_prices={"AAPL": 100}, current_positions={"AAPL": 0},
    )
    executor.execute_opg_plan(
        plan, signal_bar_at="2026-11-03T21:00:00Z", target_session="2026-11-04"
    )
    assert events.index("persist") < events.index("submit")


def test_opg_duplicate_batch_returns_existing_record_without_resubmission():
    from src.execution.broker_executor import BrokerExecutor
    from src.execution.opg_sizing import build_opg_sizing_plan

    executor = BrokerExecutor.__new__(BrokerExecutor)
    executor.state = {"account_mode": "paper", "account_id": "paper-account-1", "opg_batches": {}}
    executor._save_state = Mock()
    broker = SimpleNamespace(submit_order=Mock(side_effect=lambda request: SimpleNamespace(
        id="broker-1", status="accepted", client_order_id=request.client_order_id,
        symbol=request.symbol, side=request.side, qty=str(request.qty), type="market",
        time_in_force="opg", filled_qty="0", filled_avg_price=None,
    )))
    executor.trading_client = broker
    plan = build_opg_sizing_plan(
        equity=1000, target_weights={"AAPL": 0.2},
        close_prices={"AAPL": 100}, current_positions={"AAPL": 0},
    )
    first = executor.execute_opg_plan(
        plan, signal_bar_at="2026-11-03T21:00:00Z", target_session="2026-11-04"
    )
    second = executor.execute_opg_plan(
        plan, signal_bar_at="2026-11-03T21:00:00Z", target_session="2026-11-04"
    )
    assert first["batch_id"] == second["batch_id"]
    broker.submit_order.assert_called_once()


@PENDING_IMPLEMENTATION
def test_opg_restart_reconciles_without_resubmission():
    from src.execution.broker_executor import BrokerExecutor

    executor = BrokerExecutor.__new__(BrokerExecutor)
    executor.state = {
        "account_mode": "paper",
        "opg_batches": {
            "batch-1": {
                "status": "submission_pending",
                "orders": {"AAPL": {"client_order_id": "stable-id"}},
            }
        },
    }
    broker_order = SimpleNamespace(
        id="broker-1",
        client_order_id="stable-id",
        symbol="AAPL",
        side="buy",
        qty="2",
        filled_qty="0",
        status="accepted",
        type="market",
        time_in_force="opg",
    )
    executor.trading_client = SimpleNamespace(
        get_order_by_client_id=Mock(return_value=broker_order),
        submit_order=Mock(),
    )
    executor._save_state = Mock()

    executor._recover_opg_batches()

    executor.trading_client.get_order_by_client_id.assert_called_once_with("stable-id")
    executor.trading_client.submit_order.assert_not_called()


def test_opg_ambiguous_submission_stops_batch_without_resubmission():
    from src.execution.broker_executor import BrokerExecutor

    executor = BrokerExecutor.__new__(BrokerExecutor)
    executor.state = {"account_mode": "paper", "account_id": "paper-account-1", "opg_batches": {}}
    submitted = []

    class BrokerMock:
        def submit_order(self, request):
            submitted.append(request.client_order_id)
            raise TimeoutError("response lost after broker acceptance")

        def get_order_by_client_id(self, client_order_id):
            raise RuntimeError("temporarily not visible")

    executor.trading_client = BrokerMock()
    executor._save_state = lambda: None
    from src.execution.opg_sizing import build_opg_sizing_plan

    plan = build_opg_sizing_plan(
        equity=1000, target_weights={"AAPL": 0.2, "MSFT": 0.2},
        close_prices={"AAPL": 100, "MSFT": 100},
        current_positions={"AAPL": 0, "MSFT": 0},
    )
    with pytest.raises(RuntimeError, match="unresolved"):
        executor.execute_opg_plan(
            plan, signal_bar_at="2026-11-03T21:00:00Z", target_session="2026-11-04"
        )
    assert len(submitted) == 1


@PENDING_IMPLEMENTATION
def test_opg_order_identity_mismatch_fails_closed():
    from src.execution.broker_executor import BrokerExecutor

    executor = BrokerExecutor.__new__(BrokerExecutor)
    executor.state = {
        "account_mode": "paper",
        "opg_batches": {
            "batch-1": {
                "orders": {
                    "AAPL": {
                        "client_order_id": "stable-id",
                        "quantity": 2,
                        "side": "buy",
                    }
                }
            }
        },
    }
    broker_order = SimpleNamespace(
        id="broker-1", client_order_id="other-id", symbol="AAPL", side="buy",
        qty="2", filled_qty="0", status="accepted", type="market", time_in_force="opg",
    )
    executor.trading_client = SimpleNamespace(
        get_order_by_client_id=Mock(return_value=broker_order)
    )
    executor._save_state = Mock()

    with pytest.raises(RuntimeError, match="identity"):
        executor._recover_opg_batches()


def test_opg_unresolved_order_blocks_new_batch():
    from src.execution.broker_executor import BrokerExecutor

    executor = BrokerExecutor.__new__(BrokerExecutor)
    executor.state = {
        "account_mode": "paper",
        "account_id": "paper-account-1",
        "opg_batches": {"batch-old": {"status": "unresolved", "orders": {}}},
    }
    executor.trading_client = SimpleNamespace(
        submit_order=Mock(side_effect=AssertionError("must not submit"))
    )

    from src.execution.opg_sizing import build_opg_sizing_plan

    plan = build_opg_sizing_plan(
        equity=1000, target_weights={"AAPL": 0.2},
        close_prices={"AAPL": 100}, current_positions={"AAPL": 0},
    )
    with pytest.raises(RuntimeError, match="unresolved"):
        executor.execute_opg_plan(
            plan, signal_bar_at="2026-11-04T21:00:00Z", target_session="2026-11-05"
        )
    executor.trading_client.submit_order.assert_not_called()
