"""Fail-closed preflight shared by manual, bounded Paper smoke orders."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
import os


def validate_smoke_order_preflight(executor, symbol: str, max_notional: float, quote: dict) -> dict:
    """Validate account, positions, orders, exposure budget and fresh quote before a smoke buy.

    This is intentionally stricter than the normal strategy path: any open broker
    order blocks the smoke order because these diagnostic scripts must not compete
    with unrelated pending exposure.
    """
    try:
        notional = float(max_notional)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Smoke order notional is invalid") from exc
    if not math.isfinite(notional) or notional <= 0:
        raise RuntimeError("Smoke order notional must be finite and positive")

    account = executor.get_account_status()
    if not isinstance(account, dict) or account.get("account_data_fresh") is not True:
        raise RuntimeError("Smoke order blocked: broker account snapshot is missing or stale")
    account_id = account.get("account_id")
    if not isinstance(account_id, str) or not account_id.strip():
        raise RuntimeError("Smoke order blocked: broker account identity is unverified")
    try:
        equity = float(account.get("equity"))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Smoke order blocked: broker equity is invalid") from exc
    if not math.isfinite(equity) or equity <= 0:
        raise RuntimeError("Smoke order blocked: broker equity is invalid")

    state = getattr(executor, "state", {})
    unresolved = state.get("order_submission_unresolved", {})
    confirmed = state.get("order_submission_confirmed", {})
    terminal = {"filled", "canceled", "cancelled", "expired", "rejected"}
    active_confirmed = [
        order for order in confirmed.values()
        if str(order.get("status", "unknown")).strip().lower() not in terminal
    ]
    if unresolved or active_confirmed:
        raise RuntimeError("Smoke order blocked: persisted broker submissions need reconciliation")

    positions = executor.get_positions()
    if not isinstance(positions, dict):
        raise RuntimeError("Smoke order blocked: broker positions are unreadable")
    gross_positions = 0.0
    for position_symbol, position in positions.items():
        if not isinstance(position, dict) or "market_value" not in position:
            raise RuntimeError(
                f"Smoke order blocked: exposure is unknown for position {position_symbol}"
            )
        try:
            market_value = float(position["market_value"])
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                f"Smoke order blocked: exposure is invalid for position {position_symbol}"
            ) from exc
        if not math.isfinite(market_value):
            raise RuntimeError(
                f"Smoke order blocked: exposure is invalid for position {position_symbol}"
            )
        gross_positions += abs(market_value)

    open_orders = executor._get_open_orders()
    if not isinstance(open_orders, (list, tuple)):
        raise RuntimeError("Smoke order blocked: broker open-order snapshot is invalid")
    if open_orders:
        raise RuntimeError("Smoke order blocked: existing open orders make exposure reconciliation ambiguous")

    try:
        price = float(quote.get("price")) if isinstance(quote, dict) else float("nan")
        raw_timestamp = quote.get("timestamp") if isinstance(quote, dict) else None
        if isinstance(raw_timestamp, datetime):
            quote_time = raw_timestamp
        elif raw_timestamp:
            quote_time = datetime.fromisoformat(str(raw_timestamp).replace("Z", "+00:00"))
        else:
            raise ValueError("quote timestamp missing")
        if quote_time.tzinfo is None:
            raise ValueError("quote timestamp has no timezone")
        quote_time = quote_time.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError("Smoke order blocked: a valid timestamped market quote is required") from exc

    age = datetime.now(timezone.utc) - quote_time
    if (
        not math.isfinite(price) or price <= 0
        or age > timedelta(minutes=5)
        or age < -timedelta(seconds=30)
    ):
        raise RuntimeError("Smoke order blocked: market quote is invalid, stale, or future-dated")

    try:
        max_exposure = float(os.getenv("MAX_PORTFOLIO_EXPOSURE", "0.50"))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Smoke order blocked: exposure limit is invalid") from exc
    if not math.isfinite(max_exposure) or not 0 < max_exposure <= 1:
        raise RuntimeError("Smoke order blocked: exposure limit is invalid")
    if gross_positions + notional > equity * max_exposure:
        raise RuntimeError("Smoke order blocked: position plus smoke order exceeds exposure limit")

    return account
