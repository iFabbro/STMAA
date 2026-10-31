"""Broker-free tests for Alpaca corporate-action activity reconciliation."""

import pytest

from src.execution.corporate_action_reconciler import CorporateActionReconciler


def _activity(activity_id, activity_type="SPIN", symbol="NEWCO", **extra):
    return {
        "id": activity_id,
        "activity_type": activity_type,
        "symbol": symbol,
        "status": "executed",
        "date": "2026-10-30",
        **extra,
    }


def _reconciler(pages, state=None, page_size=2, max_pages=10):
    requests = []
    persisted = []

    def read_page(params):
        requests.append(dict(params))
        if not pages:
            raise AssertionError("unexpected extra page request")
        page = pages.pop(0)
        if isinstance(page, Exception):
            raise page
        return page

    current_state = state if state is not None else {}
    instance = CorporateActionReconciler(
        activity_page_reader=read_page,
        state=current_state,
        persist=lambda: persisted.append(True),
        page_size=page_size,
        max_pages=max_pages,
    )
    return instance, current_state, requests, persisted


def test_reads_all_pages_and_persists_latest_checkpoint():
    pages = [
        [_activity("id-3"), _activity("id-2", "SPLIT", "AAA")],
        [_activity("id-1", "MA", "BBB")],
    ]
    reconciler, state, requests, persisted = _reconciler(pages)

    result = reconciler.reconcile(
        ["AAA", "BBB", "NEWCO"],
        {"AAA": {"qty": 2}, "BBB": {"qty": 3}, "NEWCO": {"qty": 1}},
    )

    assert result["new_activity_count"] == 3
    assert state["corporate_action_reconciliation"]["last_activity_id"] == "id-3"
    assert len(state["corporate_action_reconciliation"]["recent_events"]) == 3
    assert requests[0]["activity_types"] == "MA,NC,REO,REORG,SC,SPIN,SPLIT"
    assert requests[1]["page_token"] == "id-2"
    assert len(persisted) == 1


def test_unconfigured_spin_off_holding_blocks_and_is_persisted():
    pages = [[_activity("spin-1", symbol="CHILD")]]
    reconciler, state, _, persisted = _reconciler(pages)

    with pytest.raises(RuntimeError, match="outside the configured PPO universe"):
        reconciler.reconcile(["PARENT"], {"CHILD": {"qty": 4}})

    stored = state["corporate_action_reconciliation"]
    assert stored["blocked_symbols"] == ["CHILD"]
    assert stored["last_activity_id"] == "spin-1"
    assert persisted == [True]


def test_existing_checkpoint_stops_pagination_without_reprocessing_old_events():
    pages = [
        [_activity("new-2"), _activity("old-checkpoint")],
    ]
    state = {
        "corporate_action_reconciliation": {
            "last_activity_id": "old-checkpoint",
            "recent_events": {},
        }
    }
    reconciler, updated, requests, _ = _reconciler(pages, state=state)

    result = reconciler.reconcile(["NEWCO"], {"NEWCO": {"qty": 1}})

    assert result["new_activity_count"] == 1
    assert updated["corporate_action_reconciliation"]["last_activity_id"] == "new-2"
    assert len(requests) == 1


def test_missing_persisted_checkpoint_fails_closed_without_advancing_state():
    state = {
        "corporate_action_reconciliation": {
            "last_activity_id": "checkpoint-no-longer-visible",
            "recent_events": {},
        }
    }
    pages = [[_activity("newer-activity")]]
    reconciler, updated, _, persisted = _reconciler(pages, state=state)

    with pytest.raises(RuntimeError, match="checkpoint was not found"):
        reconciler.reconcile(["NEWCO"], {"NEWCO": {"qty": 1}})

    assert updated["corporate_action_reconciliation"]["last_activity_id"] == (
        "checkpoint-no-longer-visible"
    )
    assert updated["corporate_action_reconciliation"]["recent_events"] == {}
    assert persisted == []


def test_missing_persisted_checkpoint_on_empty_history_fails_closed():
    state = {
        "corporate_action_reconciliation": {
            "last_activity_id": "checkpoint-no-longer-visible",
            "recent_events": {},
        }
    }
    reconciler, updated, _, persisted = _reconciler([[]], state=state)

    with pytest.raises(RuntimeError, match="checkpoint was not found"):
        reconciler.reconcile(["NEWCO"], {"NEWCO": {"qty": 1}})

    assert updated["corporate_action_reconciliation"]["last_activity_id"] == (
        "checkpoint-no-longer-visible"
    )
    assert persisted == []


def test_missing_symbol_is_persisted_and_blocks_fail_closed():
    pages = [[_activity("spin-no-symbol", symbol=None)]]
    reconciler, state, _, _ = _reconciler(pages)

    with pytest.raises(RuntimeError, match="without a resolvable security symbol"):
        reconciler.reconcile(["AAA"], {"AAA": {"qty": 1}})

    assert state["corporate_action_reconciliation"]["unresolved_activity_ids"] == [
        "spin-no-symbol"
    ]


def test_api_failure_does_not_advance_or_persist_checkpoint():
    pages = [ConnectionError("offline")]
    reconciler, state, _, persisted = _reconciler(pages)

    with pytest.raises(RuntimeError, match="Unable to retrieve Alpaca"):
        reconciler.reconcile(["AAA"], {"AAA": {"qty": 1}})

    assert "corporate_action_reconciliation" not in state
    assert persisted == []


@pytest.mark.parametrize(
    "page",
    [
        {"items": []},
        [_activity("", symbol="AAA")],
        [_activity("id-1", activity_type="UNKNOWN", symbol="AAA")],
        [_activity("id-1", symbol="")],
        [_activity("id-1", symbol="AAA", status="pending")],
    ],
)
def test_malformed_or_nonfinal_activity_fails_closed(page):
    reconciler, state, _, persisted = _reconciler([page])

    with pytest.raises(RuntimeError):
        reconciler.reconcile(["AAA"], {"AAA": {"qty": 1}})

    assert "corporate_action_reconciliation" not in state
    assert persisted == []


def test_duplicate_activity_ids_across_pages_fail_closed():
    pages = [
        [_activity("id-2"), _activity("id-1")],
        [_activity("id-1")],
    ]
    reconciler, state, _, persisted = _reconciler(pages)

    with pytest.raises(RuntimeError, match="duplicate activity IDs"):
        reconciler.reconcile(["NEWCO"], {"NEWCO": {"qty": 1}})

    assert "corporate_action_reconciliation" not in state
    assert persisted == []


def test_page_limit_exhaustion_fails_closed():
    pages = [
        [_activity("id-4"), _activity("id-3")],
        [_activity("id-2"), _activity("id-1")],
    ]
    reconciler, state, _, persisted = _reconciler(pages, max_pages=1)

    with pytest.raises(RuntimeError, match="page safety limit"):
        reconciler.reconcile(["NEWCO"], {"NEWCO": {"qty": 1}})

    assert "corporate_action_reconciliation" not in state
    assert persisted == []


def test_restart_uses_persisted_checkpoint_and_event_ledger():
    state = {
        "corporate_action_reconciliation": {
            "last_activity_id": "previous",
            "recent_events": {
                "previous": {
                    "id": "previous",
                    "activity_type": "SPIN",
                    "symbol": "CHILD",
                    "activity_sub_type": None,
                    "date": "2026-10-01",
                    "status": "executed",
                }
            },
        }
    }
    pages = [[_activity("latest", symbol="AAA"), _activity("previous", symbol="CHILD")]]
    reconciler, updated, requests, _ = _reconciler(pages, state=state)

    with pytest.raises(RuntimeError, match="outside the configured PPO universe"):
        reconciler.reconcile(["AAA"], {"AAA": {"qty": 1}, "CHILD": {"qty": 2}})

    assert requests[0]["direction"] == "desc"
    assert updated["corporate_action_reconciliation"]["last_activity_id"] == "latest"
    assert updated["corporate_action_reconciliation"]["blocked_symbols"] == ["CHILD"]


def test_cash_only_reorganization_without_symbol_does_not_block():
    pages = [[_activity("reo-cash", activity_type="REO", symbol=None, activity_sub_type="REOC")]]
    reconciler, state, _, _ = _reconciler(pages)

    result = reconciler.reconcile(["AAA"], {"AAA": {"qty": 1}})

    assert result["new_activity_count"] == 1
    assert state["corporate_action_reconciliation"]["unresolved_activity_ids"] == []


def test_broker_executor_uses_read_only_trading_api_activities_endpoint():
    from src.execution.broker_executor import BrokerExecutor

    class FakeTradingClient:
        def __init__(self):
            self.calls = []

        def get(self, path, params):
            self.calls.append((path, dict(params)))
            return [_activity("spin-1", symbol="AAA")]

    executor = BrokerExecutor.__new__(BrokerExecutor)
    executor.trading_client = FakeTradingClient()
    executor.state = {}
    persisted = []
    executor._save_state = lambda: persisted.append(True)

    result = executor.reconcile_corporate_actions(
        ["AAA"], positions={"AAA": {"qty": 1}}
    )

    assert result["new_activity_count"] == 1
    assert executor.trading_client.calls[0][0] == "/account/activities"
    assert executor.trading_client.calls[0][1]["direction"] == "desc"
    assert executor.trading_client.calls[0][1]["activity_types"] == (
        "MA,NC,REO,REORG,SC,SPIN,SPLIT"
    )
    assert persisted == [True]


def test_manual_resolution_requires_evidence_and_persists_audit_record():
    state = {
        "corporate_action_reconciliation": {
            "last_activity_id": "spin-no-symbol",
            "recent_events": {
                "spin-no-symbol": {
                    "id": "spin-no-symbol",
                    "activity_type": "SPIN",
                    "symbol": None,
                    "activity_sub_type": None,
                    "date": "2026-10-30",
                    "status": "executed",
                }
            },
            "unresolved_activity_ids": ["spin-no-symbol"],
        }
    }
    reconciler, updated, _, persisted = _reconciler([], state=state)

    record = reconciler.resolve_unresolved_activity(
        activity_id="spin-no-symbol",
        symbol="CHILD",
        evidence="broker statement ref: CA-2026-1042",
        actor="operator-1",
        reason="Issuer corporate-action notice identifies CHILD as the distributed security",
    )

    stored = updated["corporate_action_reconciliation"]
    assert stored["unresolved_activity_ids"] == []
    assert stored["resolved_activity_symbols"]["spin-no-symbol"] == record
    assert stored["manual_resolution_history"] == [record]
    assert record["symbol"] == "CHILD"
    assert record["evidence"] == "broker statement ref: CA-2026-1042"
    assert record["actor"] == "operator-1"
    assert record["reason"].startswith("Issuer corporate-action notice")
    assert record["resolved_at"]
    assert persisted == [True]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"evidence": ""},
        {"actor": ""},
        {"reason": ""},
        {"symbol": "INVALID SYMBOL"},
    ],
)
def test_manual_resolution_rejects_missing_or_invalid_operator_input(kwargs):
    state = {
        "corporate_action_reconciliation": {
            "recent_events": {
                "spin-no-symbol": {
                    "id": "spin-no-symbol",
                    "activity_type": "SPIN",
                    "symbol": None,
                }
            },
            "unresolved_activity_ids": ["spin-no-symbol"],
        }
    }
    reconciler, updated, _, persisted = _reconciler([], state=state)
    arguments = {
        "activity_id": "spin-no-symbol",
        "symbol": "CHILD",
        "evidence": "statement-123",
        "actor": "operator-1",
        "reason": "verified against issuer notice",
    }
    arguments.update(kwargs)

    with pytest.raises(ValueError):
        reconciler.resolve_unresolved_activity(**arguments)

    assert updated["corporate_action_reconciliation"]["unresolved_activity_ids"] == [
        "spin-no-symbol"
    ]
    assert "resolved_activity_symbols" not in updated["corporate_action_reconciliation"]
    assert persisted == []


def test_manual_resolution_rejects_unknown_or_non_unresolved_activity():
    reconciler, state, _, persisted = _reconciler([[]], state={
        "corporate_action_reconciliation": {
            "recent_events": {},
            "unresolved_activity_ids": [],
        }
    })

    with pytest.raises(ValueError, match="not currently unresolved"):
        reconciler.resolve_unresolved_activity(
            "missing-id", "CHILD", "statement-123", "operator-1", "verified"
        )

    assert state["corporate_action_reconciliation"]["unresolved_activity_ids"] == []
    assert persisted == []


def test_manual_resolution_rolls_back_memory_when_persistence_fails():
    state = {
        "corporate_action_reconciliation": {
            "recent_events": {
                "spin-no-symbol": {
                    "id": "spin-no-symbol",
                    "activity_type": "SPIN",
                    "symbol": None,
                }
            },
            "unresolved_activity_ids": ["spin-no-symbol"],
        }
    }
    original = {
        "recent_events": {
            "spin-no-symbol": {
                "id": "spin-no-symbol",
                "activity_type": "SPIN",
                "symbol": None,
            }
        },
        "unresolved_activity_ids": ["spin-no-symbol"],
    }
    reconciler = CorporateActionReconciler(
        activity_page_reader=lambda _params: [],
        state=state,
        persist=lambda: (_ for _ in ()).throw(OSError("disk unavailable")),
    )

    with pytest.raises(OSError, match="disk unavailable"):
        reconciler.resolve_unresolved_activity(
            "spin-no-symbol", "CHILD", "statement-123", "operator-1", "verified"
        )

    assert state["corporate_action_reconciliation"] == original


def test_reconcile_honors_audited_manual_symbol_resolution_after_restart():
    record = {
        "activity_id": "spin-no-symbol",
        "symbol": "AAA",
        "evidence": "statement-123",
        "actor": "operator-1",
        "reason": "issuer notice confirms distributed security",
        "resolved_at": "2026-10-31T10:00:00+00:00",
    }
    state = {
        "corporate_action_reconciliation": {
            "last_activity_id": "spin-no-symbol",
            "recent_events": {
                "spin-no-symbol": {
                    "id": "spin-no-symbol",
                    "activity_type": "SPIN",
                    "symbol": None,
                    "activity_sub_type": None,
                    "date": "2026-10-30",
                    "status": "executed",
                }
            },
            "unresolved_activity_ids": [],
            "resolved_activity_symbols": {"spin-no-symbol": record},
            "manual_resolution_history": [record],
        }
    }
    reconciler, updated, _, _ = _reconciler(
        [[_activity("spin-no-symbol", symbol=None)]], state=state
    )

    result = reconciler.reconcile(["AAA"], {"AAA": {"qty": 1}})

    assert result["new_activity_count"] == 0
    assert updated["corporate_action_reconciliation"]["unresolved_activity_ids"] == []
    assert updated["corporate_action_reconciliation"]["resolved_activity_symbols"][
        "spin-no-symbol"
    ]["symbol"] == "AAA"


def test_audited_resolution_still_blocks_held_symbol_outside_configured_universe():
    record = {
        "activity_id": "spin-no-symbol",
        "symbol": "CHILD",
        "evidence": "statement-123",
        "actor": "operator-1",
        "reason": "issuer notice confirms distributed security",
        "resolved_at": "2026-10-31T10:00:00+00:00",
    }
    state = {
        "corporate_action_reconciliation": {
            "last_activity_id": "spin-no-symbol",
            "recent_events": {
                "spin-no-symbol": {
                    "id": "spin-no-symbol",
                    "activity_type": "SPIN",
                    "symbol": None,
                    "activity_sub_type": None,
                    "date": "2026-10-30",
                    "status": "executed",
                }
            },
            "unresolved_activity_ids": [],
            "resolved_activity_symbols": {"spin-no-symbol": record},
            "manual_resolution_history": [record],
        }
    }
    reconciler, _, _, _ = _reconciler(
        [[_activity("spin-no-symbol", symbol=None)]], state=state
    )

    with pytest.raises(RuntimeError, match="outside the configured PPO universe"):
        reconciler.reconcile(["PARENT"], {"CHILD": {"qty": 2}})


def test_manual_resolution_map_and_history_mismatch_fails_closed():
    record = {
        "activity_id": "spin-no-symbol",
        "symbol": "CHILD",
        "evidence": "statement-123",
        "actor": "operator-1",
        "reason": "issuer notice confirms distributed security",
        "resolved_at": "2026-10-31T10:00:00+00:00",
    }
    state = {
        "corporate_action_reconciliation": {
            "last_activity_id": "spin-no-symbol",
            "recent_events": {},
            "unresolved_activity_ids": [],
            "resolved_activity_symbols": {"spin-no-symbol": record},
            "manual_resolution_history": [],
        }
    }
    reconciler, updated, _, persisted = _reconciler([[]], state=state)

    with pytest.raises(RuntimeError, match="map and audit history disagree"):
        reconciler.reconcile(["AAA"], {"AAA": {"qty": 1}})

    assert updated["corporate_action_reconciliation"]["resolved_activity_symbols"][
        "spin-no-symbol"
    ] == record
    assert persisted == []


def test_broker_executor_manual_resolution_uses_execution_lock_without_broker_calls():
    from contextlib import nullcontext
    from src.execution.broker_executor import BrokerExecutor

    executor = BrokerExecutor.__new__(BrokerExecutor)
    executor.state = {
        "corporate_action_reconciliation": {
            "recent_events": {
                "spin-no-symbol": {
                    "id": "spin-no-symbol",
                    "activity_type": "SPIN",
                    "symbol": None,
                }
            },
            "unresolved_activity_ids": ["spin-no-symbol"],
        }
    }
    executor.execution_lock = lambda: nullcontext()
    persisted = []
    executor._save_state = lambda: persisted.append(True)

    record = executor.resolve_corporate_action(
        "spin-no-symbol", "CHILD", "statement-123", "operator-1", "verified"
    )

    assert record["symbol"] == "CHILD"
    assert executor.state["corporate_action_reconciliation"]["unresolved_activity_ids"] == []
    assert persisted == [True]


def test_old_resolved_corporate_action_still_blocks_unconfigured_held_symbol():
    record = {
        "activity_id": "old-spin",
        "symbol": "CHILD",
        "evidence": "statement-123",
        "actor": "operator-1",
        "reason": "issuer notice confirms distributed security",
        "resolved_at": "2026-10-31T10:00:00+00:00",
    }
    state = {
        "corporate_action_reconciliation": {
            "last_activity_id": "current-checkpoint",
            "recent_events": {},
            "unresolved_activity_ids": [],
            "resolved_activity_symbols": {"old-spin": record},
            "manual_resolution_history": [record],
        }
    }
    reconciler, _, _, _ = _reconciler(
        [[_activity("current-checkpoint", symbol="AAA")]], state=state
    )

    with pytest.raises(RuntimeError, match="outside the configured PPO universe"):
        reconciler.reconcile(["PARENT"], {"CHILD": {"qty": 1}})


def test_manual_resolution_rejects_cash_only_reorganization():
    state = {
        "corporate_action_reconciliation": {
            "recent_events": {
                "reo-cash": {
                    "id": "reo-cash",
                    "activity_type": "REO",
                    "activity_sub_type": "REOC",
                    "symbol": None,
                }
            },
            "unresolved_activity_ids": ["reo-cash"],
        }
    }
    reconciler, updated, _, persisted = _reconciler([], state=state)

    with pytest.raises(ValueError, match="Cash-only"):
        reconciler.resolve_unresolved_activity(
            "reo-cash", "CHILD", "statement-123", "operator-1", "verified"
        )

    assert updated["corporate_action_reconciliation"]["unresolved_activity_ids"] == [
        "reo-cash"
    ]
    assert persisted == []


def test_manual_resolution_symbol_conflict_with_later_broker_evidence_fails_closed():
    record = {
        "activity_id": "spin-no-symbol",
        "symbol": "CHILD",
        "evidence": "statement-123",
        "actor": "operator-1",
        "reason": "issuer notice confirms distributed security",
        "resolved_at": "2026-10-31T10:00:00+00:00",
    }
    state = {
        "corporate_action_reconciliation": {
            "recent_events": {},
            "unresolved_activity_ids": [],
            "resolved_activity_symbols": {"spin-no-symbol": record},
            "manual_resolution_history": [record],
        }
    }
    reconciler, _, _, persisted = _reconciler(
        [[_activity("spin-no-symbol", symbol="BROKERCO")]], state=state
    )

    with pytest.raises(RuntimeError, match="conflicts with persisted manual resolution"):
        reconciler.reconcile(["CHILD", "BROKERCO"], {"CHILD": {"qty": 1}})

    assert persisted == []


def test_malformed_manual_resolution_timestamp_fails_closed():
    record = {
        "activity_id": "spin-no-symbol",
        "symbol": "CHILD",
        "evidence": "statement-123",
        "actor": "operator-1",
        "reason": "issuer notice confirms distributed security",
        "resolved_at": "not-a-timestamp",
    }
    state = {
        "corporate_action_reconciliation": {
            "recent_events": {},
            "unresolved_activity_ids": [],
            "resolved_activity_symbols": {"spin-no-symbol": record},
            "manual_resolution_history": [record],
        }
    }
    reconciler, _, _, persisted = _reconciler([[]], state=state)

    with pytest.raises(RuntimeError, match="invalid timestamp"):
        reconciler.reconcile(["AAA"], {"AAA": {"qty": 1}})

    assert persisted == []
