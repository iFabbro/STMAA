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
    include_timeseries: bool = False,
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
    exposure_rows = []
    first_close_equity = float(cash + np.dot(shares, closes[1]))
    if not np.isfinite(first_close_equity) or first_close_equity <= 0:
        raise ValueError("benchmark produced invalid equity after initial entry")
    equity_curve.append(first_close_equity)
    first_weights = shares * closes[1] / first_close_equity
    if include_timeseries:
        exposure_rows.append({
            "timestamp": str(data.index[1]),
            "equity": first_close_equity,
            "turnover": turnover,
            "transaction_cost": float(cost),
            "close_weights": first_weights.astype(float).tolist(),
            "gross_exposure_close": float(np.abs(first_weights).sum()),
        })

    # Buy-and-hold: shares and cash remain unchanged after the initial entry.
    for row in range(2, len(data)):
        value = float(cash + np.dot(shares, closes[row]))
        if not np.isfinite(value) or value <= 0:
            raise ValueError("benchmark produced non-positive or non-finite equity")
        equity_curve.append(value)
        turnover_series.append(0.0)
        cost_series.append(0.0)
        if include_timeseries:
            weights = shares * closes[row] / value
            exposure_rows.append({
                "timestamp": str(data.index[row]),
                "equity": value,
                "turnover": 0.0,
                "transaction_cost": 0.0,
                "close_weights": weights.astype(float).tolist(),
                "gross_exposure_close": float(np.abs(weights).sum()),
            })

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
        **({
            "tickers": list(tickers),
            "timeseries": exposure_rows,
            "timeseries_contract": "benchmark_daily_exposure_v1",
        } if include_timeseries else {}),
    }



def evaluate_exposure_matched_equal_weight(
    data: pd.DataFrame,
    tickers: list[str],
    ppo_timeseries: list[dict],
    *,
    initial_capital: float,
    transaction_cost: float,
    slippage: float,
    max_position_size: float,
    periods_per_year: float = 252.0,
    risk_free_rate: float = 0.0,
    include_timeseries: bool = False,
) -> dict:
    """Rebalance equal weights at each next Open to PPO's target gross exposure.

    The PPO trace at execution timestamp t contains the target generated from
    the preceding completed bar. The comparator uses only that target gross
    exposure, not PPO's asset weights, and distributes it equally across the
    same ticker universe. Both portfolios therefore act at the same next Open.
    """
    if not isinstance(data, pd.DataFrame) or len(data) < 2:
        raise ValueError("data must contain at least two observations")
    if not isinstance(data.index, pd.DatetimeIndex):
        raise ValueError("data must have a DatetimeIndex")
    if data.index.hasnans or not data.index.is_unique or not data.index.is_monotonic_increasing:
        raise ValueError("data timestamps must be valid, unique, and chronological")
    if not tickers or len(set(tickers)) != len(tickers):
        raise ValueError("tickers must be a non-empty list of unique symbols")
    if not isinstance(ppo_timeseries, list) or len(ppo_timeseries) != len(data) - 1:
        raise ValueError("PPO exposure trace must contain one row per next-Open execution")
    if not 0 < max_position_size <= 1:
        raise ValueError("max_position_size must be in (0, 1]")
    rate = float(transaction_cost) + float(slippage)
    if not np.isfinite([initial_capital, rate, max_position_size]).all():
        raise ValueError("benchmark parameters must be finite")
    if initial_capital <= 0 or rate < 0 or rate >= 1:
        raise ValueError("capital must be positive and combined costs must be in [0, 1)")
    open_columns = [f"Open_{ticker}" for ticker in tickers]
    close_columns = [f"Close_{ticker}" for ticker in tickers]
    missing = [column for column in open_columns + close_columns if column not in data]
    if missing:
        raise ValueError(f"benchmark data is missing price columns: {missing}")
    opens = data[open_columns].to_numpy(dtype=float)
    closes = data[close_columns].to_numpy(dtype=float)
    if not np.isfinite(opens).all() or not np.isfinite(closes).all() or (opens <= 0).any() or (closes <= 0).any():
        raise ValueError("benchmark prices must be finite and strictly positive")

    shares = np.zeros(len(tickers), dtype=float)
    cash = float(initial_capital)
    equity_curve = [float(initial_capital)]
    turnovers, costs, rows = [], [], []
    for offset, trace in enumerate(ppo_timeseries, start=1):
        if not isinstance(trace, dict) or str(trace.get("timestamp")) != str(data.index[offset]):
            raise ValueError("PPO exposure trace timestamps do not align with benchmark execution bars")
        target_weights = np.asarray(trace.get("target_weights"), dtype=float)
        if target_weights.shape != (len(tickers),) or not np.isfinite(target_weights).all() or (target_weights < 0).any():
            raise ValueError("PPO trace contains invalid target weights")
        exposure = float(target_weights.sum())
        if exposure > 1.0 + 1e-8:
            raise ValueError("PPO target gross exposure exceeds 100%")
        target = np.full(len(tickers), exposure / len(tickers), dtype=float)
        if (target > max_position_size + 1e-8).any():
            raise ValueError("exposure-matched equal weights exceed the per-position cap")

        equity_open = float(cash + np.dot(shares, opens[offset]))
        if not np.isfinite(equity_open) or equity_open <= 0:
            raise ValueError("matched-exposure benchmark has invalid open equity")
        current_weights = shares * opens[offset] / equity_open
        turnover = float(np.abs(target - current_weights).sum())
        cost = equity_open * turnover * rate
        equity_after_cost = equity_open - cost
        if not np.isfinite(equity_after_cost) or equity_after_cost <= 0:
            raise ValueError("matched-exposure transaction costs exhaust capital")
        shares = target * equity_after_cost / opens[offset]
        cash = equity_after_cost * (1.0 - exposure)
        equity_close = float(cash + np.dot(shares, closes[offset]))
        if not np.isfinite(equity_close) or equity_close <= 0:
            raise ValueError("matched-exposure benchmark produced invalid close equity")
        equity_curve.append(equity_close)
        turnovers.append(turnover)
        costs.append(float(cost))
        if include_timeseries:
            close_weights = shares * closes[offset] / equity_close
            rows.append({
                "timestamp": str(data.index[offset]),
                "equity": equity_close,
                "target_gross_exposure": exposure,
                "turnover": turnover,
                "transaction_cost": float(cost),
                "close_weights": close_weights.astype(float).tolist(),
                "gross_exposure_close": float(np.abs(close_weights).sum()),
            })

    metrics = calculate_performance_metrics(
        equity_curve, periods_per_year=periods_per_year,
        risk_free_rate=risk_free_rate, turnover=turnovers,
        transaction_costs=costs,
    )
    return {
        "observations": int(len(data)),
        "steps": int(len(equity_curve) - 1),
        "initial_value": float(initial_capital),
        "final_value": float(equity_curve[-1]),
        "entry_execution": "next_bar_open",
        "holding_policy": "daily_equal_weight_rebalance_at_ppo_target_gross_exposure",
        "exposure_policy": "match_ppo_target_gross_exposure_each_execution_bar",
        "exposure_source": "ppo_timeseries.target_weights",
        **metrics,
        **({
            "tickers": list(tickers),
            "timeseries": rows,
            "timeseries_contract": "ppo_exposure_matched_equal_weight_daily_v1",
        } if include_timeseries else {}),
    }


def evaluate_single_asset_buy_and_hold(
    data: pd.DataFrame, ticker: str, *, exposure: float,
    initial_capital: float, transaction_cost: float, slippage: float,
    periods_per_year: float = 252.0, risk_free_rate: float = 0.0,
    include_timeseries: bool = False,
) -> dict:
    """Evaluate one ETF at fixed exposure, without the PPO per-name cap."""
    if not isinstance(ticker, str) or not ticker.strip():
        raise ValueError("ticker must be a non-empty symbol")
    if (
        isinstance(exposure, bool)
        or not isinstance(exposure, (int, float, np.integer, np.floating))
        or not np.isfinite(exposure)
        or not 0 < exposure <= 1
    ):
        raise ValueError("exposure must be finite and in (0, 1]")
    result = evaluate_equal_weight_buy_and_hold(
        data, [ticker], initial_capital=initial_capital,
        transaction_cost=transaction_cost, slippage=slippage,
        max_position_size=1.0, max_gross_exposure=float(exposure),
        periods_per_year=periods_per_year, risk_free_rate=risk_free_rate,
        include_timeseries=include_timeseries,
    )
    return {
        **result, "benchmark_ticker": ticker, "target_exposure": float(exposure),
        "exposure_policy": "fixed_single_asset_exposure_no_ppo_per_position_cap",
    }
