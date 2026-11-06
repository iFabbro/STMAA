"""Shared hard operational gross-notional limit for DAY and OPG execution."""
from decimal import Decimal, InvalidOperation

MAX_GROSS_NOTIONAL_USD = Decimal("1000")


def _amount(value, field):
    if isinstance(value, bool):
        raise RuntimeError(f"Cannot determine aggregate gross notional: invalid {field}")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise RuntimeError(
            f"Cannot determine aggregate gross notional: invalid {field}"
        ) from exc
    if not parsed.is_finite() or parsed < 0:
        raise RuntimeError(f"Cannot determine aggregate gross notional: invalid {field}")
    return parsed


def calculate_position_gross_notional(positions, prices=None):
    """Calculate gross marked positions; missing quantities/prices fail closed."""
    if not isinstance(positions, dict):
        raise RuntimeError("Cannot determine aggregate gross notional: invalid positions snapshot")
    total = Decimal("0")
    for raw_symbol, position in positions.items():
        symbol = str(raw_symbol or "").strip().upper()
        if not symbol or not isinstance(position, dict):
            raise RuntimeError("Cannot determine aggregate gross notional: malformed position")
        try:
            quantity = Decimal(str(position.get("qty")))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise RuntimeError(
                f"Cannot determine aggregate gross notional: invalid position quantity for {symbol}"
            ) from exc
        if not quantity.is_finite():
            raise RuntimeError(
                f"Cannot determine aggregate gross notional: invalid position quantity for {symbol}"
            )
        if prices is None or symbol not in prices:
            raise RuntimeError(
                f"Cannot determine aggregate gross notional: missing price for {symbol}"
            )
        price = _amount(prices[symbol], f"position price for {symbol}")
        if price <= 0:
            raise RuntimeError(
                f"Cannot determine aggregate gross notional: non-positive price for {symbol}"
            )
        total += abs(quantity * price)
    return total


def calculate_aggregate_gross_notional(
    *, position_notional, pending_buy_notional=0, additional_buy_notional=0
):
    """Combine positions, active BUY reserves, and a proposed BUY."""
    positions = _amount(position_notional, "position notional")
    pending = _amount(pending_buy_notional, "pending BUY notional")
    additional = _amount(additional_buy_notional, "additional BUY notional")
    total = positions + pending + additional
    if not total.is_finite():
        raise RuntimeError("Cannot determine aggregate gross notional: total is non-finite")
    return total


def enforce_aggregate_gross_notional_cap(
    *, position_notional, pending_buy_notional=0, additional_buy_notional=0
):
    """Fail closed when projected gross exposure exceeds the hard USD 1,000 cap."""
    total = calculate_aggregate_gross_notional(
        position_notional=position_notional,
        pending_buy_notional=pending_buy_notional,
        additional_buy_notional=additional_buy_notional,
    )
    if total > MAX_GROSS_NOTIONAL_USD:
        raise RuntimeError(
            f"Aggregate gross notional {total:.2f} USD exceeds hard cap "
            f"{MAX_GROSS_NOTIONAL_USD:.2f} USD"
        )
    return total
