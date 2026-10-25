from decimal import Decimal
from types import SimpleNamespace

from scripts.alpaca_paper_btc_readonly_reconcile import (
    QTY_TOLERANCE,
    as_decimal,
    first_available_decimal,
    norm_status,
    norm_symbol,
)


def test_normalize_crypto_symbol_variants():
    assert norm_symbol("BTC/USD") == "BTCUSD"
    assert norm_symbol("btc-usd") == "BTCUSD"
    assert norm_symbol("BTCUSD") == "BTCUSD"


def test_normalize_enum_and_string_status():
    assert norm_status("OrderStatus.FILLED") == "filled"
    assert norm_status(SimpleNamespace(value="filled")) == "filled"


def test_decimal_conversion_is_exact_and_fail_closed():
    assert as_decimal("0.000121728") == Decimal("0.000121728")
    assert as_decimal(None) is None
    assert as_decimal("not-a-number") is None


def test_quantity_tolerance_is_explicit_and_small():
    assert QTY_TOLERANCE == Decimal("0.000000001")
    assert abs(Decimal("0.000121728") - Decimal("0.0001217275")) > QTY_TOLERANCE


def test_commission_is_only_reported_if_payload_exposes_it():
    order_without_fee = SimpleNamespace(fee=None, commission=None)
    order_with_fee = SimpleNamespace(fee="0.01", commission=None)

    assert first_available_decimal(
        order_without_fee, ("commission", "commissions", "fee", "fees")
    ) == (None, None)
    assert first_available_decimal(
        order_with_fee, ("commission", "commissions", "fee", "fees")
    ) == ("fee", Decimal("0.01"))
