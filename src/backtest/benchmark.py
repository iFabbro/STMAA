"""Execution-aligned buy-and-hold benchmarks for PPO evaluation."""

from __future__ import annotations

import numpy as np
import pandas as pd

try:
    from src.backtest.performance_metrics import calculate_performance_metrics
except ModuleNotFoundError:
    from backtest.performance_metrics import calculate_performance_metrics


def evaluate_equal_weight_buy_and_hold(
    data: pd.DataFrame,
    tickers: list[str],
    *,
    initial_capital: float,
    transaction_cost: float,
    slippage: float,
    max_position_size: float,
    max_gross_exposure: float,
    periods_per_year: float = 252.0,
    risk_free_rate: float = 0.0,
) -> dict:
    """Evaluate a one-time equal-weight entry using PPO's execution convention.

    The portfolio starts in cash at the first test observation. It enters at
    the next observation's Open, pays the same turnover-based transaction cost
    and slippage as StockTradingEnv, and then holds the resulting shares without
    rebalancing. The target respects the same per-name and gross-exposure caps.
    """
    if not isinstance(data, pd.DataFrame) or len(data) < 2:
        raise ValueError("data must contain at least two test observations")
    if not isinstance(data.index, pd.DatetimeIndex):
        raise ValueError("data must have a DatetimeIndex")
    if data.index.hasnans or not data.index.is_unique or not data.index.is_monotonic_increasing:
        raise ValueError("data timestamps must be valid, unique, and chronological")
    if not tickers or len(set(tickers)) != len(tickers):
        raise ValueError("tickers must be a non-empty list of unique symbols")

    parameters = np.asarray(
        [initial_capital, transaction_cost, slippage, max_position_size, max_gross_exposure],
        dtype=float,
    )
    if not np.isfinite(parameters).all():
        raise ValueError("benchmark parameters must be finite")
    if initial_capital <= 0 or transaction_cost < 0 or slippage < 0:
        raise ValueError("capital must be positive and costs must be non-negative")
    if transaction_cost + slippage >= 1:
        raise ValueError("transaction_cost plus slippage must be less than 1")
    if not 0 < max_position_size <= 1 or not 0 < max_gross_exposure <= 1:
        raise ValueError("allocation limits must be in (0, 1]")

    open_columns = [f"Open_{ticker}" for ticker in tickers]
    close_columns = [f"Close_{ticker}" for ticker in tickers]
    missing = [column for column in open_columns + close_columns if column not in data]
    if missing:
        raise ValueError(f"benchmark data is missing price columns: {missing}")

    opens = data[open_columns].to_numpy(dtype=float)
    closes = data[close_columns].to_numpy(dtype=float)
    if not np.isfinite(opens).all() or not np.isfinite(closes).all():
        raise ValueError("benchmark prices must be finite")
    if (opens <= 0).any() or (closes <= 0).any():
        raise ValueError("benchmark prices must be strictly positive")

    # Equal scores are mapped through the same long-only target-weight limits
    # as PPO. Any allocation clipped by the per-name cap remains in cash.
    target = np.full(len(tickers), 1.0 / len(tickers), dtype=float)
    target = np.minimum(target * max_gross_exposure, max_position_size)
    gross_target = float(target.sum())

    # Like PPO evaluation, the first observation is informational; entry occurs
    # at the following bar's Open. No exposure is assumed before that execution.
    equity_at_open = float(initial_capital)
    turnover = float(np.abs(target).sum())
    cost = equity_at_open * turnover * (transaction_cost + slippage)
    equity_after_cost = equity_at_open - cost
    if not np.isfinite(equity_after_cost) or equity_after_cost <= 0:
        raise ValueError("benchmark entry costs exhaust initial capital")

    shares = target * equity_after_cost / opens[1]
    cash = equity_after_cost * (1.0 - gross_target)

    equity_curve = [float(initial_capital)]
    turnover_series = [turnover]
    cost_series = [float(cost)]
    first_close_equity = float(cash + np.dot(shares, closes[1]))
    if not np.isfinite(first_close_equity) or first_close_equity <= 0:
        raise ValueError("benchmark produced invalid equity after initial entry")
    equity_curve.append(first_close_equity)

    # Buy-and-hold: shares and cash remain unchanged after the initial entry.
    for row in range(2, len(data)):
        value = float(cash + np.dot(shares, closes[row]))
        if not np.isfinite(value) or value <= 0:
            raise ValueError("benchmark produced non-positive or non-finite equity")
        equity_curve.append(value)
        turnover_series.append(0.0)
        cost_series.append(0.0)

    metrics = calculate_performance_metrics(
        equity_curve,
        periods_per_year=periods_per_year,
        risk_free_rate=risk_free_rate,
        turnover=turnover_series,
        transaction_costs=cost_series,
    )
    return {
        "observations": int(len(data)),
        "steps": int(len(equity_curve) - 1),
        "initial_value": float(initial_capital),
        "final_value": float(equity_curve[-1]),
        "target_weights": target.tolist(),
        "gross_target_exposure": gross_target,
        "entry_execution": "next_bar_open",
        "holding_policy": "buy_and_hold_no_rebalancing",
        **metrics,
    }


def evaluate_single_asset_buy_and_hold(
    data: pd.DataFrame, ticker: str, *, exposure: float,
    initial_capital: float, transaction_cost: float, slippage: float,
    periods_per_year: float = 252.0, risk_free_rate: float = 0.0,
) -> dict:
    """Evaluate one ETF at fixed exposure, without the PPO per-name cap."""
    if not isinstance(ticker, str) or not ticker.strip():
        raise ValueError("ticker must be a non-empty symbol")
    if isinstance(exposure, bool) or not np.isfinite(exposure) or not 0 < exposure <= 1:
        raise ValueError("exposure must be finite and in (0, 1]")
    result = evaluate_equal_weight_buy_and_hold(
        data, [ticker], initial_capital=initial_capital,
        transaction_cost=transaction_cost, slippage=slippage,
        max_position_size=1.0, max_gross_exposure=float(exposure),
        periods_per_year=periods_per_year, risk_free_rate=risk_free_rate,
    )
    return {
        **result, "benchmark_ticker": ticker, "target_exposure": float(exposure),
        "exposure_policy": "fixed_single_asset_exposure_no_ppo_per_position_cap",
    }
