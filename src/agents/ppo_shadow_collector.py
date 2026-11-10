"""Independent read-only PPO shadow collector; no broker execution imports."""
from pathlib import Path
import pandas as pd
from src.agents.ppo_shadow_ledger import PPOShadowLedger
from src.agents.ppo_shadow_portfolio import PPOShadowPortfolio


class PPOShadowCollector:
    def __init__(self, agent, feed, ledger_path, tickers, initial_capital=100000.0,
                 transaction_cost=0.001, slippage=0.0005):
        self.agent, self.feed = agent, feed
        self.ledger = PPOShadowLedger(ledger_path)
        self.tickers = tuple(tickers)
        self.defaults = dict(tickers=self.tickers, initial_capital=initial_capital,
                             transaction_cost=transaction_cost, slippage=slippage)

    def _restore(self):
        portfolio = PPOShadowPortfolio(**self.defaults)
        pending, last_session = None, None
        for record in self.ledger.read():
            event = record["payload"]
            if event["type"] == "portfolio_transition":
                portfolio = PPOShadowPortfolio.from_state(event["portfolio"])
            elif event["type"] == "decision":
                pending, last_session = event, event["session"]
        if tuple(portfolio.tickers) != self.tickers:
            raise ValueError("Ledger symbols differ from configured symbols")
        return portfolio, pending, last_session

    def collect_once(self, days=100):
        frame = self.feed.get_bars(list(self.tickers), days=days)
        if not isinstance(frame, pd.DataFrame) or frame.empty or not isinstance(frame.index, pd.DatetimeIndex) or frame.index.has_duplicates:
            raise ValueError("Feed must return non-empty daily data with a unique DatetimeIndex")
        session = pd.Timestamp(frame.index[-1]).date().isoformat()
        row = frame.iloc[-1]
        opens = {t: float(row[f"Open_{t}"]) for t in self.tickers}
        closes = {t: float(row[f"Close_{t}"]) for t in self.tickers}
        portfolio, pending, last_session = self._restore()
        if last_session == session:
            return {"status": "duplicate_session", "session": session}
        transition = None
        if pending is not None:
            if session <= pending["session"]:
                raise ValueError("Market data did not advance beyond pending signal")
            transition = portfolio.rebalance_at_open(pending["target_weights"], opens, closes, session)
            self.ledger.append({"type": "portfolio_transition", "session": session,
                                "signal_session": pending["session"], "portfolio": portfolio.to_state(),
                                "metrics": transition,
                                "market_data_fingerprint": frame.attrs.get("market_data_fingerprint")})
        current = portfolio.weights(closes)
        target = [float(x) for x in self.agent.predict_weights(frame, current_weights=list(current.values()))]
        if len(target) != len(self.tickers) or not all(pd.notna(x) for x in target):
            raise ValueError("PPO returned invalid target weights")
        target_map = dict(zip(self.tickers, target, strict=True))
        self.ledger.append({"type": "decision", "session": session, "target_weights": target_map,
                            "current_weights": current,
                            "market_data_fingerprint": frame.attrs.get("market_data_fingerprint"),
                            "market_data_contract": frame.attrs.get("market_data_contract")})
        return {"status": "collected", "session": session,
                "executed_pending_target": transition is not None,
                "target_weights": target_map, "portfolio": portfolio.to_state()}


def build_default_collector(agent, ledger_path, tickers, **portfolio_options):
    """Explicitly construct Alpaca historical-data client only when called."""
    from src.data.alpaca_feed import AlpacaFeed
    return PPOShadowCollector(agent, AlpacaFeed(), ledger_path, tickers, **portfolio_options)
