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
        "version": 1,
        "provider": "alpaca",
        "feed": "iex",
        "timeframe": "1Day",
        "adjustment": "all",
        "features": ["Close", "RSI", "MACD", "BB_middle", "ATR"],
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
