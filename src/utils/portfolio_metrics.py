import numpy as np
from .metrics import Metrics


class PortfolioMetrics:
    def __init__(self, initial_capital=500000.0):
        capital = float(initial_capital)
        if not np.isfinite(capital) or capital <= 0:
            raise ValueError("initial_capital must be finite and positive")
        self.initial_capital = capital
        self.equity_curve = [capital]

    def update(self, portfolio_value, positions=None):
        value = float(portfolio_value)
        if not np.isfinite(value) or value <= 0:
            raise ValueError("portfolio_value must be finite and positive")
        self.equity_curve.append(value)

    def get_all_metrics(self):
        eq = np.asarray(self.equity_curve, dtype=float)
        returns = np.diff(eq) / eq[:-1] if eq.size > 1 else np.array([])
        return {
            "sharpe_ratio": Metrics.sharpe(returns),
            "sortino_ratio": Metrics.sortino(returns),
            "max_drawdown": Metrics.max_drawdown(eq),
            "total_return": Metrics.total_return(eq),
            "regime": "n/a",
        }
