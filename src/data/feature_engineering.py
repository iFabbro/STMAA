"""Canonical causal OHLCV feature calculations shared by training and inference."""
import numpy as np
import pandas as pd

try:
    from src.data.market_data_contract import FEATURE_PARAMETERS
except ModuleNotFoundError:
    from data.market_data_contract import FEATURE_PARAMETERS


def calculate_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Calculate the versioned feature set from one ticker's adjusted OHLCV bars."""
    required = {"High", "Low", "Close"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"Missing OHLCV columns for feature calculation: {sorted(missing)}")

    params = FEATURE_PARAMETERS
    close = pd.to_numeric(frame["Close"], errors="coerce")
    high = pd.to_numeric(frame["High"], errors="coerce")
    low = pd.to_numeric(frame["Low"], errors="coerce")
    result = pd.DataFrame(index=frame.index)

    delta = close.diff()
    gain = delta.clip(lower=0).rolling(window=params["rsi_period"], min_periods=params["rsi_period"]).mean()
    loss = (-delta.clip(upper=0)).rolling(window=params["rsi_period"], min_periods=params["rsi_period"]).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    result["RSI"] = rsi.where(loss.ne(0), np.where(gain.gt(0), params["rsi_gain_only"], params["rsi_flat"]))

    fast = close.ewm(span=params["macd_fast"], adjust=False).mean()
    slow = close.ewm(span=params["macd_slow"], adjust=False).mean()
    result["MACD"] = fast - slow
    result["MACD_Signal"] = result["MACD"].ewm(span=params["macd_signal"], adjust=False).mean()

    result["BB_middle"] = close.rolling(window=params["bb_period"], min_periods=params["bb_period"]).mean()
    result["BB_std"] = close.rolling(window=params["bb_period"], min_periods=params["bb_period"]).std()
    result["BB_upper"] = result["BB_middle"] + result["BB_std"] * params["bb_std"]
    result["BB_lower"] = result["BB_middle"] - result["BB_std"] * params["bb_std"]

    previous_close = close.shift(1)
    true_range = pd.concat(
        [high - low, (high - previous_close).abs(), (low - previous_close).abs()],
        axis=1,
    ).max(axis=1)
    result["TR"] = true_range
    result["ATR"] = true_range.rolling(window=params["atr_period"], min_periods=params["atr_period"]).mean()
    return result
