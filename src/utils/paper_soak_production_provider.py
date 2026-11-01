"""Production read-only provider and CLI for the STMAA Paper soak shadow runner.

The provider reuses PaperTradingBot's market-data and PPO inference path, then
collects fresh broker/account/position/open-order evidence. It never invokes
trade execution, order submission, replacement, cancellation, or liquidation.
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np

from src.utils.paper_soak_monitor import (
    DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
    PaperSoakMonitor,
)
from src.utils.paper_soak_shadow_runner import (
    PaperSoakShadowObservation,
    PaperSoakShadowRunner,
)
from src.utils.paper_soak_stop_controller import PaperSoakHealthSnapshot
from src.utils.sqlite_logger import DB_PATH

PAPER_ENDPOINT = "https://paper-api.alpaca.markets"


class ProductionPaperSoakObservationProvider:
    """Adapt the production PaperTradingBot/PPO path to read-only soak evidence.

    The injected bot is used only for market-data retrieval, PPO inference,
    VIX validation and read-only broker snapshots. This adapter intentionally
    has no reference to the bot's trade-execution methods.
    """

    def __init__(self, bot: Any) -> None:
        self.bot = bot
        self.executor = bot.executor
        state = self.executor.state
        self._prior_state_has_account_history = any(
            (
                bool(state.get("account_id")),
                bool(state.get("last_execution_time")),
                bool(state.get("total_orders")),
                bool(state.get("equity_history")),
                bool(state.get("order_submission_unresolved")),
                bool(state.get("order_submission_confirmed")),
                bool(state.get("emergency_liquidation_pending")),
                bool(state.get("emergency_liquidation_unresolved")),
            )
        )

    @staticmethod
    def _finite_positive(value: Any, field: str) -> float:
        if isinstance(value, bool):
            raise RuntimeError(f"{field} must be finite and positive")
        try:
            parsed = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise RuntimeError(f"{field} must be finite and positive") from exc
        if not math.isfinite(parsed) or parsed <= 0:
            raise RuntimeError(f"{field} must be finite and positive")
        return parsed

    def _risk_peak(self, equity: float, account_id: str) -> float:
        state = self.executor.state
        account_mode = "paper"
        has_history = self._prior_state_has_account_history
        stored_peak = state.get("risk_peak_portfolio_value")
        provenance_matches = (
            state.get("risk_peak_equity_source") == "alpaca_account"
            and state.get("risk_peak_account_mode") == account_mode
            and state.get("risk_peak_account_id") == account_id
        )
        if stored_peak is None and has_history:
            raise RuntimeError(
                "Cannot calculate shadow drawdown: persisted risk peak is missing"
            )
        if stored_peak is None or not provenance_matches:
            return equity
        peak = self._finite_positive(stored_peak, "persisted risk peak")
        return max(peak, equity)

    def __call__(self) -> PaperSoakShadowObservation:
        # Market data and VIX use the production feeds. One broker snapshot is
        # then collected under the execution lock and passed into generate_weights,
        # which performs corporate-action reconciliation and PPO inference.
        market_data = self.bot.get_market_data()
        vix = self.bot.get_vix_value()
        execution_lock = getattr(self.executor, "execution_lock", None)
        if not callable(execution_lock):
            raise RuntimeError("Broker execution lock is unavailable")
        with execution_lock():
            account = self.executor.get_account_status()
            if account.get("account_data_fresh") is not True:
                raise RuntimeError("Fresh Alpaca account snapshot unavailable")
            if account.get("status") != "active" or account.get("trading_blocked") is not False:
                raise RuntimeError("Alpaca Paper account is not active and unblocked")
            account_id = account.get("account_id")
            if not isinstance(account_id, str) or not account_id.strip():
                raise RuntimeError("Current Alpaca account identity is not verified")
            account_id = account_id.strip()
            equity = self._finite_positive(account.get("equity"), "broker equity")
    
            positions = self.executor.get_positions()
            if not isinstance(positions, dict):
                raise RuntimeError("Broker positions snapshot is incomplete")
            raw_weights = self.bot.generate_weights(
                market_data,
                account_snapshot=account,
                positions_snapshot=positions,
            )
            open_orders_reader = getattr(self.executor, "_get_open_orders", None)
            reserved_reader = getattr(self.executor, "_get_reserved_notional", None)
            if not callable(open_orders_reader) or not callable(reserved_reader):
                raise RuntimeError("Broker open-order exposure readers are unavailable")
            open_orders = open_orders_reader()
            if not isinstance(open_orders, list):
                raise RuntimeError("Broker open-orders snapshot is incomplete")
    
            prices: dict[str, float] = {}
            for ticker in self.bot.tickers:
                column = f"Close_{ticker}"
                if column not in market_data.columns or market_data.empty:
                    raise RuntimeError(f"Fresh market close is unavailable for {ticker}")
                prices[ticker] = self._finite_positive(
                    market_data[column].iloc[-1], f"latest market close for {ticker}"
                )
    
            position_gross = 0.0
            for symbol, position in positions.items():
                if not isinstance(position, dict):
                    raise RuntimeError(f"Malformed broker position for {symbol}")
                market_value = position.get("market_value")
                if isinstance(market_value, bool):
                    raise RuntimeError(f"Invalid market value for {symbol}")
                try:
                    market_value = float(market_value)
                except (TypeError, ValueError, OverflowError) as exc:
                    raise RuntimeError(f"Invalid market value for {symbol}") from exc
                if not math.isfinite(market_value):
                    raise RuntimeError(f"Non-finite market value for {symbol}")
                position_gross += abs(market_value)
    
            reserved_notional = float(reserved_reader(open_orders, prices=prices))
            if not math.isfinite(reserved_notional) or reserved_notional < 0:
                raise RuntimeError("Pending-order exposure is not determinable")
            estimate_reader = getattr(self.executor, "_estimate_order_notional", None)
            if not callable(estimate_reader):
                raise RuntimeError("Per-symbol pending-order exposure reader is unavailable")
            pending_by_symbol: dict[str, float] = {}
            for order in open_orders:
                symbol = getattr(order, "symbol", None)
                if not isinstance(symbol, str) or not symbol.strip():
                    raise RuntimeError("Pending order has no verifiable symbol")
                symbol = symbol.strip().upper()
                if symbol not in self.bot.tickers:
                    raise RuntimeError(
                        f"Pending order symbol is outside the configured PPO universe: {symbol}"
                    )
                order_notional = float(estimate_reader(order, prices=prices))
                if not math.isfinite(order_notional) or order_notional < 0:
                    raise RuntimeError(
                        f"Pending-order exposure is not determinable for {symbol}"
                    )
                pending_by_symbol[symbol] = (
                    pending_by_symbol.get(symbol, 0.0) + order_notional
                )
            if not math.isclose(
                sum(pending_by_symbol.values()), reserved_notional,
                rel_tol=1e-8, abs_tol=0.01,
            ):
                raise RuntimeError(
                    "Aggregate and per-symbol pending-order exposure estimates disagree"
                )
            gross_exposure = position_gross + reserved_notional
            exposure_ratio = gross_exposure / equity
            if not math.isfinite(exposure_ratio):
                raise RuntimeError("Aggregate broker exposure is not determinable")
            max_position_limit = float(self.bot.ppo_agent.max_position_size)
            position_exposure_by_symbol: dict[str, float] = {}
            for symbol, position in positions.items():
                market_value = float(position["market_value"])
                position_exposure_by_symbol[symbol] = (
                    position_exposure_by_symbol.get(symbol, 0.0) + abs(market_value)
                )
            for symbol, pending_value in pending_by_symbol.items():
                position_exposure_by_symbol[symbol] = (
                    position_exposure_by_symbol.get(symbol, 0.0) + pending_value
                )
            position_limits_consistent = all(
                value / equity <= max_position_limit + 1e-8
                for value in position_exposure_by_symbol.values()
            )
    
            state = self.executor.state
            persisted_account_id = state.get("account_id")
            identity_consistent = (
                getattr(self.executor, "_current_account_identity_verified", False) is True
                and persisted_account_id == account_id
                and state.get("account_mode") == "paper"
            )
            corporate_actions = state.get("corporate_action_reconciliation")
            corporate_actions_consistent = (
                isinstance(corporate_actions, dict)
                and isinstance(corporate_actions.get("last_reconciled_at"), str)
                and not corporate_actions.get("unresolved_activity_ids")
                and not corporate_actions.get("blocked_symbols")
            )
            unresolved_execution_state = any(
                not isinstance(state.get(field), dict) or bool(state.get(field))
                for field in (
                    "order_submission_unresolved",
                    "emergency_liquidation_pending",
                    "emergency_liquidation_unresolved",
                )
            )
            state_revision = state.get("state_revision")
            persisted_revision = getattr(self.executor, "_persisted_state_revision", None)
            persistence_healthy = (
                getattr(self.executor, "_state_persistence_failed", True) is False
                and type(state_revision) is int
                and persisted_revision == state_revision
            )
    
            risk_module = getattr(getattr(self.bot, "pipeline", None), "risk_module", None)
            if risk_module is None:
                raise RuntimeError("Production risk overlay is unavailable")
            peak_equity = self._risk_peak(equity, account_id)
            risked_weights = np.asarray(
                risk_module.apply(
                    None,
                    raw_weights.weights,
                    raw_weights.tickers,
                    vix=vix,
                    portfolio_value=equity,
                    peak_portfolio_value=peak_equity,
                ),
                dtype=float,
            )
            if (
                risked_weights.shape != (len(self.bot.tickers),)
                or not np.isfinite(risked_weights).all()
            ):
                raise RuntimeError("Production risk overlay returned invalid target weights")
    
            drawdown = max(0.0, (peak_equity - equity) / peak_equity)
            circuit_breaker = drawdown >= float(risk_module.max_drawdown_threshold)
            gross_limit = float(self.bot.max_gross_exposure)
            exposure_within_limit = exposure_ratio <= gross_limit + 1e-8
            paper_endpoint = (
                getattr(self.executor, "paper_trading", None) is True
                and getattr(self.executor, "base_url", None) == PAPER_ENDPOINT
            )
            broker_consistent = (
                identity_consistent
                and corporate_actions_consistent
                and not unresolved_execution_state
                and exposure_within_limit
                and position_limits_consistent
            )
            snapshot_complete = (
                account.get("account_data_fresh") is True
                and bool(positions is not None)
                and isinstance(open_orders, list)
                and corporate_actions_consistent
            )
    
            health = PaperSoakHealthSnapshot(
                broker_endpoint_is_paper=paper_endpoint,
                order_mutation_attempted=False,
                broker_state_consistent=broker_consistent,
                exposure_determinable=True,
                market_data_valid=True,
                market_data_fresh=True,
                persistence_healthy=persistence_healthy,
                broker_snapshot_complete=snapshot_complete,
                api_consecutive_failures=0,
                heartbeat_age_seconds=0.0,
            )
            decision = {
                "decision_type": "production_ppo_shadow",
                "tickers": list(self.bot.tickers),
                "raw_ppo_weights": {
                    ticker: float(weight)
                    for ticker, weight in zip(raw_weights.tickers, raw_weights.weights)
                },
                "risk_adjusted_weights": {
                    ticker: float(weight)
                    for ticker, weight in zip(self.bot.tickers, risked_weights)
                },
                "vix": float(vix),
                "equity": equity,
                "peak_equity": peak_equity,
                "drawdown": drawdown,
                "circuit_breaker_triggered": circuit_breaker,
                "target_execution_skipped": bool(
                    circuit_breaker and not getattr(self.bot, "liquidate_on_drawdown", False)
                ),
                "position_gross_notional": position_gross,
                "pending_order_reserved_notional": reserved_notional,
                "gross_exposure_notional": gross_exposure,
                "gross_exposure_ratio": exposure_ratio,
                "gross_exposure_limit": gross_limit,
                "max_position_size_limit": max_position_limit,
                "position_limits_consistent": position_limits_consistent,
                "pending_order_count": len(open_orders),
                "broker_account_id": account_id,
                "broker_snapshot_at": account.get("account_snapshot_at"),
                "unresolved_execution_state": unresolved_execution_state,
                "order_submission": "disabled",
            }
            return PaperSoakShadowObservation(health=health, decision=decision)
    

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the production-inference, read-only STMAA Paper soak shadow runner."
    )
    parser.add_argument("--duration-hours", type=float, default=48.0)
    parser.add_argument(
        "--interval-seconds",
        type=float,
        default=DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
        help="market/broker observation and heartbeat interval",
    )
    parser.add_argument("--branch", default="main")
    parser.add_argument("--commit-sha", required=True)
    parser.add_argument("--db-path", type=Path, default=DB_PATH)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if (
            isinstance(args.duration_hours, bool)
            or not math.isfinite(args.duration_hours)
            or args.duration_hours <= 0
        ):
            raise ValueError("duration-hours must be positive and finite")
        if (
            isinstance(args.interval_seconds, bool)
            or not math.isfinite(args.interval_seconds)
            or args.interval_seconds <= 0
        ):
            raise ValueError("interval-seconds must be positive and finite")
        if len(args.commit_sha) != 40 or any(
            character not in "0123456789abcdefABCDEF" for character in args.commit_sha
        ):
            raise ValueError("commit-sha must be a 40-character hexadecimal SHA")

        from src.utils.startup_config import load_startup_config
        from trade import PaperTradingBot

        config = load_startup_config()
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
        monitor = PaperSoakMonitor(
            db_path=args.db_path,
            duration_seconds=args.duration_hours * 60 * 60,
            heartbeat_interval_seconds=args.interval_seconds,
            branch=args.branch,
            commit_sha=args.commit_sha,
            run_metadata={
                "provider_name": "stmaa_paper_trading_bot_ppo",
                "provider_kind": "production_read_only",
                "production_strategy_inference": True,
                "observation_interval_seconds": args.interval_seconds,
                "broker_mutations_enabled": False,
            },
        )
        runner = PaperSoakShadowRunner(
            monitor,
            observation_provider=ProductionPaperSoakObservationProvider(bot),
            observation_interval_seconds=args.interval_seconds,
        )
        run_id = runner.run()
    except KeyboardInterrupt:
        print("PAPER_SOAK_STATUS: INTERRUPTED")
        return 130
    except Exception as exc:
        print(f"PAPER_SOAK_STATUS: FAILED ({type(exc).__name__}: {exc})")
        return 1

    print(f"PAPER_SOAK_RUN_ID: {run_id}")
    print("PAPER_SOAK_STATUS: COMPLETED")
    print(f"EVENT_DATABASE: {args.db_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
