"""Read-only reconciliation of persisted STMAA state against Alpaca Paper.

This script performs GET/read operations only. It never submits, cancels, replaces,
or closes orders and never modifies the persisted state file.
Run it on the bot host (or provide a copy of its exact state file) so the state
being compared is the one used by the running deployment.
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import QueryOrderStatus
from alpaca.trading.requests import GetOrdersRequest

TERMINAL = {"filled", "canceled", "cancelled", "expired", "rejected"}
DEFAULT_STATE = Path(__file__).resolve().parents[1] / "logs" / "execution" / "broker_state.json"


def text_value(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw or "").rsplit(".", 1)[-1].lower()


def decimal_value(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return result if result.is_finite() else None


def load_state(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        state = json.load(handle)
    if not isinstance(state, dict) or state.get("state_schema_version") != 1:
        raise ValueError("unsupported or missing persisted state schema")
    for key in (
        "emergency_liquidation_pending",
        "emergency_liquidation_unresolved",
        "order_submission_unresolved",
        "order_submission_confirmed",
    ):
        if not isinstance(state.get(key), dict):
            raise ValueError(f"persisted state field {key} is missing or invalid")
    for key in ("emergency_liquidation_unresolved", "order_submission_unresolved", "order_submission_confirmed"):
        if any(not isinstance(value, dict) for value in state[key].values()):
            raise ValueError(f"persisted state entries in {key} must be objects")
    if any(not isinstance(value, (dict, str)) for value in state["emergency_liquidation_pending"].values()):
        raise ValueError("persisted liquidation pending entries must be objects or order ID strings")
    return state


def read_fill_activities(key: str, secret: str) -> list[dict]:
    """Fetch all available FILL activities with bounded, advancing pagination."""
    import requests

    url = "https://paper-api.alpaca.markets/v2/account/activities"
    headers = {
        "APCA-API-KEY-ID": key,
        "APCA-API-SECRET-KEY": secret,
        "Accept": "application/json",
    }
    records: list[dict] = []
    token = None
    while True:
        params = {
            "activity_types": "FILL",
            "direction": "asc",
            "page_size": 100,
        }
        if token:
            params["page_token"] = token
        response = requests.get(url, headers=headers, params=params, timeout=20)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list) or any(not isinstance(item, dict) for item in payload):
            raise ValueError("unexpected FILL activities response shape")
        records.extend(payload)
        if len(payload) < 100:
            return records
        next_token = payload[-1].get("id")
        if not next_token or next_token == token:
            raise ValueError("FILL activity pagination did not advance")
        token = next_token


def reconcile(state: dict, account: Any, orders: list[Any], positions: list[Any],
              fills: list[dict]) -> dict:
    """Produce a diagnostic report; aggregate position comparisons are not order-scoped."""
    issues: list[str] = []
    checks: list[dict] = []
    account_id = str(getattr(account, "id", "") or "") or None
    state_account_id = str(state.get("account_id", "") or "") or None
    if state.get("account_mode") != "paper":
        issues.append("persisted state account_mode is not paper")
    if not account_id:
        issues.append("current broker account ID is unavailable")
    if state_account_id and account_id and state_account_id != account_id:
        issues.append("persisted account ID differs from current Paper account")
    if not bool(getattr(account, "trading_blocked", False)) and text_value(
        getattr(account, "status", "")
    ) not in {"active", "accountstatus.active"}:
        issues.append("broker account status is not clearly active")

    if len(orders) >= 500:
        issues.append("broker order snapshot reached the 500-order limit; complete order-history coverage is not established")
    orders_by_id = {str(getattr(o, "id", "")): o for o in orders if getattr(o, "id", None)}
    orders_by_client = {
        str(getattr(o, "client_order_id", "")): o
        for o in orders if getattr(o, "client_order_id", None)
    }
    fills_by_order: dict[str, list[dict]] = defaultdict(list)
    malformed_fills = 0
    for fill in fills:
        order_id = str(fill.get("order_id", "") or "")
        qty = decimal_value(fill.get("qty"))
        price = decimal_value(fill.get("price"))
        if not order_id or qty is None or qty <= 0 or price is None or price <= 0:
            malformed_fills += 1
            continue
        fills_by_order[order_id].append(fill)
    if malformed_fills:
        issues.append(f"{malformed_fills} FILL activity records have missing/invalid order ID, qty, or price")

    references: list[tuple[str, str, dict, str]] = []
    for ticker, intent in state["order_submission_unresolved"].items():
        references.append(("unresolved_submission", str(ticker), intent, "unresolved"))
    for key, order_info in state["order_submission_confirmed"].items():
        references.append(("confirmed_submission", str(key), order_info, "confirmed"))
    for ticker, intent in state["emergency_liquidation_pending"].items():
        references.append(("liquidation_pending", str(ticker), intent, "liquidation"))
    for ticker, intent in state["emergency_liquidation_unresolved"].items():
        references.append(("liquidation_unresolved", str(ticker), intent, "liquidation"))

    for kind, key, ref, _ in references:
        if isinstance(ref, dict):
            client_id = str(ref.get("client_order_id", "") or "")
            order_id = str(ref.get("order_id", "") or "")
        else:
            client_id = ""
            order_id = str(ref or "") if kind == "liquidation_pending" else ""
        order = orders_by_client.get(client_id) if client_id else None
        if order is None and order_id:
            order = orders_by_id.get(order_id)
        status = text_value(getattr(order, "status", "")) if order is not None else "not_found"
        found_id = str(getattr(order, "id", "") or "") if order is not None else ""
        order_fills = fills_by_order.get(found_id, []) if found_id else []
        filled_qty = sum((decimal_value(f.get("qty")) or Decimal(0) for f in order_fills), Decimal(0))
        broker_filled_qty = decimal_value(getattr(order, "filled_qty", None)) if order is not None else None
        fill_qty_match = (
            broker_filled_qty is not None
            and abs(filled_qty - broker_filled_qty) <= Decimal("0.000000001")
        ) if order_fills else None
        checks.append({
            "kind": kind,
            "key": key,
            "client_order_id": client_id or None,
            "persisted_order_id": order_id or None,
            "broker_order_found": order is not None,
            "broker_order_id": found_id or None,
            "broker_status": status,
            "fill_activity_count": len(order_fills),
            "fill_activity_qty": str(filled_qty),
            "broker_order_filled_qty": str(broker_filled_qty) if broker_filled_qty is not None else None,
            "fill_qty_matches_order_summary": fill_qty_match,
        })
        if order is not None and order_fills and fill_qty_match is False:
            issues.append(f"{kind} {key}: summed FILL activity quantity differs from broker order filled_qty")
        if order is not None and status == "filled" and broker_filled_qty and not order_fills:
            issues.append(f"{kind} {key}: broker order is filled but no order-linked FILL activities were returned")
        if kind in {"unresolved_submission", "liquidation_unresolved"} and order is None:
            issues.append(f"{kind} {key}: persisted intent has no matching order in fetched broker order snapshot")
        if kind == "unresolved_submission" and order is not None:
            issues.append(f"unresolved submission {key}: broker order exists; state still needs reconciliation")
        if kind == "confirmed_submission" and order is None:
            issues.append(f"confirmed submission {key}: persisted broker order was not found in fetched order snapshot")
        if kind == "liquidation_pending" and order is None:
            issues.append(f"pending liquidation {key}: broker order was not found in fetched order snapshot")
        if order is not None and status not in TERMINAL and not found_id:
            issues.append(f"{kind} {key}: active order has no broker order ID")

    positions_by_symbol: dict[str, dict] = {}
    for position in positions:
        symbol = str(getattr(position, "symbol", "") or "").upper()
        if not symbol or symbol in positions_by_symbol:
            issues.append(f"position snapshot has missing or duplicate symbol: {symbol or '<missing>'}")
            continue
        qty = decimal_value(getattr(position, "qty", None))
        market_value = decimal_value(getattr(position, "market_value", None))
        if qty is None or market_value is None:
            issues.append(f"position {symbol}: quantity or market value is unavailable/invalid")
        positions_by_symbol[symbol] = {
            "qty": str(qty) if qty is not None else None,
            "market_value": str(market_value) if market_value is not None else None,
        }

    return {
        "account_id_match": bool(account_id and (not state_account_id or account_id == state_account_id)),
        "persisted_order_checks": checks,
        "position_snapshot": positions_by_symbol,
        "fill_activity_count": len(fills),
        "issue_count": len(issues),
        "issues": issues,
        "result": "REVIEW_REQUIRED" if issues else "READS_CONSISTENT_WITHIN_CHECKED_SCOPE",
        "limitations": [
            "Broker position quantities are account aggregates, not attributable to individual orders.",
            "FILL activity history is limited to the records exposed by the broker endpoint and is not proof of complete lifetime ledger coverage.",
            "This diagnostic does not modify or repair persisted state.",
        ],
    }


def main() -> int:
    key = os.environ.get("ALPACA_API_KEY", "")
    secret = os.environ.get("ALPACA_API_SECRET", "")
    state_path = Path(os.environ.get("STMAA_BROKER_STATE_FILE", str(DEFAULT_STATE))).expanduser()
    if not key or not secret:
        print("RESULT: INCOMPLETE — Alpaca Paper credentials are missing")
        return 2
    try:
        state = load_state(state_path)
    except Exception as exc:
        print(f"STATE: read/validation failed ({type(exc).__name__}); details withheld")
        print("RESULT: INCOMPLETE — persisted state cannot be validated")
        return 1

    try:
        client = TradingClient(key, secret, paper=True)
        account = client.get_account()
        orders = client.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.ALL, limit=500))
        positions = client.get_all_positions()
        fills = read_fill_activities(key, secret)
    except Exception as exc:
        print(f"BROKER_READ: failed ({type(exc).__name__}); details withheld")
        print("RESULT: INCOMPLETE — broker snapshots could not all be read")
        return 1

    report = reconcile(state, account, orders, positions, fills)
    print("MODE: ALPACA_PAPER_READ_ONLY")
    print(f"STATE_FILE: {state_path}")
    print(f"ACCOUNT_ID: {getattr(account, 'id', 'unavailable')}")
    print(f"ACCOUNT_STATUS: {text_value(getattr(account, 'status', 'unknown'))}")
    print(f"ORDERS_READ: {len(orders)}")
    print(f"POSITIONS_READ: {len(positions)}")
    print(f"FILL_ACTIVITIES_READ: {len(fills)}")
    print(json.dumps(report, indent=2, sort_keys=True))
    print(f"RESULT: {report['result']}")
    return 0 if report["issue_count"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
