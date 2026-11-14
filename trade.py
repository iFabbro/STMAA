#!/usr/bin/env python3
"""Paper Trading Script - Sistema Multi-Agente STMAA

Collega il sistema trading con Alpaca Paper Trading per operatività reale.
Fase 1: Dati Alpaca reali
Fase 2: Agente PPO vero
"""
import argparse
import os
import sys
import time
import signal
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Dict

sys.path.append(str(Path(__file__).parent / "src"))

from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(__file__).absolute().parent / ".env", override=False)

from src.utils.sqlite_logger import start_run, end_run, log_event, write_snapshot
from src.utils.monitoring_state import (
    SnapshotDurabilityError,
    load_equity_curve,
    write_hub_state,
)
RUN_ID = None

import numpy as np
from contextlib import nullcontext
import pandas as pd
import yfinance as yf
import pandas_market_calendars as mcal

from src.execution.broker_executor import BrokerExecutor
from src.execution.opg_scheduler import OpgSessionScheduler
from src.strategy.weight_interface import WeightCentricPipeline, PortfolioWeights
from src.strategy.risk_overlay import RiskOverlayModule
from src.data.data_processor import DataProcessor
from src.data.alpaca_feed import AlpacaFeed
from src.config import TRANSACTION_COST, SLIPPAGE
from src.utils.model_path import resolve_model_path
try:
    from src.agents.ppo_agent import PPOAgent  # <-- Fase 2
    PPO_AVAILABLE = True
except Exception:
    PPOAgent = None
    PPO_AVAILABLE = False

running = True

def signal_handler(sig, frame):
    global running
    running = False

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)



class PaperTradingBot:
    """Bot trading per paper trading con Alpaca"""

    def __init__(self, tickers: List[str], initial_capital: float = 100000.0,
                 max_drawdown_threshold: float = 0.15, max_gross_exposure: float = 0.5,
                 liquidate_on_drawdown: bool = False, api_key: str | None = None,
                 api_secret: str | None = None, base_url: str = "https://paper-api.alpaca.markets",
                 max_position_size: float = 0.2, transaction_cost: float = TRANSACTION_COST,
                 slippage: float = SLIPPAGE, vix_threshold_high: float = 30.0,
                 vix_threshold_low: float = 15.0,
                 model_path: str | Path | None = None):
        self.tickers = tickers
        self.model_path = resolve_model_path(model_path)
        self.initial_capital = initial_capital
        self.max_gross_exposure = float(max_gross_exposure)
        self.liquidate_on_drawdown = liquidate_on_drawdown
        # Restore verified broker-equity history from the last atomic dashboard snapshot.
        self.equity_curve = load_equity_curve()

        self.executor = BrokerExecutor(
            api_key=api_key if api_key is not None else os.getenv('ALPACA_API_KEY'),
            api_secret=api_secret if api_secret is not None else os.getenv('ALPACA_API_SECRET'),
            base_url=base_url,
            paper_trading=True,
            initial_capital=initial_capital,
            configured_symbols=tickers,
            risk_limits={
                "max_portfolio_exposure": float(max_gross_exposure),
                "max_position_pct": float(max_position_size),
                "max_drawdown": float(max_drawdown_threshold),
            },
        )

        self.pipeline = WeightCentricPipeline(initial_capital=initial_capital)
        self.pipeline.set_risk(RiskOverlayModule(
            max_position_size=max_position_size,
            max_drawdown_threshold=max_drawdown_threshold,
            vix_threshold_high=vix_threshold_high,
            vix_threshold_low=vix_threshold_low,
            initial_capital=initial_capital,
            max_gross_exposure=max_gross_exposure,
        ))

        self.processor = DataProcessor(Path("data"))
        self.alpaca_feed = AlpacaFeed(
            api_key=api_key if api_key is not None else os.getenv('ALPACA_API_KEY'),
            api_secret=api_secret if api_secret is not None else os.getenv('ALPACA_API_SECRET'),
        )

        # Agente PPO (Fase 2)
        self.ppo_agent = PPOAgent(
            tickers=tickers,
            initial_capital=initial_capital,
            model_path=str(self.model_path),
            transaction_cost=transaction_cost,
            slippage=slippage,
            max_position_size=max_position_size,
            max_gross_exposure=self.max_gross_exposure,
        ) if PPO_AVAILABLE else None

        if self.ppo_agent is None:
            print("PPO lifecycle: package unavailable; inference and operational cycles are disabled")
        else:
            print(f"PPO lifecycle status: {self.ppo_agent.lifecycle_status}")

    def get_market_data(self) -> pd.DataFrame:
        """Ottieni dati di mercato reali da Alpaca (bars giornaliere)."""
        print("📊 Scaricamento dati da Alpaca...")
        data = self.alpaca_feed.get_bars(self.tickers, days=100)
        print(f"✅ Dati: {len(data)} righe, {len(self.tickers)} tickers")
        return data

    def generate_weights(
        self,
        market_data: pd.DataFrame,
        account_snapshot: Dict | None = None,
        positions_snapshot: Dict | None = None,
    ) -> PortfolioWeights:
        """Generate target weights with PPO; fail closed when inference is unavailable."""
        if not PPO_AVAILABLE or self.ppo_agent is None:
            raise RuntimeError(
                "PPO unavailable; refusing to generate fallback weights or submit trades"
            )

        # The PPO observation includes current holdings so the policy can account
        # for turnover. Obtain a verified account snapshot and pass current weights.
        if (account_snapshot is None) != (positions_snapshot is None):
            raise ValueError(
                "account_snapshot and positions_snapshot must be supplied together"
            )
        if account_snapshot is None:
            account = self.executor.get_account_status()
            positions = self.executor.get_positions()
        else:
            account = account_snapshot
            positions = positions_snapshot
        if not isinstance(account, dict) or account.get("account_data_fresh") is not True:
            raise RuntimeError("Cannot verify current broker account equity for PPO inference")
        portfolio_value = float(account.get("equity", 0.0))
        if not np.isfinite(portfolio_value) or portfolio_value <= 0:
            raise RuntimeError("Cannot calculate current PPO holdings weights from invalid broker equity")

        if not isinstance(positions, dict):
            raise RuntimeError("Cannot verify current broker positions for PPO inference")
        corporate_action_reconcile = getattr(
            self.executor, "reconcile_corporate_actions", None
        )
        if not callable(corporate_action_reconcile):
            raise RuntimeError(
                "Corporate-action reconciliation is unavailable; refusing PPO inference"
            )
        corporate_action_reconcile(self.tickers, positions=positions)
        # The bot's configured ticker list is the actual PPO universe. Corporate
        # actions can create broker holdings that the model cannot observe.
        # Validate them here rather than confusing a missing model symbol with
        # an intentional zero target in the executor's partial rebalance vector.
        unconfigured_positions = []
        configured_symbols = set(self.tickers)
        for symbol, position in positions.items():
            if symbol in configured_symbols:
                continue
            try:
                quantity = float(position.get("qty"))
            except (AttributeError, TypeError, ValueError, OverflowError) as exc:
                raise RuntimeError(
                    f"Cannot verify broker position quantity for unconfigured symbol {symbol}"
                ) from exc
            if not np.isfinite(quantity):
                raise RuntimeError(
                    f"Cannot verify broker position quantity for unconfigured symbol {symbol}"
                )
            if abs(quantity) > 1e-12:
                unconfigured_positions.append(symbol)
        if unconfigured_positions:
            raise RuntimeError(
                "Broker holds positions outside the configured PPO universe; "
                "manual corporate-action reconciliation is required before inference: "
                + ", ".join(sorted(unconfigured_positions))
            )
        current_weights = []
        for ticker in self.tickers:
            position = positions.get(ticker, {})
            market_value = float(position.get("market_value", 0.0) or 0.0)
            if not np.isfinite(market_value):
                raise RuntimeError(f"Invalid current market value for {ticker}")
            if market_value < 0:
                raise RuntimeError(
                    f"Long-only PPO cannot infer safely with an existing short position in {ticker}"
                )
            current_weights.append(market_value / portfolio_value)
        current_weights = np.asarray(current_weights, dtype=float)
        if float(current_weights.sum()) > 1.0 + 1e-6:
            raise RuntimeError("Current long holdings exceed account equity; refusing PPO inference")

        # Trading must never train a policy implicitly. Train and validate a compatible
        # model explicitly before starting the paper-trading process.
        weights = np.asarray(
            self.ppo_agent.predict_weights(market_data, current_weights=current_weights),
            dtype=float,
        )
        if weights.shape != (len(self.tickers),):
            raise RuntimeError(
                f"PPO returned invalid weight shape {weights.shape}; expected {(len(self.tickers),)}"
            )
        if not np.isfinite(weights).all() or (weights < 0).any():
            raise RuntimeError("PPO returned non-finite or negative target weights")
        if (
            (weights > self.ppo_agent.max_position_size + 1e-8).any()
            or float(weights.sum()) > self.ppo_agent.max_gross_exposure + 1e-8
        ):
            raise RuntimeError("PPO returned target weights outside the configured position/gross-exposure limits")

        # Preserve the source market-bar timestamp when available. The run loop
        # independently requires a valid DatetimeIndex before claiming a decision.
        weights_timestamp = (
            self._daily_bar_timestamp(market_data)
            if isinstance(getattr(market_data, "index", None), pd.DatetimeIndex)
            else datetime.now().isoformat()
        )
        portfolio_weights = PortfolioWeights(
            weights=weights,
            tickers=self.tickers,
            timestamp=weights_timestamp,
        )
        print(f" PPO target weights: {portfolio_weights.to_dict()}")
        return portfolio_weights

    def _latest_completed_vix_session_date(self, now=None):
        """Return the latest completed NYSE session date, including holidays/weekends."""
        current = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now)
        if current.tzinfo is None:
            current = current.tz_localize("UTC")
        current = current.tz_convert("UTC")
        calendar = mcal.get_calendar("NYSE")
        start = (current - pd.Timedelta(days=14)).date().isoformat()
        end = current.date().isoformat()
        schedule = calendar.schedule(start_date=start, end_date=end)
        if schedule.empty:
            raise RuntimeError("NYSE calendar has no recent sessions")
        completed = schedule[schedule["market_close"] <= current]
        if completed.empty:
            raise RuntimeError("No completed NYSE session available for VIX validation")
        return completed.index[-1].date()

    def get_vix_value(self) -> float:
        """Fetch a VIX close from the latest completed NYSE session; fail closed if stale."""
        try:
            history = yf.Ticker("^VIX").history(period="10d", interval="1d")
            if history.empty or "Close" not in history.columns:
                raise ValueError("VIX history is empty or has no Close column")
            close = history["Close"].dropna()
            if close.empty:
                raise ValueError("VIX history has no valid closing value")

            latest_session = self._latest_completed_vix_session_date()
            dated_values = []
            for timestamp, value in close.items():
                observed_at = pd.Timestamp(timestamp)
                if observed_at.tzinfo is not None:
                    observed_date = observed_at.tz_convert("America/New_York").date()
                else:
                    observed_date = observed_at.date()
                if observed_date <= latest_session:
                    dated_values.append((observed_date, float(value)))
            if not dated_values:
                raise ValueError("VIX history has no observation for a completed market session")
            observed_date, vix = max(dated_values, key=lambda item: item[0])
            if observed_date != latest_session:
                raise ValueError(
                    f"Stale VIX close: latest observation {observed_date}, expected {latest_session}"
                )
            if not np.isfinite(vix) or vix < 0:
                raise ValueError(f"Invalid VIX value: {vix}")
            return vix
        except Exception as exc:
            raise RuntimeError(
                "Cannot obtain a valid VIX close (missing, invalid, or stale); refusing to submit new target weights"
            ) from exc

    def execute_trade(self, weights: PortfolioWeights, *, opg_context=None):
        """Serialize the risk decision and either DAY or explicitly selected OPG execution."""
        execution_lock = getattr(self.executor, "execution_lock", None)
        lock_context = execution_lock() if callable(execution_lock) else nullcontext()
        with lock_context:
            return self._execute_trade_locked(weights, opg_context=opg_context)

    def _execute_trade_locked(self, weights: PortfolioWeights, *, opg_context=None):
        """Apply portfolio risk controls before submitting target weights."""
        print("🔄 Esecuzione trade...")
        risk_module = getattr(self.pipeline, "risk_module", None)
        if risk_module is not None:
            # Capture durable history before get_account_status refreshes the
            # persisted account ID. A missing peak is only a valid first-run
            # baseline when no prior account/execution history exists.
            prior_state = getattr(self.executor, "state", None)
            prior_state_has_account_history = isinstance(prior_state, dict) and any(
                (
                    bool(prior_state.get("account_id")),
                    bool(prior_state.get("last_execution_time")),
                    bool(prior_state.get("total_orders")),
                    bool(prior_state.get("equity_history")),
                    bool(prior_state.get("order_submission_unresolved")),
                    bool(prior_state.get("order_submission_confirmed")),
                    bool(prior_state.get("emergency_liquidation_pending")),
                    bool(prior_state.get("emergency_liquidation_unresolved")),
                )
            )
            account = self.executor.get_account_status()
            if account.get("account_data_fresh") is not True:
                raise RuntimeError("Cannot verify current broker account equity; refusing to submit target weights")
            portfolio_value = float(account.get("equity", 0.0))
            if not np.isfinite(portfolio_value) or portfolio_value <= 0:
                raise RuntimeError("Cannot obtain a valid positive broker equity")

            # Persist the high-water mark independently of configured initial capital.
            # A first verified account snapshot establishes the baseline; later runs
            # retain the historical peak and advance it only when a new high is reached.
            execution_lock = getattr(self.executor, "execution_lock", None)
            lock_context = execution_lock() if callable(execution_lock) else nullcontext()
            with lock_context:
                executor_state = getattr(self.executor, "state", None)
                if isinstance(executor_state, dict):
                    account_mode = "paper" if getattr(self.executor, "paper_trading", True) else "live"
                    # Provenance must come from the current broker snapshot. A persisted
                    # account ID is historical state and cannot fill a missing ID in this
                    # response; otherwise a stale high-water mark could be trusted.
                    account_id = account.get("account_id")
                    if not isinstance(account_id, str) or not account_id.strip():
                        raise RuntimeError(
                            "Cannot verify current broker account identity; refusing to submit target weights"
                        )
                    account_id = account_id.strip()
                    stored_peak = executor_state.get("risk_peak_portfolio_value")
                    stored_peak_account_id = executor_state.get("risk_peak_account_id")
                    provenance_matches = (
                        executor_state.get("risk_peak_equity_source") == "alpaca_account"
                        and executor_state.get("risk_peak_account_mode") == account_mode
                        and bool(account_id)
                        and bool(stored_peak_account_id)
                        and account_id == stored_peak_account_id
                    )
                    # Legacy peaks without matching provenance may have come from
                    # configured initial capital; re-anchor to this verified snapshot.
                    if stored_peak is None and prior_state_has_account_history:
                        raise RuntimeError(
                            "Cannot calculate drawdown: persisted risk peak is missing for existing broker state"
                        )
                    if stored_peak is None or not provenance_matches:
                        stored_peak = portfolio_value
                        # The overlay also keeps an in-memory high-water mark. Re-anchor
                        # it whenever persisted provenance is incomplete or mismatched;
                        # otherwise a stale peak can zero targets while this account's
                        # circuit-breaker flag remains false.
                        if hasattr(risk_module, "peak_portfolio_value"):
                            risk_module.peak_portfolio_value = portfolio_value
                    else:
                        stored_peak = float(stored_peak)
                        if not np.isfinite(stored_peak) or stored_peak <= 0:
                            raise RuntimeError("Stored risk drawdown peak is invalid")
                    peak_portfolio_value = max(stored_peak, portfolio_value)
                    executor_state["risk_peak_portfolio_value"] = peak_portfolio_value
                    executor_state["risk_peak_equity_source"] = "alpaca_account"
                    executor_state["risk_peak_account_mode"] = account_mode
                    executor_state["risk_peak_account_id"] = account_id
                    executor_state["peak_equity"] = peak_portfolio_value
                    executor_state["peak_equity_source"] = "verified_account"
                    executor_state["peak_equity_account_mode"] = account_mode
                    executor_state["peak_equity_account_id"] = account_id
                    save_state = getattr(self.executor, "_save_state", None)
                    if callable(save_state):
                        save_state()
                else:
                    # Without persisted account identity there is no trustworthy
                    # provenance for an in-memory peak; anchor to verified equity.
                    peak_portfolio_value = portfolio_value
                    if hasattr(risk_module, "peak_portfolio_value"):
                        risk_module.peak_portfolio_value = portfolio_value


            self.pipeline.current_portfolio_value = portfolio_value
            drawdown = max(0.0, (peak_portfolio_value - portfolio_value) / peak_portfolio_value)
            circuit_breaker_triggered = drawdown >= float(
                getattr(risk_module, "max_drawdown_threshold", 1.0)
            )
            liquidate_on_breaker = getattr(self, "liquidate_on_drawdown", False)
            # An explicitly configured emergency liquidation does not require a VIX
            # signal: VIX gates new target decisions, not broker-side risk reduction.
            # Ordinary execution remains fail-closed when VIX is missing or stale.
            if circuit_breaker_triggered and liquidate_on_breaker:
                vix_value = None
                print("Drawdown circuit breaker: bypassing VIX gate for explicit emergency liquidation")
            else:
                vix_value = self.get_vix_value()
                print(f"📉 VIX latest completed-session close: {vix_value:.2f}")
            risked_weights = risk_module.apply(
                None,
                weights.weights,
                weights.tickers,
                vix=vix_value,
                portfolio_value=portfolio_value,
                peak_portfolio_value=peak_portfolio_value,
            )
            weights = PortfolioWeights(
                weights=risked_weights,
                tickers=weights.tickers,
                timestamp=weights.timestamp,
                metadata={
                    **(weights.metadata or {}),
                    "portfolio_value": portfolio_value,
                    "vix": vix_value,
                    "risk_overlay_applied": True,
                    "circuit_breaker_triggered": circuit_breaker_triggered,
                    "liquidate_on_circuit_breaker": liquidate_on_breaker,
                },
            )
        if risk_module is not None and circuit_breaker_triggered and not liquidate_on_breaker:
            # A zero target is interpreted as a close request by the executor.
            # When emergency liquidation is not explicitly enabled, do not send
            # any target weights to the broker at all.
            print("Drawdown circuit breaker: skipping target execution; liquidation disabled")
            result = {
                "orders": [],
                "portfolio_value": account.get("portfolio_value", portfolio_value),
                "equity": account.get("equity", portfolio_value),
                "account_data_fresh": True,
                "account_data_source": account.get("account_data_source", "alpaca_account"),
                "account_snapshot_at": account.get("account_snapshot_at"),
                "pre_execution_portfolio_value": account.get("portfolio_value", portfolio_value),
                "pre_execution_equity": account.get("equity", portfolio_value),
                "metrics_equity": None,
                "target_execution_skipped": True,
                "circuit_breaker_triggered": True,
                "liquidate_on_circuit_breaker": False,
            }
        else:
            if opg_context is None:
                result = self.executor.execute_weights(weights)
                result["execution_mode"] = "day"
            else:
                result = self._execute_opg_plan(weights, opg_context)
            result["target_execution_skipped"] = False
        print(f"✅ Trade eseguito: {len(result['orders'])} ordini")
        for order in result['orders']:
            print(f"  • {order['side']} {order['quantity']:.4f} {order['ticker']}")
        write_snapshot(
            RUN_ID,
            "trade",
            portfolio_value=result.get("portfolio_value"),
            equity=result.get("equity"),
            payload={
                "tickers": self.tickers,
                "rows": len(result),
                "account_data_source": "alpaca_account" if result.get("account_data_fresh") is True else None,
                "account_snapshot_at": result.get("account_snapshot_at"),
                "account_data_fresh": result.get("account_data_fresh", False),
                "pre_execution_portfolio_value": result.get("pre_execution_portfolio_value"),
                "pre_execution_equity": result.get("pre_execution_equity"),
                "metrics_equity": result.get("metrics_equity"),
                "metrics_refresh_error": result.get("metrics_refresh_error"),
            },
        )
        log_event(RUN_ID, "trade", "bars_loaded", "bars loaded", payload={"rows": len(result), "tickers": self.tickers})
        return result

    def _execute_opg_plan(self, weights: PortfolioWeights, opg_context: Dict) -> Dict:
        """Build and execute one integer-share OPG plan; never fall back to DAY."""
        from src.execution.notional_cap import MAX_GROSS_NOTIONAL_USD
        from src.execution.opg_sizing import build_opg_sizing_plan

        if not isinstance(opg_context, dict):
            raise RuntimeError("OPG execution context is missing or invalid")
        market_data = opg_context.get("market_data")
        signal_bar_at = opg_context.get("signal_bar_at")
        target_session = opg_context.get("target_session")
        if not isinstance(market_data, pd.DataFrame) or market_data.empty:
            raise RuntimeError("OPG execution requires the completed signal-bar market data")
        if not isinstance(signal_bar_at, str) or not signal_bar_at.strip():
            raise RuntimeError("OPG execution requires a canonical signal-bar timestamp")
        if not isinstance(target_session, str) or not target_session.strip():
            raise RuntimeError("OPG execution requires a resolved NYSE target session")

        account = self.executor.get_account_status()
        if not isinstance(account, dict) or account.get("account_data_fresh") is not True:
            raise RuntimeError("OPG sizing requires a fresh verified Paper account snapshot")
        equity = account.get("equity")
        positions = self.executor.get_positions()
        if not isinstance(positions, dict):
            raise RuntimeError("OPG sizing requires a fresh broker positions snapshot")

        current_positions = {}
        for symbol, position in positions.items():
            if not isinstance(symbol, str) or not isinstance(position, dict):
                raise RuntimeError("OPG sizing received a malformed broker position")
            current_positions[symbol.strip().upper()] = position.get("qty")
        for ticker in self.tickers:
            current_positions.setdefault(ticker, 0)

        close_prices = {}
        target_weights = {}
        if list(weights.tickers) != list(self.tickers):
            raise RuntimeError("OPG target ticker order differs from the configured PPO universe")
        for ticker, weight in zip(weights.tickers, weights.weights):
            column = f"Close_{ticker}"
            if column not in market_data.columns:
                raise RuntimeError(f"OPG signal-bar Close is missing for {ticker}")
            close_prices[ticker] = market_data[column].iloc[-1]
            target_weights[ticker] = weight

        plan = build_opg_sizing_plan(
            equity=equity,
            target_weights=target_weights,
            close_prices=close_prices,
            current_positions=current_positions,
            # Strategic account equity remains the model's reference. Actual
            # OPG target notionals are scaled to the smaller operational budget.
            sizing_capital=min(float(equity), float(MAX_GROSS_NOTIONAL_USD)),
        )
        batch = self.executor.execute_opg_plan(
            plan,
            signal_bar_at=signal_bar_at,
            target_session=target_session,
        )
        order_rows = []
        for symbol, order in sorted(batch.get("orders", {}).items()):
            order_rows.append({
                "ticker": symbol,
                "side": order.get("side"),
                "quantity": order.get("quantity"),
                "status": order.get("status"),
                "order_id": order.get("order_id"),
                "client_order_id": order.get("client_order_id"),
                "filled_qty": order.get("filled_qty", 0),
                "average_fill_price": order.get("average_fill_price"),
                "time_in_force": "opg",
            })
        metrics_provider = getattr(self.executor, "metrics", None)
        metrics_reader = getattr(metrics_provider, "get_all_metrics", None)
        return {
            "orders": order_rows,
            "portfolio_value": account.get("portfolio_value", equity),
            "equity": equity,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "account_data_fresh": True,
            "account_data_source": account.get("account_data_source"),
            "account_snapshot_at": account.get("account_snapshot_at"),
            "pre_execution_portfolio_value": account.get("portfolio_value", equity),
            "pre_execution_equity": equity,
            "metrics_equity": None,
            "metrics": metrics_reader() if callable(metrics_reader) else {},
            "execution_mode": "opg",
            "opg_batch": batch,
            "target_session": target_session,
            "signal_bar_at": signal_bar_at,
        }

    def update_hub_state(self, result: Dict):
        """Persist the complete dashboard snapshot through the single atomic writer."""
        status = self.executor.get_account_status()
        if status.get("account_data_fresh") is not True:
            raise RuntimeError("Cannot persist hub state without a fresh broker account snapshot")
        portfolio_value = float(status["portfolio_value"])
        equity = float(status["equity"])
        cash = float(status["cash"])
        if not all(np.isfinite(value) for value in (portfolio_value, equity, cash)):
            raise RuntimeError("Cannot persist hub state with invalid broker account values")

        # Persist a separately timestamped, read-only broker position observation.
        # A position read failure must not erase the account snapshot or masquerade
        # as an empty portfolio.
        positions = None
        positions_status = "UNAVAILABLE"
        positions_snapshot_at = None
        positions_source = "alpaca_positions"
        positions_reader = getattr(self.executor, "get_positions", None)
        if callable(positions_reader):
            try:
                raw_positions = positions_reader()
                if not isinstance(raw_positions, dict):
                    raise ValueError("invalid broker positions response")
                normalized_positions = []
                for raw_symbol, raw_values in sorted(raw_positions.items()):
                    symbol = str(raw_symbol).strip().upper()
                    if not symbol or not isinstance(raw_values, dict):
                        raise ValueError("invalid broker position record")
                    row = {"symbol": symbol}
                    for field in ("qty", "market_value", "avg_entry_price", "unrealized_pl"):
                        value = raw_values.get(field)
                        if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value):
                            raise ValueError("invalid broker position numeric value")
                        row[field] = float(value)
                    if row["qty"] == 0 or row["avg_entry_price"] <= 0:
                        raise ValueError("invalid broker position quantity or average entry price")
                    normalized_positions.append(row)
                positions = normalized_positions
                positions_status = "OK"
                positions_snapshot_at = datetime.now(timezone.utc).isoformat()
            except Exception:
                # Do not reuse a previous positions snapshot after a failed read.
                positions = None
                positions_status = "UNAVAILABLE"

        # The monitoring curve tracks broker equity only; configured initial capital
        # is not a substitute for the account's actual starting equity.
        equity_curve = (self.equity_curve + [equity])[-120:]
        metrics = result.get("metrics")
        if not isinstance(metrics, dict):
            metrics_provider = getattr(self.executor, "metrics", None)
            get_all_metrics = getattr(metrics_provider, "get_all_metrics", None)
            metrics = get_all_metrics() if callable(get_all_metrics) else {}

        hub_state = {
            "timestamp": result.get("timestamp") or datetime.now().isoformat(),
            "mode": "paper" if getattr(self.executor, "paper_trading", True) else "live",
            "portfolio_value": portfolio_value,
            "cash": cash,
            "equity": equity,
            "account_data_fresh": True,
            "account_data_source": status.get("account_data_source"),
            "account_snapshot_at": status.get("account_snapshot_at"),
            "equity_curve_source": "verified_broker_equity",
            "equity_curve": equity_curve,
            "positions": positions,
            "positions_status": positions_status,
            "positions_source": positions_source,
            "positions_snapshot_at": positions_snapshot_at,
            "orders": result.get("orders", []),
            "pre_execution_portfolio_value": result.get("pre_execution_portfolio_value"),
            "pre_execution_equity": result.get("pre_execution_equity"),
            "metrics_equity": result.get("metrics_equity"),
            "metrics_refresh_error": result.get("metrics_refresh_error"),
            "metrics": metrics,
            "last_result": result,
        }
        try:
            write_hub_state(hub_state)
        except SnapshotDurabilityError:
            # The snapshot is already visible after os.replace; keep memory aligned
            # even though the directory entry's crash durability is uncertain.
            self.equity_curve = equity_curve
            raise
        self.equity_curve = equity_curve

    def execute_and_persist_cycle(self, weights: PortfolioWeights, *, opg_context=None):
        """Serialize broker execution and its matching dashboard snapshot as one cycle."""
        execution_lock = getattr(self.executor, "execution_lock", None)
        lock_context = execution_lock() if callable(execution_lock) else nullcontext()
        with lock_context:
            if opg_context is None:
                result = self.execute_trade(weights)
            else:
                result = self.execute_trade(weights, opg_context=opg_context)
            self.update_hub_state(result)
            return result

    @staticmethod
    def _daily_bar_timestamp(market_data: pd.DataFrame) -> str:
        """Return the latest unique daily-bar timestamp in canonical UTC form."""
        if not isinstance(market_data, pd.DataFrame) or market_data.empty:
            raise RuntimeError("Daily decision blocked: market data is empty or invalid")
        if not isinstance(market_data.index, pd.DatetimeIndex):
            raise RuntimeError("Daily decision blocked: market data index is not a DatetimeIndex")
        if market_data.index.has_duplicates or not market_data.index.is_monotonic_increasing:
            raise RuntimeError("Daily decision blocked: daily-bar timestamps are duplicated or unordered")
        latest = pd.Timestamp(market_data.index[-1])
        if latest.tzinfo is None:
            latest = latest.tz_localize("UTC")
        else:
            latest = latest.tz_convert("UTC")
        return latest.isoformat()

    def generate_and_execute_daily_cycle(self, market_data: pd.DataFrame):
        """Run at most one PPO decision per daily bar with a durable pre-inference claim."""
        bar_timestamp = self._daily_bar_timestamp(market_data)
        reconcile = getattr(self.executor, "reconcile_daily_decision_if_required", None)
        claim = getattr(self.executor, "claim_daily_decision", None)
        complete = getattr(self.executor, "complete_daily_decision", None)
        if not all(callable(method) for method in (reconcile, claim, complete)):
            raise RuntimeError("Daily decision guard is unavailable; refusing PPO inference and broker execution")

        # If the prior process stopped mid-cycle, this path performs read-only
        # order/position reconciliation. It never retries the interrupted bar.
        reconcile()
        if not claim(bar_timestamp, self.tickers):
            print(f"Daily bar {bar_timestamp}: decision already processed; skipping PPO and execution")
            return {"daily_decision_skipped": True, "daily_bar_timestamp": bar_timestamp, "orders": []}

        # A failure deliberately leaves the durable marker in 'started'. The
        # next process must reconcile broker state before another decision.
        result = self.generate_and_execute_cycle(market_data)
        complete(bar_timestamp)
        return result

    def generate_and_execute_cycle(self, market_data: pd.DataFrame):
        """Keep the holdings snapshot, PPO inference, and execution in one locked cycle."""
        execution_lock = getattr(self.executor, "execution_lock", None)
        lock_context = execution_lock() if callable(execution_lock) else nullcontext()
        with lock_context:
            weights = self.generate_weights(market_data)
            return self.execute_and_persist_cycle(weights)

    def generate_and_execute_opg_daily_cycle(self, market_data: pd.DataFrame, *, now=None):
        """Submit only inside the deterministic OPG window; never catch up after cutoff."""
        if not isinstance(market_data, pd.DataFrame) or market_data.empty:
            raise RuntimeError("OPG scheduler requires non-empty completed daily market data")
        signal_session = pd.Timestamp(market_data.index[-1]).date()
        scheduler = OpgSessionScheduler()
        current_time = datetime.now(timezone.utc) if now is None else now
        schedule_state = scheduler.state_for(signal_session, current_time)
        target_session = scheduler.next_session(signal_session).isoformat()

        # OPG order recovery is independent of the submission window. Keep
        # reconciling active/partial orders on every polling cycle, including
        # after the 09:25 cutoff, before returning from a waiting/missed window.
        account = self.executor.get_account_status()
        if not isinstance(account, dict) or account.get("account_data_fresh") is not True:
            raise RuntimeError("OPG polling requires a fresh verified Paper account snapshot")
        reconcile = getattr(self.executor, "reconcile_daily_decision_if_required", None)
        broker_reconcile = getattr(self.executor, "reconcile_broker_state_read_only", None)
        claim = getattr(self.executor, "claim_daily_decision", None)
        complete = getattr(self.executor, "complete_daily_decision", None)
        if not all(callable(method) for method in (reconcile, broker_reconcile, claim, complete)):
            raise RuntimeError("Daily/broker reconciliation guard is unavailable; refusing OPG execution")
        reconcile()
        broker_state = broker_reconcile()
        if not isinstance(broker_state, dict) or broker_state.get("safe_for_new_decision") is not True:
            return {
                "execution_mode": "opg",
                "opg_schedule_state": schedule_state.value,
                "broker_state_blocked": True,
                "broker_reconciliation": broker_state,
                "orders": [],
            }

        if schedule_state.value == "waiting_for_window":
            return {"execution_mode": "opg", "opg_schedule_state": schedule_state.value, "orders": []}
        if schedule_state.value == "missed_open":
            return {"execution_mode": "opg", "opg_schedule_state": schedule_state.value, "orders": []}

        bar_timestamp = self._daily_bar_timestamp(market_data)
        if not claim(bar_timestamp, self.tickers):
            return {
                "execution_mode": "opg",
                "opg_schedule_state": schedule_state.value,
                "daily_decision_skipped": True,
                "daily_bar_timestamp": bar_timestamp,
                "orders": [],
            }

        # A failure leaves the durable daily marker unresolved. Restart recovery is
        # read-only; the consumed signal is never automatically resubmitted.
        execution_lock = getattr(self.executor, "execution_lock", None)
        lock_context = execution_lock() if callable(execution_lock) else nullcontext()
        with lock_context:
            weights = self.generate_weights(market_data)
            result = self.execute_and_persist_cycle(
                weights,
                opg_context={
                    "market_data": market_data,
                    "signal_bar_at": bar_timestamp,
                    "target_session": target_session,
                },
            )
        complete(bar_timestamp)
        result["opg_schedule_state"] = schedule_state.value
        return result

    def run_loop(self, interval_seconds: int = 60, *, execution_mode: str = "day"):
        """Run the Paper loop in the selected, explicit execution mode."""
        if execution_mode not in {"day", "opg"}:
            raise ValueError("execution_mode must be 'day' or 'opg'")
        print("\n" + "=" * 62)
        print("🚀 AVVIO PAPER TRADING BOT")
        print("=" * 62)
        print(f"Tickers: {self.tickers}")
        print(f"Intervallo: {interval_seconds} secondi")
        print(f"Modalità esecuzione: {execution_mode.upper()}")
        print(f"Paper Trading: {self.executor.paper_trading}")
        print("=" * 62 + "\n")

        iteration = 0

        while running:
            iteration += 1
            print(f"\n[ITERAZIONE {iteration}]")
            print("-" * 50)

            try:
                market_data = self.get_market_data()
                if execution_mode == "opg":
                    opg_result = self.generate_and_execute_opg_daily_cycle(market_data)
                    print(f"OPG scheduler: {opg_result.get('opg_schedule_state', 'not_evaluated')}")
                else:
                    self.generate_and_execute_daily_cycle(market_data)

                status = self.executor.get_account_status()
                if status.get("account_data_fresh") is not True:
                    raise RuntimeError("Cannot display account status without a fresh broker snapshot")
                print("\n💰 Status Account:")
                print(f"  • Cash:            ${status['cash']:,.2f}")
                print(f"  • Portfolio Value: ${status['portfolio_value']:,.2f}")
                print(f"  • Equity:          ${status['equity']:,.2f}")

                print(f"\n⏸️  Waiting {interval_seconds} seconds...")

            except Exception as e:
                print(f"\n❌ Errore: {e}")

            if running:
                time.sleep(interval_seconds)

        print("\n" + "=" * 62)
        print("🛑 PAPER TRADING BOT STOPPATO")
        print("=" * 62)


def main(argv=None):
    global RUN_ID
    parser = argparse.ArgumentParser(description="STMAA paper-trading entry point")
    parser.add_argument(
        "--execution-mode",
        choices=("day", "opg"),
        default="day",
        help="Paper execution path: existing DAY rebalance (default) or scheduled MOO/OPG",
    )
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="validate configuration and read the paper account without starting the trading loop",
    )
    args = parser.parse_args(argv)

    RUN_ID = start_run(mode="paper", meta={"component": "trade"})
    status = "failed"
    try:
        log_event(RUN_ID, "trade", "startup", "trade.py started")
        print("=" * 70)
        print("STMAA - Paper Trading Bot")
        print("=" * 70)

        from src.utils.startup_config import (
            load_startup_config,
            validate_broker_preflight,
            validate_ppo_preflight,
        )

        config = load_startup_config()
        # Keep validated settings explicit without printing secrets.
        print("Configurazione valida: modalità paper")
        print(f"Limite esposizione: {config.max_portfolio_exposure:.1%}")
        print(f"Limite drawdown: {config.max_drawdown:.1%}")
        print(f"Liquidazione su drawdown: {config.liquidate_on_drawdown}")
        print(f"Universo: {', '.join(config.tickers)}")
        print(f"Capitale iniziale configurato: {config.initial_capital:,.2f}")
        print(f"Intervallo ciclo: {config.interval_seconds} secondi")

        bot = PaperTradingBot(
            tickers=list(config.tickers),
            initial_capital=config.initial_capital,
            max_drawdown_threshold=config.max_drawdown,
            max_gross_exposure=config.max_portfolio_exposure,
            liquidate_on_drawdown=config.liquidate_on_drawdown,
            api_key=config.api_key,
            api_secret=config.api_secret,
            base_url=config.base_url,
            max_position_size=config.max_position_size,
            transaction_cost=config.transaction_cost,
            slippage=config.slippage,
            vix_threshold_high=config.vix_threshold_high,
            vix_threshold_low=config.vix_threshold_low,
        )

        # Normal operation requires a loaded, compatible PPO policy before
        # making even the read-only broker preflight request. The explicit
        # preflight-only mode remains a configuration/account diagnostic.
        if not args.preflight_only:
            validate_ppo_preflight(bot.ppo_agent)

        # Read-only broker request; no loop or order submission before it passes.
        account = validate_broker_preflight(bot.executor)
        print(
            "Account Alpaca Paper verificato: "
            "equity=" + format(float(account["equity"]), ",.2f")
        )
        report = account["preflight_report"]
        print("Preflight broker read-only:")
        print(f"  Posizioni aperte: {report['position_count']}")
        print(f"  Ordini aperti: {report['open_order_count']}")
        print(f"  Esposizione lorda aggregata: ${report['gross_notional_usd']:,.2f}")
        print(f"  Riserva BUY pendenti: ${report['pending_buy_notional_usd']:,.2f}")
        print(f"  Limite operativo: ${report['max_gross_notional_usd']:,.2f}")
        print(f"  Limite rispettato: {'SI' if report['cap_compliant'] else 'NO'}")
        reconciliation = report["reconciliation"]
        print("  Riconciliazione ordini: " + (
            "NESSUN BLOCCO" if reconciliation.get("safe_for_new_decision") else "BLOCCATA"
        ))
        print("  Nuova decisione consentita: " + (
            "SI" if report["safe_for_new_decision"] else "NO"
        ))
        if args.preflight_only:
            print("Preflight-only completato: nessun ciclo avviato e nessun ordine inviato/modificato/cancellato")
            status = "completed"
            return 0 if report["safe_for_new_decision"] else 2

        if not report["safe_for_new_decision"]:
            print("Avvio bloccato: il preflight read-only non autorizza una nuova decisione")
            status = "failed"
            return 2

        bot.run_loop(interval_seconds=config.interval_seconds, execution_mode=args.execution_mode)
        status = "completed"
        return 0
    except Exception as exc:
        print(f"Avvio bloccato dal preflight: {exc}")
        return 1
    finally:
        run_id = RUN_ID
        RUN_ID = None
        if run_id is not None:
            end_run(run_id, status=status)


if __name__ == "__main__":
    raise SystemExit(main())
