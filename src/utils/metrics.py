import numpy as np

class Metrics:
    @staticmethod
    def sharpe(returns, risk_free_rate=0.0):
        r = np.asarray(returns, dtype=float)
        if r.size == 0:
            return 0.0
        excess = r - risk_free_rate
        std = excess.std(ddof=1) if excess.size > 1 else 0.0
        if std == 0:
            return 0.0
        return float(excess.mean() / std)

    @staticmethod
    def sortino(returns, risk_free_rate=0.0):
        r = np.asarray(returns, dtype=float)
        if r.size == 0:
            return 0.0
        downside = np.minimum(r - risk_free_rate, 0.0)
        denom = np.sqrt(np.mean(np.square(downside)))
        if denom == 0:
            return 0.0
        return float((r - risk_free_rate).mean() / denom)

    @staticmethod
    def max_drawdown(equity_curve):
        eq = np.asarray(equity_curve, dtype=float)
        if eq.size == 0:
            return 0.0
        peaks = np.maximum.accumulate(eq)
        dd = np.where(peaks > 0, (eq - peaks) / peaks, 0.0)
        return float(dd.min())

    @staticmethod
    def total_return(equity_curve):
        eq = np.asarray(equity_curve, dtype=float)
        if eq.size < 2 or eq[0] == 0:
            return 0.0
        return float(eq[-1] / eq[0] - 1.0)


class PortfolioMetrics:
    def __init__(self, initial_capital=500000.0):
        self.initial_capital = initial_capital
        self.equity_curve = []

    def update(self, portfolio_value, positions):
        self.equity_curve.append(float(portfolio_value))

    def get_all_metrics(self):
        eq = np.asarray(self.equity_curve, dtype=float)
        if eq.size > 1:
            returns = np.diff(eq) / eq[:-1]
        else:
            returns = np.array([])
        return {
            "sharpe": Metrics.sharpe(returns) if returns.size else 0.0,
            "sortino": Metrics.sortino(returns) if returns.size else 0.0,
            "max_drawdown": Metrics.max_drawdown(eq) if eq.size else 0.0,
            "total_return": Metrics.total_return(eq) if eq.size else 0.0,
        }
