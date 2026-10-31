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
