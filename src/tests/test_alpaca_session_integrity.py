"""Regression coverage for Alpaca daily-session completeness and freshness.

All provider responses are deterministic fixtures; these tests never contact
Alpaca and never submit broker orders.
"""

import numpy as np
import pandas as pd
import pandas_market_calendars as mcal
import pytest

from src.data.alpaca_feed import AlpacaFeed


class _BarsResponse:
    def __init__(self, frame):
        self.df = frame


class _DeterministicClient:
    def __init__(self, frame):
        self.frame = frame

    def get_stock_bars(self, request):
        return _BarsResponse(self.frame.copy())


def _sessions():
    schedule = mcal.get_calendar("NYSE").schedule(
        start_date="2025-01-02",
        end_date="2025-03-31",
    )
    return schedule.index


def _alpaca_bars(sessions, *, missing_session=None, invalid_session=None):
    timestamps = []
    rows = []
    for position, timestamp in enumerate(sessions):
        if timestamp == missing_session:
            continue
        close = 100.0 + position * 0.25
        values = {
            "open": close - 0.2,
            "high": close + 0.8,
            "low": close - 0.8,
            "close": close,
            "volume": 100_000.0 + position,
        }
        if timestamp == invalid_session:
            values["close"] = np.nan
        timestamps.append(("AAA", timestamp))
        rows.append(values)
    index = pd.MultiIndex.from_tuples(
        timestamps, names=["symbol", "timestamp"]
    )
    return pd.DataFrame(rows, index=index)


def _feed(frame):
    feed = AlpacaFeed.__new__(AlpacaFeed)
    feed.client = _DeterministicClient(frame)
    return feed


def test_historical_ingestion_rejects_missing_nyse_session():
    sessions = _sessions()
    missing = sessions[len(sessions) // 2]
    feed = _feed(_alpaca_bars(sessions, missing_session=missing))

    with pytest.raises(RuntimeError, match="historical session mismatch"):
        feed.get_historical_ohlcv(
            ["AAA"],
            sessions[0].date().isoformat(),
            (sessions[-1] + pd.Timedelta(days=1)).date().isoformat(),
        )


def test_inference_ingestion_rejects_missing_nyse_session(monkeypatch):
    sessions = _sessions()
    missing = sessions[len(sessions) // 2]
    feed = _feed(_alpaca_bars(sessions, missing_session=missing))
    monkeypatch.setattr(
        AlpacaFeed,
        "_latest_completed_nyse_session_date",
        staticmethod(lambda now=None: sessions[-1].date()),
    )

    with pytest.raises(RuntimeError, match="missing NYSE daily bars"):
        feed.get_bars(["AAA"], days=len(sessions))


def test_inference_ingestion_rejects_stale_latest_session(monkeypatch):
    sessions = _sessions()
    feed = _feed(_alpaca_bars(sessions))
    expected_session = (sessions[-1] + pd.offsets.BDay(1)).date()
    monkeypatch.setattr(
        AlpacaFeed,
        "_latest_completed_nyse_session_date",
        staticmethod(lambda now=None: expected_session),
    )

    with pytest.raises(RuntimeError, match="stale daily bars"):
        feed.get_bars(["AAA"], days=len(sessions))


def test_inference_ingestion_rejects_interior_non_finite_ohlcv(monkeypatch):
    sessions = _sessions()
    invalid = sessions[len(sessions) // 2]
    feed = _feed(_alpaca_bars(sessions, invalid_session=invalid))
    monkeypatch.setattr(
        AlpacaFeed,
        "_latest_completed_nyse_session_date",
        staticmethod(lambda now=None: sessions[-1].date()),
    )

    with pytest.raises(RuntimeError, match="incomplete daily OHLCV bar"):
        feed.get_bars(["AAA"], days=len(sessions))
