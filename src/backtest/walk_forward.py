"""Expanding-window walk-forward evaluation for the experimental PPO agent.

Each fold trains a fresh policy on observations strictly preceding its test
window. Test windows are non-overlapping. Fold metrics are reported separately:
the independent all-cash fold resets do not represent one continuous portfolio.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Callable

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class WalkForwardFold:
    """Positional boundaries for one expanding-window fold; end positions exclusive."""

    number: int
    train_start: int
    train_end: int
    test_start: int
    test_end: int

    def split(self, data: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
        training = data.iloc[self.train_start : self.train_end].copy()
        testing = data.iloc[self.test_start : self.test_end].copy()
        if training.empty or testing.empty:
            raise ValueError("Walk-forward fold produced an empty partition")
        if training.index.max() >= testing.index.min():
            raise ValueError("Walk-forward training and test periods overlap")
        return training, testing


def expanding_window_folds(
    data: pd.DataFrame,
    min_train_size: int,
    test_size: int,
    step_size: int | None = None,
) -> list[WalkForwardFold]:
    """Build expanding training windows and chronological, non-overlapping tests.

    The first training window starts at row zero. Each subsequent training
    window expands by step_size; each test window starts immediately after its
    training window. No test observation is used to train the model in the same
    fold. Use fixed hyperparameters chosen before this evaluation; do not tune
    them against these out-of-sample results.
    """
    if not isinstance(data, pd.DataFrame) or data.empty:
        raise ValueError("data must be a non-empty DataFrame")
    if not isinstance(data.index, pd.DatetimeIndex):
        raise ValueError("data must have a DatetimeIndex")
    if data.index.hasnans or not data.index.is_unique or not data.index.is_monotonic_increasing:
        raise ValueError("data timestamps must be valid, unique, and chronological")
    if not isinstance(min_train_size, int) or isinstance(min_train_size, bool) or min_train_size < 22:
        raise ValueError("min_train_size must be an integer >= 22 for PPO environment warm-up")
    if not isinstance(test_size, int) or isinstance(test_size, bool) or test_size < 2:
        raise ValueError("test_size must be an integer >= 2")
    if step_size is None:
        step_size = test_size
    if not isinstance(step_size, int) or isinstance(step_size, bool) or step_size < test_size:
        raise ValueError("step_size must be an integer >= test_size to prevent overlapping test windows")

    folds: list[WalkForwardFold] = []
    train_end = min_train_size
    number = 1
    while train_end + test_size <= len(data):
        folds.append(
            WalkForwardFold(
                number=number,
                train_start=0,
                train_end=train_end,
                test_start=train_end,
                test_end=train_end + test_size,
            )
        )
        train_end += step_size
        number += 1
    if not folds:
        raise ValueError(
            f"Insufficient observations ({len(data)}): need at least "
            f"{min_train_size + test_size} for one walk-forward fold"
        )
    return folds


def run_walk_forward_validation(
    data: pd.DataFrame,
    tickers: list[str],
    *,
    min_train_size: int,
    test_size: int,
    step_size: int | None = None,
    total_timesteps: int = 50_000,
    seed: int = 42,
    initial_capital: float = 100_000.0,
    transaction_cost: float = 0.001,
    slippage: float = 0.0005,
    max_position_size: float = 0.2,
    max_gross_exposure: float = 0.5,
    learning_rate: float = 3e-4,
    n_steps: int = 2048,
    batch_size: int = 64,
    n_epochs: int = 10,
    gamma: float = 0.99,
    agent_factory: Callable | None = None,
) -> dict:
    """Train a fresh PPO model per fold and evaluate only on its future test window.

    The final training close is supplied as the test environment's previous
    close. Models are isolated in temporary directories and are not published
    as reusable production/runtime artifacts. A common seed and fixed
    hyperparameters keep this phase focused on temporal robustness; multi-seed
    analysis is a separate experiment.
    """
    if not tickers or len(set(tickers)) != len(tickers):
        raise ValueError("tickers must be a non-empty list of unique symbols")
    if not isinstance(total_timesteps, int) or isinstance(total_timesteps, bool) or total_timesteps <= 0:
        raise ValueError("total_timesteps must be a positive integer")
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise ValueError("seed must be an integer")

    folds = expanding_window_folds(data, min_train_size, test_size, step_size)

    if agent_factory is None:
        from src.agents.ppo_agent import PPOAgent

        def agent_factory(_fold_number: int, model_path: str):
            return PPOAgent(
                tickers=tickers,
                initial_capital=initial_capital,
                model_path=model_path,
                learning_rate=learning_rate,
                n_steps=n_steps,
                batch_size=batch_size,
                n_epochs=n_epochs,
                gamma=gamma,
                transaction_cost=transaction_cost,
                slippage=slippage,
                max_position_size=max_position_size,
                max_gross_exposure=max_gross_exposure,
                load_existing_model=False,
                seed=seed,
                verbose=0,
            )

    results = []
    for fold in folds:
        training, testing = fold.split(data)
        previous_close = training[
            [f"Close_{ticker}" for ticker in tickers]
        ].iloc[-1].to_numpy(dtype=float)
        if not np.isfinite(previous_close).all() or (previous_close <= 0).any():
            raise ValueError(f"Fold {fold.number} has invalid final training close")

        with TemporaryDirectory(prefix=f"stmaa-ppo-wf-{fold.number}-") as directory:
            model_path = str(Path(directory) / "fold-model.zip")
            agent = agent_factory(fold.number, model_path)
            agent.train(training, total_timesteps=total_timesteps)
            metrics = agent.evaluate(testing, previous_close=previous_close)

        results.append(
            {
                "fold": fold.number,
                "training_start": str(training.index.min()),
                "training_end": str(training.index.max()),
                "test_start": str(testing.index.min()),
                "test_end": str(testing.index.max()),
                "training_observations": len(training),
                "test_observations": len(testing),
                "seed": seed,
                "metrics": metrics,
            }
        )

    return {
        "method": "expanding_window",
        "fold_count": len(results),
        "min_train_size": min_train_size,
        "test_size": test_size,
        "step_size": test_size if step_size is None else step_size,
        "total_timesteps_per_fold": total_timesteps,
        "seed": seed,
        "folds": results,
        "aggregation": {
            "status": "not_computed",
            "reason": (
                "Each fold starts from independent all-cash capital. "
                "Fold metrics are intentionally not concatenated into a synthetic "
                "continuous equity curve."
            ),
        },
    }
