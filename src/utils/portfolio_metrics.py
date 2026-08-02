import numpy as np
from .metrics import sharpe_ratio, sortino_ratio, max_drawdown, total_return

class PortfolioMetrics:
    def __init__(self, initial_capital=500000.0):
        self.initial_capital = float(initial_capital)
        self.equity_curve = [float(initial_capital)]

    def update(self, portfolio_value, positions=None):
        self.equity_curve.append(float(portfolio_value))

    def get_all_metrics(self):
        eq = np.asarray(self.equity_curve, dtype=float)
        returns = np.diff(eq) / eq[:-1] if eq.size > 1 else np.array([])
        return {
            "sharpe_ratio": sharpe_ratio(returns),
            "sortino_ratio": sortino_ratio(returns),
            "max_drawdown": max_drawdown(eq),
            "total_return": total_return(eq),
            "regime": "n/a",
        }
