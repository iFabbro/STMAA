"""Tests for exact execution-price panel fingerprinting."""
import pandas as pd
import pytest

from src.backtest.benchmark_methodology import (
    execution_data_fingerprint,
    execution_price_panel,
)


def _frame():
    index = pd.date_range("2024-01-02", periods=3, freq="B")
    return pd.DataFrame({
        "Open_A": [10.0, 11.0, 12.0], "Close_A": [10.5, 11.5, 12.5],
        "Open_B": [20.0, 21.0, 22.0], "Close_B": [20.5, 21.5, 22.5],
        "RSI_A": [50.0, 51.0, 52.0],
    }, index=index)


def test_execution_panel_fingerprint_ignores_non_execution_features():
    original = _frame()
    changed_feature = original.copy()
    changed_feature["RSI_A"] = 99.0
    assert execution_data_fingerprint(original, ["A", "B"]) == execution_data_fingerprint(changed_feature, ["A", "B"])


def test_execution_panel_fingerprint_changes_when_price_or_timestamp_changes():
    original = _frame()
    changed_price = original.copy()
    changed_price.loc[changed_price.index[1], "Open_A"] += 0.01
    changed_time = original.copy()
    changed_time.index = changed_time.index + pd.Timedelta(days=1)
    baseline = execution_data_fingerprint(original, ["A", "B"])
    assert execution_data_fingerprint(changed_price, ["A", "B"]) != baseline
    assert execution_data_fingerprint(changed_time, ["A", "B"]) != baseline


def test_execution_price_panel_rejects_missing_or_invalid_prices():
    with pytest.raises(ValueError, match="missing columns"):
        execution_price_panel(_frame(), ["A", "C"])
    invalid = _frame()
    invalid.loc[invalid.index[0], "Close_A"] = 0
    with pytest.raises(ValueError, match="finite and strictly positive"):
        execution_data_fingerprint(invalid, ["A", "B"])



def test_single_ticker_legacy_ohlcv_columns_are_normalized_for_fingerprint():
    index = pd.date_range("2024-01-02", periods=3, freq="B")
    legacy = pd.DataFrame({
        "Open": [10.0, 11.0, 12.0],
        "Close": [10.5, 11.5, 12.5],
    }, index=index)
    suffixed = legacy.rename(columns={"Open": "Open_A", "Close": "Close_A"})
    assert execution_data_fingerprint(legacy, ["A"]) == execution_data_fingerprint(suffixed, ["A"])


def test_multi_ticker_fingerprint_does_not_fallback_to_unsuffixed_prices():
    frame = _frame().drop(columns=["Open_B", "Close_B"])
    with pytest.raises(ValueError, match="missing columns"):
        execution_price_panel(frame, ["A", "B"])
