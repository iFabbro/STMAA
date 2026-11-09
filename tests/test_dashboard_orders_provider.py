from __future__ import annotations

import json
from pathlib import Path

import pytest
from textual.widgets import Static

from src.dashboard import app as dashboard_app
from src.dashboard.app import STMAADashboard
from src.dashboard.local_providers import (
    SourceStatus,
    read_persisted_broker_state,
)


def _state() -> dict:
    return {
        "state_schema_version": 2,
        "state_revision": 17,
        "account_mode": "paper",
        "account_id": "paper-account",
        "order_submission_unresolved": {
            "AAPL": {
                "ticker": "AAPL",
                "client_order_id": "client-unresolved",
                "side": "buy",
                "quantity": 2,
                "reason": "submission_outcome_unconfirmed",
            }
        },
        "order_submission_confirmed": {
            "client-confirmed": {
                "ticker": "MSFT",
                "status": "filled",
                "side": "buy",
                "filled_qty": 1,
                "position_reconciled": False,
            }
        },
        "opg_batches": {
            "batch-1": {
                "status": "unresolved",
                "target_session": "2026-11-09",
            }
        },
        "emergency_liquidation_pending": {"NVDA": "broker-order-1"},
        "emergency_liquidation_unresolved": {
            "TSLA": {
                "requested_qty": 3,
                "expected_side": "sell",
                "reason": "close_submission_outcome_unconfirmed",
            }
        },
    }


def test_provider_reads_all_execution_categories_without_mutating_file(tmp_path):
    path = tmp_path / "broker_state.json"
    original = json.dumps(_state(), separators=(",", ":")).encode()
    path.write_bytes(original)

    result = read_persisted_broker_state(path)

    assert result.status is SourceStatus.OK
    assert result.value["order_submission_unresolved"]["AAPL"]["client_order_id"] == "client-unresolved"
    assert result.value["order_submission_confirmed"]["client-confirmed"]["status"] == "filled"
    assert result.value["opg_batches"]["batch-1"]["status"] == "unresolved"
    assert result.value["emergency_liquidation_pending"]["NVDA"] == "broker-order-1"
    assert result.value["emergency_liquidation_unresolved"]["TSLA"]["requested_qty"] == 3
    assert path.read_bytes() == original


def test_provider_reports_missing_and_invalid_states_without_creating_files(tmp_path):
    missing_path = tmp_path / "missing" / "broker_state.json"
    assert read_persisted_broker_state(missing_path).status is SourceStatus.MISSING
    assert not missing_path.parent.exists()

    malformed_path = tmp_path / "malformed.json"
    malformed_path.write_text("{broken", encoding="utf-8")
    assert read_persisted_broker_state(malformed_path).status is SourceStatus.INVALID

    invalid_path = tmp_path / "invalid-map.json"
    invalid = _state()
    invalid["order_submission_unresolved"] = ["not", "a", "map"]
    invalid_path.write_text(json.dumps(invalid), encoding="utf-8")
    assert read_persisted_broker_state(invalid_path).status is SourceStatus.INVALID


@pytest.mark.parametrize(
    "mutate",
    [
        lambda state: state.update(state_schema_version=99),
        lambda state: state.update(state_revision=-1),
        lambda state: state.update(opg_batches={"broken": "not-an-object"}),
        lambda state: state.update(emergency_liquidation_pending={"AAPL": ""}),
        lambda state: state.update(order_submission_confirmed={"bad": None}),
    ],
)
def test_provider_fails_closed_on_invalid_execution_state_shapes(tmp_path, mutate):
    state = _state()
    mutate(state)
    path = tmp_path / "broker_state.json"
    path.write_text(json.dumps(state), encoding="utf-8")
    assert read_persisted_broker_state(path).status is SourceStatus.INVALID


def test_schema_v1_does_not_fabricate_an_opg_collection(tmp_path):
    state = _state()
    state["state_schema_version"] = 1
    state.pop("opg_batches")
    path = tmp_path / "broker_state.json"
    path.write_text(json.dumps(state), encoding="utf-8")

    result = read_persisted_broker_state(path)

    assert result.status is SourceStatus.OK
    assert result.value["opg_batches"] is None


def test_orders_view_renders_four_separate_execution_categories(monkeypatch, tmp_path):
    state_path = tmp_path / "broker_state.json"
    state_path.write_text(json.dumps(_state()), encoding="utf-8")
    monkeypatch.setattr(
        dashboard_app.providers,
        "read_persisted_broker_state",
        lambda **kwargs: read_persisted_broker_state(state_path, **kwargs),
    )
    monkeypatch.setattr(
        dashboard_app.providers,
        "read_json_snapshot",
        lambda **kwargs: dashboard_app.providers.SourceResult(
            SourceStatus.EMPTY, "snapshot", value={}
        ),
    )
    monkeypatch.setattr(
        dashboard_app.providers,
        "read_latest_snapshot",
        lambda **kwargs: dashboard_app.providers.SourceResult(
            SourceStatus.EMPTY, "sqlite", value={}
        ),
    )
    monkeypatch.setattr(
        dashboard_app.providers,
        "read_latest_run",
        lambda: dashboard_app.providers.SourceResult(
            SourceStatus.EMPTY, "sqlite", value={}
        ),
    )
    monkeypatch.setattr(
        dashboard_app.providers,
        "read_recent_events",
        lambda **kwargs: dashboard_app.providers.SourceResult(
            SourceStatus.EMPTY, "sqlite", value=[]
        ),
    )

    app = STMAADashboard()

    async def run():
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.press("3")
            rendered = str(app.query_one("#content", Static).render())
            for expected in (
                "UNRESOLVED SUBMISSION INTENTS",
                "BROKER-CONFIRMED ORDER RECORDS",
                "OPG BATCHES",
                "EMERGENCY LIQUIDATION — PENDING",
                "EMERGENCY LIQUIDATION — UNRESOLVED INTENTS",
                "client-unresolved",
                "client-confirmed",
                "batch-1",
                "broker-order-1",
                "TSLA",
            ):
                assert expected in rendered

    import asyncio
    asyncio.run(run())


def test_dashboard_orders_provider_is_local_and_has_no_execution_imports():
    source = Path("src/dashboard/local_providers.py").read_text(encoding="utf-8").lower()
    forbidden = (
        "src.execution",
        "brokerexecutor",
        "alpaca",
        "trading_client",
        "submit_order",
        "cancel_order",
        "close_position",
        "httpx",
        "requests.",
    )
    assert all(token not in source for token in forbidden)

@pytest.mark.parametrize(
    "mutate",
    [
        lambda state: state["order_submission_unresolved"]["AAPL"].update(side="hold"),
        lambda state: state["order_submission_unresolved"]["AAPL"].update(quantity=float("inf")),
        lambda state: state["order_submission_confirmed"]["client-confirmed"].update(status="mystery"),
        lambda state: state["order_submission_confirmed"]["client-confirmed"].update(filled_qty=2, qty=1),
        lambda state: state["order_submission_confirmed"]["client-confirmed"].update(client_order_id="different-id"),
        lambda state: state["emergency_liquidation_unresolved"]["TSLA"].update(expected_side="hold"),
        lambda state: state.update(account_id=""),
    ],
)
def test_provider_rejects_semantically_invalid_execution_records(tmp_path, mutate):
    state = _state()
    mutate(state)
    path = tmp_path / "broker_state.json"
    path.write_text(json.dumps(state), encoding="utf-8")
    assert read_persisted_broker_state(path).status is SourceStatus.INVALID


def test_provider_rejects_duplicate_json_object_keys(tmp_path):
    path = tmp_path / "duplicate.json"
    path.write_text(
        '{"state_schema_version":2,"state_revision":0,"account_mode":"paper",'
        '"order_submission_unresolved":{},"order_submission_confirmed":{},'
        '"emergency_liquidation_pending":{},"emergency_liquidation_unresolved":{},'
        '"opg_batches":{},"opg_batches":{}}',
        encoding="utf-8",
    )
    assert read_persisted_broker_state(path).status is SourceStatus.INVALID

def test_provider_accepts_valid_persisted_opg_order_lifecycle(tmp_path):
    state = _state()
    state["opg_batches"]["batch-1"].update({
        "status": "reconciliation_pending",
        "account_mode": "paper",
        "orders": {
            "AAPL": {
                "symbol": "AAPL",
                "side": "buy",
                "quantity": 2,
                "requested_quantity": 2,
                "client_order_id": "client-opg",
                "order_type": "limit",
                "limit_price": 190.0,
                "time_in_force": "opg",
                "status": "submission_pending",
                "order_id": None,
                "filled_qty": 0,
                "average_fill_price": None,
            }
        },
    })
    path = tmp_path / "broker_state.json"
    path.write_text(json.dumps(state), encoding="utf-8")
    result = read_persisted_broker_state(path)
    assert result.status is SourceStatus.OK
