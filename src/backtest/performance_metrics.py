"""Deterministic performance metrics for portfolio equity curves."""

from typing import Iterable, Optional

import numpy as np


def calculate_performance_metrics(
    equity_curve: Iterable[float],
    *,
    periods_per_year: float = 252.0,
    risk_free_rate: float = 0.0,
    turnover: Optional[Iterable[float]] = None,
    transaction_costs: Optional[Iterable[float]] = None,
) -> dict:
    """Calculate metrics from an equity curve including initial capital.

    risk_free_rate is an annual effective rate. Returns are assumed equally
    spaced; callers must select periods_per_year for the input frequency.
    Undefined ratios are None, not zero. Turnover and transaction costs are
    reported as sums over evaluated steps.
    """
    values = np.asarray(list(equity_curve), dtype=float)
    if values.ndim != 1 or values.size < 2:
        raise ValueError("equity_curve must contain initial capital and at least one later value")
    if not np.isfinite(values).all() or (values <= 0).any():
        raise ValueError("equity_curve values must be finite and strictly positive")
    if not np.isfinite(periods_per_year) or periods_per_year <= 0:
        raise ValueError("periods_per_year must be finite and positive")
    if not np.isfinite(risk_free_rate) or risk_free_rate <= -1:
        raise ValueError("risk_free_rate must be finite and greater than -1")

    returns = values[1:] / values[:-1] - 1.0
    periods = returns.size
    total_return = float(values[-1] / values[0] - 1.0)
    cagr = float((values[-1] / values[0]) ** (periods_per_year / periods) - 1.0)
    volatility = float(np.std(returns, ddof=1) * np.sqrt(periods_per_year)) if periods > 1 else 0.0

    period_risk_free = float((1.0 + risk_free_rate) ** (1.0 / periods_per_year) - 1.0)
    excess_returns = returns - period_risk_free
    return_std = float(np.std(returns, ddof=1)) if periods > 1 else 0.0
    sharpe = (
        float(np.mean(excess_returns) / return_std * np.sqrt(periods_per_year))
        if return_std > 0 else None
    )
    downside_deviation = float(np.sqrt(np.mean(np.minimum(excess_returns, 0.0) ** 2)))
    sortino = (
        float(np.mean(excess_returns) / downside_deviation * np.sqrt(periods_per_year))
        if downside_deviation > 0 else None
    )

    drawdown = values / np.maximum.accumulate(values) - 1.0
    max_drawdown = float(np.min(drawdown))
    calmar = float(cagr / abs(max_drawdown)) if max_drawdown < 0 else None

    turnover_values = np.asarray(list(turnover) if turnover is not None else [], dtype=float)
    cost_values = np.asarray(
        list(transaction_costs) if transaction_costs is not None else [], dtype=float
    )
    for name, series in (("turnover", turnover_values), ("transaction_costs", cost_values)):
        if series.ndim != 1 or not np.isfinite(series).all() or (series < 0).any():
            raise ValueError(f"{name} must contain finite non-negative values")
        if series.size not in (0, periods):
            raise ValueError(f"{name} must be empty or contain one value per return period")

    return {
        "total_return": total_return,
        "cagr": cagr,
        "annualized_volatility": volatility,
        "sharpe_ratio": sharpe,
        "sortino_ratio": sortino,
        "max_drawdown": max_drawdown,
        "calmar_ratio": calmar,
        "total_turnover": float(turnover_values.sum()),
        "average_turnover": float(turnover_values.mean()) if turnover_values.size else 0.0,
        "total_transaction_costs": float(cost_values.sum()),
        "periods": int(periods),
        "periods_per_year": float(periods_per_year),
        "risk_free_rate": float(risk_free_rate),
    }
