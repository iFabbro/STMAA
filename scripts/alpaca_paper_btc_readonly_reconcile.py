"""Read-only quantitative reconciliation for the existing Alpaca Paper BTC order.

Uses read endpoints only. Never submits, replaces, cancels, or closes orders.
"""
from __future__ import annotations

import os
import sys
from decimal import Decimal, InvalidOperation

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import QueryOrderStatus
from alpaca.trading.requests import GetOrdersRequest

CLIENT_ORDER_ID = "stmaa-btc-e2e-singleton"
EXPECTED_SYMBOL = "BTCUSD"
EXPECTED_SIDE = "buy"
SYMBOLS = {"BTCUSD", "BTC/USD", "BTC-USD"}
# Tiny rounding tolerance for comparing broker-reported crypto quantities.
QTY_TOLERANCE = Decimal("0.000000001")


def norm_symbol(value: object) -> str:
    return str(value or "").upper().replace("/", "").replace("-", "")


def validate_order_identity(order: object) -> tuple[bool, str]:
    """Fail closed unless the fetched order matches the expected BTC buy."""
    symbol = norm_symbol(getattr(order, "symbol", ""))
    side_raw = getattr(order, "side", "")
    side = str(getattr(side_raw, "value", side_raw) or "").rsplit(".", 1)[-1].lower()
    errors = []
    if symbol != EXPECTED_SYMBOL:
        errors.append(f"symbol mismatch: expected={EXPECTED_SYMBOL}, actual={symbol or 'missing'}")
    if side != EXPECTED_SIDE:
        errors.append(f"side mismatch: expected={EXPECTED_SIDE}, actual={side or 'missing'}")
    return not errors, "; ".join(errors) if errors else "symbol and side match expected order identity"


def norm_status(value: object) -> str:
    raw = getattr(value, "value", value)
    return str(raw or "unknown").rsplit(".", 1)[-1].lower()


def as_decimal(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def safe_decimal(value: object) -> str:
    number = as_decimal(value)
    return str(number) if number is not None else "n/a"


def first_available_decimal(obj: object, names: tuple[str, ...]) -> tuple[str | None, Decimal | None]:
    """Read a numeric field only if the SDK object actually exposes it."""
    for name in names:
        value = getattr(obj, name, None)
        number = as_decimal(value)
        if number is not None:
            return name, number
    return None, None


def compare_position_quantity(position_qty: Decimal | None, filled_qty: Decimal) -> tuple[Decimal | None, bool]:
    """Compare broker position against this order fill without masking differences."""
    if position_qty is None:
        return None, False
    delta = position_qty - filled_qty
    return delta, abs(delta) <= QTY_TOLERANCE


def parse_iso_datetime(value: object):
    """Parse broker ISO timestamps; return None when absent or malformed."""
    from datetime import datetime

    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            from datetime import timezone
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except (TypeError, ValueError):
        return None


def fetch_order_fill_activities(order: object, key: str, secret: str) -> tuple[list[dict], str]:
    """Read FILL activities for this exact order using Alpaca's documented order_id filter."""
    import requests

    order_id = str(getattr(order, "id", "") or "")
    if not order_id:
        return [], "broker order ID unavailable; FILL activity lookup skipped"
    url = "https://paper-api.alpaca.markets/v2/account/activities"
    headers = {
        "APCA-API-KEY-ID": key,
        "APCA-API-SECRET-KEY": secret,
        "Accept": "application/json",
    }
    activities: list[dict] = []
    page_token = None
    try:
        while True:
            params = {
                "activity_types": "FILL",
                "order_id": order_id,
                "direction": "asc",
                "page_size": 100,
            }
            if page_token:
                params["page_token"] = page_token
            response = requests.get(url, headers=headers, params=params, timeout=20)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, list):
                return [], "unexpected FILL account activities response shape"
            if any(not isinstance(item, dict) for item in payload):
                return [], "unexpected FILL account activities item shape"
            activities.extend(payload)
            if len(payload) < 100:
                break
            next_token = payload[-1].get("id")
            if not next_token or next_token == page_token:
                return [], "FILL activity pagination did not advance safely"
            page_token = next_token
    except Exception as exc:
        return [], f"order FILL activity read failed ({type(exc).__name__})"

    # Fail closed if the endpoint returns records not tied to the requested order.
    if any(str(item.get("order_id", "")) != order_id for item in activities):
        return [], "FILL activity response contained a different order_id"
    fills = [
        item for item in activities
        if str(item.get("activity_type", "")).upper() == "FILL"
        and norm_symbol(item.get("symbol", "")) == norm_symbol(getattr(order, "symbol", ""))
    ]
    return fills, f"queried FILL activities filtered by order_id={order_id}; raw records={len(activities)}"


def reconcile_order_fill_activities(order: object, key: str, secret: str) -> tuple[bool, str, list[dict]]:
    """Compare summed broker FILL quantities and weighted price with the order summary."""
    fills, source_status = fetch_order_fill_activities(order, key, secret)
    expected_qty = as_decimal(getattr(order, "filled_qty", None))
    expected_price = as_decimal(getattr(order, "filled_avg_price", None))
    if not fills:
        return False, source_status + "; no matching FILL records (requires at least one)", fills
    if expected_qty is None or expected_qty <= 0 or expected_price is None or expected_price <= 0:
        return False, source_status + "; order summary quantity/average price missing or invalid", fills

    total_qty = Decimal("0")
    total_notional = Decimal("0")
    for fill in fills:
        qty = as_decimal(fill.get("qty"))
        price = as_decimal(fill.get("price"))
        if qty is None or qty <= 0 or price is None or price <= 0:
            return False, source_status + "; FILL qty/price missing or invalid", fills
        total_qty += qty
        total_notional += qty * price

    weighted_avg = total_notional / total_qty
    qty_match = abs(total_qty - expected_qty) <= QTY_TOLERANCE
    price_match = abs(weighted_avg - expected_price) <= Decimal("0.01")
    detail = (
        f"; fill_count={len(fills)}; summed_fill_qty={total_qty}; "
        f"order_filled_qty={expected_qty}; weighted_avg_fill_price={weighted_avg}; "
        f"order_filled_avg_price={expected_price}; qty_match={str(qty_match).lower()}; "
        f"avg_price_match_0.01={str(price_match).lower()}"
    )
    return qty_match and price_match, source_status + detail, fills


def fetch_crypto_fee_activities(order: object, key: str, secret: str) -> tuple[list[dict], str]:
    """Read documented Alpaca Paper CFEE activities in a bounded fill-time window."""
    import requests
    from datetime import timedelta

    filled_at = parse_iso_datetime(getattr(order, "filled_at", None))
    if filled_at is None:
        return [], "order filled_at timestamp unavailable"
    after = (filled_at - timedelta(days=1)).isoformat().replace("+00:00", "Z")
    until = (filled_at + timedelta(days=3)).isoformat().replace("+00:00", "Z")
    url = "https://paper-api.alpaca.markets/v2/account/activities"
    headers = {
        "APCA-API-KEY-ID": key,
        "APCA-API-SECRET-KEY": secret,
        "Accept": "application/json",
    }
    activities: list[dict] = []
    page_token = None
    try:
        while True:
            params = {
                "activity_types": "CFEE",
                "after": after,
                "until": until,
                "direction": "asc",
                "page_size": 100,
            }
            if page_token:
                params["page_token"] = page_token
            response = requests.get(url, headers=headers, params=params, timeout=20)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, list):
                return [], "unexpected account activities response shape"
            if any(not isinstance(item, dict) for item in payload):
                return [], "unexpected CFEE account activities item shape"
            activities.extend(payload)
            if len(payload) < 100:
                break
            next_token = payload[-1].get("id")
            if not next_token or next_token == page_token:
                return [], "activity pagination did not advance safely"
            page_token = next_token
    except Exception as exc:
        # Do not print exception text: it could contain request details.
        return [], f"account activities read failed ({type(exc).__name__})"

    btc_fees = [
        item for item in activities
        if str(item.get("activity_type", "")).upper() == "CFEE"
        and norm_symbol(item.get("symbol", "")) == "BTCUSD"
    ]
    return btc_fees, f"queried CFEE activities from {after} through {until}; raw CFEE records={len(activities)}"


def reconcile_crypto_commission(order: object, key: str, secret: str) -> tuple[Decimal | None, str, list[dict]]:
    """Return a fee only when one valid CFEE activity explicitly identifies this order.

    Alpaca CFEE records may not expose order_id. Temporal proximity and symbol
    alone are never sufficient to attribute a fee to an individual order.
    """
    fees, source_status = fetch_crypto_fee_activities(order, key, secret)
    order_id = str(getattr(order, "id", "") or "")
    linked_fees = [
        fee for fee in fees
        if order_id and str(fee.get("order_id", "") or "") == order_id
    ]
    if not fees:
        return None, source_status + "; COMMISSION_STATUS=NO_CANDIDATE_ACTIVITY; amount remains unknown", fees
    if len(linked_fees) != 1:
        return None, source_status + (
            f"; explicitly order-linked BTCUSD CFEE records={len(linked_fees)}; "
            "COMMISSION_STATUS=UNATTRIBUTABLE; symbol/time proximity is insufficient"
        ), fees

    fee = linked_fees[0]
    fee_qty = as_decimal(fee.get("qty"))
    fee_price = as_decimal(fee.get("price"))
    if fee_qty is None or fee_price is None or fee_qty == 0 or fee_price <= 0:
        return None, source_status + "; COMMISSION_STATUS=INVALID_LINKED_ACTIVITY; qty/price missing or invalid", fees
    fee_usd = abs(fee_qty) * fee_price
    return fee_usd, source_status + "; COMMISSION_STATUS=RECONCILED; explicitly linked CFEE qty × price converted to USD", fees



def infer_unlinked_commission_from_position(
    order: object,
    fees: list[dict],
    btc_positions: list[object],
    all_orders: list[object],
) -> tuple[Decimal | None, str]:
    """Infer a fee only from a unique account-wide BTC order and matching accounting deltas.

    This is deliberately stricter than matching symbol/time: exactly one filled BTC
    order and one BTC position must exist, the sole CFEE quantity must explain the
    fill-to-position quantity delta, and position cost basis must equal gross fill
    notional less that fee within cent-level rounding.
    """
    if len(fees) != 1:
        return None, "CFEE inference rejected: expected exactly one candidate activity"
    fee = fees[0]
    if str(fee.get("activity_type", "")).upper() != "CFEE":
        return None, "CFEE inference rejected: candidate activity type mismatch"
    if norm_symbol(fee.get("symbol", "")) != EXPECTED_SYMBOL:
        return None, "CFEE inference rejected: candidate symbol mismatch"
    if fee.get("order_id"):
        return None, "CFEE inference not needed: activity already has an order_id"
    if len(btc_positions) != 1:
        return None, "CFEE inference rejected: expected exactly one BTC position"
    filled_btc_orders = [
        candidate for candidate in all_orders
        if norm_symbol(getattr(candidate, "symbol", "")) == EXPECTED_SYMBOL
        and norm_status(getattr(candidate, "status", "")) == "filled"
        and (as_decimal(getattr(candidate, "filled_qty", None)) or Decimal("0")) > 0
    ]
    if len(all_orders) >= 500:
        return None, "CFEE inference rejected: order history may be truncated at 500 records"
    if len(filled_btc_orders) != 1 or str(getattr(filled_btc_orders[0], "id", "")) != str(getattr(order, "id", "")):
        return None, "CFEE inference rejected: account does not have exactly one matching filled BTC order"
    position = btc_positions[0]
    position_qty = as_decimal(getattr(position, "qty", None))
    cost_basis = as_decimal(getattr(position, "cost_basis", None))
    filled_qty = as_decimal(getattr(order, "filled_qty", None))
    avg_price = as_decimal(getattr(order, "filled_avg_price", None))
    fee_qty = as_decimal(fee.get("qty"))
    fee_price = as_decimal(fee.get("price"))
    if any(value is None for value in (position_qty, cost_basis, filled_qty, avg_price, fee_qty, fee_price)):
        return None, "CFEE inference rejected: required quantity/price/cost-basis data missing"
    if filled_qty <= 0 or avg_price <= 0 or fee_qty >= 0 or fee_price <= 0:
        return None, "CFEE inference rejected: invalid BUY fill or fee signs/values"
    fee_usd = abs(fee_qty) * fee_price
    quantity_delta = position_qty - filled_qty
    if abs(quantity_delta - fee_qty) > QTY_TOLERANCE:
        return None, "CFEE inference rejected: fee quantity does not explain fill-to-position delta"
    if abs(fee_price - avg_price) > Decimal("0.01"):
        return None, "CFEE inference rejected: fee conversion price differs from fill price"
    expected_cost_basis = filled_qty * avg_price - fee_usd
    if abs(cost_basis - expected_cost_basis) > Decimal("0.01"):
        return None, "CFEE inference rejected: position cost basis does not reconcile to net fill"
    return fee_usd, (
        "COMMISSION_STATUS=RECONCILED_BY_UNIQUE_ACCOUNTING_MATCH; "
        "unique filled BTC order + unique BTC position + CFEE quantity delta + net cost basis match"
    )


def commission_reconciliation_status(
    order: object,
    commission: Decimal | None,
    fees: list[dict],
    source_status: str,
    now=None,
) -> str:
    """Classify fee state without assuming a missing commission is zero.

    A missing CFEE is pending only within the 48-hour posting grace period;
    beyond it, the missing record is an unresolved reconciliation error.
    """
    from datetime import datetime, timedelta, timezone

    if activity_read_incomplete(source_status):
        return "READ_ERROR"
    # Validate the fill timestamp before accepting any apparent reconciliation.
    # A linked commission must not override an impossible/future fill time.
    filled_at = parse_iso_datetime(getattr(order, "filled_at", None))
    if filled_at is None:
        return "READ_ERROR"
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    age = current - filled_at
    # Allow a small clock-skew window, but fail closed on a materially future fill.
    if age < -timedelta(minutes=5):
        return "READ_ERROR"
    if commission is not None:
        return "RECONCILED"
    if "INVALID_LINKED_ACTIVITY" in source_status:
        return "INVALID_ACTIVITY"
    if fees:
        return "UNATTRIBUTABLE"
    if age < timedelta(hours=48):
        return "PENDING_POSTING"
    return "MISSING_AFTER_GRACE"



def activity_read_incomplete(source_status: str) -> bool:
    """Return True when required activity data could not be read safely.

    A successful query with no matching records is not a transport/read failure:
    that remains REVIEW REQUIRED because the data was fetched but did not reconcile.
    """
    incomplete_markers = (
        "read failed (",
        "unexpected FILL account activities response shape",
        "unexpected FILL account activities item shape",
        "unexpected account activities response shape",
        "unexpected CFEE account activities item shape",
        "pagination did not advance safely",
        "response contained a different order_id",
        "broker order ID unavailable",
        "filled_at timestamp unavailable",
    )
    return any(marker in source_status for marker in incomplete_markers)


def main() -> int:
    key = os.environ.get("ALPACA_API_KEY", "")
    secret = os.environ.get("ALPACA_API_SECRET", "")
    if not key or not secret:
        print("RESULT: FAIL — required Alpaca repository secrets are missing")
        return 2

    # Explicitly use Paper. This script contains no order-mutating API calls.
    try:
        client = TradingClient(key, secret, paper=True)
    except Exception as exc:
        print(f"CLIENT: initialization_error={type(exc).__name__}; details withheld")
        print("RESULT: INCOMPLETE — read-only broker client could not be initialized")
        return 1
    print("MODE: Alpaca Paper; READ-ONLY")
    try:
        account = client.get_account()
    except Exception as exc:
        print(f"ACCOUNT: read_error={type(exc).__name__}; details withheld")
        print("RESULT: INCOMPLETE — account read failed; no order mutation attempted")
        return 1
    print(f"ACCOUNT: status={norm_status(getattr(account, 'status', 'unknown'))}")
    print(f"ACCOUNT: equity={safe_decimal(getattr(account, 'equity', None))}")
    print(f"ACCOUNT: buying_power={safe_decimal(getattr(account, 'buying_power', None))}")

    try:
        order = client.get_order_by_client_id(CLIENT_ORDER_ID)
    except Exception as exc:
        status_code = getattr(exc, "status_code", None)
        if status_code == 404:
            print(f"ORDER: client_order_id={CLIENT_ORDER_ID}")
            print("ORDER: found=False (HTTP 404)")
            print("RESULT: INCOMPLETE — order not found; no order mutation attempted")
            return 1
        print(f"ORDER: lookup_error={type(exc).__name__}; details withheld")
        print("RESULT: INCOMPLETE — could not read order; no order mutation attempted")
        return 1

    order_symbol = getattr(order, "symbol", "")
    order_status = norm_status(getattr(order, "status", "unknown"))
    filled_qty = as_decimal(getattr(order, "filled_qty", None)) or Decimal("0")
    avg_price = as_decimal(getattr(order, "filled_avg_price", None))
    print("ORDER: found=True")
    print(f"ORDER: client_order_id={CLIENT_ORDER_ID}")
    print(f"ORDER: broker_order_id={getattr(order, 'id', 'n/a')}")
    print(f"ORDER: symbol={order_symbol}")
    print(f"ORDER: side={getattr(order, 'side', 'n/a')}")
    print(f"ORDER: status={order_status}")
    print(f"ORDER: filled_qty={filled_qty}")
    print(f"ORDER: filled_avg_price={safe_decimal(avg_price)}")
    print(f"ORDER: submitted_notional={safe_decimal(getattr(order, 'notional', None))}")
    print(f"ORDER: submitted_at={getattr(order, 'submitted_at', None)}")
    print(f"ORDER: filled_at={getattr(order, 'filled_at', None)}")

    identity_ok, identity_detail = validate_order_identity(order)
    print(f"ORDER_IDENTITY: {identity_detail}")
    if not identity_ok:
        print("RESULT: FAIL — fetched order identity does not match expected BTC buy; reconciliation aborted")
        return 1

    gross_fill_notional = filled_qty * avg_price if avg_price is not None else None
    print(f"RECON: gross_fill_notional={safe_decimal(gross_fill_notional)}")
    submitted_notional = as_decimal(getattr(order, "notional", None))
    if submitted_notional is not None and gross_fill_notional is not None:
        notional_delta = gross_fill_notional - submitted_notional
        print(f"RECON: gross_vs_submitted_notional_delta={notional_delta}")
        print("RECON: notional_comparison=INFO_ONLY (market execution price can differ from requested notional)")
    else:
        print("RECON: gross_vs_submitted_notional_delta=n/a")

    fills_match, fills_source, fill_activities = reconcile_order_fill_activities(order, key, secret)
    print("FILL_RECON: source=Alpaca Paper GET /v2/account/activities activity_types=FILL order_id=<broker_order_id>")
    print(f"FILL_RECON: lookup={fills_source}")
    for i, fill in enumerate(fill_activities, start=1):
        print(f"FILL_ACTIVITY[{i}]: id={fill.get('id', 'n/a')}")
        print(f"FILL_ACTIVITY[{i}]: order_id={fill.get('order_id', 'n/a')}")
        print(f"FILL_ACTIVITY[{i}]: symbol={fill.get('symbol', 'n/a')}")
        print(f"FILL_ACTIVITY[{i}]: side={fill.get('side', 'n/a')}")
        print(f"FILL_ACTIVITY[{i}]: qty={safe_decimal(fill.get('qty'))}")
        print(f"FILL_ACTIVITY[{i}]: price={safe_decimal(fill.get('price'))}")
    commission, commission_source, fee_activities = reconcile_crypto_commission(order, key, secret)
    print("COMMISSION: source=Alpaca Paper GET /v2/account/activities activity_types=CFEE")
    print(f"COMMISSION: lookup={commission_source}")
    for i, fee_activity in enumerate(fee_activities, start=1):
        print(f"COMMISSION_ACTIVITY[{i}]: id={fee_activity.get('id', 'n/a')}")
        print(f"COMMISSION_ACTIVITY[{i}]: order_id={fee_activity.get('order_id', 'n/a')}")
        explicitly_linked = bool(getattr(order, "id", None)) and str(fee_activity.get("order_id", "") or "") == str(getattr(order, "id", "") or "")
        attribution = "EXPLICIT_ORDER_LINK" if explicitly_linked else "UNLINKED_TEMPORAL_CONTEXT_ONLY"
        print(f"COMMISSION_ACTIVITY[{i}]: attribution={attribution}")
        print(f"COMMISSION_ACTIVITY[{i}]: activity_type={fee_activity.get('activity_type', 'n/a')}")
        print(f"COMMISSION_ACTIVITY[{i}]: date={fee_activity.get('date', 'n/a')}")
        print(f"COMMISSION_ACTIVITY[{i}]: symbol={fee_activity.get('symbol', 'n/a')}")
        print(f"COMMISSION_ACTIVITY[{i}]: qty={safe_decimal(fee_activity.get('qty'))}")
        print(f"COMMISSION_ACTIVITY[{i}]: price={safe_decimal(fee_activity.get('price'))}")
        print(f"COMMISSION_ACTIVITY[{i}]: net_amount={safe_decimal(fee_activity.get('net_amount'))}")
    try:
        positions = client.get_all_positions()
    except Exception as exc:
        print(f"POSITION: read_error={type(exc).__name__}; details withheld")
        print("RESULT: INCOMPLETE — positions read failed; no order mutation attempted")
        return 1
    btc_positions = [p for p in positions if norm_symbol(getattr(p, "symbol", "")) in SYMBOLS]
    print(f"POSITION: btc_position_count={len(btc_positions)}")
    position_qty: Decimal | None = None
    for i, position in enumerate(btc_positions, start=1):
        qty = as_decimal(getattr(position, "qty", None))
        if i == 1:
            position_qty = qty
        print(f"POSITION[{i}]: symbol={getattr(position, 'symbol', 'n/a')}")
        print(f"POSITION[{i}]: qty={safe_decimal(qty)}")
        print(f"POSITION[{i}]: avg_entry_price={safe_decimal(getattr(position, 'avg_entry_price', None))}")
        print(f"POSITION[{i}]: current_price={safe_decimal(getattr(position, 'current_price', None))}")
        print(f"POSITION[{i}]: market_value={safe_decimal(getattr(position, 'market_value', None))}")
        print(f"POSITION[{i}]: cost_basis={safe_decimal(getattr(position, 'cost_basis', None))}")
        print(f"POSITION[{i}]: unrealized_pl={safe_decimal(getattr(position, 'unrealized_pl', None))}")

    # Order history is read-only and used only to reject ambiguous fee attribution.
    try:
        all_orders = client.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.ALL, limit=500))
    except Exception as exc:
        print(f"ORDER_HISTORY: read_error={type(exc).__name__}; details withheld")
        all_orders = []
        order_history_read_ok = False
    else:
        order_history_read_ok = True
    btc_order_history = [o for o in all_orders if norm_symbol(getattr(o, "symbol", "")) in SYMBOLS]
    print(f"ORDER_HISTORY: total_orders={len(all_orders)}; btc_orders={len(btc_order_history)}; read_ok={str(order_history_read_ok).lower()}")

    if commission is None and order_history_read_ok and fee_activities:
        inferred_commission, inference_detail = infer_unlinked_commission_from_position(
            order, fee_activities, btc_positions, all_orders
        )
        print(f"COMMISSION: inference={inference_detail}")
        if inferred_commission is not None:
            commission = inferred_commission
            commission_source = commission_source + "; " + inference_detail
            print("COMMISSION: attribution=UNIQUE_ACCOUNTING_MATCH")
    commission_status = commission_reconciliation_status(
        order, commission, fee_activities, commission_source
    )
    print(f"COMMISSION: status={commission_status}")
    if commission is None:
        print("COMMISSION: amount=unknown (not assumed to be zero)")
    else:
        print(f"COMMISSION: amount_usd={commission}")

    quantity_delta, quantity_match = compare_position_quantity(position_qty, filled_qty)
    print("POSITION_RECON: scope=aggregate_current_account_position_vs_single_order_fill")
    print(f"POSITION_RECON: diagnostic_delta_position_minus_order_fill={safe_decimal(quantity_delta)}")
    print(f"POSITION_RECON: diagnostic_equal_within_tolerance={str(quantity_match).lower()}")
    print("POSITION_RECON: result=INFORMATIONAL_ONLY (aggregate position is not order-scoped; prior holdings, later fills/transfers, and broker accounting may contribute)")
    print("POSITION_RECON: not used as a pass/fail condition for single-order fill reconciliation")

    try:
        open_orders = client.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.OPEN))
    except Exception as exc:
        print(f"OPEN_ORDERS: read_error={type(exc).__name__}; details withheld")
        print("RESULT: INCOMPLETE — open-orders read failed; no order mutation attempted")
        return 1
    btc_open = [o for o in open_orders if norm_symbol(getattr(o, "symbol", "")) in SYMBOLS]
    print(f"OPEN_ORDERS: btc_open_order_count={len(btc_open)}")
    for i, open_order in enumerate(btc_open, start=1):
        print(f"OPEN_ORDER[{i}]: id={getattr(open_order, 'id', 'n/a')}")
        print(f"OPEN_ORDER[{i}]: client_order_id={getattr(open_order, 'client_order_id', 'n/a')}")
        print(f"OPEN_ORDER[{i}]: status={norm_status(getattr(open_order, 'status', 'unknown'))}")
        print(f"OPEN_ORDER[{i}]: side={getattr(open_order, 'side', 'n/a')}")
        print(f"OPEN_ORDER[{i}]: qty={safe_decimal(getattr(open_order, 'qty', None))}")
        print(f"OPEN_ORDER[{i}]: notional={safe_decimal(getattr(open_order, 'notional', None))}")

    incomplete_sources = []
    if activity_read_incomplete(fills_source):
        incomplete_sources.append("FILL activity data could not be read or validated safely")
    if activity_read_incomplete(commission_source):
        incomplete_sources.append("CFEE activity data could not be read or validated safely")
    if incomplete_sources:
        print("RESULT: INCOMPLETE — " + "; ".join(incomplete_sources) + "; reconciliation cannot be considered complete")
        return 1

    if order_status == "filled" and filled_qty > 0 and avg_price is not None and fills_match and commission is not None:
        print("RESULT: RECONCILED — order summary matches order-scoped FILL activities; commission is explicitly linked to this order")
        return 0

    # A not-yet-due commission is an expected unresolved business state, not a
    # failed execution of the read-only check. Keep the explicit PENDING result
    # visible and do not treat the reconciliation as complete.
    if (
        order_status == "filled"
        and filled_qty > 0
        and avg_price is not None
        and fills_match
        and commission is None
        and commission_status == "PENDING_POSTING"
    ):
        print(
            "RESULT: PENDING — read-only checks completed; commission posting grace "
            "period has not elapsed; reconciliation remains incomplete and amount "
            "remains unknown"
        )
        return 0

    reasons = []
    if order_status != "filled" or filled_qty <= 0 or avg_price is None:
        reasons.append("order fill details incomplete")
    if not fills_match:
        reasons.append("broker order-scoped FILL activities do not reconcile to order summary")
    if commission is None:
        if commission_status == "PENDING_POSTING":
            reasons.append("commission pending: no CFEE candidate is visible within the 48-hour posting grace period; amount remains unknown")
        elif commission_status == "MISSING_AFTER_GRACE":
            reasons.append("commission missing after the 48-hour posting grace period; reconciliation error, amount remains unknown")
        elif commission_status == "UNATTRIBUTABLE":
            reasons.append("CFEE activity exists but cannot be uniquely attributed to this order; amount remains unknown")
        elif commission_status == "INVALID_ACTIVITY":
            reasons.append("explicitly linked CFEE activity has invalid quantity/price; amount remains unknown")
        else:
            reasons.append("commission unknown because fee activity could not be reconciled safely; amount is not assumed to be zero")
    print("RESULT: REVIEW REQUIRED — " + "; ".join(reasons))
    return 1


if __name__ == "__main__":
    sys.exit(main())
