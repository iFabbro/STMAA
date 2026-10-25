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



def test_reconcile_crypto_commission_converts_unique_cfee_to_usd(monkeypatch):
    from scripts import alpaca_paper_btc_readonly_reconcile as reconcile

    monkeypatch.setattr(
        reconcile,
        "fetch_crypto_fee_activities",
        lambda order, key, secret: (
            [{"id": "fee-1", "activity_type": "CFEE", "symbol": "BTC/USD",
              "qty": "-0.0000001", "price": "80500"}],
            "mocked supported read-only source",
        ),
    )
    amount, source, records = reconcile.reconcile_crypto_commission(
        SimpleNamespace(filled_at="2026-10-25T12:00:00Z"), "key", "secret"
    )
    assert amount == Decimal("0.0080500")
    assert "unique CFEE" in source
    assert len(records) == 1


def test_reconcile_crypto_commission_fails_closed_on_ambiguous_records(monkeypatch):
    from scripts import alpaca_paper_btc_readonly_reconcile as reconcile

    monkeypatch.setattr(
        reconcile,
        "fetch_crypto_fee_activities",
        lambda order, key, secret: (
            [{"id": "fee-1"}, {"id": "fee-2"}],
            "mocked supported read-only source",
        ),
    )
    amount, source, records = reconcile.reconcile_crypto_commission(
        SimpleNamespace(filled_at="2026-10-25T12:00:00Z"), "key", "secret"
    )
    assert amount is None
    assert "matching BTCUSD CFEE records=2" in source
    assert len(records) == 2


def test_reconcile_crypto_commission_fails_closed_when_fee_fields_missing(monkeypatch):
    from scripts import alpaca_paper_btc_readonly_reconcile as reconcile

    monkeypatch.setattr(
        reconcile,
        "fetch_crypto_fee_activities",
        lambda order, key, secret: (
            [{"id": "fee-1", "activity_type": "CFEE", "symbol": "BTCUSD",
              "qty": None, "price": "80500"}],
            "mocked supported read-only source",
        ),
    )
    amount, source, _ = reconcile.reconcile_crypto_commission(
        SimpleNamespace(filled_at="2026-10-25T12:00:00Z"), "key", "secret"
    )
    assert amount is None
    assert "qty/price missing or invalid" in source
