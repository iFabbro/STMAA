from decimal import Decimal
from types import SimpleNamespace

from scripts.alpaca_paper_btc_readonly_reconcile import (
    QTY_TOLERANCE,
    as_decimal,
    compare_position_quantity,
    commission_reconciliation_status,
    first_available_decimal,
    norm_status,
    norm_symbol,
    validate_order_identity,
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
            [{"id": "fee-1", "order_id": "order-1", "activity_type": "CFEE", "symbol": "BTC/USD",
              "qty": "-0.0000001", "price": "80500"}],
            "mocked supported read-only source",
        ),
    )
    amount, source, records = reconcile.reconcile_crypto_commission(
        SimpleNamespace(id="order-1", filled_at="2026-10-25T12:00:00Z"), "key", "secret"
    )
    assert amount == Decimal("0.0080500")
    assert "explicitly order-linked CFEE" in source
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
    assert "COMMISSION_STATUS=UNATTRIBUTABLE" in source
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


def test_nearby_fee_without_order_link_remains_unknown(monkeypatch):
    from scripts import alpaca_paper_btc_readonly_reconcile as reconcile

    monkeypatch.setattr(
        reconcile, "fetch_crypto_fee_activities",
        lambda order, key, secret: (
            [{"id": "fee-nearby", "activity_type": "CFEE", "symbol": "BTCUSD",
              "qty": "-0.0000001", "price": "80500"}],
            "mocked nearby accounting activity",
        ),
    )
    amount, source, records = reconcile.reconcile_crypto_commission(
        SimpleNamespace(id="order-1", filled_at="2026-10-25T12:00:00Z"), "key", "secret"
    )
    assert amount is None
    assert "COMMISSION_STATUS=UNATTRIBUTABLE" in source
    assert "symbol/time proximity is insufficient" in source
    assert len(records) == 1


def test_commission_absent_fails_closed(monkeypatch):
    from scripts import alpaca_paper_btc_readonly_reconcile as reconcile

    monkeypatch.setattr(
        reconcile, "fetch_crypto_fee_activities",
        lambda order, key, secret: ([], "mocked empty supported source"),
    )
    amount, source, records = reconcile.reconcile_crypto_commission(
        SimpleNamespace(id="order-1", filled_at="2026-10-25T12:00:00Z"), "key", "secret"
    )
    assert amount is None
    assert "COMMISSION_STATUS=NO_CANDIDATE_ACTIVITY" in source
    assert records == []

def test_quantity_difference_is_not_hidden_by_commission_hypothesis():
    delta, matches = compare_position_quantity(
        Decimal("0.000121423"), Decimal("0.000121728")
    )
    assert delta == Decimal("-0.000000305")
    assert matches is False


def test_quantity_match_within_explicit_tolerance():
    delta, matches = compare_position_quantity(
        Decimal("0.0001217285"), Decimal("0.000121728")
    )
    assert delta == Decimal("5E-10")
    assert matches is True


def test_missing_position_quantity_fails_closed():
    delta, matches = compare_position_quantity(None, Decimal("0.000121728"))
    assert delta is None
    assert matches is False



def test_order_fill_activity_lookup_uses_documented_order_id_filter(monkeypatch):
    from scripts import alpaca_paper_btc_readonly_reconcile as reconcile

    calls = []

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return [{
                "id": "fill-1",
                "activity_type": "FILL",
                "order_id": "order-123",
                "symbol": "BTC/USD",
                "qty": "0.00005",
                "price": "80000",
            }]

    def fake_get(url, headers, params, timeout):
        calls.append((url, params))
        return Response()

    monkeypatch.setattr("requests.get", fake_get)
    fills, source = reconcile.fetch_order_fill_activities(
        SimpleNamespace(id="order-123", symbol="BTC/USD"), "key", "secret"
    )

    assert len(fills) == 1
    assert calls[0][1]["activity_types"] == "FILL"
    assert calls[0][1]["order_id"] == "order-123"
    assert "filtered by order_id=order-123" in source


def test_order_fill_reconciliation_matches_summed_qty_and_weighted_average(monkeypatch):
    from scripts import alpaca_paper_btc_readonly_reconcile as reconcile

    monkeypatch.setattr(
        reconcile,
        "fetch_order_fill_activities",
        lambda order, key, secret: (
            [
                {"id": "fill-1", "activity_type": "FILL", "symbol": "BTC/USD",
                 "qty": "0.00005", "price": "80000"},
                {"id": "fill-2", "activity_type": "FILL", "symbol": "BTC/USD",
                 "qty": "0.00005", "price": "80200"},
            ],
            "mocked order-scoped fill source",
        ),
    )
    order = SimpleNamespace(
        id="order-123", symbol="BTC/USD", filled_qty="0.0001",
        filled_avg_price="80100",
    )

    matches, source, fills = reconcile.reconcile_order_fill_activities(order, "key", "secret")

    assert matches is True
    assert len(fills) == 2
    assert "summed_fill_qty=0.00010" in source
    assert "avg_price_match_0.01=true" in source


def test_order_fill_reconciliation_fails_closed_on_quantity_mismatch(monkeypatch):
    from scripts import alpaca_paper_btc_readonly_reconcile as reconcile

    monkeypatch.setattr(
        reconcile,
        "fetch_order_fill_activities",
        lambda order, key, secret: (
            [{"id": "fill-1", "activity_type": "FILL", "symbol": "BTC/USD",
              "qty": "0.00009", "price": "80100"}],
            "mocked order-scoped fill source",
        ),
    )
    order = SimpleNamespace(
        id="order-123", symbol="BTC/USD", filled_qty="0.0001",
        filled_avg_price="80100",
    )

    matches, source, _ = reconcile.reconcile_order_fill_activities(order, "key", "secret")

    assert matches is False
    assert "qty_match=false" in source


def test_order_fill_reconciliation_fails_closed_when_no_fills(monkeypatch):
    from scripts import alpaca_paper_btc_readonly_reconcile as reconcile

    monkeypatch.setattr(
        reconcile, "fetch_order_fill_activities",
        lambda order, key, secret: ([], "mocked empty fill source"),
    )
    matches, source, fills = reconcile.reconcile_order_fill_activities(
        SimpleNamespace(id="order-123", symbol="BTC/USD",
                        filled_qty="0.0001", filled_avg_price="80100"),
        "key", "secret",
    )

    assert matches is False
    assert fills == []
    assert "no matching FILL records" in source



def test_order_identity_accepts_expected_btc_buy():
    ok, detail = validate_order_identity(SimpleNamespace(symbol="BTC/USD", side="buy"))
    assert ok is True
    assert "match expected order identity" in detail


def test_order_identity_rejects_wrong_symbol():
    ok, detail = validate_order_identity(SimpleNamespace(symbol="ETH/USD", side="buy"))
    assert ok is False
    assert "symbol mismatch" in detail


def test_order_identity_rejects_wrong_side():
    ok, detail = validate_order_identity(SimpleNamespace(symbol="BTC/USD", side="sell"))
    assert ok is False
    assert "side mismatch" in detail


def test_order_identity_rejects_missing_identity_fields():
    ok, detail = validate_order_identity(SimpleNamespace())
    assert ok is False
    assert "symbol mismatch" in detail
    assert "side mismatch" in detail


def test_main_missing_credentials_returns_fail_exit_code(monkeypatch, capsys):
    from scripts import alpaca_paper_btc_readonly_reconcile as reconcile

    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET", raising=False)
    assert reconcile.main() == 2
    assert "RESULT: FAIL" in capsys.readouterr().out


def test_cfee_output_distinguishes_explicit_link_from_temporal_context():
    order_id = "order-123"
    linked = {"order_id": order_id}
    nearby = {"order_id": ""}
    assert str(linked.get("order_id", "")) == order_id
    assert str(nearby.get("order_id", "")) != order_id



def test_main_fails_closed_when_account_read_raises(monkeypatch, capsys):
    from scripts import alpaca_paper_btc_readonly_reconcile as reconcile

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def get_account(self):
            raise RuntimeError("sensitive broker detail")

    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "test-secret")
    monkeypatch.setattr(reconcile, "TradingClient", FakeClient)

    assert reconcile.main() == 1
    output = capsys.readouterr().out
    assert "ACCOUNT: read_error=RuntimeError; details withheld" in output
    assert "RESULT: INCOMPLETE — account read failed" in output
    assert "sensitive broker detail" not in output


def test_main_fails_closed_when_client_initialization_raises(monkeypatch, capsys):
    from scripts import alpaca_paper_btc_readonly_reconcile as reconcile

    def fail_client(*args, **kwargs):
        raise RuntimeError("sensitive initialization detail")

    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "test-secret")
    monkeypatch.setattr(reconcile, "TradingClient", fail_client)

    assert reconcile.main() == 1
    output = capsys.readouterr().out
    assert "CLIENT: initialization_error=RuntimeError; details withheld" in output
    assert "RESULT: INCOMPLETE — read-only broker client could not be initialized" in output
    assert "sensitive initialization detail" not in output


def test_commission_status_pending_when_cfee_not_yet_posted(monkeypatch):
    from datetime import datetime, timezone
    from scripts import alpaca_paper_btc_readonly_reconcile as reconcile

    order = SimpleNamespace(filled_at="2026-10-25T12:00:00Z")
    monkeypatch.setattr(
        reconcile, "fetch_crypto_fee_activities",
        lambda order, key, secret: ([], "mocked successful empty CFEE query"),
    )
    amount, source, fees = reconcile.reconcile_crypto_commission(order, "key", "secret")
    status = reconcile.commission_reconciliation_status(
        order, amount, fees, source, now=datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)
    )
    assert amount is None
    assert status == "PENDING_POSTING"
    assert "not assumed to be zero" not in source
    assert fees == []


def test_commission_status_missing_after_posting_grace(monkeypatch):
    from datetime import datetime, timezone
    from scripts import alpaca_paper_btc_readonly_reconcile as reconcile

    order = SimpleNamespace(filled_at="2026-10-20T12:00:00Z")
    monkeypatch.setattr(
        reconcile, "fetch_crypto_fee_activities",
        lambda order, key, secret: ([], "mocked successful empty CFEE query"),
    )
    amount, source, fees = reconcile.reconcile_crypto_commission(order, "key", "secret")
    status = reconcile.commission_reconciliation_status(
        order, amount, fees, source, now=datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
    )
    assert amount is None
    assert status == "MISSING_AFTER_GRACE"


def test_commission_status_unattributable_when_cfee_has_no_order_id(monkeypatch):
    from datetime import datetime, timezone
    from scripts import alpaca_paper_btc_readonly_reconcile as reconcile

    order = SimpleNamespace(id="order-1", filled_at="2026-10-20T12:00:00Z")
    monkeypatch.setattr(
        reconcile, "fetch_crypto_fee_activities",
        lambda order, key, secret: (
            [{"id": "fee-1", "activity_type": "CFEE", "symbol": "BTCUSD",
              "qty": "-0.0000001", "price": "80500"}],
            "mocked CFEE activity without order_id",
        ),
    )
    amount, source, fees = reconcile.reconcile_crypto_commission(order, "key", "secret")
    status = reconcile.commission_reconciliation_status(
        order, amount, fees, source, now=datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
    )
    assert amount is None
    assert status == "UNATTRIBUTABLE"
    assert "symbol/time proximity is insufficient" in source


def test_commission_status_reconciled_only_for_unique_explicit_order_link(monkeypatch):
    from datetime import datetime, timezone
    from scripts import alpaca_paper_btc_readonly_reconcile as reconcile

    order = SimpleNamespace(id="order-1", filled_at="2026-10-20T12:00:00Z")
    monkeypatch.setattr(
        reconcile,
        "fetch_crypto_fee_activities",
        lambda order, key, secret: (
            [{"id": "fee-1", "order_id": "order-1", "activity_type": "CFEE",
              "symbol": "BTCUSD", "qty": "-0.0000001", "price": "80500"}],
            "mocked explicitly linked CFEE activity",
        ),
    )
    amount, source, fees = reconcile.reconcile_crypto_commission(order, "key", "secret")
    status = reconcile.commission_reconciliation_status(
        order, amount, fees, source, now=datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
    )
    assert amount == Decimal("0.0080500")
    assert status == "RECONCILED"
