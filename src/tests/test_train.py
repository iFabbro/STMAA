import numpy as np
import pandas as pd
import pytest

from train import chronological_split, flatten_processed_data


def test_flatten_processed_yfinance_columns_to_ppo_feature_contract():
    fields = ["Open", "High", "Low", "Close", "Volume", "RSI", "MACD", "BB_middle", "ATR"]
    tickers = ["AAA", "BBB"]
    columns = pd.MultiIndex.from_product([fields, tickers])
    values = np.arange(5 * len(columns), dtype=float).reshape(5, len(columns)) + 1
    data = pd.DataFrame(
        values,
        index=pd.date_range("2024-01-01", periods=5, freq="D"),
        columns=columns,
    )

    flattened = flatten_processed_data(data, tickers)

    assert list(flattened.columns) == [
        f"{feature}_{ticker}"
        for ticker in tickers
        for feature in ("Close", "RSI", "MACD", "BB_middle", "ATR")
    ]
    np.testing.assert_allclose(flattened["Close_AAA"], data[("Close", "AAA")])


def test_flatten_rejects_missing_ticker_or_feature():
    columns = pd.MultiIndex.from_tuples([
        ("Close", "AAA"), ("RSI", "AAA"), ("MACD", "AAA"),
        ("BB_middle", "AAA"), ("ATR", "AAA"),
    ])
    data = pd.DataFrame([[100, 50, 0, 100, 1]], columns=columns)
    with pytest.raises(ValueError, match="missing tickers"):
        flatten_processed_data(data, ["AAA", "BBB"])


def test_chronological_split_keeps_later_holdout_and_no_overlap():
    data = pd.DataFrame(
        {"Close_AAA": np.arange(100, dtype=float)},
        index=pd.date_range("2020-01-01", periods=100, freq="D"),
    )

    training, validation = chronological_split(data, validation_fraction=0.2)

    assert len(training) == 80
    assert len(validation) == 20
    assert training.index.max() < validation.index.min()
    assert training.iloc[-1, 0] == 79
    assert validation.iloc[0, 0] == 80


def test_chronological_split_rejects_too_small_or_invalid_fraction():
    data = pd.DataFrame({"Close_AAA": np.arange(40, dtype=float)})
    with pytest.raises(ValueError, match="between 0.1 and 0.4"):
        chronological_split(data, validation_fraction=0.5)
    with pytest.raises(ValueError, match="at least 30 training"):
        chronological_split(data, validation_fraction=0.2)



def test_flatten_processed_single_ticker_flat_columns():
    data = pd.DataFrame({
        "Close": [100.0, 101.0],
        "RSI": [50.0, 55.0],
        "MACD": [0.0, 0.2],
        "BB_middle": [99.0, 100.0],
        "ATR": [1.0, 1.1],
    })
    flattened = flatten_processed_data(data, ["AAA"])
    assert list(flattened.columns) == [
        "Close_AAA", "RSI_AAA", "MACD_AAA", "BB_middle_AAA", "ATR_AAA"
    ]
    np.testing.assert_allclose(flattened["Close_AAA"], data["Close"])
