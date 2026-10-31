"""Versioned market-data contract shared by training and inference.

All policy inputs must originate from Alpaca IEX daily bars adjusted for
all supported corporate actions. Changing any field requires a contract bump
and retraining/revalidation of deployed models.
"""
from typing import Any

DATA_CONTRACT_VERSION = 1
DATA_PROVIDER = "alpaca"
DATA_FEED = "iex"
DATA_TIMEFRAME = "1Day"
DATA_ADJUSTMENT = "all"
DATA_FEATURES = ("Close", "RSI", "MACD", "BB_middle", "ATR")
DATA_ADJUSTMENT_EVENTS = ("split", "dividend", "spin-off")


def market_data_contract() -> dict[str, Any]:
    return {
        "version": DATA_CONTRACT_VERSION,
        "provider": DATA_PROVIDER,
        "feed": DATA_FEED,
        "timeframe": DATA_TIMEFRAME,
        "adjustment": DATA_ADJUSTMENT,
        "features": list(DATA_FEATURES),
        "corporate_actions": list(DATA_ADJUSTMENT_EVENTS),
    }


def validate_market_data_contract(metadata: Any) -> None:
    if not isinstance(metadata, dict) or metadata != market_data_contract():
        raise ValueError(
            "Model market-data contract is missing or incompatible; retrain with "
            "the current Alpaca IEX daily adjustment contract"
        )
