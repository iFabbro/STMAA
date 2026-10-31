"""Market-data ingestion, cleaning, and causal technical indicators."""
from pathlib import Path
from typing import List

import numpy as np
import pandas as pd
from src.data.alpaca_feed import AlpacaFeed


_OHLCV_FIELDS = {"Open", "High", "Low", "Close", "Volume"}
_PRICE_FIELDS = ("Open", "High", "Low", "Close")


class DataProcessor:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.raw_dir = data_dir / "raw"
        self.processed_dir = data_dir / "processed"
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.processed_dir.mkdir(parents=True, exist_ok=True)

    def download_data(self, tickers: List[str], start_date: str, end_date: str) -> pd.DataFrame:
        """Download contract-compliant adjusted OHLCV data from Alpaca."""
        if not tickers:
            raise ValueError("tickers must contain at least one symbol")
        data = AlpacaFeed().get_historical_ohlcv(tickers, start_date, end_date)
        if data.empty:
            raise ValueError("Market-data provider returned no rows")
        raw_file = self.raw_dir / f"raw_{start_date}_{end_date}.csv"
        data.to_csv(raw_file)
        return data

    @staticmethod
    def _field_level(columns: pd.Index) -> int | None:
        """Return the MultiIndex level containing OHLCV field names."""
        if not isinstance(columns, pd.MultiIndex):
            return None
        candidates = [
            level
            for level in range(columns.nlevels)
            if _OHLCV_FIELDS.intersection(set(columns.get_level_values(level)))
        ]
        if len(candidates) != 1:
            raise ValueError("Cannot unambiguously identify the OHLCV level in MultiIndex columns")
        return candidates[0]

    @staticmethod
    def _clean_ohlcv_frame(frame: pd.DataFrame) -> pd.DataFrame:
        missing = _OHLCV_FIELDS.difference(frame.columns)
        if missing:
            raise ValueError(f"Missing required OHLCV columns: {sorted(missing)}")
        cleaned = frame.copy()
        # Leading missing values are allowed (for example, before a symbol's
        # first available observation). Once a field has started, every later
        # bar must be present: forward-filling would manufacture stale prices
        # and silently compress missing market sessions in downstream training.
        for col in ("Open", "High", "Low", "Close", "Volume"):
            first_valid = cleaned[col].first_valid_index()
            if first_valid is None:
                raise ValueError(f"No valid {col} observations in market data")
            missing_after_start = cleaned.loc[first_valid:, col].isna()
            if missing_after_start.any():
                missing_at = missing_after_start[missing_after_start].index[0]
                raise ValueError(
                    f"Missing {col} observation after first valid bar at {missing_at!r}"
                )
        return cleaned

    def clean_data(self, data: pd.DataFrame) -> pd.DataFrame:
        """Clean single- or multi-ticker OHLCV data without backward filling."""
        if data.empty:
            return data.copy()
        field_level = self._field_level(data.columns)
        if field_level is None:
            return self._clean_ohlcv_frame(data)

        ticker_level = 1 - field_level if data.columns.nlevels == 2 else None
        if ticker_level is None:
            raise ValueError("Only two-level ticker/field MultiIndex columns are supported")

        # Preserve fully missing rows so gaps are detected per symbol.
        cleaned = data.copy()
        tickers = list(dict.fromkeys(data.columns.get_level_values(ticker_level)))
        for ticker in tickers:
            ticker_frame = data.xs(ticker, axis=1, level=ticker_level, drop_level=True)
            ticker_frame = self._clean_ohlcv_frame(ticker_frame)
            for field in ticker_frame.columns:
                key = (field, ticker) if field_level == 0 else (ticker, field)
                cleaned[key] = ticker_frame[field].reindex(cleaned.index)
        return cleaned

    @staticmethod
    def _indicators(frame: pd.DataFrame) -> pd.DataFrame:
        """Calculate indicators for one ticker using only current and prior bars."""
        result = pd.DataFrame(index=frame.index)
        close = frame["Close"]
        high = frame["High"]
        low = frame["Low"]

        delta = close.diff()
        gain = delta.clip(lower=0).rolling(window=14).mean()
        loss = (-delta.clip(upper=0)).rolling(window=14).mean()
        rs = gain / loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        # A positive gain with zero average loss has RSI 100; a flat window is 50.
        rsi = rsi.where(loss.ne(0), np.where(gain.gt(0), 100.0, 50.0))
        result["RSI"] = rsi

        exp1 = close.ewm(span=12, adjust=False).mean()
        exp2 = close.ewm(span=26, adjust=False).mean()
        result["MACD"] = exp1 - exp2
        result["MACD_Signal"] = result["MACD"].ewm(span=9, adjust=False).mean()

        result["BB_middle"] = close.rolling(window=20).mean()
        result["BB_std"] = close.rolling(window=20).std()
        result["BB_upper"] = result["BB_middle"] + result["BB_std"] * 2
        result["BB_lower"] = result["BB_middle"] - result["BB_std"] * 2

        previous_close = close.shift(1)
        true_range = pd.concat(
            [
                high - low,
                (high - previous_close).abs(),
                (low - previous_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        result["TR"] = true_range
        result["ATR"] = true_range.rolling(window=14).mean()
        return result

    def add_technical_indicators(self, data: pd.DataFrame) -> pd.DataFrame:
        """Add technical indicators to flat or two-level ticker/field OHLCV data."""
        if data.empty:
            return data.copy()
        field_level = self._field_level(data.columns)
        if field_level is None:
            indicators = self._indicators(data)
            result = data.copy()
            for column in indicators:
                result[column] = indicators[column]
            return result

        ticker_level = 1 - field_level if data.columns.nlevels == 2 else None
        if ticker_level is None:
            raise ValueError("Only two-level ticker/field MultiIndex columns are supported")

        result = data.copy()
        tickers = list(dict.fromkeys(data.columns.get_level_values(ticker_level)))
        for ticker in tickers:
            ticker_frame = data.xs(ticker, axis=1, level=ticker_level, drop_level=True)
            missing = _OHLCV_FIELDS.difference(ticker_frame.columns)
            if missing:
                raise ValueError(f"Missing required OHLCV columns for {ticker}: {sorted(missing)}")
            indicators = self._indicators(ticker_frame)
            for field in indicators.columns:
                key = (field, ticker) if field_level == 0 else (ticker, field)
                result[key] = indicators[field]
        return result

    def process(self, tickers: List[str], start_date: str, end_date: str) -> pd.DataFrame:
        """Run download, causal cleaning, and indicator calculation."""
        data = self.download_data(tickers, start_date, end_date)
        cleaned = self.clean_data(data)
        processed = self.add_technical_indicators(cleaned)
        processed_file = self.processed_dir / f"processed_{start_date}_{end_date}.csv"
        processed.to_csv(processed_file)
        return processed
