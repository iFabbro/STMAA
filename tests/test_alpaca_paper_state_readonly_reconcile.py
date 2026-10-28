"""Mocked tests for read-only reconciliation of persisted state and broker snapshots."""
from types import SimpleNamespace

from scripts.alpaca_paper_state_readonly_reconcile import load_state, reconcile


def valid_state():
    return {
        "state_schema_version": 1,
        "account_mode": "paper",
        "account_id": "paper-account-1",
        "emergency_liquidation_pending": {},
        "emergency_liquidation_unresolved": {},
        "order_submission_unresolved": {},
        "order_submission_confirmed": {},
    }


def account(account_id="paper-account-1"):
    return SimpleNamespace(id=account_id, status="ACTIVE", trading_blocked=False)


def test_reconcile_matches_persisted_confirmed_order_and_fill_activities():
    state = valid_state()
    state["order_submission_confirmed"] = {
        "client-1": {"client_order_id": "client-1", "order_id": "order-1", "status": "filled"}
    }
    order = SimpleNamespace(
        id="order-1", client_order_id="client-1", symbol="AAPL",
        status="filled", filled_qty="2", filled_avg_price="100",
    )
    fills = [
        {"order_id": "order-1", "qty": "1", "price": "99"},
        {"order_id": "order-1", "qty": "1", "price": "101"},
    ]
    positions = [SimpleNamespace(symbol="AAPL", qty="2", market_value="200")]

    report = reconcile(state, account(), [order], positions, fills)

    assert report["issue_count"] == 0
    assert report["result"] == "READS_CONSISTENT_WITHIN_CHECKED_SCOPE"
    check = report["persisted_order_checks"][0]
    assert check["broker_order_found"] is True
    assert check["fill_activity_qty"] == "2"
    assert check["fill_qty_matches_order_summary"] is True
    assert report["position_snapshot"]["AAPL"]["qty"] == "2"


def test_reconcile_flags_unresolved_persisted_intent_missing_from_broker():
    state = valid_state()
    state["order_submission_unresolved"] = {
        "AAPL": {"client_order_id": "unknown-client", "side": "buy", "quantity": 1}
    }

    report = reconcile(state, account(), [], [], [])

    assert report["issue_count"] > 0
    assert any("no matching order" in issue for issue in report["issues"])
    assert report["result"] == "REVIEW_REQUIRED"


def test_reconcile_flags_fill_quantity_mismatch_and_account_identity_mismatch():
    state = valid_state()
    state["order_submission_confirmed"] = {
        "client-1": {"client_order_id": "client-1", "order_id": "order-1", "status": "filled"}
    }
    order = SimpleNamespace(
        id="order-1", client_order_id="client-1", symbol="AAPL",
        status="filled", filled_qty="2", filled_avg_price="100",
    )
    fills = [{"order_id": "order-1", "qty": "1", "price": "100"}]

    report = reconcile(state, account("different-account"), [order], [], fills)

    assert report["account_id_match"] is False
    assert any("account ID differs" in issue for issue in report["issues"])
    assert any("FILL activity quantity differs" in issue for issue in report["issues"])


def test_reconcile_supports_legacy_pending_liquidation_order_id_string():
    state = valid_state()
    state["emergency_liquidation_pending"] = {"AAPL": "close-order-1"}
    order = SimpleNamespace(
        id="close-order-1", client_order_id="close-client-1", symbol="AAPL",
        status="accepted", filled_qty="0", filled_avg_price=None,
    )

    report = reconcile(state, account(), [order], [], [])

    assert report["persisted_order_checks"][0]["broker_order_found"] is True
    assert report["persisted_order_checks"][0]["broker_status"] == "accepted"
    assert report["issue_count"] == 0


def test_load_state_accepts_legacy_string_for_pending_liquidation(tmp_path):
    import json

    state = valid_state()
    state["emergency_liquidation_pending"] = {"AAPL": "close-order-1"}
    path = tmp_path / "broker_state.json"
    path.write_text(json.dumps(state), encoding="utf-8")

    assert load_state(path)["emergency_liquidation_pending"]["AAPL"] == "close-order-1"
