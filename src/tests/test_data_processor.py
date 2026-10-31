"""Regression tests for causal single- and multi-ticker market-data processing."""
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.append(str(Path(__file__).parent.parent.parent))

from src.data.data_processor import DataProcessor
from src.data.alpaca_feed import AlpacaFeed


@pytest.fixture
def processor(tmp_path):
    return DataProcessor(tmp_path / "data")


def _ohlcv(index, offset=0):
    close = np.arange(len(index), dtype=float) + 100 + offset
    return pd.DataFrame(
        {
            "Open": close - 0.5,
            "High": close + 1.0,
            "Low": close - 1.0,
            "Close": close,
            "Volume": np.full(len(index), 1000.0),
        },
        index=index,
    )


def test_data_processor_creation(processor):
    assert processor.data_dir.exists()
    assert processor.raw_dir.exists()
    assert processor.processed_dir.exists()


def test_technical_indicators_for_flat_ohlcv(processor):
    data = _ohlcv(pd.date_range("2024-01-01", periods=40, freq="D"))
    processed = processor.add_technical_indicators(data)
    assert {"RSI", "MACD", "MACD_Signal", "BB_middle", "ATR"}.issubset(processed.columns)
    assert processed["MACD"].notna().any()
    assert processed["RSI"].notna().any()


def test_clean_data_does_not_backfill_initial_missing_prices(processor):
    data = _ohlcv(pd.date_range("2024-01-01", periods=4, freq="D"))
    data.loc[data.index[0], ["Open", "High", "Low", "Close"]] = np.nan

    cleaned = processor.clean_data(data)

    assert cleaned.loc[cleaned.index[0], ["Open", "High", "Low", "Close"]].isna().all()
    assert cleaned.loc[cleaned.index[1], "Close"] == data.loc[cleaned.index[1], "Close"]


def test_clean_data_forward_fills_only_after_first_observation(processor):
    data = _ohlcv(pd.date_range("2024-01-01", periods=4, freq="D"))
    data.loc[data.index[1], "Close"] = np.nan

    cleaned = processor.clean_data(data)

    assert cleaned.loc[cleaned.index[1], "Close"] == data.loc[cleaned.index[0], "Close"]


@pytest.mark.parametrize("field_level", [0, 1])
def test_multi_ticker_multiindex_cleaning_and_indicators(processor, field_level):
    index = pd.date_range("2024-01-01", periods=40, freq="D")
    a = _ohlcv(index)
    b = _ohlcv(index, offset=20)
    wide = pd.concat({"AAA": a, "BBB": b}, axis=1)
    # Exercise both yfinance MultiIndex layouts: (ticker, field) and (field, ticker).
    data = wide if field_level == 1 else wide.swaplevel(0, 1, axis=1)
    data.columns.names = ["ticker", "field"] if field_level == 1 else ["field", "ticker"]

    cleaned = processor.clean_data(data)
    processed = processor.add_technical_indicators(cleaned)

    assert processed.shape[0] == 40
    for ticker in ("AAA", "BBB"):
        close_key = (ticker, "Close") if field_level == 1 else ("Close", ticker)
        rsi_key = (ticker, "RSI") if field_level == 1 else ("RSI", ticker)
        macd_key = (ticker, "MACD") if field_level == 1 else ("MACD", ticker)
        assert processed[close_key].notna().all()
        assert processed[rsi_key].notna().any()
        assert processed[macd_key].notna().any()


def test_multi_ticker_cleaning_does_not_leak_future_price(processor):
    index = pd.date_range("2024-01-01", periods=3, freq="D")
    a = _ohlcv(index)
    b = _ohlcv(index, offset=20)
    data = pd.concat({"AAA": a, "BBB": b}, axis=1)
    data.loc[index[0], ("AAA", "Close")] = np.nan

    cleaned = processor.clean_data(data)

    assert np.isnan(cleaned.loc[index[0], ("AAA", "Close")])
    assert cleaned.loc[index[1], ("AAA", "Close")] == a.loc[index[1], "Close"]


def test_clean_data_rejects_missing_ohlcv_columns(processor):
    with pytest.raises(ValueError, match="Missing required OHLCV columns"):
        processor.clean_data(pd.DataFrame({"Close": [100.0]}))



@pytest.mark.parametrize(
    "prices, expected_value",
    [
        ([100.0] * 20, 50.0),
        ([100.0 + i for i in range(20)], 100.0),
    ],
)
def test_alpaca_rsi_matches_historical_processor_on_zero_loss_windows(prices, expected_value):
    close = pd.Series(prices, dtype=float)
    frame = pd.DataFrame({
        "Close": close,
        "High": close + 1.0,
        "Low": close - 1.0,
    })
    expected = DataProcessor._indicators(frame)["RSI"]
    actual = AlpacaFeed._calculate_rsi(close)

    np.testing.assert_allclose(actual.to_numpy(), expected.to_numpy(), equal_nan=True)
    assert actual.iloc[-1] == pytest.approx(expected_value)
