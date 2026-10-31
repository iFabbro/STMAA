#!/usr/bin/env python3
"""Train and validate the deployable STMAA PPO policy on historical market data."""
import argparse
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent / "src"))

import numpy as np
import pandas as pd

from config import (
    DATA_DIR,
    INITIAL_CAPITAL,
    MAX_POSITION_SIZE,
    MODELS_DIR,
    SLIPPAGE,
    TRANSACTION_COST,
)
from agents.ppo_agent import PPOAgent
from data.data_processor import DataProcessor


DEFAULT_TICKERS = ["AAPL", "GOOGL", "MSFT", "AMZN", "TSLA", "NVDA", "META"]
MODEL_PATH = MODELS_DIR / "ppo_trading_model_v3.zip"
REQUIRED_FEATURES = ("Close", "RSI", "MACD", "BB_middle", "ATR")


def flatten_processed_data(data: pd.DataFrame, tickers: list[str]) -> pd.DataFrame:
    """Convert yfinance field/ticker columns into the PPO feature contract."""
    if not isinstance(data, pd.DataFrame) or data.empty:
        raise ValueError("Historical market data must be a non-empty DataFrame")
    field_level = DataProcessor._field_level(data.columns)
    if field_level is None:
        result = data.copy()
        required = [f"{feature}_{ticker}" for ticker in tickers for feature in REQUIRED_FEATURES]
        missing = [column for column in required if column not in result.columns]
        if missing:
            raise ValueError(f"Processed data is missing PPO features: {missing}")
        return result.loc[:, required]

    if data.columns.nlevels != 2:
        raise ValueError("Only two-level yfinance OHLCV columns are supported")
    ticker_level = 1 - field_level
    available = set(data.columns.get_level_values(ticker_level))
    missing_tickers = [ticker for ticker in tickers if ticker not in available]
    if missing_tickers:
        raise ValueError(f"Historical data is missing tickers: {missing_tickers}")

    flattened = {}
    for ticker in tickers:
        frame = data.xs(ticker, axis=1, level=ticker_level, drop_level=True)
        missing = [feature for feature in REQUIRED_FEATURES if feature not in frame.columns]
        if missing:
            raise ValueError(f"Missing PPO features for {ticker}: {missing}")
        for feature in REQUIRED_FEATURES:
            flattened[f"{feature}_{ticker}"] = frame[feature]
    return pd.DataFrame(flattened, index=data.index)


def prepare_historical_data(
    processor: DataProcessor,
    tickers: list[str],
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    """Download, clean, calculate causal indicators, and validate history."""
    processed = processor.process(tickers, start_date, end_date)
    data = flatten_processed_data(processed, tickers)
    data = data.replace([np.inf, -np.inf], np.nan).dropna()
    if data.empty or len(data) < 100:
        raise ValueError(
            f"Insufficient complete historical observations after indicator warm-up: {len(data)}"
        )
    if not data.index.is_unique or not data.index.is_monotonic_increasing:
        raise ValueError("Historical data timestamps must be unique and chronological")
    prices = data[[f"Close_{ticker}" for ticker in tickers]].to_numpy(dtype=float)
    if not np.isfinite(prices).all() or (prices <= 0).any():
        raise ValueError("Historical close prices must be finite and strictly positive")
    features = data.to_numpy(dtype=float)
    if not np.isfinite(features).all():
        raise ValueError("Historical PPO features contain non-finite values")
    return data


def chronological_split(
    data: pd.DataFrame, validation_fraction: float = 0.2
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split in time order; no random shuffle or validation-to-training leakage."""
    if not 0.1 <= validation_fraction <= 0.4:
        raise ValueError("validation_fraction must be between 0.1 and 0.4")
    split_at = int(len(data) * (1.0 - validation_fraction))
    training = data.iloc[:split_at].copy()
    validation = data.iloc[split_at:].copy()
    if len(training) < 30 or len(validation) < 20:
        raise ValueError("Need at least 30 training and 20 validation observations")
    if training.index.max() >= validation.index.min():
        raise ValueError("Training and validation periods overlap")
    return training, validation


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Train STMAA PPO on historical adjusted OHLCV data and validate on a later holdout"
    )
    parser.add_argument("--start-date", required=True, help="Inclusive history start, YYYY-MM-DD")
    parser.add_argument("--end-date", required=True, help="Exclusive history end, YYYY-MM-DD")
    parser.add_argument("--tickers", nargs="+", default=DEFAULT_TICKERS)
    parser.add_argument("--total-timesteps", type=int, default=50000)
    parser.add_argument("--validation-fraction", type=float, default=0.2)
    parser.add_argument("--model-path", type=Path, default=MODEL_PATH)
    parser.add_argument("--max-position-size", type=float, default=MAX_POSITION_SIZE)
    parser.add_argument("--max-gross-exposure", type=float, default=0.5)
    parser.add_argument("--transaction-cost", type=float, default=TRANSACTION_COST)
    parser.add_argument("--slippage", type=float, default=SLIPPAGE)
    args = parser.parse_args(argv)

    if args.total_timesteps <= 0:
        parser.error("--total-timesteps must be positive")
    if not args.tickers or len(set(args.tickers)) != len(args.tickers):
        parser.error("--tickers must contain unique symbols")

    processor = DataProcessor(DATA_DIR)
    history = prepare_historical_data(
        processor, args.tickers, args.start_date, args.end_date
    )
    training, validation = chronological_split(history, args.validation_fraction)

    agent = PPOAgent(
        tickers=args.tickers,
        initial_capital=INITIAL_CAPITAL,
        model_path=str(args.model_path),
        transaction_cost=args.transaction_cost,
        slippage=args.slippage,
        max_position_size=args.max_position_size,
        max_gross_exposure=args.max_gross_exposure,
        verbose=1,
    )
    metadata = {
        "data_source": "yfinance_adjusted_ohlcv",
        "start_date": str(history.index.min()),
        "end_date": str(history.index.max()),
        "training_start": str(training.index.min()),
        "training_end": str(training.index.max()),
        "validation_start": str(validation.index.min()),
        "validation_end": str(validation.index.max()),
        "validation_fraction": args.validation_fraction,
        "total_timesteps": args.total_timesteps,
        "seed": None,
    }
    metrics = agent.train(
        training,
        total_timesteps=args.total_timesteps,
        validation_df=validation,
        training_metadata=metadata,
    )
    print("PPO training and chronological holdout evaluation completed.")
    print(f"Model: {args.model_path}")
    print(f"Training observations: {len(training)}")
    print(f"Validation observations: {len(validation)}")
    print(f"Validation metrics: {metrics}")
    print("Validation metrics are diagnostic; they are not a guarantee of future performance.")


if __name__ == "__main__":
    main()
