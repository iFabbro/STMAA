"""Daily strategy inputs must be complete and aligned to the latest completed session."""
from datetime import date

import pandas as pd
import pytest

from src.data.alpaca_feed import AlpacaFeed


@pytest.fixture
def feed(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "test-secret")
    instance = AlpacaFeed()
    monkeypatch.setattr(
        instance,
        "_latest_completed_nyse_session_date",
        lambda now=None: date(2026, 10, 27),
    )
    return instance


def _bars(rows):
    index = pd.MultiIndex.from_tuples(
        [(symbol, pd.Timestamp(timestamp, tz="UTC")) for symbol, timestamp, *_ in rows],
        names=["symbol", "timestamp"],
    )
    values = [
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
        for _, _, open_, high, low, close, volume in rows
    ]
    return pd.DataFrame(values, index=index)


def test_daily_bars_accept_complete_fresh_snapshot_for_every_ticker(feed, monkeypatch):
    snapshot = _bars([
        ("AAPL", "2026-10-27", 99.0, 102.0, 98.0, 101.0, 1000.0),
        ("MSFT", "2026-10-27", 199.0, 202.0, 198.0, 201.0, 2000.0),
    ])
    monkeypatch.setattr(feed.client, "get_stock_bars", lambda request: type("Response", (), {"df": snapshot})())

    result = feed.get_bars(["AAPL", "MSFT"], days=1)

    assert not result.empty
    assert result["Close_AAPL"].iloc[-1] == pytest.approx(101.0)
    assert result["Close_MSFT"].iloc[-1] == pytest.approx(201.0)


def test_daily_bars_fail_closed_when_a_requested_ticker_is_missing(feed, monkeypatch):
    snapshot = _bars([
        ("AAPL", "2026-10-27", 99.0, 102.0, 98.0, 101.0, 1000.0),
    ])
    monkeypatch.setattr(feed.client, "get_stock_bars", lambda request: type("Response", (), {"df": snapshot})())

    with pytest.raises(RuntimeError, match="no market bars received for MSFT"):
        feed.get_bars(["AAPL", "MSFT"], days=1)


def test_daily_bars_fail_closed_when_latest_session_is_stale(feed, monkeypatch):
    snapshot = _bars([
        ("AAPL", "2026-10-26", 99.0, 102.0, 98.0, 101.0, 1000.0),
    ])
    monkeypatch.setattr(feed.client, "get_stock_bars", lambda request: type("Response", (), {"df": snapshot})())

    with pytest.raises(RuntimeError, match="stale daily bars for AAPL"):
        feed.get_bars(["AAPL"], days=1)


@pytest.mark.parametrize(
    "ohlcv",
    [
        (99.0, 98.0, 100.0, 101.0, 1000.0),
        (99.0, 102.0, 98.0, float("nan"), 1000.0),
        (99.0, 102.0, 98.0, 101.0, -1.0),
    ],
)
def test_daily_bars_fail_closed_on_invalid_latest_ohlcv(feed, monkeypatch, ohlcv):
    snapshot = _bars([("AAPL", "2026-10-27", *ohlcv)])
    monkeypatch.setattr(feed.client, "get_stock_bars", lambda request: type("Response", (), {"df": snapshot})())

    with pytest.raises(RuntimeError, match="invalid latest daily"):
        feed.get_bars(["AAPL"], days=1)
