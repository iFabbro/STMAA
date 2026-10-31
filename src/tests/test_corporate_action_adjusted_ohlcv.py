"""Regression tests for split/dividend-adjusted OHLCV flowing through STMAA.

The fixture represents bars already adjusted by Alpaca. These tests verify
that training and inference request the all-adjustments contract and preserve
the provider's adjusted prices/volume without applying a second local
adjustment. They do not independently certify Alpaca's upstream calculations.
"""

import numpy as np
import pandas as pd
import pandas_market_calendars as mcal

from src.data.alpaca_feed import AlpacaFeed, _DAILY_TIMEFRAME
from src.data.data_processor import DataProcessor


class _BarsResponse:
    def __init__(self, frame):
        self.df = frame


class _CorporateActionFixtureClient:
    def __init__(self, frame):
        self.frame = frame
        self.requests = []

    def get_stock_bars(self, request):
        self.requests.append(request)
        return _BarsResponse(self.frame.copy())


def _adjusted_corporate_action_bars(sessions):
    """Build adjusted bars with a 2:1 split and a later cash dividend."""
    step = np.arange(len(sessions), dtype=float)
    close = 100.0 + step * 0.08 + np.sin(step / 4.0) * 0.4
    open_price = close - 0.15
    high = close + 0.55
    low = close - 0.60

    # Alpaca's split adjustment changes historical price and volume. The
    # fixture is already split-adjusted: pre-split prices are on the
    # post-split scale and pre-split volume carries the 2:1 multiplier.
    split_position = 30
    raw_volume = 100_000.0 + step * 100.0
    adjusted_volume = raw_volume.copy()
    adjusted_volume[:split_position] *= 2.0

    # A later cash-dividend adjustment changes historical prices, not volume.
    # Prices in this fixture are already on the dividend-adjusted scale.
    dividend_position = 55
    dividend_factor = 0.99
    raw_close = close.copy()
    raw_close[:dividend_position] /= dividend_factor
    adjusted_close = raw_close.copy()
    adjusted_close[:dividend_position] *= dividend_factor
    assert np.allclose(adjusted_close, close, rtol=0.0, atol=1e-12)

    rows = []
    index = []
    for i, timestamp in enumerate(sessions):
        index.append(("AAA", timestamp))
        rows.append(
            {
                "open": open_price[i],
                "high": high[i],
                "low": low[i],
                "close": adjusted_close[i],
                "volume": adjusted_volume[i],
            }
        )

    multi_index = pd.MultiIndex.from_tuples(
        index, names=["symbol", "timestamp"]
    )
    return pd.DataFrame(rows, index=multi_index), adjusted_close, adjusted_volume


def test_split_and_dividend_adjusted_ohlcv_are_preserved_in_training_and_inference(
    tmp_path, monkeypatch
):
    sessions = mcal.get_calendar("NYSE").schedule(
        start_date="2025-01-02", end_date="2025-06-30"
    ).index
    bars, expected_close, expected_volume = _adjusted_corporate_action_bars(sessions)

    feed = AlpacaFeed.__new__(AlpacaFeed)
    feed.client = _CorporateActionFixtureClient(bars)
    monkeypatch.setattr(
        AlpacaFeed,
        "_latest_completed_nyse_session_date",
        staticmethod(lambda now=None: sessions[-1].date()),
    )

    start_date = sessions[0].date().isoformat()
    end_date = (sessions[-1] + pd.Timedelta(days=1)).date().isoformat()

    # Historical training ingestion and canonical feature calculation.
    historical = feed.get_historical_ohlcv(["AAA"], start_date, end_date)
    processor = DataProcessor(tmp_path / "corporate-actions")
    cleaned = processor.clean_data(historical)
    processed = processor.add_technical_indicators(cleaned)

    # Live inference ingestion uses the same provider fixture and contract.
    inference = feed.get_bars(["AAA"], days=len(sessions))

    assert len(feed.client.requests) == 2
    for request in feed.client.requests:
        assert request.feed == "iex"
        assert request.adjustment == "all"
        assert request.timeframe == _DAILY_TIMEFRAME

    # Split adjustment is preserved exactly: historical pre-split volume is
    # doubled, while post-split volume remains on the post-split scale.
    np.testing.assert_allclose(
        historical[("Volume", "AAA")].to_numpy(dtype=float),
        expected_volume,
        rtol=0.0,
        atol=0.0,
    )
    split_position = 30
    assert expected_volume[split_position - 1] == 2.0 * (100_000.0 + (split_position - 1) * 100.0)
    assert expected_volume[split_position] == 100_000.0 + split_position * 100.0

    # Cash-dividend adjustment is price-only: the fixture's adjusted prices
    # are passed through unchanged and its volume series is not dividend-scaled.
    np.testing.assert_allclose(
        historical[("Close", "AAA")].to_numpy(dtype=float),
        expected_close,
        rtol=0.0,
        atol=0.0,
    )
    dividend_position = 55
    assert expected_volume[dividend_position - 1] == 2.0 * (
        100_000.0 + (dividend_position - 1) * 100.0
    )
    assert expected_volume[dividend_position] == 100_000.0 + dividend_position * 100.0

    # Training and inference must derive identical PPO inputs from the
    # adjusted OHLCV response, including the rolling-indicator warm-up.
    for feature in ("Close", "RSI", "MACD", "BB_middle", "ATR"):
        pd.testing.assert_series_equal(
            processed[(feature, "AAA")],
            inference[f"{feature}_AAA"],
            check_names=False,
            check_exact=True,
            check_freq=False,
        )
