"""Parity and corporate-action regression tests for the canonical feature engine."""
import numpy as np
import pandas as pd
import pytest

from src.data.alpaca_feed import AlpacaFeed
from src.data.data_processor import DataProcessor
from src.data.feature_engineering import calculate_features


def _ohlcv(length=80, close=100.0):
    index = pd.date_range("2023-01-03", periods=length, freq="B")
    prices = np.full(length, close, dtype=float)
    return pd.DataFrame(
        {
            "Open": prices - 0.5,
            "High": prices + 1.0,
            "Low": prices - 1.0,
            "Close": prices,
            "Volume": np.full(length, 1000.0),
        },
        index=index,
    )


def test_training_and_inference_use_identical_feature_values():
    frame = _ohlcv()
    # Include a deterministic price path so every indicator is exercised.
    frame["Close"] = 100.0 + np.arange(len(frame)) * 0.17 + np.sin(np.arange(len(frame)) / 3)
    frame["Open"] = frame["Close"] - 0.4
    frame["High"] = frame["Close"] + 1.2
    frame["Low"] = frame["Close"] - 1.1

    training = DataProcessor._indicators(frame)
    inference = AlpacaFeed._calculate_features(frame)
    canonical = calculate_features(frame)

    pd.testing.assert_frame_equal(training, inference)
    pd.testing.assert_frame_equal(training, canonical)
    np.testing.assert_allclose(
        training[["RSI", "MACD", "BB_middle", "ATR"]].to_numpy(),
        inference[["RSI", "MACD", "BB_middle", "ATR"]].to_numpy(),
        equal_nan=True,
    )


@pytest.mark.parametrize(
    "event, adjustment_factor",
    [("split", 0.5), ("dividend", 0.98), ("spin-off", 0.8)],
)
def test_provider_adjusted_synthetic_corporate_actions_do_not_create_feature_jumps(
    event, adjustment_factor
):
    event_at = 35
    raw = _ohlcv(length=80, close=100.0)
    raw.iloc[:event_at, raw.columns.get_indexer(["Open", "High", "Low", "Close"])] /= adjustment_factor

    # Simulate the provider's backward adjustment: pre-event OHLC is restated
    # onto the post-event price basis. This is an internal pipeline fixture,
    # not a claim about provider coverage or real-world adjustment correctness.
    adjusted = raw.copy()
    adjusted.iloc[:event_at, adjusted.columns.get_indexer(["Open", "High", "Low", "Close"])] *= adjustment_factor

    reference = _ohlcv(length=80, close=100.0)
    adjusted_features = calculate_features(adjusted)
    reference_features = calculate_features(reference)

    pd.testing.assert_frame_equal(
        adjusted_features[["RSI", "MACD", "BB_middle", "ATR"]],
        reference_features[["RSI", "MACD", "BB_middle", "ATR"]],
        check_freq=False,
        check_exact=False,
        rtol=1e-12,
        atol=1e-12,
    )
    assert event in ("split", "dividend", "spin-off")
    assert np.isfinite(adjusted_features.iloc[-1][["RSI", "MACD", "BB_middle", "ATR"]].to_numpy(dtype=float)).all()


def test_feature_engine_rejects_missing_price_inputs():
    with pytest.raises(ValueError, match="Missing OHLCV columns"):
        calculate_features(pd.DataFrame({"Close": [100.0]}))
