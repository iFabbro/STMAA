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
SYMBOLS = {"BTCUSD", "BTC/USD", "BTC-USD"}
# Tiny rounding tolerance for comparing broker-reported crypto quantities.
QTY_TOLERANCE = Decimal("0.000000001")


def norm_symbol(value: object) -> str:
    return str(value or "").upper().replace("/", "").replace("-", "")


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


def fetch_crypto_fee_activities(order: object, key: str, secret: str) -> tuple[list[dict], str]:
    """Read Alpaca Paper account activities; never mutate account state.

    Alpaca documents CFEE activities as the source for crypto trading fees.
    Legacy CFEE records may not carry order_id, so only a unique BTCUSD fee
    record in the bounded fill-time window can be associated automatically.
    """
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
            activities.extend(item for item in payload if isinstance(item, dict))
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
    return btc_fees, f"queried CFEE activities from {after} through {until}"


def reconcile_crypto_commission(order: object, key: str, secret: str) -> tuple[Decimal | None, str, list[dict]]:
    """Return USD-equivalent crypto fee only when one supported CFEE record matches."""
    fees, source_status = fetch_crypto_fee_activities(order, key, secret)
    if len(fees) != 1:
        return None, source_status + f"; matching BTCUSD CFEE records={len(fees)} (requires unique record)", fees

    fee = fees[0]
    fee_qty = as_decimal(fee.get("qty"))
    fee_price = as_decimal(fee.get("price"))
    # For BTC/USD buys, Alpaca documents the crypto fee quantity in BTC;
    # multiplying its absolute quantity by the broker-reported fee price
    # expresses it in USD. Do not substitute net_amount when it is zero.
    if fee_qty is None or fee_price is None or fee_qty == 0 or fee_price <= 0:
        return None, source_status + "; CFEE qty/price missing or invalid", fees
    fee_usd = abs(fee_qty) * fee_price
    return fee_usd, source_status + "; unique CFEE qty × price converted to USD", fees


def main() -> int:
    key = os.environ.get("ALPACA_API_KEY", "")
    secret = os.environ.get("ALPACA_API_SECRET", "")
    if not key or not secret:
        print("RESULT: FAIL — required Alpaca repository secrets are missing")
        return 2

    # Explicitly use Paper. This script contains no order-mutating API calls.
    client = TradingClient(key, secret, paper=True)
    print("MODE: Alpaca Paper; READ-ONLY")
    account = client.get_account()
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

    gross_fill_notional = filled_qty * avg_price if avg_price is not None else None
    print(f"RECON: gross_fill_notional={safe_decimal(gross_fill_notional)}")
    submitted_notional = as_decimal(getattr(order, "notional", None))
    if submitted_notional is not None and gross_fill_notional is not None:
        notional_delta = gross_fill_notional - submitted_notional
        print(f"RECON: gross_vs_submitted_notional_delta={notional_delta}")
        print("RECON: notional_comparison=INFO_ONLY (market execution price can differ from requested notional)")
    else:
        print("RECON: gross_vs_submitted_notional_delta=n/a")

    # Preferred source: Alpaca Paper Account Activities CFEE records.
    # Crypto fees can be posted after execution; missing/ambiguous records stay
    # REVIEW REQUIRED. Never infer zero from an empty result.
    commission, commission_source, fee_activities = reconcile_crypto_commission(order, key, secret)
    print("COMMISSION: source=Alpaca Paper GET /v2/account/activities activity_types=CFEE")
    print(f"COMMISSION: lookup={commission_source}")
    for i, fee_activity in enumerate(fee_activities, start=1):
        print(f"COMMISSION_ACTIVITY[{i}]: id={fee_activity.get('id', 'n/a')}")
        print(f"COMMISSION_ACTIVITY[{i}]: activity_type={fee_activity.get('activity_type', 'n/a')}")
        print(f"COMMISSION_ACTIVITY[{i}]: date={fee_activity.get('date', 'n/a')}")
        print(f"COMMISSION_ACTIVITY[{i}]: symbol={fee_activity.get('symbol', 'n/a')}")
        print(f"COMMISSION_ACTIVITY[{i}]: qty={safe_decimal(fee_activity.get('qty'))}")
        print(f"COMMISSION_ACTIVITY[{i}]: price={safe_decimal(fee_activity.get('price'))}")
        print(f"COMMISSION_ACTIVITY[{i}]: net_amount={safe_decimal(fee_activity.get('net_amount'))}")
    if commission is None:
        print("COMMISSION: amount=unknown (not assumed to be zero)")
    else:
        print(f"COMMISSION: amount_usd={commission}")

    positions = client.get_all_positions()
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

    quantity_match = False
    if position_qty is not None:
        quantity_delta = position_qty - filled_qty
        quantity_match = abs(quantity_delta) <= QTY_TOLERANCE
        print(f"RECON: position_minus_order_filled_qty={quantity_delta}")
        print(f"RECON: position_qty_matches_order_fill={str(quantity_match).lower()}")
        if not quantity_match:
            print("RECON: quantity_difference_requires_review=true (possible prior holdings, later activity, or broker accounting)")
    else:
        print("RECON: position_minus_order_filled_qty=n/a")
        print("RECON: position_qty_matches_order_fill=false")

    open_orders = client.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.OPEN))
    btc_open = [o for o in open_orders if norm_symbol(getattr(o, "symbol", "")) in SYMBOLS]
    print(f"OPEN_ORDERS: btc_open_order_count={len(btc_open)}")
    for i, open_order in enumerate(btc_open, start=1):
        print(f"OPEN_ORDER[{i}]: id={getattr(open_order, 'id', 'n/a')}")
        print(f"OPEN_ORDER[{i}]: client_order_id={getattr(open_order, 'client_order_id', 'n/a')}")
        print(f"OPEN_ORDER[{i}]: status={norm_status(getattr(open_order, 'status', 'unknown'))}")
        print(f"OPEN_ORDER[{i}]: side={getattr(open_order, 'side', 'n/a')}")
        print(f"OPEN_ORDER[{i}]: qty={safe_decimal(getattr(open_order, 'qty', None))}")
        print(f"OPEN_ORDER[{i}]: notional={safe_decimal(getattr(open_order, 'notional', None))}")

    # A green result requires quantitative quantity agreement and an actual
    # broker-reported commission value. Missing commission data stays REVIEW.
    if (
        order_status == "filled"
        and filled_qty > 0
        and avg_price is not None
        and position_qty is not None
        and quantity_match
        and commission is not None
    ):
        print("RESULT: RECONCILED — order, position quantity, and commission field verified")
        return 0

    reasons = []
    if order_status != "filled" or filled_qty <= 0 or avg_price is None:
        reasons.append("order fill details incomplete")
    if not quantity_match:
        reasons.append("position quantity does not match order fill within tolerance")
    if commission is None:
        reasons.append("unique supported Alpaca CFEE activity could not be reconciled to the order")
    print("RESULT: REVIEW REQUIRED — " + "; ".join(reasons))
    return 1


if __name__ == "__main__":
    sys.exit(main())
