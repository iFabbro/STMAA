"""Regression tests for endpoint-specific Alpaca market-data feeds."""
from types import SimpleNamespace

import pandas as pd

from src.data.alpaca_feed import AlpacaFeed
from src.data.market_data_contract import DATA_FEED, DATA_QUOTE_FEED


def test_historical_model_feed_is_consolidated_sip():
    assert DATA_FEED == "sip"


def test_latest_quote_feed_is_delayed_sip():
    assert DATA_QUOTE_FEED == "delayed_sip"


def test_historical_end_uses_twenty_minute_cutoff_before_session_open():
    now = pd.Timestamp("2026-11-03T14:26:00Z")
    actual = pd.Timestamp(AlpacaFeed._historical_request_end(now))
    assert actual == now - pd.Timedelta(minutes=20)


def test_historical_end_excludes_in_progress_daily_bar():
    now = pd.Timestamp("2026-11-03T15:00:00Z")
    actual = pd.Timestamp(AlpacaFeed._historical_request_end(now))
    session_start = pd.Timestamp("2026-11-03T00:00:00").tz_localize(
        "America/New_York"
    ).tz_convert("UTC")
    assert actual == session_start - pd.Timedelta(seconds=1)


def test_historical_end_excludes_daily_bar_during_post_close_finalization():
    session_start = pd.Timestamp("2026-11-11T05:00:00Z")
    expected_end = session_start - pd.Timedelta(seconds=1)

    for value in ("2026-11-11T21:00:27Z", "2026-11-11T21:19:59Z"):
        now = pd.Timestamp(value)
        actual = pd.Timestamp(AlpacaFeed._historical_request_end(now))
        assert actual == expected_end
        latest_session = AlpacaFeed._latest_completed_nyse_session_date(actual)
        assert latest_session.isoformat() == "2026-11-10"


def test_historical_end_includes_session_at_twenty_minute_post_close_boundary():
    now = pd.Timestamp("2026-11-11T21:20:00Z")
    actual = pd.Timestamp(AlpacaFeed._historical_request_end(now))
    assert actual == now - pd.Timedelta(minutes=20)
    latest_session = AlpacaFeed._latest_completed_nyse_session_date(actual)
    assert latest_session.isoformat() == "2026-11-11"


def test_historical_end_includes_completed_session_after_delay():
    now = pd.Timestamp("2026-11-03T21:30:00Z")
    actual = pd.Timestamp(AlpacaFeed._historical_request_end(now))
    assert actual == now - pd.Timedelta(minutes=20)

def test_historical_request_uses_sip():
    feed = object.__new__(AlpacaFeed)
    captured = {}

    dates = pd.to_datetime(
        ["2018-01-02T05:00:00Z", "2018-01-03T05:00:00Z", "2018-01-04T05:00:00Z"],
        utc=True,
    )
    index = pd.MultiIndex.from_arrays(
        [["AAPL"] * len(dates), dates], names=["symbol", "timestamp"]
    )
    frame = pd.DataFrame(
        {
            "open": [10.0, 11.0, 12.0],
            "high": [11.0, 12.0, 13.0],
            "low": [9.0, 10.0, 11.0],
            "close": [10.5, 11.5, 12.5],
            "volume": [100.0, 110.0, 120.0],
        },
        index=index,
    )

    class Client:
        def get_stock_bars(self, request):
            captured["feed"] = getattr(request.feed, "value", request.feed)
            return SimpleNamespace(df=frame)

    feed.client = Client()
    feed.get_historical_ohlcv(["AAPL"], "2018-01-02", "2018-01-05")
    assert captured["feed"] == "sip"


def test_latest_quote_request_uses_delayed_sip():
    feed = object.__new__(AlpacaFeed)
    captured = {}

    class Client:
        def get_stock_latest_quote(self, request):
            captured["feed"] = getattr(request.feed, "value", request.feed)
            return {}

    feed.client = Client()
    feed._get_prev_close = lambda tickers, quote_timestamps=None: {}
    feed.get_latest_quotes(["AAPL"])
    assert captured["feed"] == "delayed_sip"


def _quote_feed(quotes, now):
    feed = object.__new__(AlpacaFeed)

    class Client:
        def get_stock_latest_quote(self, request):
            return quotes

    feed.client = Client()
    feed._get_prev_close = lambda tickers, quote_timestamps=None: {}
    return feed.get_latest_quotes(list(quotes), now=pd.Timestamp(now))


def test_quote_quality_accepts_delayed_sip_age_within_twenty_minutes():
    result = _quote_feed({"AAPL": SimpleNamespace(
        bid_price=99.9, ask_price=100.1,
        timestamp=pd.Timestamp("2026-11-09T14:45:00Z"))},
        "2026-11-09T15:00:00Z")["AAPL"]
    assert result["quality_status"] == "ok"
    assert result["quote_age_seconds"] == 900
    assert result["price"] == 100.0


def test_quote_quality_flags_stale_without_changing_mid_price():
    result = _quote_feed({"AAPL": SimpleNamespace(
        bid_price=99.9, ask_price=100.1,
        timestamp=pd.Timestamp("2026-11-09T14:39:59Z"))},
        "2026-11-09T15:00:00Z")["AAPL"]
    assert result["quality_status"] == "stale"
    assert result["price"] == 100.0


def test_quote_quality_flags_small_clock_skew_but_keeps_quote():
    result = _quote_feed({"AAPL": SimpleNamespace(
        bid_price=99.9, ask_price=100.1,
        timestamp=pd.Timestamp("2026-11-09T15:00:02Z"))},
        "2026-11-09T15:00:00Z")["AAPL"]
    assert result["quality_status"] == "clock_skew"
    assert result["price"] == 100.0


def test_quote_quality_flags_materially_future_timestamp():
    result = _quote_feed({"AAPL": SimpleNamespace(
        bid_price=99.9, ask_price=100.1,
        timestamp=pd.Timestamp("2026-11-09T15:00:06Z"))},
        "2026-11-09T15:00:00Z")["AAPL"]
    assert result["quality_status"] == "future_timestamp"


def test_quote_quality_reports_wide_spread_without_rejecting_price():
    result = _quote_feed({"AAPL": SimpleNamespace(
        bid_price=98.0, ask_price=102.0,
        timestamp=pd.Timestamp("2026-11-09T14:59:00Z"))},
        "2026-11-09T15:00:00Z")["AAPL"]
    assert result["quality_status"] == "wide_spread"
    assert result["spread_pct"] == 4.0
    assert result["price"] == 100.0


def test_quote_quality_rejects_naive_timestamp_as_invalid():
    result = _quote_feed({"AAPL": SimpleNamespace(
        bid_price=99.9, ask_price=100.1,
        timestamp=pd.Timestamp("2026-11-09T15:00:00"))},
        "2026-11-09T15:00:00Z")["AAPL"]
    assert result["quality_status"] == "invalid"
    assert result["price"] == 0.0


def test_quote_quality_rejects_non_finite_bid_or_ask():
    result = _quote_feed({"AAPL": SimpleNamespace(
        bid_price=float("inf"), ask_price=100.1,
        timestamp=pd.Timestamp("2026-11-09T15:00:00Z"))},
        "2026-11-09T15:00:00Z")["AAPL"]
    assert result["quality_status"] == "invalid"
    assert result["price"] == 0.0
