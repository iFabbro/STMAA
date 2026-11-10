"""Independent read-only PPO shadow collector; no broker execution imports."""
import numpy as np
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
        pending, last_decision_session, last_event_session = None, None, None
        for record in self.ledger.read():
            event = record["payload"]
            last_event_session = event.get("session", last_event_session)
            if event["type"] == "portfolio_transition":
                portfolio = PPOShadowPortfolio.from_state(event["portfolio"])
                pending = None
            elif event["type"] == "decision":
                pending, last_decision_session = event, event["session"]
            else:
                raise ValueError(f"Unsupported shadow ledger event: {event.get('type')!r}")
        if tuple(portfolio.tickers) != self.tickers:
            raise ValueError("Ledger symbols differ from configured symbols")
        return portfolio, pending, last_decision_session, last_event_session

    def collect_once(self, days=100):
        frame = self.feed.get_bars(list(self.tickers), days=days)
        if (not isinstance(frame, pd.DataFrame) or frame.empty
                or not isinstance(frame.index, pd.DatetimeIndex)
                or frame.index.has_duplicates or not frame.index.is_monotonic_increasing):
            raise ValueError("Feed must return chronological daily data with a unique DatetimeIndex")
        session_dates = [pd.Timestamp(value).date().isoformat() for value in frame.index]
        if len(set(session_dates)) != len(session_dates):
            raise ValueError("Feed contains multiple bars for one session")
        session = session_dates[-1]
        row = frame.iloc[-1]
        opens = {t: float(row[f"Open_{t}"]) for t in self.tickers}
        closes = {t: float(row[f"Close_{t}"]) for t in self.tickers}
        portfolio, pending, last_decision_session, last_event_session = self._restore()
        if last_decision_session == session:
            return {"status": "duplicate_session", "session": session}
        if last_event_session is not None and session < last_event_session:
            raise ValueError("Market data moved backwards relative to the ledger")
        if last_event_session is not None and session > last_event_session:
            if last_event_session not in session_dates:
                raise ValueError("Last ledger session is outside the supplied market-data window")
            previous_index = session_dates.index(last_event_session)
            if previous_index + 1 >= len(session_dates) or session_dates[previous_index + 1] != session:
                raise ValueError("Missed one or more NYSE sessions; refusing a delayed shadow fill")

        transition = None
        if pending is not None:
            if session <= pending["session"]:
                raise ValueError("Market data did not advance beyond pending signal")
            transition = portfolio.rebalance_at_open(pending["target_weights"], opens, closes, session)
            # Persist the transition before inference. On restart, this event clears
            # the old pending target, so a failed inference cannot execute it twice.
            self.ledger.append({"type": "portfolio_transition", "session": session,
                                "signal_session": pending["session"], "portfolio": portfolio.to_state(),
                                "metrics": transition,
                                "market_data_fingerprint": frame.attrs.get("market_data_fingerprint")})
        current = portfolio.weights(closes)
        target = np.asarray(self.agent.predict_weights(frame, current_weights=list(current.values())), dtype=float)
        if target.shape != (len(self.tickers),) or not np.isfinite(target).all():
            raise ValueError("PPO returned invalid target weights")
        if (target < 0).any() or (target > 0.2 + 1e-8).any() or target.sum() > 0.5 + 1e-8:
            raise ValueError("PPO target violates the configured long-only allocation caps")
        target_map = dict(zip(self.tickers, target.tolist(), strict=True))
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
