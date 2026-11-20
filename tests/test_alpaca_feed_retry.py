import requests
import pytest

from src.data.alpaca_feed import (
    MARKET_DATA_CONNECT_TIMEOUT_SECONDS,
    MARKET_DATA_READ_TIMEOUT_SECONDS,
    MARKET_DATA_REQUEST_TIMEOUT_SECONDS,
    MAX_TRANSIENT_API_ATTEMPTS,
    TRANSIENT_API_BACKOFF_SECONDS,
    _MarketDataTimeoutSession,
    AlpacaFeed,
    Urllib3Timeout,
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
            if self.calls <= 4:
                raise requests.exceptions.ConnectionError("temporary DNS failure")
            return type("Response", (), {"df": "bars"})()

    delays = []
    monkeypatch.setattr("src.data.alpaca_feed.time.sleep", delays.append)
    feed = make_feed(Client())

    result = feed._request_stock_bars_with_retry(object())

    assert result == "bars"
    assert feed.client.calls == 5
    assert feed._last_get_bars_retry_count == 4
    assert delays == [
        TRANSIENT_API_BACKOFF_SECONDS,
        TRANSIENT_API_BACKOFF_SECONDS * 2,
        TRANSIENT_API_BACKOFF_SECONDS * 4,
        TRANSIENT_API_BACKOFF_SECONDS * 8,
    ]


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

    assert captured.value.attempts == MAX_TRANSIENT_API_ATTEMPTS == 6
    assert feed.client.calls == 6
    assert delays == [
        TRANSIENT_API_BACKOFF_SECONDS,
        TRANSIENT_API_BACKOFF_SECONDS * 2,
        TRANSIENT_API_BACKOFF_SECONDS * 4,
        TRANSIENT_API_BACKOFF_SECONDS * 8,
        TRANSIENT_API_BACKOFF_SECONDS * 16,
    ]
    assert sum(delays) == 15.5


def test_get_stock_bars_retries_timeout_then_recovers(monkeypatch):
    class Client:
        def __init__(self):
            self.calls = 0

        def get_stock_bars(self, _request):
            self.calls += 1
            if self.calls == 1:
                raise requests.exceptions.Timeout("read timed out")
            return type("Response", (), {"df": "bars"})()

    delays = []
    monkeypatch.setattr("src.data.alpaca_feed.time.sleep", delays.append)
    feed = make_feed(Client())

    assert feed._request_stock_bars_with_retry(object()) == "bars"
    assert feed.client.calls == 2
    assert delays == [TRANSIENT_API_BACKOFF_SECONDS]


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



def test_market_data_timeout_session_applies_finite_default_timeout(monkeypatch):
    captured = {}

    def fake_request(_session, method, url, **kwargs):
        captured.update(kwargs)
        return "response"

    monkeypatch.setattr(requests.Session, "request", fake_request)
    session = _MarketDataTimeoutSession()

    assert session.request("GET", "https://data.alpaca.markets") == "response"
    timeout = captured["timeout"]
    assert isinstance(timeout, Urllib3Timeout)
    assert timeout.total == MARKET_DATA_REQUEST_TIMEOUT_SECONDS
    assert timeout._connect == MARKET_DATA_CONNECT_TIMEOUT_SECONDS
    assert timeout._read == MARKET_DATA_READ_TIMEOUT_SECONDS


def test_market_data_timeout_session_preserves_explicit_timeout(monkeypatch):
    captured = {}

    def fake_request(_session, method, url, **kwargs):
        captured.update(kwargs)
        return "response"

    monkeypatch.setattr(requests.Session, "request", fake_request)
    session = _MarketDataTimeoutSession()

    session.request("GET", "https://data.alpaca.markets", timeout=(1, 2))
    assert captured["timeout"] == (1, 2)


def test_market_data_timeout_session_refuses_request_after_deadline(monkeypatch):
    calls = []

    def fake_request(_session, method, url, **kwargs):
        calls.append((method, url, kwargs))
        return "response"

    monkeypatch.setattr(requests.Session, "request", fake_request)
    session = _MarketDataTimeoutSession()
    session.set_deadline(0.0)

    with pytest.raises(requests.exceptions.Timeout, match="deadline expired"):
        session.request("GET", "https://data.alpaca.markets")

    assert calls == []
