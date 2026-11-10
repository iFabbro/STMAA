"""Long-only virtual portfolio with next-open rebalance accounting."""
from dataclasses import dataclass
import numpy as np


@dataclass
class PPOShadowPortfolio:
    tickers: tuple
    initial_capital: float = 100000.0
    transaction_cost: float = 0.001
    slippage: float = 0.0005
    cash: float | None = None
    shares: dict | None = None
    last_equity: float | None = None

    def __post_init__(self):
        self.tickers = tuple(self.tickers)
        if not self.tickers or len(set(self.tickers)) != len(self.tickers):
            raise ValueError("Tickers must be non-empty and unique")
        vals = np.asarray([self.initial_capital, self.transaction_cost, self.slippage], dtype=float)
        if not np.isfinite(vals).all() or self.initial_capital <= 0 or min(self.transaction_cost, self.slippage) < 0 or self.transaction_cost + self.slippage >= 1:
            raise ValueError("Invalid portfolio capital or costs")
        self.cash = float(self.initial_capital if self.cash is None else self.cash)
        self.shares = {t: 0.0 for t in self.tickers} if self.shares is None else dict(self.shares)
        self.last_equity = float(self.initial_capital if self.last_equity is None else self.last_equity)
        self._validate_state()

    def _validate_state(self):
        values = [self.cash, self.last_equity, *self.shares.values()]
        if set(self.shares) != set(self.tickers) or not np.isfinite(values).all() or self.cash < -1e-7 or self.last_equity <= 0 or any(v < -1e-10 for v in self.shares.values()):
            raise ValueError("Invalid persisted virtual portfolio state")

    def _prices(self, prices):
        if set(prices) != set(self.tickers):
            raise ValueError("Prices must cover exactly the configured tickers")
        vals = np.asarray([prices[t] for t in self.tickers], dtype=float)
        if not np.isfinite(vals).all() or (vals <= 0).any():
            raise ValueError("Prices must be finite and positive")

    def weights(self, prices):
        self._prices(prices)
        equity = self.cash + sum(self.shares[t] * prices[t] for t in self.tickers)
        if not np.isfinite(equity) or equity <= 0:
            raise ValueError("Invalid portfolio equity")
        return {t: self.shares[t] * prices[t] / equity for t in self.tickers}

    def rebalance_at_open(self, target_weights, open_prices, close_prices, session):
        self._prices(open_prices)
        self._prices(close_prices)
        if set(target_weights) != set(self.tickers):
            raise ValueError("Target weights must cover exactly the configured tickers")
        w = np.asarray([target_weights[t] for t in self.tickers], dtype=float)
        if not np.isfinite(w).all() or (w < 0).any() or (w > 0.2 + 1e-8).any() or w.sum() > 0.5 + 1e-8:
            raise ValueError("Targets violate long-only, 20% position, or 50% gross caps")
        # Mark existing holdings at today's open before changing the portfolio.
        equity_open = self.cash + sum(self.shares[t] * open_prices[t] for t in self.tickers)
        if not np.isfinite(equity_open) or equity_open <= 0:
            raise ValueError("Invalid equity at open")
        old = {t: self.shares[t] * open_prices[t] for t in self.tickers}
        new = {t: equity_open * float(target_weights[t]) for t in self.tickers}
        traded = sum(abs(new[t] - old[t]) for t in self.tickers)
        turnover, costs = traded / equity_open, traded * (self.transaction_cost + self.slippage)
        self.shares = {t: new[t] / open_prices[t] for t in self.tickers}
        self.cash = equity_open - sum(new.values()) - costs
        if self.cash < -1e-6:
            raise ValueError("Rebalance creates negative cash")
        self.cash = max(0.0, self.cash)
        equity_close = self.cash + sum(self.shares[t] * close_prices[t] for t in self.tickers)
        if not np.isfinite(equity_close) or equity_close <= 0:
            raise ValueError("Invalid equity at close")
        previous = self.last_equity
        self.last_equity = equity_close
        self._validate_state()
        return {"session": session, "equity_open": equity_open, "equity_close": equity_close,
                "step_return": equity_close / previous - 1, "turnover": turnover,
                "turnover_notional": traded, "transaction_cost": costs,
                "cash": self.cash, "shares": dict(self.shares),
                "weights_at_close": self.weights(close_prices)}

    def to_state(self):
        return {"tickers": list(self.tickers), "initial_capital": self.initial_capital,
                "transaction_cost": self.transaction_cost, "slippage": self.slippage,
                "cash": self.cash, "shares": dict(self.shares), "last_equity": self.last_equity}

    @classmethod
    def from_state(cls, state):
        return cls(tuple(state["tickers"]), state["initial_capital"], state["transaction_cost"],
                   state["slippage"], state["cash"], state["shares"], state["last_equity"])
