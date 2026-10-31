"""Versioned market-data and feature contract shared by training and inference.

Provider-adjusted Alpaca IEX daily OHLCV is the sole input contract. Changes to
provider semantics, feature formulas, parameters, or warm-up policy require a
contract bump and model retraining/revalidation.
"""
import hashlib
import hmac
import json
from typing import Any

import pandas as pd

DATA_CONTRACT_VERSION = 2
DATA_PROVIDER = "alpaca"
DATA_FEED = "iex"
DATA_TIMEFRAME = "1Day"
DATA_ADJUSTMENT = "all"
DATA_FEATURE_VERSION = 1
DATA_FEATURES = ("Close", "RSI", "MACD", "BB_middle", "ATR")
DATA_ADJUSTMENT_EVENTS = ("split", "dividend", "spin-off")
FEATURE_FORMULAS = {
    "RSI": "rolling_sma_gain_loss; zero_loss_gain_only_100_flat_50",
    "MACD": "ema_12_minus_ema_26_adjust_false",
    "BB_middle": "rolling_sma_20",
    "ATR": "rolling_sma_14_of_true_range",
}
FEATURE_PARAMETERS = {
    "rsi_period": 14,
    "rsi_gain_only": 100.0,
    "rsi_flat": 50.0,
    "macd_fast": 12,
    "macd_slow": 26,
    "macd_signal": 9,
    "bb_period": 20,
    "bb_std": 2.0,
    "atr_period": 14,
}
DATA_SEMANTICS = {
    "adjustment_scope": "provider_adjusted_ohlcv_for_all_supported_corporate_actions",
    "corporate_action_events": list(DATA_ADJUSTMENT_EVENTS),
    "feature_input": "adjusted_open_high_low_close_volume",
    "indicator_warmup": "full_rolling_window; leading_nan_rows_dropped_before_training",
    "missing_values": "no_forward_fill; fail_on_interior_or_trailing_ohlcv_gaps",
    "timestamp": "UTC_provider_timestamp_normalized_to_naive_UTC_for_daily_index",
}


def market_data_contract() -> dict[str, Any]:
    return {
        "version": DATA_CONTRACT_VERSION,
        "provider": DATA_PROVIDER,
        "feed": DATA_FEED,
        "timeframe": DATA_TIMEFRAME,
        "adjustment": DATA_ADJUSTMENT,
        "features": list(DATA_FEATURES),
        "feature_version": DATA_FEATURE_VERSION,
        "feature_formulas": dict(FEATURE_FORMULAS),
        "feature_parameters": dict(FEATURE_PARAMETERS),
        "semantics": {
            **DATA_SEMANTICS,
            "corporate_action_events": list(DATA_ADJUSTMENT_EVENTS),
        },
        "corporate_actions": list(DATA_ADJUSTMENT_EVENTS),
    }


def validate_market_data_contract(metadata: Any) -> None:
    if not isinstance(metadata, dict) or metadata != market_data_contract():
        raise ValueError(
            "Model market-data contract is missing or incompatible; retrain with "
            "the current Alpaca IEX daily adjustment and feature contract"
        )



def market_data_fingerprint(frame: pd.DataFrame) -> str:
    """Fingerprint the exact inference frame, including schema, order, index and values."""
    if not isinstance(frame, pd.DataFrame):
        raise ValueError("Market-data fingerprint requires a DataFrame")
    if not frame.columns.is_unique or not all(isinstance(column, str) for column in frame.columns):
        raise ValueError("Market-data columns must be unique strings")
    metadata = {
        "columns": list(frame.columns),
        "dtypes": [str(dtype) for dtype in frame.dtypes],
        "shape": list(frame.shape),
        "index_dtype": str(frame.index.dtype),
        "index_name": frame.index.name,
    }
    digest = hashlib.sha256()
    digest.update(json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    try:
        row_hashes = pd.util.hash_pandas_object(
            frame, index=True, categorize=True
        ).to_numpy(dtype="uint64", copy=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("Market-data frame cannot be fingerprinted safely") from exc
    digest.update(row_hashes.tobytes())
    return digest.hexdigest()


def validate_market_data_fingerprint(frame: pd.DataFrame, metadata: Any) -> None:
    """Reject frames changed after ingestion even when pandas preserved attrs."""
    if (
        not isinstance(metadata, str)
        or len(metadata) != 64
        or any(character not in "0123456789abcdef" for character in metadata)
    ):
        raise ValueError(
            "Market-data fingerprint is missing or invalid; reacquire market data"
        )
    actual = market_data_fingerprint(frame)
    if not hmac.compare_digest(metadata, actual):
        raise ValueError(
            "Market-data frame changed after ingestion; reacquire market data before PPO inference"
        )
