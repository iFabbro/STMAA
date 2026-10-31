"""End-to-end parity tests for historical training and Alpaca inference preprocessing."""

from pathlib import Path

import numpy as np
import pandas as pd
import pandas_market_calendars as mcal

import src.data.data_processor as data_processor_module
from src.data.alpaca_feed import AlpacaFeed, _DAILY_TIMEFRAME


class _BarsResponse:
    def __init__(self, frame):
        self.df = frame


class _DeterministicAlpacaClient:
    """Return the same deterministic Alpaca-shaped bars to both data paths."""

    def __init__(self, frame):
        self.frame = frame
        self.requests = []

    def get_stock_bars(self, request):
        self.requests.append(request)
        return _BarsResponse(self.frame.copy())


def _alpaca_bars(tickers, sessions):
    rows = []
    index = []
    for ticker_number, ticker in enumerate(tickers):
        step = np.arange(len(sessions), dtype=float)
        close = (
            80.0
            + ticker_number * 17.0
            + step * (0.11 + ticker_number * 0.015)
            + np.sin(step / (3.0 + ticker_number)) * 1.7
        )
        open_price = close + np.cos(step / 4.0) * 0.25
        high = np.maximum(open_price, close) + 0.8
        low = np.minimum(open_price, close) - 0.7
        volume = 100_000.0 + step * 137.0 + ticker_number * 1_000.0

        for position, timestamp in enumerate(sessions):
            index.append((ticker, timestamp))
            rows.append(
                {
                    "open": open_price[position],
                    "high": high[position],
                    "low": low[position],
                    "close": close[position],
                    "volume": volume[position],
                }
            )

    multi_index = pd.MultiIndex.from_tuples(
        index, names=["symbol", "timestamp"]
    )
    return pd.DataFrame(rows, index=multi_index)


def test_historical_training_and_live_inference_build_identical_feature_matrix(
    tmp_path: Path, monkeypatch
):
    """Exercise both public ingestion paths against one deterministic Alpaca response."""
    tickers = ["AAA", "BBB"]
    calendar = mcal.get_calendar("NYSE")
    sessions = calendar.schedule(
        start_date="2025-01-02", end_date="2025-06-30"
    ).index
    assert len(sessions) > 40

    raw_bars = _alpaca_bars(tickers, sessions)
    feed = AlpacaFeed.__new__(AlpacaFeed)
    feed.client = _DeterministicAlpacaClient(raw_bars)

    # Pin the live endpoint's freshness check to the final fixture session.
    monkeypatch.setattr(
        AlpacaFeed,
        "_latest_completed_nyse_session_date",
        staticmethod(lambda now=None: sessions[-1].date()),
    )
    # DataProcessor constructs its own feed; inject the exact same stubbed client.
    monkeypatch.setattr(data_processor_module, "AlpacaFeed", lambda: feed)

    start_date = sessions[0].date().isoformat()
    end_date = (sessions[-1] + pd.Timedelta(days=1)).date().isoformat()

    # Historical training path: get_historical_ohlcv -> DataProcessor.
    processor = data_processor_module.DataProcessor(tmp_path / "training-data")
    historical = processor.process(tickers, start_date, end_date)

    # Inference path: get_bars, with identical provider bars/configuration.
    inference = feed.get_bars(tickers, days=len(sessions))

    # Both callers must request the same versioned Alpaca data configuration.
    assert len(feed.client.requests) == 2
    for request in feed.client.requests:
        assert request.feed == "iex"
        assert request.adjustment == "all"
        assert request.timeframe == _DAILY_TIMEFRAME

    # PPO consumes these five features per ticker in ticker order.
    feature_names = ("Close", "RSI", "MACD", "BB_middle", "ATR")
    training_matrix = pd.DataFrame(
        {
            f"{feature}_{ticker}": historical[(feature, ticker)]
            for ticker in tickers
            for feature in feature_names
        },
        index=historical.index,
    )
    inference_columns = [
        f"{feature}_{ticker}"
        for ticker in tickers
        for feature in feature_names
    ]
    inference_matrix = inference.loc[:, inference_columns]

    # Compare actual output matrices, including dates, order, values, and NaN warm-up.
    pd.testing.assert_index_equal(training_matrix.index, inference_matrix.index)
    assert list(training_matrix.columns) == inference_columns
    pd.testing.assert_frame_equal(
        training_matrix,
        inference_matrix,
        check_exact=True,
        check_freq=False,
    )

    # Indicator warm-up legitimately produces NaNs at the start of the series.
    # Both pipelines must preserve the same NaN mask; infinities are never valid.
    values = inference_matrix.to_numpy(dtype=float)
    assert not np.isinf(values).any()
    assert np.isfinite(values).any(axis=0).all()
    assert np.isfinite(values[-1]).all()

    assert inference.attrs["market_data_contract"]
    assert inference.attrs["market_data_fingerprint"]
