"""Regression tests for endpoint-specific Alpaca market-data feeds."""
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
    expected = pd.Timestamp("2026-11-03T00:00:00").tz_localize(
        "America/New_York"
    ).tz_convert("UTC")
    assert actual == expected


def test_historical_end_includes_completed_session_after_delay():
    now = pd.Timestamp("2026-11-03T21:30:00Z")
    actual = pd.Timestamp(AlpacaFeed._historical_request_end(now))
    assert actual == now - pd.Timedelta(minutes=20)
