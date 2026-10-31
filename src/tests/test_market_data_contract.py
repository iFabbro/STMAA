"""Regression tests for the versioned training/inference market-data contract."""
import pytest

from src.data.market_data_contract import (
    DATA_ADJUSTMENT,
    DATA_ADJUSTMENT_EVENTS,
    DATA_FEED,
    DATA_PROVIDER,
    DATA_TIMEFRAME,
    market_data_contract,
    validate_market_data_contract,
)


def test_training_and_inference_share_one_alpaca_adjustment_contract():
    contract = market_data_contract()
    assert contract == {
        "version": 2,
        "provider": "alpaca",
        "feed": "iex",
        "timeframe": "1Day",
        "adjustment": "all",
        "features": ["Close", "RSI", "MACD", "BB_middle", "ATR"],
        "feature_version": 1,
        "feature_formulas": {
            "RSI": "rolling_sma_gain_loss; zero_loss_gain_only_100_flat_50",
            "MACD": "ema_12_minus_ema_26_adjust_false",
            "BB_middle": "rolling_sma_20",
            "ATR": "rolling_sma_14_of_true_range",
        },
        "feature_parameters": {
            "rsi_period": 14, "rsi_gain_only": 100.0, "rsi_flat": 50.0,
            "macd_fast": 12, "macd_slow": 26, "macd_signal": 9,
            "bb_period": 20, "bb_std": 2.0, "atr_period": 14,
        },
        "semantics": {
            "adjustment_scope": "provider_adjusted_ohlcv_for_all_supported_corporate_actions",
            "corporate_action_events": ["split", "dividend", "spin-off"],
            "feature_input": "adjusted_open_high_low_close_volume",
            "indicator_warmup": "full_rolling_window; leading_nan_rows_dropped_before_training",
            "missing_values": "no_forward_fill; fail_on_interior_or_trailing_ohlcv_gaps",
            "timestamp": "UTC_provider_timestamp_normalized_to_naive_UTC_for_daily_index",
        },
        "corporate_actions": ["split", "dividend", "spin-off"],
    }
    assert (DATA_PROVIDER, DATA_FEED, DATA_TIMEFRAME, DATA_ADJUSTMENT) == (
        "alpaca", "iex", "1Day", "all"
    )


@pytest.mark.parametrize("event", ["split", "dividend", "spin-off"])
def test_contract_explicitly_covers_corporate_action_adjustment_classes(event):
    # Alpaca's "all" adjustment mode is the single provider contract used by
    # both training and inference; this test guards the declared event scope.
    assert event in DATA_ADJUSTMENT_EVENTS
    validate_market_data_contract(market_data_contract())


@pytest.mark.parametrize(
    "mutation",
    [
        {"adjustment": "raw"},
        {"adjustment": "split"},
        {"feed": "sip"},
        {"provider": "yfinance"},
        {"version": 0},
        {"corporate_actions": ["split", "dividend"]},
        {"feature_version": 0},
        {"feature_formulas": {"RSI": "different"}},
        {"feature_parameters": {"rsi_period": 10}},
    ],
)
def test_contract_rejects_pipeline_or_corporate_action_drift(mutation):
    contract = market_data_contract()
    contract.update(mutation)
    with pytest.raises(ValueError, match="market-data contract"):
        validate_market_data_contract(contract)


def test_contract_rejects_missing_metadata():
    with pytest.raises(ValueError, match="market-data contract"):
        validate_market_data_contract(None)
