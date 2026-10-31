"""Synthetic corporate-action regression coverage for training/inference parity.

The fixtures represent bars already adjusted by Alpaca's documented
adjustment="all" mode. They validate STMAA's downstream handling and parity;
they do not independently certify the provider's adjustment calculations.
"""

from pathlib import Path

import numpy as np
import pandas as pd
import pandas_market_calendars as mcal
import pytest

import src.data.data_processor as data_processor_module
from src.data.alpaca_feed import AlpacaFeed


class _BarsResponse:
    def __init__(self, frame):
        self.df = frame


class _DeterministicAlpacaClient:
    def __init__(self, frame):
        self.frame = frame
        self.requests = []

    def get_stock_bars(self, request):
        self.requests.append(request)
        return _BarsResponse(self.frame.copy())


def _corporate_action_bars(event, sessions):
    """Build a synthetic raw event and its expected provider-adjusted OHLCV."""
    step = np.arange(len(sessions), dtype=float)
    baseline_close = 100.0 + step * 0.12 + np.sin(step / 4.0) * 0.3
    baseline_volume = 100_000.0 + step * 125.0
    event_index = len(sessions) // 2

    raw_close = baseline_close.copy()
    raw_volume = baseline_volume.copy()
    if event == "split":
        # 2-for-1 split: pre-event raw prices double and raw volume halves.
        raw_close[:event_index] *= 2.0
        raw_volume[:event_index] /= 2.0
        price_factor = 0.5
        volume_factor = 2.0
    elif event == "dividend":
        # Cash dividend: 2% backward price adjustment, no volume adjustment.
        raw_close[:event_index] /= 0.98
        price_factor = 0.98
        volume_factor = 1.0
    elif event == "spin-off":
        # Spin-off: parent price is reduced by 10% on the ex-date.
        raw_close[:event_index] /= 0.90
        price_factor = 0.90
        volume_factor = 1.0
    else:
        raise AssertionError(f"Unsupported fixture event: {event}")

    raw_open = raw_close - 0.10
    raw_high = raw_close + 0.60
    raw_low = raw_close - 0.60

    adjusted = pd.DataFrame(
        {
            "Open": raw_open,
            "High": raw_high,
            "Low": raw_low,
            "Close": raw_close,
            "Volume": raw_volume,
        },
        index=sessions,
    )
    adjusted.loc[adjusted.index[:event_index], ["Open", "High", "Low", "Close"]] *= price_factor
    adjusted.loc[adjusted.index[:event_index], "Volume"] *= volume_factor

    # Confirm the fixture models the expected post-adjustment series.
    np.testing.assert_allclose(adjusted["Close"], baseline_close, rtol=0, atol=1e-10)
    np.testing.assert_allclose(adjusted["Volume"], baseline_volume, rtol=0, atol=1e-8)

    multi_index = pd.MultiIndex.from_tuples(
        [("AAA", timestamp) for timestamp in sessions],
        names=["symbol", "timestamp"],
    )
    adjusted.index = multi_index
    return adjusted, event_index


@pytest.mark.parametrize("event", ["split", "dividend", "spin-off"])
def test_corporate_action_adjusted_history_has_training_inference_feature_parity(
    event, tmp_path: Path, monkeypatch
):
    """Both public ingestion paths consume the same adjusted event history."""
    sessions = mcal.get_calendar("NYSE").schedule(
        start_date="2025-01-02",
        end_date="2025-06-30",
    ).index
    adjusted_bars, event_index = _corporate_action_bars(event, sessions)

    feed = AlpacaFeed.__new__(AlpacaFeed)
    feed.client = _DeterministicAlpacaClient(adjusted_bars)
    monkeypatch.setattr(
        AlpacaFeed,
        "_latest_completed_nyse_session_date",
        staticmethod(lambda now=None: sessions[-1].date()),
    )
    monkeypatch.setattr(data_processor_module, "AlpacaFeed", lambda: feed)

    start_date = sessions[0].date().isoformat()
    end_date = (sessions[-1] + pd.Timedelta(days=1)).date().isoformat()

    processor = data_processor_module.DataProcessor(tmp_path / event)
    historical = processor.process(["AAA"], start_date, end_date)
    inference = feed.get_bars(["AAA"], days=len(sessions))

    feature_names = ("Close", "RSI", "MACD", "BB_middle", "ATR")
    training_matrix = pd.DataFrame(
        {
            f"{feature}_AAA": historical[(feature, "AAA")]
            for feature in feature_names
        },
        index=historical.index,
    )
    inference_matrix = inference.loc[:, [f"{feature}_AAA" for feature in feature_names]]

    pd.testing.assert_index_equal(training_matrix.index, inference_matrix.index)
    pd.testing.assert_frame_equal(
        training_matrix,
        inference_matrix,
        check_exact=True,
        check_freq=False,
    )

    # The adjusted parent series must not inject an artificial ex-date price gap.
    adjusted_close = adjusted_bars["close"].to_numpy(dtype=float)
    event_return = adjusted_close[event_index] / adjusted_close[event_index - 1] - 1.0
    assert abs(event_return) < 0.01

    # Both callers must request the provider's complete adjustment mode.
    assert len(feed.client.requests) == 2
    for request in feed.client.requests:
        adjustment = getattr(request.adjustment, "value", request.adjustment)
        feed = getattr(request.feed, "value", request.feed)
        assert adjustment == "all"
        assert feed == "iex"
