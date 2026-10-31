""""Daily strategy inputs must be complete and aligned to the latest completed session."""
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


def _history(symbol, base_price, *, flat=False, invalid_close_at=None):
    dates = pd.bdate_range(end="2026-10-27", periods=30)
    rows = []
    for index, timestamp in enumerate(dates):
        close = float(base_price if flat else base_price + index)
        if invalid_close_at == index:
            close = float("nan")
        rows.append((
            symbol,
            timestamp.strftime("%Y-%m-%d"),
            close,
            (base_price if flat else base_price + index) + 1.0,
            (base_price if flat else base_price + index) - 1.0,
            close,
            1000.0 + index,
        ))
    return rows


def test_daily_bars_accept_complete_fresh_snapshot_for_every_ticker(feed, monkeypatch):
    snapshot = _bars(_history("AAPL", 100.0) + _history("MSFT", 200.0))
    monkeypatch.setattr(
        feed.client,
        "get_stock_bars",
        lambda request: type("Response", (), {"df": snapshot})(),
    )

    result = feed.get_bars(["AAPL", "MSFT"], days=1)

    assert not result.empty
    assert result["Close_AAPL"].iloc[-1] == pytest.approx(129.0)
    assert result["Close_MSFT"].iloc[-1] == pytest.approx(229.0)


def test_daily_bars_fail_closed_when_a_requested_ticker_is_missing(feed, monkeypatch):
    snapshot = _bars(_history("AAPL", 100.0))
    monkeypatch.setattr(
        feed.client,
        "get_stock_bars",
        lambda request: type("Response", (), {"df": snapshot})(),
    )

    with pytest.raises(RuntimeError, match="no market bars received for MSFT"):
        feed.get_bars(["AAPL", "MSFT"], days=1)


def test_daily_bars_fail_closed_when_latest_session_is_stale(feed, monkeypatch):
    snapshot = _bars([
        ("AAPL", "2026-10-26", 99.0, 102.0, 98.0, 101.0, 1000.0),
    ])
    monkeypatch.setattr(
        feed.client,
        "get_stock_bars",
        lambda request: type("Response", (), {"df": snapshot})(),
    )

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
    monkeypatch.setattr(
        feed.client,
        "get_stock_bars",
        lambda request: type("Response", (), {"df": snapshot})(),
    )

    with pytest.raises(RuntimeError, match="invalid latest daily"):
        feed.get_bars(["AAPL"], days=1)


def test_daily_bars_use_neutral_rsi_for_flat_price_history(feed, monkeypatch):
    # Match DataProcessor: a flat lookback has a defined neutral RSI of 50.
    # It is a valid feature, not missing data, and must reach PPO consistently
    # with the historical training pipeline.
    snapshot = _bars(_history("AAPL", 100.0, flat=True))
    monkeypatch.setattr(
        feed.client,
        "get_stock_bars",
        lambda request: type("Response", (), {"df": snapshot})(),
    )

    result = feed.get_bars(["AAPL"], days=1)

    assert result["RSI_AAPL"].iloc[-1] == pytest.approx(50.0)


def test_daily_bars_fail_closed_when_recent_history_makes_latest_features_invalid(
    feed, monkeypatch
):
    # The final OHLCV row is valid, but a missing close inside the indicator
    # lookback contaminates the latest rolling features.
    snapshot = _bars(_history("AAPL", 100.0, invalid_close_at=27))
    monkeypatch.setattr(
        feed.client,
        "get_stock_bars",
        lambda request: type("Response", (), {"df": snapshot})(),
    )

    with pytest.raises(RuntimeError, match="incomplete daily OHLCV bar for AAPL"):
        feed.get_bars(["AAPL"], days=1)


@pytest.mark.parametrize("invalid_field", ["Open", "High", "Low", "Close", "Volume"])
def test_daily_bars_fail_closed_on_incomplete_historical_ohlcv(
    feed, monkeypatch, invalid_field
):
    rows = _history("AAPL", 100.0)
    row = list(rows[5])
    field_index = {"Open": 2, "High": 3, "Low": 4, "Close": 5, "Volume": 6}[invalid_field]
    row[field_index] = float("nan")
    rows[5] = tuple(row)
    snapshot = _bars(rows)
    monkeypatch.setattr(
        feed.client,
        "get_stock_bars",
        lambda request: type("Response", (), {"df": snapshot})(),
    )

    with pytest.raises(RuntimeError, match="incomplete daily OHLCV bar"):
        feed.get_bars(["AAPL"], days=1)


def test_daily_bars_fail_closed_when_an_interior_nyse_session_is_absent(feed, monkeypatch):
    rows = _history("AAPL", 100.0)
    rows.pop(5)
    snapshot = _bars(rows)
    monkeypatch.setattr(
        feed.client,
        "get_stock_bars",
        lambda request: type("Response", (), {"df": snapshot})(),
    )

    with pytest.raises(RuntimeError, match="missing NYSE daily bars for AAPL"):
        feed.get_bars(["AAPL"], days=1)


def test_daily_bars_fail_closed_on_invalid_ohlcv_in_old_history(feed, monkeypatch):
    rows = _history("AAPL", 100.0)
    row = list(rows[5])
    row[3] = row[2] - 1.0  # High below Open; final indicator row remains valid.
    rows[5] = tuple(row)
    snapshot = _bars(rows)
    monkeypatch.setattr(
        feed.client,
        "get_stock_bars",
        lambda request: type("Response", (), {"df": snapshot})(),
    )

    with pytest.raises(RuntimeError, match="invalid daily OHLCV values"):
        feed.get_bars(["AAPL"], days=1)
