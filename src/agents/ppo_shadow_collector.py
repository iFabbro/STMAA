"""Independent read-only PPO shadow collector; no broker execution imports."""
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from src.data.market_data_contract import validate_market_data_contract
from src.agents.ppo_shadow_ledger import PPOShadowLedger
from src.agents.ppo_shadow_portfolio import PPOShadowPortfolio


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _agent_provenance(agent):
    """Verify the loaded PPO artifact against its immutable manifest."""
    if not getattr(agent, "inference_available", False):
        raise ValueError("PPO agent is not validated and inference-ready")
    model_path = Path(agent.model_path)
    manifest_path = Path(str(model_path) + ".metadata.json")
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("PPO artifact manifest is missing or unsafe")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifact_name = manifest.get("artifact_file")
    if not isinstance(artifact_name, str) or Path(artifact_name).name != artifact_name:
        raise ValueError("PPO manifest has an invalid artifact filename")
    artifact_path = model_path.parent / artifact_name
    if artifact_path.is_symlink() or not artifact_path.is_file():
        raise ValueError("PPO model artifact is missing or unsafe")
    digest = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    if digest != manifest.get("model_sha256"):
        raise ValueError("PPO model artifact SHA-256 does not match its manifest")
    expected = {
        "tickers": list(agent.tickers),
        "transaction_cost": float(agent.transaction_cost),
        "slippage": float(agent.slippage),
        "max_position_size": float(agent.max_position_size),
        "max_gross_exposure": float(agent.max_gross_exposure),
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise ValueError(f"PPO manifest does not match active agent setting: {key}")
    training = manifest.get("training")
    validate_market_data_contract(manifest.get("market_data_contract"))
    if (manifest.get("contract_version") != 7
            or manifest.get("action_contract") != "long_only_capped_target_weights_zero_floor"
            or manifest.get("execution_contract") != "close_signal_next_bar_open_fill"
            or manifest.get("observation_contract") != "dimensionless_ohlc_features_v1"
            or not isinstance(training, dict)
            or training.get("initial_capital") != float(agent.initial_capital)):
        raise ValueError("PPO manifest model/training contract differs from active agent")
    return {
        "model_sha256": digest,
        "artifact_file": artifact_name,
        "manifest_sha256": hashlib.sha256(_canonical(manifest)).hexdigest(),
        "contract_version": manifest.get("contract_version"),
        "training": training,
        "strategy_config": {
            **expected,
            "initial_capital": float(agent.initial_capital),
        },
    }


class PPOShadowCollector:
    def __init__(self, agent, feed, ledger_path, tickers, initial_capital=100000.0,
                 transaction_cost=0.001, slippage=0.0005, benchmark_ticker="SPY",
                 provenance=None, max_position_size=None, max_gross_exposure=None):
        self.agent, self.feed = agent, feed
        self.ledger = PPOShadowLedger(ledger_path)
        self.tickers = tuple(tickers)
        self.benchmark_ticker = benchmark_ticker
        self.provenance = provenance if provenance is not None else _agent_provenance(agent)
        if not isinstance(self.provenance, dict):
            raise ValueError("Shadow provenance must be a dictionary")
        _canonical(self.provenance)
        frozen_config = self.provenance.get("strategy_config") or {}
        max_position_size = frozen_config.get("max_position_size", 0.2) if max_position_size is None else max_position_size
        max_gross_exposure = frozen_config.get("max_gross_exposure", 0.5) if max_gross_exposure is None else max_gross_exposure
        self.defaults = dict(tickers=self.tickers, initial_capital=initial_capital,
                             transaction_cost=transaction_cost, slippage=slippage,
                             max_position_size=max_position_size, max_gross_exposure=max_gross_exposure)
        if frozen_config:
            for key in ("tickers", "initial_capital", "transaction_cost", "slippage",
                        "max_position_size", "max_gross_exposure"):
                configured = list(self.tickers) if key == "tickers" else self.defaults[key]
                if frozen_config.get(key) != configured:
                    raise ValueError(f"Collector configuration differs from model provenance: {key}")

    def _restore(self):
        records = self.ledger.read()
        config = {**self.defaults, "tickers": list(self.tickers),
                  "benchmark_ticker": self.benchmark_ticker}
        if not records:
            self.ledger.append({"type": "run_metadata", "schema": "ppo_shadow_v1",
                                "config": config, "provenance": self.provenance})
            records = self.ledger.read()
        metadata = records[0]["payload"]
        if (metadata.get("type") != "run_metadata" or metadata.get("schema") != "ppo_shadow_v1"
                or metadata.get("config") != config or metadata.get("provenance") != self.provenance):
            raise ValueError("Shadow ledger run metadata differs from active model/configuration")
        portfolio = PPOShadowPortfolio(**self.defaults)
        pending, last_decision_session, last_event_session, last_event_type, benchmark = None, None, None, None, None
        for record in records[1:]:
            event = record["payload"]
            last_event_session = event.get("session", last_event_session)
            last_event_type = event["type"]
            if event["type"] == "portfolio_transition":
                portfolio = PPOShadowPortfolio.from_state(event["portfolio"])
                pending = None
                benchmark = event["benchmark"]
            elif event["type"] == "decision":
                pending, last_decision_session = event, event["session"]
            else:
                raise ValueError(f"Unsupported shadow ledger event: {event.get('type')!r}")
        if tuple(portfolio.tickers) != self.tickers:
            raise ValueError("Ledger symbols differ from configured symbols")
        return portfolio, pending, last_decision_session, last_event_session, last_event_type, benchmark

    @staticmethod
    def _latest(frame, expected_session, label):
        if (not isinstance(frame, pd.DataFrame) or frame.empty
                or not isinstance(frame.index, pd.DatetimeIndex)
                or frame.index.has_duplicates or not frame.index.is_monotonic_increasing):
            raise ValueError(f"{label} feed must return chronological daily data with a unique DatetimeIndex")
        dates = [pd.Timestamp(value).date().isoformat() for value in frame.index]
        if len(set(dates)) != len(dates) or dates[-1] != expected_session:
            raise ValueError(f"{label} data is not aligned to the model market-data session")
        return dates

    def collect_once(self, days=100):
        frame = self.feed.get_bars(list(self.tickers), days=days)
        if not isinstance(frame, pd.DataFrame) or frame.empty or not isinstance(frame.index, pd.DatetimeIndex):
            raise ValueError("Model feed returned no valid daily market data")
        session = pd.Timestamp(frame.index[-1]).date().isoformat()
        session_dates = self._latest(frame, session, "Model")
        benchmark_frame = self.feed.get_bars([self.benchmark_ticker], days=days)
        self._latest(benchmark_frame, session, "Benchmark")
        model_row, benchmark_row = frame.iloc[-1], benchmark_frame.iloc[-1]
        opens = {t: float(model_row[f"Open_{t}"]) for t in self.tickers}
        closes = {t: float(model_row[f"Close_{t}"]) for t in self.tickers}
        benchmark_open = float(benchmark_row[f"Open_{self.benchmark_ticker}"])
        benchmark_close = float(benchmark_row[f"Close_{self.benchmark_ticker}"])
        if not np.isfinite([benchmark_open, benchmark_close]).all() or min(benchmark_open, benchmark_close) <= 0:
            raise ValueError("Benchmark prices must be finite and positive")

        portfolio, pending, last_decision_session, last_event_session, last_event_type, benchmark = self._restore()
        if last_decision_session == session:
            return {"status": "duplicate_session", "session": session}
        if last_event_session is not None and session < last_event_session:
            raise ValueError("Market data moved backwards relative to the ledger")
        if last_event_session is not None and session > last_event_session:
            if last_event_type != "decision":
                raise ValueError("Previous session has no completed decision; refusing to skip shadow inference")
            if last_event_session not in session_dates:
                raise ValueError("Last ledger session is outside the supplied market-data window")
            previous_index = session_dates.index(last_event_session)
            if previous_index + 1 >= len(session_dates) or session_dates[previous_index + 1] != session:
                raise ValueError("Missed one or more NYSE sessions; refusing a delayed shadow fill")

        transition, benchmark_metrics = None, None
        if pending is not None:
            if session <= pending["session"]:
                raise ValueError("Market data did not advance beyond pending signal")
            transition = portfolio.rebalance_at_open(pending["target_weights"], opens, closes, session)
            if benchmark is None:
                benchmark = {
                    "ticker": self.benchmark_ticker,
                    "shares": float(self.defaults["initial_capital"]) / benchmark_open,
                    "last_equity": float(self.defaults["initial_capital"]),
                    "start_session": session,
                }
            benchmark_equity_open = benchmark["shares"] * benchmark_open
            benchmark_equity_close = benchmark["shares"] * benchmark_close
            benchmark_metrics = {
                "session": session, "ticker": self.benchmark_ticker,
                "equity_open": benchmark_equity_open,
                "equity_close": benchmark_equity_close,
                "step_return": benchmark_equity_close / benchmark["last_equity"] - 1.0,
            }
            benchmark = {**benchmark, "last_equity": benchmark_equity_close}
            self.ledger.append({"type": "portfolio_transition", "session": session,
                                "signal_session": pending["session"], "portfolio": portfolio.to_state(),
                                "metrics": transition, "benchmark": benchmark,
                                "benchmark_metrics": benchmark_metrics,
                                "market_data_fingerprint": frame.attrs.get("market_data_fingerprint"),
                                "benchmark_data_fingerprint": benchmark_frame.attrs.get("market_data_fingerprint")})

        current = portfolio.weights(closes)
        target = np.asarray(self.agent.predict_weights(frame, current_weights=list(current.values())), dtype=float)
        if target.shape != (len(self.tickers),) or not np.isfinite(target).all():
            raise ValueError("PPO returned invalid target weights")
        if ((target < 0).any()
                or (target > self.defaults["max_position_size"] + 1e-8).any()
                or target.sum() > self.defaults["max_gross_exposure"] + 1e-8):
            raise ValueError("PPO target violates the frozen long-only allocation caps")
        target_map = dict(zip(self.tickers, target.tolist(), strict=True))
        feature_snapshot = {str(key): float(value) for key, value in model_row.items()}
        self.ledger.append({"type": "decision", "session": session, "target_weights": target_map,
                            "current_weights": current, "feature_snapshot": feature_snapshot,
                            "benchmark_snapshot": {"ticker": self.benchmark_ticker,
                                                   "open": benchmark_open, "close": benchmark_close},
                            "market_data_fingerprint": frame.attrs.get("market_data_fingerprint"),
                            "market_data_contract": frame.attrs.get("market_data_contract"),
                            "benchmark_data_fingerprint": benchmark_frame.attrs.get("market_data_fingerprint")})
        return {"status": "collected", "session": session,
                "executed_pending_target": transition is not None,
                "target_weights": target_map, "portfolio": portfolio.to_state(),
                "benchmark": benchmark, "benchmark_metrics": benchmark_metrics}


def build_default_collector(agent, ledger_path, tickers, **portfolio_options):
    """Explicitly construct Alpaca historical-data client only when called."""
    from src.data.alpaca_feed import AlpacaFeed
    return PPOShadowCollector(agent, AlpacaFeed(), ledger_path, tickers, **portfolio_options)
