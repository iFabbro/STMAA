"""Execution-consistent multi-asset portfolio backtesting."""
from typing import Callable, Dict, List
import numpy as np
import pandas as pd


class ExecutionConsistentBacktest:
    """Use prior-close holdings for close-to-close returns."""

    def __init__(self, initial_capital: float = 100000.0,
                 transaction_cost: float = 0.001, slippage: float = 0.0005):
        values = np.asarray([initial_capital, transaction_cost, slippage], dtype=float)
        if not np.isfinite(values).all():
            raise ValueError("Capital and cost parameters must be finite")
        if initial_capital <= 0:
            raise ValueError("initial_capital must be positive")
        if transaction_cost < 0 or slippage < 0:
            raise ValueError("transaction_cost and slippage must be non-negative")
        if transaction_cost + slippage >= 1:
            raise ValueError("Combined transaction cost and slippage must be < 1")
        self.initial_capital = float(initial_capital)
        self.transaction_cost = float(transaction_cost)
        self.slippage = float(slippage)

    def run_backtest(self, price_data: pd.DataFrame, weights_generator: Callable,
                     tickers: List[str]) -> Dict:
        """Simulate multi-asset returns and charge costs on turnover."""
        if not isinstance(price_data, pd.DataFrame) or price_data.empty:
            raise ValueError("price_data must be a non-empty DataFrame")
        if not tickers or len(set(tickers)) != len(tickers):
            raise ValueError("tickers must be a non-empty list of unique symbols")
        close_cols = [f"Close_{ticker}" for ticker in tickers]
        missing = [column for column in close_cols if column not in price_data.columns]
        if missing:
            raise ValueError(f"Missing close price columns: {missing}")
        prices = price_data.loc[:, close_cols].to_numpy(dtype=float)
        if not np.isfinite(prices).all() or (prices <= 0).any():
            raise ValueError("Close prices must be finite and strictly positive")
        if not price_data.index.is_unique:
            raise ValueError("price_data index must contain unique timestamps")

        current_weights = np.zeros(len(tickers), dtype=float)
        portfolio_value = self.initial_capital
        portfolio_values = []
        for row_number, (timestamp, row) in enumerate(price_data.iterrows()):
            current_prices = row[close_cols].to_numpy(dtype=float)
            # Existing holdings earn returns over the interval ending now.
            if row_number > 0:
                asset_returns = current_prices / prices[row_number - 1] - 1.0
                portfolio_return = float(np.dot(current_weights, asset_returns))
                if not np.isfinite(portfolio_return) or portfolio_return <= -1.0:
                    raise ValueError(f"Portfolio return at {timestamp!r} would exhaust capital")
                portfolio_value *= 1.0 + portfolio_return
                # Holdings drift as assets earn different returns. Use these
                # post-return weights as the starting point for turnover at the
                # current rebalance, rather than the previous target weights.
                denominator = 1.0 + portfolio_return
                current_weights = (
                    current_weights * (1.0 + asset_returns) / denominator
                )

            generated = weights_generator(timestamp, row)
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
                raise ValueError(f"Execution costs at {timestamp!r} would exhaust capital")
            portfolio_value *= 1.0 - execution_cost
            current_weights = target_weights.copy()
            portfolio_values.append(portfolio_value)

        portfolio_series = pd.Series(
            portfolio_values, index=price_data.index, name="portfolio_value"
        )
        metrics = self._calculate_metrics(portfolio_series)
        return {"portfolio_values": portfolio_series, "metrics": metrics,
                "final_value": float(portfolio_value)}

    def _calculate_metrics(self, portfolio_series: pd.Series) -> Dict:
        """Calculate metrics relative to initial capital."""
        if portfolio_series.empty:
            raise ValueError("portfolio_series must not be empty")
        values = portfolio_series.astype(float)
        if not np.isfinite(values.to_numpy()).all() or (values <= 0).any():
            raise ValueError("Portfolio values must be finite and positive")
        period_returns = values.pct_change().dropna()
        cumulative_return = float(values.iloc[-1] / self.initial_capital - 1.0)
        periods = len(values) - 1
        annualized_return = (
            float((values.iloc[-1] / self.initial_capital) ** (252 / periods) - 1.0)
            if periods > 0 else 0.0
        )
        annualized_vol = (
            float(period_returns.std(ddof=1) * np.sqrt(252))
            if len(period_returns) > 1 else 0.0
        )
        sharpe_ratio = annualized_return / annualized_vol if annualized_vol > 0 else 0.0
        # Include initial capital as the baseline so entry costs on the first
        # rebalance are reflected in maximum drawdown as well as total return.
        drawdown_values = np.concatenate(([self.initial_capital], values.to_numpy()))
        drawdown_peaks = np.maximum.accumulate(drawdown_values)
        drawdown = drawdown_values / drawdown_peaks - 1.0
        max_drawdown = float(drawdown.min())
        downside_returns = period_returns[period_returns < 0]
        downside_vol = (
            float(downside_returns.std(ddof=1) * np.sqrt(252))
            if len(downside_returns) > 1 else 0.0
        )
        sortino_ratio = annualized_return / downside_vol if downside_vol > 0 else 0.0
        calmar_ratio = annualized_return / abs(max_drawdown) if max_drawdown < 0 else 0.0
        return {
            "cumulative_return": cumulative_return,
            "annualized_return": annualized_return,
            "annualized_volatility": annualized_vol,
            "sharpe_ratio": sharpe_ratio,
            "sortino_ratio": sortino_ratio,
            "calmar_ratio": calmar_ratio,
            "max_drawdown": max_drawdown,
        }
