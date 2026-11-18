import requests
import pytest

from src.data.alpaca_feed import (
    MAX_TRANSIENT_API_ATTEMPTS,
    TRANSIENT_API_BACKOFF_SECONDS,
    AlpacaFeed,
    TransientMarketDataError,
)


def make_feed(client):
    feed = object.__new__(AlpacaFeed)
    feed.client = client
    return feed


def test_get_stock_bars_retries_transient_transport_then_recovers(monkeypatch):
    class Client:
        def __init__(self):
            self.calls = 0

        def get_stock_bars(self, _request):
            self.calls += 1
            if self.calls == 1:
                raise requests.exceptions.ConnectionError("remote disconnected")
            return type("Response", (), {"df": "bars"})()

    delays = []
    monkeypatch.setattr("src.data.alpaca_feed.time.sleep", delays.append)
    feed = make_feed(Client())

    result = feed._request_stock_bars_with_retry(object())

    assert result == "bars"
    assert feed.client.calls == 2
    assert feed._last_get_bars_retry_count == 1
    assert delays == [TRANSIENT_API_BACKOFF_SECONDS]


def test_get_stock_bars_stops_after_bounded_transient_failures(monkeypatch):
    class Client:
        def __init__(self):
            self.calls = 0

        def get_stock_bars(self, _request):
            self.calls += 1
            raise requests.exceptions.ConnectionError("remote disconnected")

    delays = []
    monkeypatch.setattr("src.data.alpaca_feed.time.sleep", delays.append)
    feed = make_feed(Client())

    with pytest.raises(TransientMarketDataError) as captured:
        feed._request_stock_bars_with_retry(object())

    assert captured.value.attempts == MAX_TRANSIENT_API_ATTEMPTS == 3
    assert feed.client.calls == 3
    assert delays == [
        TRANSIENT_API_BACKOFF_SECONDS,
        TRANSIENT_API_BACKOFF_SECONDS * 2,
    ]


def test_get_stock_bars_does_not_retry_structural_errors(monkeypatch):
    class Client:
        def __init__(self):
            self.calls = 0

        def get_stock_bars(self, _request):
            self.calls += 1
            raise ValueError("invalid request")

    delays = []
    monkeypatch.setattr("src.data.alpaca_feed.time.sleep", delays.append)
    feed = make_feed(Client())

    with pytest.raises(ValueError, match="invalid request"):
        feed._request_stock_bars_with_retry(object())

    assert feed.client.calls == 1
    assert feed._last_get_bars_retry_count == 0
    assert delays == []
