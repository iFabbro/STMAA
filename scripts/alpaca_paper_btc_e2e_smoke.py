"""Bounded Alpaca Paper crypto smoke test for BTC/USD.

Preflight is read-only. Submits at most one $10 notional Paper buy, reconciles
ambiguous submission by client_order_id, and never retries or cancels it.
"""
from __future__ import annotations

import os
import sys
import time
from decimal import Decimal
from pathlib import Path

from alpaca.data.historical import CryptoHistoricalDataClient
from alpaca.data.requests import CryptoLatestQuoteRequest
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, QueryOrderStatus, TimeInForce
from alpaca.trading.requests import GetOrdersRequest, MarketOrderRequest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.execution.broker_executor import BrokerExecutor  # noqa: E402
from src.execution.smoke_order_preflight import validate_smoke_order_preflight  # noqa: E402

SYMBOL = "BTC/USD"
MAX_NOTIONAL_USD = Decimal("10.00")
PAPER_ENDPOINT = "https://paper-api.alpaca.markets"
TERMINAL = {"filled", "partially_filled", "rejected", "canceled", "cancelled", "expired"}


def norm_symbol(value: object) -> str:
    """Canonicalize Alpaca crypto symbols (BTC/USD, BTCUSD, or BTC-USD)."""
    return str(value or "").upper().replace("/", "").replace("-", "")


def norm_status(value: object) -> str:
    """Normalize alpaca-py enum values and plain strings to stable status names."""
    raw = getattr(value, "value", value)
    return str(raw or "unknown").rsplit(".", 1)[-1].lower()


def main() -> int:
    key = os.environ.get("ALPACA_API_KEY", "")
    secret = os.environ.get("ALPACA_API_SECRET", "")
    if not key or not secret:
        raise SystemExit("FAIL: required Alpaca repository secrets are missing")

    # Explicitly pin all requests to the Paper environment.
    client = TradingClient(key, secret, paper=True)
    executor = BrokerExecutor(
        api_key=key,
        api_secret=secret,
        base_url=PAPER_ENDPOINT,
        paper_trading=True,
    )
    if executor.paper_trading is not True:
        raise SystemExit("FAIL: STMAA executor is not in Paper mode")

    # Hold the same state-path lock used by normal BrokerExecutor execution.
    with executor.execution_lock():
    
        account = client.get_account()
        account_status = str(getattr(account, "status", "")).lower()
        if account_status not in {"active", "accountstatus.active"}:
            raise SystemExit(f"FAIL: Paper account is not active (status={account_status or 'unknown'})")
    
        asset = client.get_asset(SYMBOL)
        if not bool(getattr(asset, "tradable", False)):
            raise SystemExit(f"FAIL: {SYMBOL} is not marked tradable for this account")
        asset_class = str(getattr(asset, "class", "")).lower()
        if asset_class and "crypto" not in asset_class:
            raise SystemExit(f"FAIL: {SYMBOL} resolved to unexpected asset class {asset_class}")
    
        # Crypto purchasing power is not necessarily the stock buying_power field.
        crypto_bp = getattr(account, "non_marginable_buying_power", None)
        if crypto_bp is None:
            crypto_bp = getattr(account, "buying_power", None)
        if crypto_bp is None or Decimal(str(crypto_bp)) < MAX_NOTIONAL_USD:
            raise SystemExit("FAIL: crypto/non-marginable buying power is insufficient")
    
        # A fixed broker-side client_order_id is the durable idempotency key.
        # Unlike runner-local files, Alpaca's order record survives completed runs.
        # Any lookup error is fail-closed: never submit when prior outcome is uncertain.
        client_order_id = "stmaa-btc-e2e-singleton"
        try:
            prior_order = client.get_order_by_client_id(client_order_id)
        except Exception as exc:
            # Alpaca reports an absent client_order_id as HTTP 404. Only that
            # definitive response permits the first submission; all other errors
            # leave the previous outcome uncertain and must block execution.
            if getattr(exc, "status_code", None) == 404:
                prior_order = None
            else:
                raise SystemExit(
                    f"FAIL: cannot verify prior BTC E2E order ({type(exc).__name__}); refusing submission"
                ) from None
        if prior_order is not None:
            prior_status = str(getattr(prior_order, "status", "unknown")).lower()
            raise SystemExit(
                "FAIL: durable BTC E2E idempotency key already exists "
                f"(status={prior_status}); refusing another order"
            )
    
        open_orders = client.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.OPEN))
        btc_orders = [o for o in open_orders if norm_symbol(getattr(o, "symbol", "")) == SYMBOL]
        if btc_orders:
            raise SystemExit(
                f"FAIL: {len(btc_orders)} open {SYMBOL} order(s) already exist; refusing duplicate order"
            )
    
        # Crypto has a separate market-data endpoint. Require a current, timestamped
        # quote and the same strict account/position/open-order/exposure preflight
        # used by the stock smoke path before placing this bounded notional order.
        crypto_data = CryptoHistoricalDataClient(key, secret)
        crypto_quotes = crypto_data.get_crypto_latest_quote(
            CryptoLatestQuoteRequest(symbol_or_symbols=[SYMBOL])
        )
        raw_quote = crypto_quotes.get(SYMBOL) if isinstance(crypto_quotes, dict) else None
        try:
            bid = float(getattr(raw_quote, "bid_price", None))
            ask = float(getattr(raw_quote, "ask_price", None))
            quote_timestamp = getattr(raw_quote, "timestamp", None)
            if not (bid > 0 and ask > 0 and ask >= bid):
                raise ValueError("invalid bid/ask")
            smoke_quote = {"price": (bid + ask) / 2.0, "timestamp": quote_timestamp}
        except (TypeError, ValueError, OverflowError) as exc:
            raise SystemExit("FAIL: no valid BTC/USD market quote; refusing smoke order") from exc
        validate_smoke_order_preflight(
            executor, SYMBOL, float(MAX_NOTIONAL_USD), smoke_quote
        )
    
        print("PREFLIGHT: PASS")
        print(f"Account status: {account_status}")
        print(f"Asset tradable: {getattr(asset, 'tradable', None)}")
        print(f"Crypto buying power sufficient: {Decimal(str(crypto_bp)) >= MAX_NOTIONAL_USD}")
        print(f"Existing open {SYMBOL} orders: 0")
        print("Environment: Alpaca Paper; no Live endpoint configured.")
    
        request = MarketOrderRequest(
            symbol=SYMBOL,
            notional=float(MAX_NOTIONAL_USD),
            side=OrderSide.BUY,
            time_in_force=TimeInForce.GTC,
            client_order_id=client_order_id,
        )
    
        try:
            order = client.submit_order(order_data=request)
        except Exception as exc:
            print(f"SUBMISSION: response ambiguous ({type(exc).__name__}); reconciling once.")
            try:
                order = client.get_order_by_client_id(client_order_id)
            except Exception:
                raise SystemExit("FAIL: order outcome unknown; no retry performed") from None
            if order is None:
                raise SystemExit(
                    "FAIL: order not found during reconciliation; no retry performed"
                ) from None
    
        print(f"ORDER: client_order_id={client_order_id}")
        print(f"ORDER: broker_order_id_present={bool(getattr(order, 'id', None))}")
        status = norm_status(getattr(order, "status", "unknown"))
        deadline = time.monotonic() + 90
        while status not in TERMINAL and time.monotonic() < deadline:
            time.sleep(3)
            try:
                order = client.get_order_by_client_id(client_order_id)
                status = norm_status(getattr(order, "status", "unknown"))
            except Exception as exc:
                print(f"ORDER POLL: transient read error ({type(exc).__name__}); no resubmission.")
        filled_qty = Decimal(str(getattr(order, "filled_qty", "0") or "0"))
        avg_price = getattr(order, "filled_avg_price", None)
        print(f"ORDER: final_status={status}")
        print(f"ORDER: filled_qty={filled_qty}")
        print(f"ORDER: filled_avg_price_present={avg_price is not None}")
    
        # Read back BTC-specific position and open-order state.
        positions = client.get_all_positions()
        btc_positions = [p for p in positions if norm_symbol(getattr(p, "symbol", "")) == SYMBOL]
        remaining_orders = client.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.OPEN))
        btc_open = [o for o in remaining_orders if norm_symbol(getattr(o, "symbol", "")) == SYMBOL]
        print(f"POSTCHECK: btc_position_present={bool(btc_positions)}")
        print(f"POSTCHECK: btc_open_orders={len(btc_open)}")
    
        # Persist the broker order identity/status in STMAA state and verify the
        # actual round-trip, not merely that a file exists.
        executor.state["btc_paper_e2e_order"] = {
            "client_order_id": client_order_id,
            "broker_order_id": str(getattr(order, "id", "") or ""),
            "symbol": SYMBOL,
            "side": "buy",
            "notional_usd": str(MAX_NOTIONAL_USD),
            "status": status,
            "filled_qty": str(filled_qty),
            "filled_avg_price": str(avg_price) if avg_price is not None else None,
        }
        executor._save_state()
        expected_order_state = dict(executor.state["btc_paper_e2e_order"])
        restarted = BrokerExecutor(
            api_key=key,
            api_secret=secret,
            base_url=PAPER_ENDPOINT,
            paper_trading=True,
        )
        actual_order_state = restarted.state.get("btc_paper_e2e_order")
        if actual_order_state != expected_order_state:
            raise SystemExit(
                "FAIL: BTC order state did not survive save/reload round-trip"
            )
        print("RESTART: persisted_state_round_trip=PASS")
    
        if status != "filled" or filled_qty <= 0 or avg_price is None:
            raise SystemExit(
                "FAIL: BTC order was not confirmed filled within 90 seconds. "
                "No retry or automatic cancellation performed; inspect the Paper dashboard."
            )
        if not btc_positions:
            raise SystemExit("FAIL: order filled but BTC position was not visible in postcheck")
        print("RESULT: BTC Paper order filled and position verified.")
        return 0
    

if __name__ == "__main__":
    raise SystemExit(main())
