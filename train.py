#!/usr/bin/env python3
"""Train and diagnostically validate an experimental STMAA PPO policy."""
import argparse
import hashlib
import subprocess
import sys
from dotenv import load_dotenv
from pathlib import Path

sys.path.append(str(Path(__file__).parent / "src"))

import numpy as np
import pandas as pd

from config import (
    BASE_DIR,
    DATA_DIR,
    MODELS_DIR,
)
from agents.ppo_agent import PPOAgent
from data.data_processor import DataProcessor
from src.data.market_data_contract import market_data_contract
from src.utils.startup_config import load_strategy_config


MODEL_PATH = MODELS_DIR / "ppo_trading_model.zip"
REQUIRED_FEATURES = ("Open", "Close", "RSI", "MACD", "BB_middle", "ATR")


def load_training_config(overrides=None):
    """Load project-root .env before resolving the shared strategy settings.

    Existing process environment variables retain precedence over .env values.
    Broker credentials are not required by this configuration loader.
    """
    load_dotenv(dotenv_path=BASE_DIR / ".env", override=False)
    return load_strategy_config(overrides=overrides)


def training_data_source() -> str:
    """Return provenance metadata derived from the active market-data contract."""
    contract = market_data_contract()
    return f"{contract['provider']}_{contract['feed']}_adjusted_ohlcv"


def flatten_processed_data(data: pd.DataFrame, tickers: list[str]) -> pd.DataFrame:
    """Convert provider field/ticker columns into the PPO feature contract."""
    if not isinstance(data, pd.DataFrame) or data.empty:
        raise ValueError("Historical market data must be a non-empty DataFrame")
    field_level = DataProcessor._field_level(data.columns)
    if field_level is None:
        required = [f"{feature}_{ticker}" for ticker in tickers for feature in REQUIRED_FEATURES]
        if all(column in data.columns for column in required):
            return data.loc[:, required].copy()
        # A single-ticker provider response may use flat field names; map those
        # fields to the same ticker-suffixed feature contract used at inference.
        if len(tickers) == 1 and all(feature in data.columns for feature in REQUIRED_FEATURES):
            return pd.DataFrame(
                {f"{feature}_{tickers[0]}": data[feature] for feature in REQUIRED_FEATURES},
                index=data.index,
            )
        missing = [column for column in required if column not in data.columns]
        raise ValueError(f"Processed data is missing PPO features: {missing}")

    if data.columns.nlevels != 2:
        raise ValueError("Only two-level provider OHLCV columns are supported")
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


def training_dataset_sha256(data: pd.DataFrame) -> str:
    """Fingerprint the exact processed feature frame supplied to PPO."""
    if not isinstance(data, pd.DataFrame) or data.empty:
        raise ValueError("Training dataset fingerprint requires a non-empty DataFrame")
    canonical_csv = data.to_csv(
        index=True,
        date_format="%Y-%m-%dT%H:%M:%S.%f",
        float_format="%.17g",
        lineterminator="\n",
    )
    payload = "stmaa-ppo-training-dataset-v1\n" + canonical_csv
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def training_git_commit() -> str | None:
    """Return the checked-out source commit, or None outside a Git checkout."""
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=BASE_DIR,
        capture_output=True,
        text=True,
        check=False,
    )
    commit = result.stdout.strip().lower()
    if result.returncode == 0 and len(commit) == 40 and all(
        character in "0123456789abcdef" for character in commit
    ):
        return commit
    return None


def training_git_worktree_clean() -> bool | None:
    """Report whether the Git checkout has no tracked or untracked changes."""
    result = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=BASE_DIR,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    return not result.stdout.strip()


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
        description="Train STMAA PPO on Alpaca contract-adjusted historical OHLCV data and validate on a later holdout"
    )
    parser.add_argument("--start-date", required=True, help="Inclusive history start, YYYY-MM-DD")
    parser.add_argument("--end-date", required=True, help="Exclusive history end, YYYY-MM-DD")
    parser.add_argument("--tickers", nargs="+", default=None)
    parser.add_argument("--total-timesteps", type=int, default=50000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--learning-rate", type=float, default=3e-4)
    parser.add_argument("--n-steps", type=int, default=2048)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--n-epochs", type=int, default=10)
    parser.add_argument("--gamma", type=float, default=0.99)
    parser.add_argument("--validation-fraction", type=float, default=0.2)
    parser.add_argument("--model-path", type=Path, default=MODEL_PATH)
    parser.add_argument("--initial-capital", type=float, default=None)
    parser.add_argument("--max-position-size", type=float, default=None)
    parser.add_argument("--max-gross-exposure", type=float, default=None)
    parser.add_argument("--transaction-cost", type=float, default=None)
    parser.add_argument("--slippage", type=float, default=None)
    args = parser.parse_args(argv)

    if args.total_timesteps <= 0:
        parser.error("--total-timesteps must be positive")
    if not np.isfinite(args.learning_rate) or args.learning_rate <= 0:
        parser.error("--learning-rate must be finite and positive")
    if args.n_steps <= 0 or args.batch_size <= 0 or args.n_epochs <= 0:
        parser.error("--n-steps, --batch-size and --n-epochs must be positive")
    if not np.isfinite(args.gamma) or not 0 <= args.gamma <= 1:
        parser.error("--gamma must be finite and between 0 and 1")
    overrides = {}
    for argument, environment_name in (
        ("tickers", "STMAA_TICKERS"),
        ("initial_capital", "STMAA_INITIAL_CAPITAL"),
        ("max_position_size", "MAX_POSITION_SIZE"),
        ("max_gross_exposure", "MAX_PORTFOLIO_EXPOSURE"),
        ("transaction_cost", "TRANSACTION_COST"),
        ("slippage", "SLIPPAGE"),
    ):
        value = getattr(args, argument)
        if value is not None:
            overrides[environment_name] = value
    try:
        strategy_config = load_training_config(overrides=overrides)
    except ValueError as exc:
        parser.error(str(exc))
    tickers = list(strategy_config.tickers)

    processor = DataProcessor(DATA_DIR)
    history = prepare_historical_data(
        processor, tickers, args.start_date, args.end_date
    )
    training, validation = chronological_split(history, args.validation_fraction)

    agent = PPOAgent(
        tickers=tickers,
        initial_capital=strategy_config.initial_capital,
        model_path=str(args.model_path),
        transaction_cost=strategy_config.transaction_cost,
        slippage=strategy_config.slippage,
        max_position_size=strategy_config.max_position_size,
        max_gross_exposure=strategy_config.max_gross_exposure,
        load_existing_model=False,
        seed=args.seed,
        learning_rate=args.learning_rate,
        n_steps=args.n_steps,
        batch_size=args.batch_size,
        n_epochs=args.n_epochs,
        gamma=args.gamma,
        verbose=1,
    )
    metadata = {
        "data_source": training_data_source(),
        "market_data_contract": market_data_contract(),
        "start_date": str(history.index.min()),
        "end_date": str(history.index.max()),
        "training_start": str(training.index.min()),
        "training_end": str(training.index.max()),
        "validation_start": str(validation.index.min()),
        "validation_end": str(validation.index.max()),
        "validation_fraction": args.validation_fraction,
        "total_timesteps": args.total_timesteps,
        "seed": args.seed,
        "learning_rate": args.learning_rate,
        "n_steps": args.n_steps,
        "batch_size": args.batch_size,
        "n_epochs": args.n_epochs,
        "gamma": args.gamma,
        "initial_capital": strategy_config.initial_capital,
        "dataset_sha256": training_dataset_sha256(history),
        "git_commit": training_git_commit(),
        "git_worktree_clean": training_git_worktree_clean(),
    }
    metrics = agent.train(
        training,
        total_timesteps=args.total_timesteps,
        validation_df=validation,
        training_metadata=metadata,
    )
    print("PPO training and chronological holdout evaluation completed.")
    print(f"Model: {args.model_path}")
    print(f"Training tickers: {tickers}")
    print(f"Initial capital: {strategy_config.initial_capital:.2f}")
    print(f"Maximum gross exposure: {strategy_config.max_gross_exposure:.4f}")
    print(f"Training observations: {len(training)}")
    print(f"Validation observations: {len(validation)}")
    print(f"Validation metrics: {metrics}")
    print("Validation metrics are diagnostic; they are not a guarantee of future performance.")


if __name__ == "__main__":
    main()
