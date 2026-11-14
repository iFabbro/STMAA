"""Zero-write, Paper-only broker diagnostics. This module never imports the trading runtime."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

PAPER_ENDPOINT = "https://paper-api.alpaca.markets"
CAP_USD = 1000.0
TERMINAL = {"filled", "canceled", "cancelled", "expired", "rejected"}
ACTIVE = {
    "new", "partially_filled", "done_for_day", "pending_cancel",
    "pending_replace", "accepted", "pending_new", "accepted_for_bidding",
    "stopped", "suspended", "held",
}


def _number(value: Any, field: str, *, positive: bool = False) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} is not numeric")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{field} is not numeric") from exc
    if not math.isfinite(result) or result < 0 or (positive and result <= 0):
        raise ValueError(f"{field} is outside its valid range")
    return result


def _signed_number(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} is not numeric")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{field} is not numeric") from exc
    if not math.isfinite(result):
        raise ValueError(f"{field} is not finite")
    return result


def _value(obj: Any, name: str, default: Any = None) -> Any:
    return obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)


def _status(obj: Any) -> str:
    raw = _value(obj, "status", "")
    return str(getattr(raw, "value", raw) or "").strip().lower()


def _order_identity(order: Any) -> tuple[str, str]:
    order_id = str(_value(order, "id", "") or "").strip()
    client_id = str(_value(order, "client_order_id", "") or "").strip()
    if not order_id or not client_id:
        raise ValueError("open order is missing broker/client identity")
    return order_id, client_id


def _order_buy_reserve(order: Any) -> float:
    side = _value(order, "side", "")
    side = str(getattr(side, "value", side) or "").strip().lower()
    if side == "sell":
        return 0.0
    if side != "buy":
        raise ValueError("open order has unknown side")
    raw_notional = _value(order, "notional")
    if raw_notional not in (None, ""):
        return _number(raw_notional, "open BUY notional", positive=True)
    qty = _number(_value(order, "qty"), "open BUY quantity", positive=True)
    price = _value(order, "limit_price")
    if price in (None, ""):
        price = _value(order, "stop_price")
    if price in (None, ""):
        raise ValueError("open BUY has no verifiable notional or limit/stop price")
    return qty * _number(price, "open BUY price", positive=True)


def _load_state(root: Path, env: dict[str, str]) -> dict[str, Any]:
    configured = env.get("STMAA_BROKER_STATE_FILE")
    path = Path(configured).expanduser() if configured else root / "logs" / "execution" / "broker_state.json"
    if not path.is_absolute():
        path = root / path
    with path.open("r", encoding="utf-8") as stream:
        state = json.load(stream)
    if not isinstance(state, dict):
        raise ValueError("persisted broker state is not a JSON object")
    return state


def _reconcile_state(
    state: dict[str, Any], account_id: str, open_orders: list[Any]
) -> dict[str, Any]:
    if state.get("account_mode") != "paper":
        raise ValueError("persisted broker state is not explicitly Paper")
    if str(state.get("account_id") or "").strip() != account_id:
        raise ValueError("persisted broker account ID does not match current Alpaca account")
    for key in (
        "order_submission_unresolved", "order_submission_confirmed",
        "opg_batches", "emergency_liquidation_pending",
        "emergency_liquidation_unresolved",
    ):
        if not isinstance(state.get(key), dict):
            raise ValueError(f"persisted broker state field {key} is missing or malformed")

    unresolved = state["order_submission_unresolved"]
    if unresolved:
        raise ValueError(f"{len(unresolved)} unresolved local submission(s) remain")

    confirmed = state["order_submission_confirmed"]
    known_order_ids: set[str] = set()
    known_client_ids: set[str] = set()
    for client_id, record in confirmed.items():
        if not isinstance(record, dict):
            raise ValueError("confirmed submission record is malformed")
        order_id = str(record.get("order_id") or "").strip()
        stored_client_id = str(record.get("client_order_id") or client_id).strip()
        if order_id:
            known_order_ids.add(order_id)
        if stored_client_id:
            known_client_ids.add(stored_client_id)
        status = str(record.get("status") or "unknown").strip().lower()
        if status not in TERMINAL or record.get("position_reconciled") is not True:
            raise ValueError(f"confirmed submission {client_id} is not terminal and position-reconciled")

    batches = state["opg_batches"]
    for batch_id, batch in batches.items():
        if not isinstance(batch, dict):
            raise ValueError(f"OPG batch {batch_id} is malformed")
        if str(batch.get("status") or "unknown").strip().lower() not in {"completed", "no_orders"}:
            raise ValueError(f"OPG batch {batch_id} is not completed")
        orders = batch.get("orders", {})
        if not isinstance(orders, dict):
            raise ValueError(f"OPG batch {batch_id} orders are malformed")
        for record in orders.values():
            if not isinstance(record, dict):
                raise ValueError(f"OPG batch {batch_id} contains a malformed order")
            order_id = str(record.get("order_id") or "").strip()
            client_id = str(record.get("client_order_id") or "").strip()
            if order_id:
                known_order_ids.add(order_id)
            if client_id:
                known_client_ids.add(client_id)

    observed_ids: set[str] = set()
    for order in open_orders:
        order_id, client_id = _order_identity(order)
        observed_ids.add(order_id)
        if order_id not in known_order_ids and client_id not in known_client_ids:
            raise ValueError(f"broker open order {order_id} has no matching persisted submission")

    pending_ids = {
        str(_value(order, "id", "") or "").strip()
        for order in open_orders
    }
    if pending_ids:
        raise ValueError(f"{len(pending_ids)} broker open order(s) remain active")

    if state["emergency_liquidation_pending"] or state["emergency_liquidation_unresolved"]:
        raise ValueError("persisted emergency-liquidation workflow is still pending or unresolved")

    return {
        "account_id_match": True,
        "unresolved_submissions": 0,
        "confirmed_submissions": len(confirmed),
        "opg_batches": len(batches),
        "open_order_matches": True,
        "emergency_liquidation_clear": True,
    }


def run_preflight(root: Path, client: Any, env: dict[str, str] | None = None) -> dict[str, Any]:
    """Run five diagnostic controls using broker GETs and local file reads only."""
    root = Path(root).resolve()
    effective = dict(dotenv_values(root / ".env"))
    effective.update(os.environ if env is None else env)
    checks: list[dict[str, Any]] = []

    def record(name: str, action) -> Any:
        try:
            detail = action()
            status = "BLOCKED" if isinstance(detail, dict) and detail.get("cap_compliant") is False else "PASS"
            checks.append({"name": name, "status": status, "detail": detail})
            return detail
        except Exception as exc:
            checks.append({"name": name, "status": "BLOCKED", "detail": str(exc)})
            return None

    def verify_endpoint() -> dict[str, Any]:
        mode = effective.get("STMAA_TRADING_MODE", "paper")
        endpoint = effective.get("ALPACA_BASE_URL", PAPER_ENDPOINT)
        if mode != "paper" or endpoint.rstrip("/") != PAPER_ENDPOINT:
            raise ValueError("effective configuration must target Alpaca Paper exactly")
        if not effective.get("ALPACA_API_KEY", "").strip() or not effective.get("ALPACA_API_SECRET", "").strip():
            raise ValueError("Alpaca credentials are missing")
        actual = str(getattr(client, "_base_url", "")).rstrip("/")
        if actual and actual != PAPER_ENDPOINT:
            raise ValueError("SDK client endpoint is not Alpaca Paper")
        account = client.get_account()
        account_id = str(_value(account, "id", "") or "").strip()
        if not account_id:
            raise ValueError("Alpaca account response has no account ID")
        nonlocal _account_response
        _account_response = account
        return {"endpoint": PAPER_ENDPOINT, "account_id": account_id}

    account_object = None

    def verify_and_capture_identity() -> dict[str, Any]:
        nonlocal account_object
        result = verify_endpoint()
        account_object = _account_response
        return result

    _account_response = None
    identity = record("1. Paper authentication and account identity", verify_and_capture_identity)

    account_snapshot = None
    if identity:
        account_snapshot = record(
            "2. Account availability and buying power",
            lambda: _verify_account(account_object),
        )
    else:
        checks.append({"name": "2. Account availability and buying power", "status": "BLOCKED",
                       "detail": "Paper authentication/account identity did not pass"})

    positions = record("3. Positions snapshot", lambda: _read_positions(client))
    orders_and_exposure = record(
        "4. Open orders and $1,000 gross-notional cap",
        lambda: _read_orders_and_exposure(client, positions),
    ) if positions is not None else None
    if positions is None:
        checks.append({"name": "4. Open orders and $1,000 gross-notional cap", "status": "BLOCKED",
                       "detail": "positions snapshot is unavailable"})

    reconciliation = None
    if identity and orders_and_exposure is not None:
        reconciliation = record(
            "5. Local submission/OPG reconciliation",
            lambda: _read_and_reconcile(root, effective, identity["account_id"],
                                        orders_and_exposure["orders"]),
        )
    else:
        checks.append({"name": "5. Local submission/OPG reconciliation", "status": "BLOCKED",
                       "detail": "account identity or open-order snapshot is unavailable"})

    passed = sum(check["status"] == "PASS" for check in checks)
    return {
        "overall": "PASS" if passed == 5 else "BLOCKED",
        "checks_passed": passed,
        "checks_total": 5,
        "checks": checks,
        "account": account_snapshot,
        "exposure": None if orders_and_exposure is None else {
            key: value for key, value in orders_and_exposure.items() if key != "orders"
        },
        "reconciliation": reconciliation,
        "read_only": True,
        "broker_mutations": 0,
        "local_writes": 0,
    }


def _verify_account(account: Any) -> dict[str, Any]:
    status = str(_value(account, "status", "") or "").strip().lower()
    if status not in {"active", "accountstatus.active"}:
        raise ValueError(f"Alpaca account is not active (status={status or 'unknown'})")
    if _value(account, "trading_blocked", False) is True:
        raise ValueError("Alpaca account has trading_blocked enabled")
    if _value(account, "account_blocked", False) is True:
        raise ValueError("Alpaca account has account_blocked enabled")
    if _value(account, "trade_suspended_by_user", False) is True:
        raise ValueError("Alpaca account trading is suspended by user")
    currency = str(_value(account, "currency", "") or "").upper()
    if currency != "USD":
        raise ValueError(f"unsupported account currency: {currency or 'unknown'}")
    amounts = {
        key: _number(_value(account, key), key, positive=(key == "equity"))
        for key in ("equity", "cash", "buying_power")
    }
    return {"status": status, "currency": currency, **amounts}


def _read_positions(client: Any) -> dict[str, dict[str, float]]:
    raw = client.get_all_positions()
    if not isinstance(raw, (list, tuple)):
        raise ValueError("Alpaca positions response is malformed")
    positions: dict[str, dict[str, float]] = {}
    for item in raw:
        symbol = str(_value(item, "symbol", "") or "").strip().upper()
        if not symbol or symbol in positions:
            raise ValueError("Alpaca positions contain a missing or duplicate symbol")
        qty = _signed_number(_value(item, "qty"), f"{symbol} quantity")
        market_value = _signed_number(_value(item, "market_value"), f"{symbol} market value")
        positions[symbol] = {"qty": qty, "market_value": market_value}
    return positions


def _read_orders_and_exposure(client: Any, positions: dict[str, dict[str, float]] | None) -> dict[str, Any]:
    if positions is None:
        raise ValueError("positions snapshot unavailable")
    from alpaca.trading.requests import GetOrdersRequest
    from alpaca.trading.enums import QueryOrderStatus

    orders = client.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.OPEN))
    if not isinstance(orders, (list, tuple)):
        raise ValueError("Alpaca open-orders response is malformed")
    seen: set[str] = set()
    pending_buy = 0.0
    for order in orders:
        order_id, _ = _order_identity(order)
        if order_id in seen:
            raise ValueError("Alpaca returned duplicate open-order IDs")
        seen.add(order_id)
        status = _status(order)
        if status not in ACTIVE:
            raise ValueError(f"open order {order_id} has unknown/non-active status {status!r}")
        pending_buy += _order_buy_reserve(order)
    position_gross = sum(abs(item["market_value"]) for item in positions.values())
    gross = position_gross + pending_buy
    if not math.isfinite(gross) or gross < 0:
        raise ValueError("aggregate gross notional is invalid")
    return {
        "orders": list(orders),
        "position_gross_usd": position_gross,
        "pending_buy_notional_usd": pending_buy,
        "gross_notional_usd": gross,
        "cap_usd": CAP_USD,
        "cap_compliant": gross <= CAP_USD + 1e-8,
        "open_order_count": len(orders),
    }


def _read_and_reconcile(root: Path, env: dict[str, str], account_id: str,
                        open_orders: list[Any]) -> dict[str, Any]:
    state = _load_state(root, env)
    return _reconcile_state(state, account_id, open_orders)
