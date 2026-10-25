"""Read-only reconciliation for the existing Alpaca Paper BTC E2E order.

This script calls only read endpoints. It never submits, replaces, cancels, or closes orders.
"""
from __future__ import annotations

import os
import sys
from decimal import Decimal

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import QueryOrderStatus
from alpaca.trading.requests import GetOrdersRequest

CLIENT_ORDER_ID = "stmaa-btc-e2e-singleton"
SYMBOLS = {"BTCUSD", "BTC/USD", "BTC-USD"}


def norm_symbol(value: object) -> str:
    return str(value or "").upper().replace("/", "").replace("-", "")


def norm_status(value: object) -> str:
    raw = getattr(value, "value", value)
    return str(raw or "unknown").rsplit(".", 1)[-1].lower()


def safe_decimal(value: object) -> str:
    if value is None or value == "":
        return "n/a"
    try:
        return str(Decimal(str(value)))
    except Exception:
        return str(value)


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
    filled_qty = Decimal(str(getattr(order, "filled_qty", "0") or "0"))
    avg_price = getattr(order, "filled_avg_price", None)
    print(f"ORDER: found=True")
    print(f"ORDER: client_order_id={CLIENT_ORDER_ID}")
    print(f"ORDER: broker_order_id={getattr(order, 'id', 'n/a')}")
    print(f"ORDER: symbol={order_symbol}")
    print(f"ORDER: side={getattr(order, 'side', 'n/a')}")
    print(f"ORDER: status={order_status}")
    print(f"ORDER: filled_qty={filled_qty}")
    print(f"ORDER: filled_avg_price={safe_decimal(avg_price)}")
    print(f"ORDER: notional={safe_decimal(getattr(order, 'notional', None))}")
    print(f"ORDER: submitted_at={getattr(order, 'submitted_at', None)}")
    print(f"ORDER: filled_at={getattr(order, 'filled_at', None)}")

    positions = client.get_all_positions()
    btc_positions = [p for p in positions if norm_symbol(getattr(p, "symbol", "")) in SYMBOLS]
    print(f"POSITION: btc_position_count={len(btc_positions)}")
    for i, position in enumerate(btc_positions, start=1):
        print(f"POSITION[{i}]: symbol={getattr(position, 'symbol', 'n/a')}")
        print(f"POSITION[{i}]: qty={safe_decimal(getattr(position, 'qty', None))}")
        print(f"POSITION[{i}]: avg_entry_price={safe_decimal(getattr(position, 'avg_entry_price', None))}")
        print(f"POSITION[{i}]: current_price={safe_decimal(getattr(position, 'current_price', None))}")
        print(f"POSITION[{i}]: market_value={safe_decimal(getattr(position, 'market_value', None))}")
        print(f"POSITION[{i}]: cost_basis={safe_decimal(getattr(position, 'cost_basis', None))}")
        print(f"POSITION[{i}]: unrealized_pl={safe_decimal(getattr(position, 'unrealized_pl', None))}")

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

    if order_status == "filled" and filled_qty > 0 and avg_price is not None and btc_positions:
        print("RESULT: RECONCILED — order filled and BTC position visible")
        return 0
    print("RESULT: REVIEW REQUIRED — order/position state does not satisfy the expected filled-and-position-present condition")
    return 1


if __name__ == "__main__":
    sys.exit(main())
