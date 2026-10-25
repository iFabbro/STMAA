"""Regression tests for read-only Alpaca activity endpoint failures."""

from types import SimpleNamespace

import requests

from scripts import alpaca_paper_btc_readonly_reconcile as reconcile


def test_fill_activity_read_failure_is_controlled_and_does_not_leak_details(monkeypatch):
    def fail_get(*args, **kwargs):
        raise requests.RequestException("sensitive URL and credential detail")

    monkeypatch.setattr("requests.get", fail_get)

    records, status = reconcile.fetch_order_fill_activities(
        SimpleNamespace(id="order-123", symbol="BTC/USD"), "test-key", "test-secret"
    )

    assert records == []
    assert "order FILL activity read failed (RequestException)" == status
    assert "sensitive" not in status
    assert "test-secret" not in status


def test_fee_activity_read_failure_is_controlled_and_does_not_leak_details(monkeypatch):
    def fail_get(*args, **kwargs):
        raise requests.RequestException("sensitive URL and credential detail")

    monkeypatch.setattr("requests.get", fail_get)

    records, status = reconcile.fetch_crypto_fee_activities(
        SimpleNamespace(filled_at="2026-10-25T12:00:00Z"), "test-key", "test-secret"
    )

    assert records == []
    assert "account activities read failed (RequestException)" == status
    assert "sensitive" not in status
    assert "test-secret" not in status


def test_fill_activity_non_list_response_fails_closed(monkeypatch):
    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"unexpected": "shape"}

    monkeypatch.setattr("requests.get", lambda *args, **kwargs: Response())

    records, status = reconcile.fetch_order_fill_activities(
        SimpleNamespace(id="order-123", symbol="BTC/USD"), "test-key", "test-secret"
    )

    assert records == []
    assert status == "unexpected FILL account activities response shape"


def test_fee_activity_non_list_response_fails_closed(monkeypatch):
    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"unexpected": "shape"}

    monkeypatch.setattr("requests.get", lambda *args, **kwargs: Response())

    records, status = reconcile.fetch_crypto_fee_activities(
        SimpleNamespace(filled_at="2026-10-25T12:00:00Z"), "test-key", "test-secret"
    )

    assert records == []
    assert status == "unexpected account activities response shape"



def test_fill_read_failure_is_classified_as_incomplete():
    assert reconcile.activity_read_incomplete(
        "order FILL activity read failed (RequestException)"
    )


def test_malformed_or_unsafe_activity_response_is_incomplete():
    assert reconcile.activity_read_incomplete(
        "unexpected FILL account activities response shape"
    )
    assert reconcile.activity_read_incomplete(
        "activity pagination did not advance safely"
    )
    assert reconcile.activity_read_incomplete(
        "FILL activity response contained a different order_id"
    )


def test_fee_timestamp_or_read_failure_is_incomplete():
    assert reconcile.activity_read_incomplete("order filled_at timestamp unavailable")
    assert reconcile.activity_read_incomplete(
        "account activities read failed (RequestException)"
    )


def test_successful_activity_query_without_matching_records_is_review_not_incomplete():
    assert not reconcile.activity_read_incomplete(
        "queried FILL activities filtered by order_id=order-123; raw records=0; no matching FILL records"
    )
    assert not reconcile.activity_read_incomplete(
        "queried CFEE activities from start through end; raw CFEE records=0; explicitly order-linked BTCUSD CFEE records=0"
    )
