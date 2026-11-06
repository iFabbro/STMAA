"""Deterministic request and batch primitives for RW-08.3 OPG execution."""

import hashlib
import json
import re
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest


def _normalize_signal_timestamp(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("signal_bar_at must be a non-empty timezone-aware timestamp")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("signal_bar_at must be a valid ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError("signal_bar_at must be timezone-aware")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_target_session(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("target_session must be an ISO date")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise ValueError("target_session must be an ISO date") from exc


def build_opg_batch_id(*, account_id, signal_bar_at, target_session, model_contract_version, execution_sizing_contract) -> str:
    account = str(account_id or "").strip()
    contract = str(execution_sizing_contract or "").strip()
    if not account or not contract:
        raise ValueError("account_id and execution_sizing_contract are required")
    if type(model_contract_version) is not int or model_contract_version <= 0:
        raise ValueError("model_contract_version must be a positive integer")
    identity = {
        "account_id": account,
        "signal_bar_at": _normalize_signal_timestamp(signal_bar_at),
        "target_session": _canonical_target_session(target_session),
        "model_contract_version": model_contract_version,
        "execution_sizing_contract": contract,
    }
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return "stmaa-opg-" + digest[:32]


def build_opg_client_order_id(batch_id: str, symbol: str) -> str:
    batch = str(batch_id or "").strip()
    normalized = str(symbol or "").strip().upper()
    if not batch or not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,14}", normalized):
        raise ValueError("batch_id and a valid normalized symbol are required")
    digest = hashlib.sha256(f"{batch}:{normalized}".encode()).hexdigest()
    value = "stmaa-opg-" + digest[:32]
    if len(value) > 48:
        raise ValueError("client_order_id exceeds Alpaca's supported length")
    return value


def build_opg_order_request(
    symbol: str,
    quantity: int,
    client_order_id: str,
    side: str = "buy",
    *,
    limit_price=None,
):
    """Build a price-bounded LOO BUY or risk-reducing MOO SELL.

    A market-on-open BUY cannot enforce a hard dollar exposure limit because
    its execution price is unknown until the opening auction. BUYs therefore
    use a limit-on-open order capped at the completed signal-bar Close.
    """
    normalized = str(symbol or "").strip().upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,14}", normalized):
        raise ValueError("symbol is invalid")
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
        raise ValueError("OPG quantity must be a positive whole-share integer")
    if not isinstance(client_order_id, str) or not client_order_id or len(client_order_id) > 48:
        raise ValueError("client_order_id must contain 1 to 48 characters")
    normalized_side = str(side or "").strip().lower()
    if normalized_side not in {"buy", "sell"}:
        raise ValueError("OPG side must be buy or sell")
    order_side = OrderSide.BUY if normalized_side == "buy" else OrderSide.SELL
    if normalized_side == "buy":
        try:
            price = Decimal(str(limit_price))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError("OPG BUY requires a finite positive limit price") from exc
        if not price.is_finite() or price <= 0:
            raise ValueError("OPG BUY requires a finite positive limit price")
        return LimitOrderRequest(
            symbol=normalized,
            qty=quantity,
            side=order_side,
            time_in_force=TimeInForce.OPG,
            limit_price=float(price),
            client_order_id=client_order_id,
        )
    if limit_price is not None:
        raise ValueError("OPG SELL uses a market-on-open order without a limit price")
    return MarketOrderRequest(
        symbol=normalized,
        qty=quantity,
        side=order_side,
        time_in_force=TimeInForce.OPG,
        client_order_id=client_order_id,
    )


def build_opg_batch_record(*, account_id, signal_bar_at, target_session, plan) -> dict:
    from src.execution.opg_sizing import EXECUTION_SIZING_CONTRACT, MODEL_CONTRACT_VERSION

    if not callable(getattr(plan, "to_dict", None)) or not isinstance(getattr(plan, "assets", None), dict):
        raise TypeError("plan must be an OpgSizingPlan")
    batch_id = build_opg_batch_id(
        account_id=account_id,
        signal_bar_at=signal_bar_at,
        target_session=target_session,
        model_contract_version=getattr(plan, "model_contract_version", MODEL_CONTRACT_VERSION),
        execution_sizing_contract=getattr(plan, "execution_sizing_contract", EXECUTION_SIZING_CONTRACT),
    )
    orders = {}
    normalized_symbols = [str(symbol).strip().upper() for symbol in plan.assets]
    if len(set(normalized_symbols)) != len(normalized_symbols):
        raise ValueError("plan contains duplicate symbols after normalization")
    for symbol in sorted(plan.assets, key=lambda item: str(item).strip().upper()):
        normalized_symbol = str(symbol).strip().upper()
        asset = plan.assets[symbol]
        delta = getattr(asset, "delta_quantity", None)
        if isinstance(delta, bool) or not isinstance(delta, int):
            raise ValueError(f"delta quantity for {symbol} must be a whole-share integer")
        if delta == 0:
            continue
        side = "buy" if delta > 0 else "sell"
        quantity = abs(delta)
        client_id = build_opg_client_order_id(batch_id, normalized_symbol)
        orders[normalized_symbol] = {
            "symbol": normalized_symbol,
            "side": side,
            "quantity": quantity,
            "requested_quantity": quantity,
            "pre_order_position_qty": getattr(asset, "current_quantity", None),
            "client_order_id": client_id,
            "order_type": "limit" if side == "buy" else "market",
            "limit_price": float(asset.close_price) if side == "buy" else None,
            "time_in_force": "opg",
            "status": "not_submitted",
            "order_id": None,
            "filled_qty": 0,
            "average_fill_price": None,
        }
    now = datetime.now(timezone.utc).isoformat()
    return {
        "batch_id": batch_id,
        "account_id": str(account_id),
        "account_mode": "paper",
        "signal_bar_at": _normalize_signal_timestamp(signal_bar_at),
        "target_session": _canonical_target_session(target_session),
        "model_contract_version": getattr(plan, "model_contract_version", MODEL_CONTRACT_VERSION),
        "execution_sizing_contract": getattr(plan, "execution_sizing_contract", EXECUTION_SIZING_CONTRACT),
        "plan": json.loads(json.dumps(plan.to_dict(), sort_keys=True)),
        "orders": orders,
        "status": "planned" if orders else "no_orders",
        "created_at": now,
        "updated_at": now,
        "blocking_reason": None,
    }
