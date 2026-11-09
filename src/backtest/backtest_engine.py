"""Execution-consistent multi-asset portfolio backtesting."""
from typing import Callable, Dict, List

import numpy as np
import pandas as pd

try:
    from src.backtest.performance_metrics import calculate_performance_metrics
except ModuleNotFoundError:
    from backtest.performance_metrics import calculate_performance_metrics


class ExecutionConsistentBacktest:
    """Use prior-close holdings for close-to-close returns."""

    def __init__(
        self,
        initial_capital: float = 100000.0,
        transaction_cost: float = 0.001,
        slippage: float = 0.0005,
        periods_per_year: float = 252.0,
    ):
        values = np.asarray(
            [initial_capital, transaction_cost, slippage, periods_per_year], dtype=float
        )
        if not np.isfinite(values).all():
            raise ValueError("Capital, costs, and periods_per_year must be finite")
        if initial_capital <= 0:
            raise ValueError("initial_capital must be positive")
        if transaction_cost < 0 or slippage < 0:
            raise ValueError("transaction_cost and slippage must be non-negative")
        if transaction_cost + slippage >= 1:
            raise ValueError("Combined transaction cost and slippage must be < 1")
        if periods_per_year <= 0:
            raise ValueError("periods_per_year must be positive")
        self.initial_capital = float(initial_capital)
        self.transaction_cost = float(transaction_cost)
        self.slippage = float(slippage)
        self.periods_per_year = float(periods_per_year)

    def run_backtest(self, price_data: pd.DataFrame, weights_generator: Callable,
                     tickers: List[str]) -> Dict:
        """Generate signals after close t and execute them at open t+1.

        Existing holdings earn the close-to-next-open gap. The new target is
        then filled at the next open, incurs turnover costs, and earns the
        open-to-close return. The final row is a valuation bar, not a signal
        whose order can be filled without a subsequent open.
        """
        if not isinstance(price_data, pd.DataFrame) or price_data.empty:
            raise ValueError("price_data must be a non-empty DataFrame")
        if len(price_data) < 2:
            raise ValueError("price_data must contain at least two bars for next-open execution")
        if not tickers or len(set(tickers)) != len(tickers):
            raise ValueError("tickers must be a non-empty list of unique symbols")
        close_cols = [f"Close_{ticker}" for ticker in tickers]
        open_cols = [f"Open_{ticker}" for ticker in tickers]
        missing = [column for column in [*close_cols, *open_cols] if column not in price_data.columns]
        if missing:
            raise ValueError(f"Missing open/close execution price columns: {missing}")
        closes = price_data.loc[:, close_cols].to_numpy(dtype=float)
        opens = price_data.loc[:, open_cols].to_numpy(dtype=float)
        if not np.isfinite(closes).all() or (closes <= 0).any():
            raise ValueError("Close prices must be finite and strictly positive")
        if not np.isfinite(opens).all() or (opens <= 0).any():
            raise ValueError("Open prices must be finite and strictly positive")
        if not price_data.index.is_unique:
            raise ValueError("price_data index must contain unique timestamps")
        if not price_data.index.is_monotonic_increasing:
            raise ValueError("price_data index must be sorted in chronological order")

        current_weights = np.zeros(len(tickers), dtype=float)
        portfolio_value = self.initial_capital
        portfolio_values = []
        for row_number in range(1, len(price_data)):
            signal_timestamp = price_data.index[row_number - 1]
            signal_row = price_data.iloc[row_number - 1]
            execution_open = opens[row_number]
            current_close = closes[row_number - 1]
            next_close = closes[row_number]

            # Old holdings, not the newly generated signal, bear the overnight gap.
            gap_returns = execution_open / current_close - 1.0
            gap_return = float(np.dot(current_weights, gap_returns))
            if not np.isfinite(gap_return) or gap_return <= -1.0:
                raise ValueError(
                    f"Overnight portfolio return at {price_data.index[row_number]!r} "
                    "would exhaust capital"
                )
            portfolio_value *= 1.0 + gap_return
            gap_denominator = 1.0 + gap_return
            current_weights = (
                current_weights * (1.0 + gap_returns) / gap_denominator
            )

            generated = weights_generator(signal_timestamp, signal_row)
            target = generated.weights if hasattr(generated, "weights") else generated
            target_weights = np.asarray(target, dtype=float)
            if target_weights.shape != (len(tickers),):
                raise ValueError(
                    f"Expected {len(tickers)} target weights, got shape {target_weights.shape}"
                )
            if not np.isfinite(target_weights).all():
                raise ValueError("Target weights must all be finite")
            turnover = float(np.abs(target_weights - current_weights).sum())
            execution_cost = turnover * (self.transaction_cost + self.slippage)
            if execution_cost >= 1.0:
                raise ValueError(f"Execution costs at {price_data.index[row_number]!r} would exhaust capital")
            portfolio_value *= 1.0 - execution_cost

            # The signal is filled at the next open; only then does it earn
            # the open-to-close return of that execution bar.
            intraday_returns = next_close / execution_open - 1.0
            intraday_return = float(np.dot(target_weights, intraday_returns))
            if not np.isfinite(intraday_return) or intraday_return <= -1.0:
                raise ValueError(
                    f"Intraday portfolio return at {price_data.index[row_number]!r} "
                    "would exhaust capital"
                )
            portfolio_value *= 1.0 + intraday_return
            intraday_denominator = 1.0 + intraday_return
            current_weights = (
                target_weights * (1.0 + intraday_returns) / intraday_denominator
            )
            portfolio_values.append(portfolio_value)

        portfolio_series = pd.Series(
            portfolio_values, index=price_data.index[1:], name="portfolio_value"
        )
        equity_curve = pd.Series(
            np.concatenate(([self.initial_capital], portfolio_series.to_numpy())),
            name="equity",
        )
        metrics = self._calculate_metrics(portfolio_series)
        final_value = float(equity_curve.iloc[-1])
        return {
            "portfolio_values": portfolio_series,
            "equity_curve": equity_curve,
            "initial_capital": self.initial_capital,
            "metrics": metrics,
            "final_value": final_value,
        }

    def _calculate_metrics(self, portfolio_series: pd.Series) -> Dict:
        """Calculate annualized metrics using the shared equity-curve implementation."""
        if portfolio_series.empty:
            raise ValueError("portfolio_series must not be empty")
        values = portfolio_series.astype(float).to_numpy()
        equity_curve = np.concatenate(([self.initial_capital], values))
        metrics = calculate_performance_metrics(
            equity_curve,
            periods_per_year=self.periods_per_year,
        )
        # Preserve the legacy backtest result names and zero-denominator behavior.
        return {
            "cumulative_return": metrics["total_return"],
            "annualized_return": metrics["cagr"],
            "annualized_volatility": metrics["annualized_volatility"],
            "sharpe_ratio": metrics["sharpe_ratio"] if metrics["sharpe_ratio"] is not None else 0.0,
            "sortino_ratio": metrics["sortino_ratio"] if metrics["sortino_ratio"] is not None else 0.0,
            "calmar_ratio": metrics["calmar_ratio"] if metrics["calmar_ratio"] is not None else 0.0,
            "max_drawdown": metrics["max_drawdown"],
        }
