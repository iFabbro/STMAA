import json
import numpy as np

class Metrics:
    @staticmethod
    def sharpe(returns, risk_free_rate=0.0, periods_per_year=252, annualize=True):
        r = np.asarray(returns, dtype=float)
        if r.size < 2:
            return 0.0
        excess = r - risk_free_rate
        std = excess.std(ddof=1)
        if std == 0:
            return 0.0
        ratio = excess.mean() / std
        return float(ratio * np.sqrt(periods_per_year)) if annualize else float(ratio)

    @staticmethod
    def sortino(returns, risk_free_rate=0.0, periods_per_year=252, annualize=True):
        r = np.asarray(returns, dtype=float)
        if r.size == 0:
            return 0.0
        downside = np.minimum(r - risk_free_rate, 0.0)
        denom = np.sqrt(np.mean(np.square(downside)))
        if denom == 0:
            return 0.0
        ratio = (r - risk_free_rate).mean() / denom
        return float(ratio * np.sqrt(periods_per_year)) if annualize else float(ratio)

    @staticmethod
    def max_drawdown(equity_curve):
        eq = np.asarray(equity_curve, dtype=float)
        if eq.size == 0:
            return 0.0
        peaks = np.maximum.accumulate(eq)
        dd = np.where(peaks > 0, (eq - peaks) / peaks, 0.0)
        return float(dd.min())

    @staticmethod
    def drawdown_duration(equity_curve):
        eq = np.asarray(equity_curve, dtype=float)
        if eq.size < 2:
            return 0
        peaks = np.maximum.accumulate(eq)
        underwater = eq < peaks
        best = cur = 0
        for flag in underwater:
            cur = cur + 1 if flag else 0
            best = max(best, cur)
        return int(best)

    @staticmethod
    def total_return(equity_curve):
        eq = np.asarray(equity_curve, dtype=float)
        if eq.size < 2 or eq[0] == 0:
            return 0.0
        return float(eq[-1] / eq[0] - 1.0)

    @staticmethod
    def write_metrics_json(metrics, path):
        from pathlib import Path
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(metrics, indent=2, default=str))
        return p

    @staticmethod
    def write_metrics_csv(metrics, path):
        import csv
        from pathlib import Path
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        is_new = not p.exists()
        with p.open('a', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=list(metrics.keys()))
            if is_new:
                writer.writeheader()
            writer.writerow(metrics)
        return p

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
            'sharpe_ratio': Metrics.sharpe(returns),
            'sortino_ratio': Metrics.sortino(returns),
            'max_drawdown': Metrics.max_drawdown(eq),
            'drawdown_duration': Metrics.drawdown_duration(eq),
            'total_return': Metrics.total_return(eq),
            'regime': 'n/a',
        }

    def write_metrics_json(self, metrics, path):
        return Metrics.write_metrics_json(metrics, path)

    def write_metrics_csv(self, metrics, path):
        return Metrics.write_metrics_csv(metrics, path)
