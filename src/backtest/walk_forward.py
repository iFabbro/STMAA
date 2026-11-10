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
    include_timeseries: bool = False,
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

    factory = agent_factory
    if factory is None:
        from src.agents.ppo_agent import PPOAgent

        def default_agent_factory(_fold_number: int, model_path: str):
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

        factory = default_agent_factory

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
            agent = factory(fold.number, model_path)
            agent.train(training, total_timesteps=total_timesteps)
            metrics = agent.evaluate(
                testing,
                previous_close=previous_close,
                include_timeseries=include_timeseries,
            ) if include_timeseries else agent.evaluate(
                testing, previous_close=previous_close
            )

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



def _finite_metric_values(values: list) -> list[float]:
    """Keep finite numeric metrics; undefined ratios remain excluded, not zero-filled."""
    finite = []
    for value in values:
        if isinstance(value, (int, float, np.integer, np.floating)) and not isinstance(
            value, (bool, np.bool_)
        ):
            numeric = float(value)
            if np.isfinite(numeric):
                finite.append(numeric)
    return finite


def _metric_dispersion(rows: list[dict]) -> dict:
    """Describe metric dispersion across independent observations without compounding."""
    metric_names = sorted(
        {
            name
            for row in rows
            for name, value in row.get("metrics", {}).items()
            if isinstance(value, (int, float, np.integer, np.floating))
            and not isinstance(value, (bool, np.bool_))
        }
    )
    summary = {}
    for name in metric_names:
        values = _finite_metric_values([row.get("metrics", {}).get(name) for row in rows])
        if not values:
            continue
        summary[name] = {
            "n": len(values),
            "mean": float(np.mean(values)),
            "median": float(np.median(values)),
            "std": float(np.std(values, ddof=1)) if len(values) > 1 else 0.0,
            "min": float(np.min(values)),
            "max": float(np.max(values)),
        }
    return summary


def run_multi_seed_walk_forward_validation(
    data: pd.DataFrame,
    tickers: list[str],
    *,
    seeds: list[int],
    min_train_size: int,
    test_size: int,
    step_size: int | None = None,
    total_timesteps: int = 50_000,
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
    """Run identical expanding-window folds independently for each requested seed.

    The same immutable input frame, fold boundaries, and training parameters are
    reused for every seed. Seed/fold runs are independent experiments: their
    returns are never concatenated or compounded into a synthetic equity curve.
    A custom factory, when supplied, receives (fold_number, model_path, seed).
    """
    if not isinstance(seeds, list) or not seeds:
        raise ValueError("seeds must be a non-empty list of integers")
    if any(not isinstance(seed, int) or isinstance(seed, bool) for seed in seeds):
        raise ValueError("seeds must contain only integers")
    if len(set(seeds)) != len(seeds):
        raise ValueError("seeds must be unique")

    folds = expanding_window_folds(data, min_train_size, test_size, step_size)
    fold_layout = [
        {
            "fold": fold.number,
            "training_start": str(data.index[fold.train_start]),
            "training_end": str(data.index[fold.train_end - 1]),
            "test_start": str(data.index[fold.test_start]),
            "test_end": str(data.index[fold.test_end - 1]),
            "training_observations": fold.train_end - fold.train_start,
            "test_observations": fold.test_end - fold.test_start,
        }
        for fold in folds
    ]

    runs = []
    for seed in seeds:
        seed_factory = None
        if agent_factory is not None:
            def seed_factory(fold_number, model_path, current_seed=seed):
                return agent_factory(fold_number, model_path, current_seed)
        report = run_walk_forward_validation(
            data,
            tickers,
            min_train_size=min_train_size,
            test_size=test_size,
            step_size=step_size,
            total_timesteps=total_timesteps,
            seed=seed,
            initial_capital=initial_capital,
            transaction_cost=transaction_cost,
            slippage=slippage,
            max_position_size=max_position_size,
            max_gross_exposure=max_gross_exposure,
            learning_rate=learning_rate,
            n_steps=n_steps,
            batch_size=batch_size,
            n_epochs=n_epochs,
            gamma=gamma,
            agent_factory=seed_factory,
        )
        if report["fold_count"] != len(fold_layout):
            raise RuntimeError("Fold count changed between seed runs")
        observed_layout = [
            {key: row[key] for key in fold_layout[0]}
            for row in report["folds"]
        ]
        if observed_layout != fold_layout:
            raise RuntimeError("Fold boundaries changed between seed runs")
        runs.append(
            {
                "seed": seed,
                "fold_count": report["fold_count"],
                "folds": report["folds"],
            }
        )

    all_rows = [
        {**fold_row, "seed": run["seed"]}
        for run in runs
        for fold_row in run["folds"]
    ]
    per_fold = []
    for fold in fold_layout:
        matching = [
            row for row in all_rows if row["fold"] == fold["fold"]
        ]
        per_fold.append(
            {
                **fold,
                "seed_count": len(matching),
                "metrics_across_seeds": _metric_dispersion(matching),
            }
        )

    return {
        "method": "expanding_window_multi_seed",
        "seed_count": len(seeds),
        "seeds": list(seeds),
        "fold_count": len(fold_layout),
        "run_count": len(all_rows),
        "min_train_size": min_train_size,
        "test_size": test_size,
        "step_size": test_size if step_size is None else step_size,
        "total_timesteps_per_fold_per_seed": total_timesteps,
        "fold_layout": fold_layout,
        "runs": runs,
        "aggregation": {
            "method": "descriptive_dispersion_over_independent_seed_fold_runs",
            "scope": "metrics are summarized across independent seed/fold evaluations; returns are not concatenated or compounded",
            "all_seed_fold_metrics": _metric_dispersion(all_rows),
            "per_fold_metrics_across_seeds": per_fold,
        },
    }
