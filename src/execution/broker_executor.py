"""Broker Executor per Alpaca (paper/live trading) – alpaca-py."""
import os
import json
import logging
import tempfile
import fcntl
import time
import threading
from contextlib import contextmanager, nullcontext
import copy
import uuid
from decimal import Decimal, InvalidOperation
import numpy as np
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime, timedelta, timezone

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest, GetOrdersRequest
from alpaca.trading.enums import OrderSide, TimeInForce, QueryOrderStatus

from src.strategy.weight_interface import PortfolioWeights
from src.data.alpaca_feed import AlpacaFeed
from src.execution.corporate_action_reconciler import CorporateActionReconciler
from src.utils.metrics import PortfolioMetrics
from src.execution.notional_cap import (
    MAX_GROSS_NOTIONAL_USD,
    calculate_aggregate_gross_notional,
    calculate_position_gross_notional,
    enforce_aggregate_gross_notional_cap,
)

PAPER_API_BASE_URL = "https://paper-api.alpaca.markets"

RISK_LIMITS = {
    "max_portfolio_exposure": float(os.getenv("MAX_PORTFOLIO_EXPOSURE", "0.50")),
    "max_drawdown": float(os.getenv("MAX_DRAWDOWN", "0.15")),
}


def _canonicalize_state_file_path(path):
    """Resolve path aliases before deriving process-shared lock-file names."""
    return Path(path).expanduser().resolve(strict=False)


def _validate_risk_limits(limits):
    """Fail fast when account-level risk limits are missing or out of range."""
    exposure = float(limits["max_portfolio_exposure"])
    drawdown = float(limits["max_drawdown"])
    if not np.isfinite(exposure) or not 0 <= exposure <= 1:
        raise ValueError("MAX_PORTFOLIO_EXPOSURE must be finite and in [0, 1]")
    if not np.isfinite(drawdown) or not 0 < drawdown < 1:
        raise ValueError("MAX_DRAWDOWN must be finite and in (0, 1)")
    return limits


_validate_risk_limits(RISK_LIMITS)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
EXECUTION_LOG_DIR = REPOSITORY_ROOT / "logs" / "execution"
EXECUTION_LOG_PATH = EXECUTION_LOG_DIR / "broker_executor.log"
EXECUTION_LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(EXECUTION_LOG_PATH),
        logging.StreamHandler(),
    ],
)




class PortfolioManager:
    def __init__(self, max_total_exposure_pct=0.60, max_symbol_count=10):
        self.max_total_exposure_pct = max_total_exposure_pct
        self.max_symbol_count = max_symbol_count

    def allocate(self, weights, portfolio_value, positions, reserved_notional=0.0):
        if portfolio_value <= 0:
            return weights, "portfolio_value_non_positive"
        if len(weights.tickers) > self.max_symbol_count:
            return weights, "max_symbol_count"
        gross = reserved_notional + sum(abs(float(p.get("market_value", 0.0) or 0.0)) for p in positions.values())
        if gross / portfolio_value > self.max_total_exposure_pct:
            scale = self.max_total_exposure_pct / max(gross / portfolio_value, 1e-9)
            return type(weights)(
                weights=weights.weights * scale,
                tickers=weights.tickers,
                timestamp=getattr(weights, "timestamp", None),
                metadata=getattr(weights, "metadata", None),
            ), None
        return weights, None


class RiskEngine:
    def __init__(self, max_position_pct=0.10, max_exposure_pct=0.50, max_symbol_concentration_pct=0.20):
        limits = {
            "max_position_pct": max_position_pct,
            "max_exposure_pct": max_exposure_pct,
            "max_symbol_concentration_pct": max_symbol_concentration_pct,
        }
        values = np.asarray(list(limits.values()), dtype=float)
        if not np.isfinite(values).all():
            raise ValueError("Risk engine limits must be finite")
        for name, value in limits.items():
            if not 0 <= value <= 1:
                raise ValueError(f"{name} must be in [0, 1]")
        self.max_position_pct = float(max_position_pct)
        self.max_exposure_pct = float(max_exposure_pct)
        self.max_symbol_concentration_pct = float(max_symbol_concentration_pct)

    def _portfolio_gross(self, positions, reserved_notional=0.0):
        gross = reserved_notional
        for pos in positions.values():
            gross += abs(float(pos.get("market_value", 0.0) or 0.0))
        return gross

    def allow(self, ticker, target_notional, portfolio_value, positions, reserved_notional=0.0):
        # This is a safety boundary: never let NaN/inf propagate through min/max,
        # where comparisons can silently produce an apparently allowed target.
        try:
            target_notional = float(target_notional)
            portfolio_value = float(portfolio_value)
            reserved_notional = float(reserved_notional)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("Risk inputs must be numeric and finite") from exc
        if not np.isfinite(target_notional):
            raise ValueError("target_notional must be finite")
        if not np.isfinite(portfolio_value):
            raise ValueError("portfolio_value must be finite")
        if not np.isfinite(reserved_notional) or reserved_notional < 0:
            raise ValueError("reserved_notional must be finite and non-negative")
        if portfolio_value <= 0:
            return 0.0, "portfolio_value_non_positive"

        # Validate every exposure input, not only the target symbol. A malformed
        # holding must stop sizing rather than be treated as zero or ignored.
        for symbol, position in positions.items():
            try:
                market_value = float(position.get("market_value", 0.0) or 0.0)
            except (AttributeError, TypeError, ValueError, OverflowError) as exc:
                raise ValueError(
                    f"Position exposure must be numeric for {symbol}"
                ) from exc
            if not np.isfinite(market_value):
                raise ValueError(f"Position exposure must be finite for {symbol}")

        if abs(target_notional) < 1e-12:
            # A zero target is an intentional request to close an existing position.
            return 0.0, None
        current = abs(float(positions.get(ticker, {}).get("market_value", 0.0) or 0.0))
        # Gross exposure includes open-order reserves and every current position.
        gross = self._portfolio_gross(positions, reserved_notional=reserved_notional)
        max_position = portfolio_value * self.max_position_pct
        # The target replaces the existing position for this symbol, so add its
        # current absolute exposure back when calculating the available budget.
        max_exposure = portfolio_value * self.max_exposure_pct - gross
        max_symbol = portfolio_value * self.max_symbol_concentration_pct

        allowed = min(
            abs(target_notional),
            max_position,
            max_symbol,
            max(0.0, max_exposure + current),
        )
        if allowed <= 0:
            return 0.0, "risk_block"
        return allowed if target_notional >= 0 else -allowed, None


class BrokerExecutor:
    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        base_url: str = "https://paper-api.alpaca.markets",
        paper_trading: bool = True,
        initial_capital: float = 500000.0,
        configured_symbols: Optional[List[str]] = None,
    ):
        # This executor is intentionally Paper-only. Reject unsafe configuration
        # before credentials, SDK clients, persistence, or any broker interaction.
        if paper_trading is not True:
            raise ValueError("BrokerExecutor is Paper-only; paper_trading must be exactly True")
        if base_url != PAPER_API_BASE_URL:
            raise ValueError(
                "BrokerExecutor accepts only the exact Alpaca Paper endpoint "
                f"{PAPER_API_BASE_URL!r}"
            )

        # The executor owns the final safety boundary. Standalone callers may
        # provide the configured model universe explicitly or through the
        # deployment environment; target weights are never a trusted substitute.
        if configured_symbols is None:
            environment_symbols = os.getenv("STMAA_CONFIGURED_SYMBOLS")
            if environment_symbols is not None:
                configured_symbols = environment_symbols.split(",")
        if configured_symbols is None:
            self.configured_symbols = None
        else:
            if isinstance(configured_symbols, str):
                configured_symbols = configured_symbols.split(",")
            if not isinstance(configured_symbols, (list, tuple, set)):
                raise ValueError("Configured corporate-action universe must be a sequence of symbols")
            normalized_symbols = []
            for symbol in configured_symbols:
                if not isinstance(symbol, str) or not symbol.strip():
                    raise ValueError("Configured corporate-action universe contains an invalid symbol")
                normalized_symbols.append(symbol.strip().upper())
            if len(normalized_symbols) != len(set(normalized_symbols)):
                raise ValueError("Configured corporate-action universe contains duplicate symbols")
            self.configured_symbols = tuple(normalized_symbols)

        self.api_key = api_key or os.getenv("ALPACA_API_KEY", "")
        self.api_secret = api_secret or os.getenv("ALPACA_API_SECRET", "")
        self.base_url = base_url
        self.paper_trading = paper_trading
        self.initial_capital = initial_capital
        self.logger = logging.getLogger(__name__)
        self._execution_lock_local = threading.local()

        try:
            self.execution_lock_timeout_seconds = float(
                os.getenv("STMAA_BROKER_EXECUTION_LOCK_TIMEOUT_SECONDS", "30")
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "STMAA_BROKER_EXECUTION_LOCK_TIMEOUT_SECONDS must be a finite positive number"
            ) from exc
        if not np.isfinite(self.execution_lock_timeout_seconds) or self.execution_lock_timeout_seconds <= 0:
            raise ValueError(
                "STMAA_BROKER_EXECUTION_LOCK_TIMEOUT_SECONDS must be a finite positive number"
            )

        if not self.api_key or not self.api_secret:
            raise ValueError(
                "ALPACA_API_KEY e ALPACA_API_SECRET devono essere impostati nel .env"
            )

        self.trading_client = TradingClient(
            api_key=self.api_key,
            secret_key=self.api_secret,
            paper=True,
            url_override=PAPER_API_BASE_URL,
        )
        self._paper_client_identity = id(self.trading_client)
        self._paper_endpoint_verified = True

        self.alpaca_feed = AlpacaFeed(
            api_key=self.api_key,
            api_secret=self.api_secret,
        )

        self.metrics = PortfolioMetrics(initial_capital=initial_capital)

        # Only a successful current broker response can verify account identity.
        # This is intentionally transient and resets on every process start.
        self._current_account_identity_verified = False
        repository_root = Path(__file__).resolve().parents[2]
        default_state_file = repository_root / "logs" / "execution" / "broker_state.json"
        configured_state_file = os.getenv("STMAA_BROKER_STATE_FILE")
        if configured_state_file:
            configured_path = Path(configured_state_file).expanduser()
            self.state_file = (
                configured_path
                if configured_path.is_absolute()
                else repository_root / configured_path
            )
        else:
            self.state_file = default_state_file
        # Resolve relative paths and symlink aliases so cooperating processes
        # derive the same execution/persistence lock paths for one state file.
        self.state_file = _canonicalize_state_file_path(self.state_file)
        self._persisted_state_revision = -1
        self._state_persistence_failed = False
        self.state = {
            "state_schema_version": 2,
            "state_revision": 0,
            "last_execution_time": None,
            "total_orders": 0,
            "configured_initial_capital": float(initial_capital),
            "account_mode": "paper" if paper_trading else "live",
            "account_id": None,
            # Legacy-compatible value only; its source explicitly marks it unverified.
            "portfolio_value": float(initial_capital),
            "portfolio_value_source": "configured_initial_capital",
            "equity": None,
            "equity_source": None,
            "last_verified_equity": None,
            "last_verified_portfolio_value": None,
            "account_snapshot_at": None,
            "account_data_fresh": False,
            "equity_history": [],
            # Safety-critical maps must exist in every supported persisted schema.
            "emergency_liquidation_pending": {},
            "emergency_liquidation_unresolved": {},
            "order_submission_unresolved": {},
            "order_submission_confirmed": {},
            "corporate_action_reconciliation": {},
            "opg_batches": {},
        }
        self._load_state()
        self._restore_metrics_history()
        # A persisted snapshot is historical evidence, never a fresh broker read.
        self.state["account_data_fresh"] = False
        self.logger.info(f"✅ BrokerExecutor inizializzato – paper={paper_trading}")

    def _assert_paper_mutation_allowed(self) -> None:
        """Fail closed unless this exact executor and SDK client target Alpaca Paper."""
        if getattr(self, "paper_trading", None) is not True:
            raise RuntimeError("Broker mutation blocked: executor is not Paper-only")
        if getattr(self, "base_url", None) != PAPER_API_BASE_URL:
            raise RuntimeError("Broker mutation blocked: configured endpoint is not Alpaca Paper")
        state = getattr(self, "state", None)
        if not isinstance(state, dict) or state.get("account_mode") != "paper":
            raise RuntimeError("Broker mutation blocked: persisted account mode is not Paper")
        client = getattr(self, "trading_client", None)
        if (
            getattr(self, "_paper_endpoint_verified", False) is not True
            or client is None
            or id(client) != getattr(self, "_paper_client_identity", None)
        ):
            raise RuntimeError("Broker mutation blocked: Paper client identity is not verified")
        client_base_url = getattr(client, "_base_url", None)
        if str(client_base_url).rstrip("/") != PAPER_API_BASE_URL:
            raise RuntimeError("Broker mutation blocked: SDK client endpoint is not Alpaca Paper")

    def _load_state(self):
        if not self.state_file.exists():
            self._save_state()
            return

        try:
            with open(self.state_file, encoding="utf-8") as state_file:
                loaded_state = json.load(state_file)
            if not isinstance(loaded_state, dict):
                raise ValueError("persisted broker state must be a JSON object")
            schema_version = loaded_state.get("state_schema_version")
            if type(schema_version) is not int or schema_version not in {1, 2}:
                raise ValueError(
                    "persisted broker state schema is missing or unsupported; manual reconciliation required"
                )
            state_revision = loaded_state.get("state_revision", 0)
            if type(state_revision) is not int or state_revision < 0:
                raise ValueError("persisted broker state revision is invalid")
            self._persisted_state_revision = state_revision
            safety_maps = (
                "emergency_liquidation_pending",
                "emergency_liquidation_unresolved",
                "order_submission_unresolved",
                "order_submission_confirmed",
            )
            for key in safety_maps:
                if key not in loaded_state or not isinstance(loaded_state[key], dict):
                    raise ValueError(
                        f"persisted broker state is incomplete: {key} must be present as an object"
                    )
            expected_mode = self.state["account_mode"]
            stored_mode = loaded_state.get("account_mode")
            if stored_mode is not None and stored_mode != expected_mode:
                raise ValueError(
                    f"persisted broker state belongs to {stored_mode} account mode, not {expected_mode}"
                )
            if stored_mode is None and any(
                loaded_state.get(key)
                for key in ("emergency_liquidation_pending", "emergency_liquidation_unresolved")
            ):
                raise ValueError(
                    "legacy liquidation state has no account-mode provenance; manual reconciliation required"
                )
            for key in ("emergency_liquidation_pending", "emergency_liquidation_unresolved"):
                if key in loaded_state and not isinstance(loaded_state[key], dict):
                    raise ValueError(f"persisted broker state field {key} must be an object")
            for key in ("order_submission_unresolved", "order_submission_confirmed"):
                if key in loaded_state:
                    entries = loaded_state[key]
                    if not isinstance(entries, dict):
                        raise ValueError(f"persisted broker state field {key} must be an object")
                    if any(not isinstance(entry, dict) for entry in entries.values()):
                        raise ValueError(
                            f"persisted broker state entries in {key} must be objects"
                        )
            if "corporate_action_reconciliation" in loaded_state and not isinstance(
                loaded_state["corporate_action_reconciliation"], dict
            ):
                raise ValueError("persisted corporate-action reconciliation state must be an object")
            if schema_version == 2 and (
                "opg_batches" not in loaded_state
                or not isinstance(loaded_state["opg_batches"], dict)
                or any(not isinstance(batch, dict) for batch in loaded_state["opg_batches"].values())
            ):
                raise ValueError("persisted OPG batch state must be an object map of batch records")
            if schema_version == 1:
                loaded_state["state_schema_version"] = 2
                loaded_state["opg_batches"] = {}
            pending_liquidations = loaded_state.get("emergency_liquidation_pending", {})
            unresolved_liquidations = loaded_state.get("emergency_liquidation_unresolved", {})
            overlapping_symbols = set(pending_liquidations).intersection(unresolved_liquidations)
            if overlapping_symbols:
                raise ValueError(
                    "persisted liquidation state has symbols recorded as both pending and unresolved"
                )
        except Exception as exc:
            # A corrupt/unreadable state may contain the only record of an accepted
            # close request. Never continue with an empty in-memory state: that could
            # cause a duplicate close after restart.
            raise RuntimeError(
                "Unable to load persisted broker state; refusing execution with unverified liquidation state"
            ) from exc

        self.state.update(loaded_state)
        if schema_version == 1:
            # The v1 payload is retained verbatim except for the explicit schema
            # marker and new empty OPG map. _save_state uses the existing revision
            # check, temp-file fsync, atomic replace, and directory fsync.
            try:
                with self.execution_lock():
                    self._save_state()
            except Exception as exc:
                raise RuntimeError(
                    "Unable to migrate persisted broker state from schema v1; refusing execution"
                ) from exc
        self.logger.info("Broker state loaded from persistence")

    def _restore_metrics_history(self):
        """Restore only explicitly persisted equity observations, never configured capital."""
        history = self.state.get("equity_history", [])
        if not isinstance(history, list):
            raise RuntimeError("Persisted equity history must be a list")
        if not history:
            return
        if self.state.get("metrics_account_mode") != self.state.get("account_mode"):
            # Legacy history without account-mode provenance cannot be mixed into
            # this account's returns or drawdown calculations.
            self.state["equity_history"] = []
            self.state.pop("metrics_baseline_source", None)
            return
        stored_metrics_account_id = self.state.get("metrics_account_id")
        stored_account_id = self.state.get("account_id")
        # Account mode alone is insufficient provenance: never restore metrics if
        # either side of the account identity comparison is missing.
        if not stored_metrics_account_id or not stored_account_id:
            self.state["equity_history"] = []
            self.state.pop("metrics_baseline_source", None)
            self.state.pop("metrics_account_mode", None)
            self.state.pop("metrics_account_id", None)
            return
        if stored_metrics_account_id != stored_account_id:
            raise RuntimeError("Persisted metrics history belongs to a different broker account")
        try:
            values = [float(value) for value in history]
        except (TypeError, ValueError) as exc:
            raise RuntimeError("Persisted equity history contains invalid values") from exc
        if not all(np.isfinite(value) and value > 0 for value in values):
            raise RuntimeError("Persisted equity history contains non-positive or non-finite values")
        self.metrics = PortfolioMetrics(initial_capital=values[0])
        for value in values[1:]:
            self.metrics.update(value)
        self.state["equity_history"] = values
        self.state["metrics_baseline_source"] = "verified_account"
        self.state["metrics_account_mode"] = self.state.get("account_mode")
        self.state["metrics_account_id"] = self.state.get("account_id")

    def _ensure_verified_metrics_baseline(self, equity: float):
        """Anchor metrics to verified broker equity with matching account identity."""
        account_id_verified = self._current_account_identity_verified is True
        account_id = self.state.get("account_id") if account_id_verified else None
        if (
            self.state.get("metrics_baseline_source") == "verified_account"
            and account_id
            and self.state.get("metrics_account_id") == account_id
            and self.state.get("metrics_account_mode") == self.state.get("account_mode")
        ):
            return
        value = float(equity)
        if not np.isfinite(value) or value <= 0:
            raise RuntimeError("Cannot establish metrics baseline from invalid broker equity")
        self.metrics = PortfolioMetrics(initial_capital=value)
        self.state["equity_history"] = [value]
        self.state["metrics_baseline_source"] = "verified_account"
        self.state["metrics_account_mode"] = self.state.get("account_mode")
        self.state["metrics_account_id"] = account_id
        self._save_state()

    def _persist_metrics_history(self):
        """Persist metrics provenance/history without leaving memory ahead of disk."""
        keys = (
            "equity_history",
            "metrics_baseline_source",
            "metrics_account_mode",
            "metrics_account_id",
        )
        previous = {key: (key in self.state, self.state.get(key)) for key in keys}
        self.state["equity_history"] = list(self.metrics.equity_curve)
        self.state["metrics_baseline_source"] = "verified_account"
        self.state["metrics_account_mode"] = self.state.get("account_mode")
        self.state["metrics_account_id"] = (
            self.state.get("account_id")
            if self._current_account_identity_verified is True
            else None
        )
        try:
            self._save_state()
        except Exception:
            for key, (existed, value) in previous.items():
                if existed:
                    self.state[key] = value
                else:
                    self.state.pop(key, None)
            raise

    @staticmethod
    def _normalize_daily_bar_timestamp(bar_timestamp: str) -> str:
        """Validate and canonicalize a timezone-aware daily-bar timestamp."""
        if not isinstance(bar_timestamp, str) or not bar_timestamp.strip():
            raise ValueError("Daily decision requires a non-empty bar timestamp")
        try:
            parsed = datetime.fromisoformat(bar_timestamp.replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise ValueError("Daily decision bar timestamp is invalid") from exc
        if parsed.tzinfo is None:
            raise ValueError("Daily decision bar timestamp must be timezone-aware")
        return parsed.astimezone(timezone.utc).isoformat()

    def claim_daily_decision(self, bar_timestamp: str, symbols: List[str]) -> bool:
        """Durably claim one PPO decision for a daily bar before inference starts.

        Returns False when this exact bar was already completed or reconciled.
        Ambiguous or legacy execution state is persisted as blocked and never
        causes an automatic resubmission.
        """
        normalized_timestamp = self._normalize_daily_bar_timestamp(bar_timestamp)
        if not isinstance(symbols, (list, tuple)) or not symbols:
            raise ValueError("Daily decision requires a non-empty symbol universe")
        normalized_symbols = [str(symbol).strip().upper() for symbol in symbols]
        if any(not symbol for symbol in normalized_symbols) or len(set(normalized_symbols)) != len(normalized_symbols):
            raise ValueError("Daily decision symbols must be non-empty and unique")

        with self.execution_lock():
            marker = self.state.get("daily_decision")
            if marker is None:
                legacy_history = any((
                    self.state.get("last_execution_time"),
                    bool(self.state.get("total_orders")),
                    bool(self.state.get("order_submission_unresolved")),
                    bool(self.state.get("order_submission_confirmed")),
                    bool(self.state.get("emergency_liquidation_pending")),
                    bool(self.state.get("emergency_liquidation_unresolved")),
                ))
                if legacy_history:
                    self.state["daily_decision"] = {
                        "bar_timestamp": normalized_timestamp,
                        "symbols": normalized_symbols,
                        "status": "reconciliation_required",
                        "reason": "legacy_execution_history_without_daily_decision_marker",
                        "claimed_at": datetime.now(timezone.utc).isoformat(),
                    }
                    self._save_state()
                    raise RuntimeError(
                        "Daily decision blocked: legacy broker execution history has no daily-bar marker; "
                        "read-only broker reconciliation is required before continuing"
                    )
            else:
                if not isinstance(marker, dict):
                    raise RuntimeError("Persisted daily decision marker is invalid; manual reconciliation required")
                status = marker.get("status")
                if status in {"started", "reconciliation_required"}:
                    raise RuntimeError(
                        "Daily decision blocked: a previous cycle is unresolved; "
                        "verified broker reconciliation is required before any new submission"
                    )
                # Operational maintenance is deliberately independent of PPO:
                # every five-minute poll reconciles broker orders even when the
                # daily bar has already been processed.
                broker_state = self.reconcile_broker_state_read_only()
                previous_timestamp = self._normalize_daily_bar_timestamp(marker.get("bar_timestamp"))
                if normalized_timestamp == previous_timestamp:
                    return False
                if normalized_timestamp < previous_timestamp:
                    raise RuntimeError(
                        "Daily decision blocked: market data bar is older than the last processed bar"
                    )
                if status not in {"completed", "reconciled"}:
                    raise RuntimeError("Persisted daily decision status is unknown; manual reconciliation required")
                if not broker_state["safe_for_new_decision"]:
                    raise RuntimeError(
                        "Daily decision blocked: broker has unresolved, active, or open orders; "
                        "no new PPO target will be generated or submitted"
                    )

            if marker is None:
                broker_state = self.reconcile_broker_state_read_only()
                if not broker_state["safe_for_new_decision"]:
                    raise RuntimeError(
                        "Daily decision blocked: broker has unresolved, active, or open orders; "
                        "no new PPO target will be generated or submitted"
                    )

            self.state["daily_decision"] = {
                "bar_timestamp": normalized_timestamp,
                "symbols": normalized_symbols,
                "status": "started",
                "claimed_at": datetime.now(timezone.utc).isoformat(),
                "reconciliation_required": False,
            }
            self._save_state()
            return True

    def complete_daily_decision(self, bar_timestamp: str) -> None:
        """Mark a claimed daily decision complete only after execution and snapshot persist."""
        normalized_timestamp = self._normalize_daily_bar_timestamp(bar_timestamp)
        with self.execution_lock():
            marker = self.state.get("daily_decision")
            if not isinstance(marker, dict) or marker.get("status") != "started":
                raise RuntimeError("Cannot complete a daily decision that is not durably claimed")
            if self._normalize_daily_bar_timestamp(marker.get("bar_timestamp")) != normalized_timestamp:
                raise RuntimeError("Daily decision completion timestamp does not match its durable claim")
            updated = dict(marker)
            updated.update({
                "status": "completed",
                "completed_at": datetime.now(timezone.utc).isoformat(),
            })
            self.state["daily_decision"] = updated
            self._save_state()

    def reconcile_broker_state_read_only(self) -> Dict:
        """Run periodic broker-order reconciliation without any broker mutation."""
        with self.execution_lock():
            self._reconcile_unresolved_submissions()
            self._refresh_confirmed_submission_statuses()
            self._reconcile_terminal_order_positions()
            opg_batches = self.state.get("opg_batches", {})
            opg_report = {"batches_checked": 0, "batches_completed": 0, "batches_blocked": 0}
            if opg_batches:
                if getattr(self, "_current_account_identity_verified", False) is True:
                    opg_report = self._recover_opg_batches()
                else:
                    blocking_opg_batches = [
                        batch for batch in opg_batches.values()
                        if not isinstance(batch, dict)
                        or batch.get("status") not in {"completed", "no_orders"}
                    ] if isinstance(opg_batches, dict) else [None]
                    opg_report = {
                        "batches_checked": len(blocking_opg_batches),
                        "batches_completed": 0,
                        "batches_blocked": len(blocking_opg_batches),
                    }
            unresolved = self.state.get("order_submission_unresolved", {})
            confirmed = self.state.get("order_submission_confirmed", {})
            terminal = {"filled", "canceled", "cancelled", "expired", "rejected"}
            active_confirmed = {
                client_id: order for client_id, order in confirmed.items()
                if self._status_text(order.get("status", "unknown"), default="unknown") not in terminal
                or order.get("position_reconciled") is not True
            }
            open_orders = self._get_open_orders()
            return {
                "safe_for_new_decision": not (
                    unresolved or active_confirmed or open_orders or opg_report["batches_blocked"]
                ),
                "opg_batches_checked": opg_report["batches_checked"],
                "opg_batches_blocked": opg_report["batches_blocked"],
                "unresolved_submissions": len(unresolved),
                "active_or_unreconciled_confirmed_orders": len(active_confirmed),
                "open_orders": len(open_orders),
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "read_only": True,
            }

    def reconcile_daily_decision_if_required(self) -> bool:
        """Resolve an interrupted daily cycle using read-only broker evidence only.

        No order is submitted, cancelled, replaced, or liquidated here. The bar
        whose decision was interrupted remains consumed after reconciliation.
        """
        with self.execution_lock():
            marker = self.state.get("daily_decision")
            if not isinstance(marker, dict) or marker.get("status") not in {
                "started", "reconciliation_required"
            }:
                return False
            if marker.get("reason") == "legacy_execution_history_without_daily_decision_marker":
                raise RuntimeError(
                    "Daily decision remains blocked: legacy execution history cannot be mapped "
                    "to a specific daily bar automatically; operator must verify historical "
                    "broker orders and positions before establishing a new baseline"
                )

            # Existing reconciliation paths only read broker state and persist
            # verified status transitions. Any mismatch or unavailable read aborts.
            report = self.reconcile_broker_state_read_only()
            if not report["safe_for_new_decision"]:
                raise RuntimeError(
                    "Daily decision remains blocked: broker reconciliation is not conclusive "
                    f"(unresolved={report['unresolved_submissions']}, "
                    f"active_or_unreconciled={report['active_or_unreconciled_confirmed_orders']}, "
                    f"open_orders={report['open_orders']})"
                )

            updated = dict(marker)
            updated.update({
                "status": "reconciled",
                "reconciled_at": datetime.now(timezone.utc).isoformat(),
                "reconciliation": {
                    "method": "broker_order_and_position_read_only",
                    "unresolved_submissions": 0,
                    "active_or_unreconciled_confirmed_orders": 0,
                    "open_orders": 0,
                },
            })
            self.state["daily_decision"] = updated
            self._save_state()
            return True

    def _save_state(self):
        """Durably persist state and reject stale writers across processes."""
        if self._state_persistence_failed:
            raise RuntimeError("Broker state persistence previously failed; refusing further execution")
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.state_file.with_name(self.state_file.name + ".lock")
        temp_path = None
        try:
            with open(lock_path, "a+", encoding="utf-8") as lock_file:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                disk_revision = -1
                if self.state_file.exists():
                    with open(self.state_file, encoding="utf-8") as existing_file:
                        existing_state = json.load(existing_file)
                    if not isinstance(existing_state, dict):
                        raise RuntimeError("Persisted broker state is not a JSON object")
                    disk_revision = existing_state.get("state_revision", 0)
                    if type(disk_revision) is not int or disk_revision < 0:
                        raise RuntimeError("Persisted broker state revision is invalid")
                if disk_revision != self._persisted_state_revision:
                    raise RuntimeError(
                        "Persisted broker state changed in another process; refusing stale overwrite"
                    )
                next_revision = disk_revision + 1
                payload = copy.deepcopy(self.state)
                payload["state_revision"] = next_revision
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    dir=self.state_file.parent,
                    prefix=f".{self.state_file.name}.",
                    suffix=".tmp",
                    delete=False,
                ) as state_file:
                    temp_path = Path(state_file.name)
                    json.dump(payload, state_file)
                    state_file.flush()
                    os.fsync(state_file.fileno())
                os.replace(temp_path, self.state_file)
                # The file contents are durable after the temporary-file fsync,
                # but the rename itself is not durable until its parent directory
                # entry is synced. If this fails, keep the executor fail-closed:
                # after os.replace the commit outcome is ambiguous across a crash.
                directory_fd = os.open(
                    self.state_file.parent,
                    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
                )
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
                self.state["state_revision"] = next_revision
                self._persisted_state_revision = next_revision
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        except Exception:
            self._state_persistence_failed = True
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    self.logger.exception("Unable to remove temporary broker state file %s", temp_path)
            raise

    def resolve_corporate_action(
        self,
        activity_id: str,
        symbol: str,
        evidence: str,
        actor: str,
        reason: str,
    ) -> Dict:
        """Record an operator-evidenced corporate-action symbol resolution.

        The shared execution lock prevents concurrent state writers. This method
        performs no broker API request and cannot submit or cancel orders.
        """
        with self.execution_lock():
            reconciler = CorporateActionReconciler(
                activity_page_reader=lambda _params: [],
                state=self.state,
                persist=self._save_state,
            )
            return reconciler.resolve_unresolved_activity(
                activity_id=activity_id,
                symbol=symbol,
                evidence=evidence,
                actor=actor,
                reason=reason,
            )

    def reconcile_corporate_actions(self, configured_symbols, positions=None) -> Dict:
        """Read Alpaca corporate-action activities and reconcile them with holdings.

        TradingClient's REST transport targets the retail Trading API account
        endpoint (/v2/account/activities). This is deliberately read-only.
        """
        if positions is None:
            positions = self.get_positions()
        reconciler = CorporateActionReconciler(
            activity_page_reader=lambda params: self.trading_client.get(
                "/account/activities", params
            ),
            state=self.state,
            persist=self._save_state,
        )
        return reconciler.reconcile(configured_symbols, positions)

    def get_account_status(self) -> Dict:
        """Refresh and persist account data under the shared broker execution lock."""
        with self.execution_lock():
            return self._get_account_status_locked()

    def _get_account_status_locked(self) -> Dict:
        """Perform the account refresh while the caller owns the execution lock."""
        self._current_account_identity_verified = False
        try:
            account = self.trading_client.get_account()
            raw_status = getattr(account, "status", None)
            status_value = getattr(raw_status, "value", raw_status)
            account_status = str(status_value or "").strip().lower().rsplit(".", 1)[-1]
            trading_blocked = getattr(account, "trading_blocked", None)
            if account_status != "active":
                raise ValueError(
                    f"Broker account status is not active ({account_status or 'unknown'})"
                )
            if trading_blocked is not False:
                raise ValueError(
                    "Broker account trading_blocked flag is true or unavailable"
                )

            raw_financial_values = {
                "cash": account.cash,
                "portfolio_value": account.portfolio_value,
                "buying_power": account.buying_power,
                "equity": account.equity,
            }
            if any(isinstance(value, bool) for value in raw_financial_values.values()):
                raise ValueError("Broker account returned boolean financial values")

            data = {
                "cash": float(raw_financial_values["cash"]),
                "portfolio_value": float(raw_financial_values["portfolio_value"]),
                "buying_power": float(raw_financial_values["buying_power"]),
                "equity": float(raw_financial_values["equity"]),
                "daytrade_count": account.daytrade_count,
                "status": account_status,
                "trading_blocked": trading_blocked,
                "account_data_fresh": True,
                "account_data_source": "alpaca_account",
                "account_snapshot_at": datetime.now(timezone.utc).isoformat(),
                "account_id": str(getattr(account, "id", "") or "") or None,
                "account_identity_verified": bool(
                    str(getattr(account, "id", "") or "").strip()
                ),
            }
            stored_account_id = self.state.get("account_id")
            if stored_account_id and data["account_id"] and stored_account_id != data["account_id"]:
                raise ValueError("Broker account ID differs from persisted state; refusing to mix account history")
            numeric_values = (data["cash"], data["portfolio_value"], data["buying_power"], data["equity"])
            if not all(np.isfinite(value) for value in numeric_values):
                raise ValueError("Broker account returned non-finite account values")
            if data["portfolio_value"] <= 0 or data["equity"] <= 0 or data["buying_power"] < 0:
                raise ValueError("Broker account returned invalid portfolio value, equity, or buying power")

            self._current_account_identity_verified = bool(data["account_id"])
            if not self._current_account_identity_verified:
                # Without a current account ID, old metrics history cannot safely
                # be attributed to the account behind this snapshot. Re-anchor to
                # verified equity but leave the history unlinked for restart.
                self.metrics = PortfolioMetrics(initial_capital=data["equity"])
                self.state["equity_history"] = [data["equity"]]
                self.state["metrics_baseline_source"] = "verified_account"
                self.state["metrics_account_mode"] = self.state.get("account_mode")
                self.state["metrics_account_id"] = None

            # Keep the broker's portfolio_value and equity separate. Risk budgets
            # use equity; persisted values carry provenance and a broker timestamp.
            self.state.update({
                "portfolio_value": data["portfolio_value"],
                "portfolio_value_source": "alpaca_account",
                "last_verified_portfolio_value": data["portfolio_value"],
                # Preserve the last known ID for explicit account-switch detection,
                # but do not treat it as verified for this snapshot when the broker
                # omitted its current ID.
                "account_id": data["account_id"] or self.state.get("account_id"),
                "equity": data["equity"],
                "equity_source": "alpaca_account",
                "last_verified_equity": data["equity"],
                "account_snapshot_at": data["account_snapshot_at"],
                "account_data_fresh": True,
            })
            self._save_state()
            return data
        except Exception as e:
            self._current_account_identity_verified = False
            self.logger.error(f"get_account_status error; broker equity is unverified: {e}")
            # Retain the last verified snapshot as historical data, but mark it stale
            # in memory and on disk so a restart cannot mistake it for a fresh read.
            self.state["account_data_fresh"] = False
            try:
                self._save_state()
            except Exception as persist_exc:
                self.logger.error("Unable to persist stale account-data status: %s", persist_exc)
            # Do not return configured or cached amounts as if they were current.
            return {
                "cash": None,
                "portfolio_value": None,
                "buying_power": None,
                "equity": None,
                "account_data_fresh": False,
                "account_data_source": None,
                "account_snapshot_at": self.state.get("account_snapshot_at"),
                "error": str(e),
            }

    def _get_open_orders(self):
        """Return a structurally verified open-order snapshot or fail closed."""
        try:
            orders = self.trading_client.get_orders(
                filter=GetOrdersRequest(status=QueryOrderStatus.OPEN)
            )
            if not isinstance(orders, (list, tuple)):
                raise ValueError("Broker returned an invalid open-orders response shape")

            seen_ids = set()
            active_statuses = {
                "new", "partially_filled", "done_for_day", "pending_cancel",
                "pending_replace", "accepted", "pending_new",
                "accepted_for_bidding", "stopped", "suspended", "held",
            }
            for order in orders:
                order_id = str(getattr(order, "id", "") or "").strip()
                symbol = str(getattr(order, "symbol", "") or "").strip()
                status = self._status_text(getattr(order, "status", None), default="")
                if not order_id or not symbol or not status:
                    raise ValueError(
                        "Broker returned an open order without a valid ID, symbol, or status"
                    )
                if status not in active_statuses:
                    raise ValueError(
                        f"Broker open-order snapshot contains unknown or non-active status for {order_id}"
                    )
                if order_id in seen_ids:
                    raise ValueError("Broker returned duplicate open-order IDs")
                seen_ids.add(order_id)
            return list(orders)
        except Exception as exc:
            self.logger.error(
                "Open-order read/validation failed; refusing execution: %s", exc
            )
            raise RuntimeError(
                "Unable to retrieve or validate broker open orders; refusing execution"
            ) from exc

    def _order_status_value(self, order) -> str:
        return self._status_text(getattr(order, "status", ""))

    def _estimate_order_notional(self, order, prices: Optional[Dict[str, float]] = None) -> float:
        """Estimate remaining exposure; fail closed when an active order is unpriceable."""
        prices = prices or {}
        status = self._order_status_value(order)
        if status in {
            "filled", "canceled", "cancelled", "expired", "rejected",
        }:
            return 0.0

        raw_qty = getattr(order, "qty", None)
        raw_filled_qty = getattr(order, "filled_qty", None)
        raw_notional = getattr(order, "notional", None)

        def parse_positive(value, field: str) -> float:
            try:
                parsed = float(value)
            except (TypeError, ValueError) as exc:
                raise RuntimeError(
                    f"Cannot estimate exposure for active order: invalid {field}"
                ) from exc
            if not np.isfinite(parsed) or parsed <= 0:
                raise RuntimeError(
                    f"Cannot estimate exposure for active order: invalid {field}"
                )
            return parsed

        def parse_nonnegative(value, field: str) -> float:
            try:
                parsed = float(value)
            except (TypeError, ValueError) as exc:
                raise RuntimeError(
                    f"Cannot estimate exposure for active order: invalid {field}"
                ) from exc
            if not np.isfinite(parsed) or parsed < 0:
                raise RuntimeError(
                    f"Cannot estimate exposure for active order: invalid {field}"
                )
            return parsed

        qty_missing = raw_qty in (None, "")
        filled_qty = 0.0 if raw_filled_qty in (None, "") else parse_nonnegative(
            raw_filled_qty, "filled_qty"
        )
        notional = None
        if raw_notional not in (None, ""):
            notional = parse_positive(raw_notional, "notional")

        if qty_missing:
            # Notional-based orders can omit qty. If any quantity was filled,
            # the remaining notional cannot safely be inferred without qty.
            if notional is not None and filled_qty == 0:
                return notional
            raise RuntimeError(
                "Cannot estimate exposure for active order: qty and remaining notional unavailable"
            )

        qty = parse_positive(raw_qty, "qty")
        if filled_qty > qty:
            raise RuntimeError(
                "Cannot estimate exposure for active order: filled_qty exceeds qty"
            )
        remaining_qty = qty - filled_qty
        if remaining_qty == 0:
            # An order returned in the active/open snapshot with no remaining
            # quantity is internally inconsistent (e.g. a stale status snapshot).
            # Do not silently drop its exposure while positions may lag fills.
            raise RuntimeError(
                "Cannot estimate exposure for active order: no remaining quantity "
                "despite non-terminal status"
            )

        # Reserve the full requested notional for every active order, not only its
        # unfilled remainder. Position snapshots can lag partial fills; counting
        # only the remainder could understate gross exposure until the fill appears
        # in get_all_positions(). This is intentionally conservative and may
        # temporarily overstate exposure when the broker already reflects the fill.
        limit_price = getattr(order, "limit_price", None)
        if limit_price not in (None, ""):
            price = parse_positive(limit_price, "limit_price")
            return qty * price

        symbol = str(getattr(order, "symbol", "") or "").strip().upper()
        if symbol and symbol in prices:
            price = parse_positive(prices[symbol], f"market price for {symbol}")
            return qty * price

        # Broker responses may provide the original order notional without a price.
        # Reserve the original notional until the order is terminal and positions
        # have been reconciled in a separate read cycle.
        if notional is not None:
            return notional

        raise RuntimeError(
            f"Cannot estimate exposure for active order {symbol or '<unknown symbol>'}: "
            "no valid limit price, market price, or notional"
        )

    def _get_reserved_buy_notional(self, orders, prices: Optional[Dict[str, float]] = None) -> float:
        """Reserve only active BUY orders against gross long exposure.

        SELL orders do not create gross exposure and must never consume the
        operational BUY budget. Missing side metadata is conservatively treated
        as BUY; an explicit unknown side blocks new exposure.
        """
        if not isinstance(orders, (list, tuple)):
            raise RuntimeError("Cannot verify aggregate exposure: open-order snapshot is invalid")
        buy_orders = []
        for order in orders:
            symbol = str(getattr(order, "symbol", "") or "").strip().upper()
            if not symbol:
                raise RuntimeError("Cannot verify aggregate exposure: active order symbol is unknown")
            # Legacy test doubles and older snapshots may omit side; treating
            # them as BUY is conservative because their full notional is reserved.
            raw_side = getattr(order, "side", "buy")
            side = str(getattr(raw_side, "value", raw_side) or "").strip().lower()
            if side == "buy":
                buy_orders.append(order)
            elif side == "sell":
                continue
            else:
                raise RuntimeError("Cannot verify aggregate exposure: active order side is unknown")
        return self._get_reserved_notional(buy_orders, prices=prices)

    def _get_aggregate_gross_notional(
        self, positions, open_orders, prices: Optional[Dict[str, float]] = None
    ) -> tuple[float, float]:
        """Return (positions + pending BUY reserve, pending BUY reserve).

        Both DAY and OPG call this same broker-boundary calculation. Position
        marks and every active BUY must be determinable before new exposure.
        Partial fills conservatively reserve the original order notional until
        broker positions and order state are reconciled.
        """
        position_notional = calculate_position_gross_notional(positions, prices)
        pending_buy_notional = self._get_reserved_buy_notional(open_orders, prices=prices)
        gross = calculate_aggregate_gross_notional(
            position_notional=position_notional,
            pending_buy_notional=pending_buy_notional,
        )
        return float(gross), float(pending_buy_notional)

    def _get_reserved_notional(self, orders, prices: Optional[Dict[str, float]] = None) -> float:
        """Return reserved notional or raise if any active order is unestimable."""
        estimates = [
            self._estimate_order_notional(order, prices=prices)
            for order in orders
        ]
        total = float(sum(estimates))
        if not np.isfinite(total) or total < 0:
            raise RuntimeError("Cannot estimate aggregate exposure from open orders")
        return total

    def _get_peak_equity(self, portfolio_value: Optional[float] = None) -> float:
        """Return a persisted high-water mark, or initialize from verified equity.

        Configured initial capital is not evidence of historical account equity.
        """
        # A numeric legacy peak is not sufficient evidence: older versions may
        # have initialized it from configured capital or portfolio_value. Trust a
        # persisted peak only when its account provenance matches this executor.
        account_mode = self.state.get("account_mode")
        account_id = self.state.get("account_id")
        peak_source = self.state.get("risk_peak_equity_source")
        peak_mode = self.state.get("risk_peak_account_mode")
        peak_account_id = self.state.get("risk_peak_account_id")
        provenance_matches = (
            self._current_account_identity_verified is True
            and peak_source == "alpaca_account"
            and peak_mode == account_mode
            and bool(account_id)
            and bool(peak_account_id)
            and account_id == peak_account_id
        )
        verified_peak = self.state.get("risk_peak_portfolio_value") if provenance_matches else None
        legacy_peak_account_id = self.state.get("peak_equity_account_id")
        legacy_peak_is_verified = (
            self._current_account_identity_verified is True
            and self.state.get("peak_equity_source") == "verified_account"
            and self.state.get("peak_equity_account_mode") == account_mode
            and bool(account_id)
            and bool(legacy_peak_account_id)
            and account_id == legacy_peak_account_id
        )
        if verified_peak is None and legacy_peak_is_verified:
            verified_peak = self.state.get("peak_equity")
        if verified_peak is not None:
            value = float(verified_peak)
            if not np.isfinite(value) or value <= 0:
                raise RuntimeError("Persisted drawdown peak is invalid")
            return value
        if portfolio_value is None:
            stored_value = self.state.get("last_verified_equity")
            if stored_value is None or self.state.get("equity_source") != "alpaca_account":
                raise RuntimeError("Cannot establish drawdown baseline without verified account equity")
            portfolio_value = float(stored_value)
        value = float(portfolio_value)
        if not np.isfinite(value) or value <= 0:
            raise RuntimeError("Cannot establish drawdown baseline from invalid account equity")
        return value

    def _update_peak_equity(self, portfolio_value: float) -> float:
        value = float(portfolio_value)
        if not np.isfinite(value) or value <= 0:
            raise RuntimeError("Cannot update drawdown peak from invalid account equity")
        peak = max(self._get_peak_equity(value), value)
        # A verified equity value without a current account ID can initialize
        # this run's risk baseline, but it cannot be persisted as account-linked
        # high-water-mark history.
        peak_account_id = (
            self.state.get("account_id")
            if self._current_account_identity_verified is True
            else None
        )
        self.state["peak_equity"] = peak
        self.state["risk_peak_portfolio_value"] = peak
        self.state["peak_equity_source"] = "verified_account"
        self.state["risk_peak_equity_source"] = "alpaca_account"
        self.state["risk_peak_account_mode"] = self.state.get("account_mode")
        self.state["risk_peak_account_id"] = peak_account_id
        self.state["peak_equity_account_mode"] = self.state.get("account_mode")
        self.state["peak_equity_account_id"] = peak_account_id
        return peak

    def _risk_gate(self, portfolio_value: float, tradable_budget: float):
        peak = self._update_peak_equity(portfolio_value)
        dd = 0.0 if peak <= 0 else max(0.0, (peak - portfolio_value) / peak)
        if dd >= RISK_LIMITS["max_drawdown"]:
            return 0.0, f"max_drawdown:{dd:.2%}"
        max_exposure = peak * RISK_LIMITS["max_portfolio_exposure"]
        capped = min(tradable_budget, max_exposure)
        if capped <= 0:
            return 0.0, "max_exposure:0.00%"
        return capped, None

    @staticmethod
    def _status_text(status, default=""):
        """Normalize broker status strings and string-valued Enum instances."""
        if status is None:
            return default
        value = getattr(status, "value", status)
        normalized = str(value).strip().lower()
        return normalized or default

    @staticmethod
    def _validate_broker_execution_state(status, filled_qty, requested_qty):
        """Reject broker status/fill combinations that contradict each other."""
        normalized_status = BrokerExecutor._status_text(status, default="unknown")
        try:
            filled = float(filled_qty)
            requested = float(requested_qty)
        except (TypeError, ValueError, OverflowError) as exc:
            raise RuntimeError(
                "Broker execution state has invalid fill/request quantity; manual reconciliation required"
            ) from exc
        if (
            not np.isfinite(filled)
            or not np.isfinite(requested)
            or requested <= 0
            or filled < 0
        ):
            raise RuntimeError(
                "Broker execution state is malformed; manual reconciliation required"
            )
        if filled > requested + 1e-8:
            raise RuntimeError(
                "Broker order reports filled quantity greater than requested quantity"
            )
        known_statuses = {
            "new", "partially_filled", "filled", "done_for_day", "canceled",
            "cancelled", "expired", "replaced", "pending_cancel",
            "pending_replace", "accepted", "pending_new",
            "accepted_for_bidding", "stopped", "rejected", "suspended",
            "calculated", "held",
        }
        if normalized_status not in known_statuses:
            raise RuntimeError(
                "Broker execution status is unrecognized; manual reconciliation required"
            )
        tolerance = max(1e-8, requested * 1e-6)
        if normalized_status == "filled" and filled < requested - tolerance:
            raise RuntimeError(
                "Broker reports filled status with incomplete fill quantity; manual reconciliation required"
            )
        if normalized_status == "partially_filled" and (
            filled <= 0 or filled >= requested - tolerance
        ):
            raise RuntimeError(
                "Broker partially_filled status contradicts fill quantity; manual reconciliation required"
            )
        if normalized_status in {"new", "accepted", "pending_new"} and filled > tolerance:
            raise RuntimeError(
                "Broker unfilled status contradicts positive fill quantity; manual reconciliation required"
            )
        if normalized_status != "filled" and filled >= requested - tolerance:
            raise RuntimeError(
                "Broker non-filled status contradicts complete fill quantity; manual reconciliation required"
            )
        return filled

    def _reconcile_unresolved_liquidation_intents(self, positions, active_pending):
        """Resolve persisted ambiguous close intents only with unique broker evidence.

        The close-position endpoint does not expose a client-supplied idempotency key.
        Therefore, never resubmit an ambiguous request merely because it is absent from
        the open-order snapshot. Match broker history by symbol, side, quantity, and
        creation time; zero or multiple matches remain unresolved and fail closed.
        """
        unresolved = dict(self.state.get("emergency_liquidation_unresolved", {}))
        changed = False
        terminal_rejections = {"canceled", "cancelled", "expired", "rejected"}

        for symbol, intent in list(unresolved.items()):
            if not isinstance(intent, dict):
                continue
            created_at_raw = intent.get("created_at")
            requested_qty = intent.get("requested_qty")
            expected_side = str(intent.get("expected_side", "")).lower()
            if not created_at_raw or requested_qty is None or expected_side not in {"buy", "sell"}:
                # Legacy intents lack enough evidence for safe automated matching.
                continue

            try:
                created_at = (
                    created_at_raw if isinstance(created_at_raw, datetime)
                    else datetime.fromisoformat(str(created_at_raw))
                )
                # Naive timestamps have no defensible timezone. Treating them
                # as UTC can associate an unrelated broker order with this intent.
                if created_at.tzinfo is None or created_at.utcoffset() is None:
                    continue
                # The broker order must have been created shortly after the
                # persisted intent. An upper bound prevents a later, unrelated order
                # with the same symbol/side/quantity from being misidentified.
                query_after = created_at - timedelta(seconds=5)
                query_until = created_at + timedelta(minutes=5)
                orders = self.trading_client.get_orders(
                    filter=GetOrdersRequest(
                        status=QueryOrderStatus.ALL,
                        symbols=[symbol],
                        after=query_after,
                        until=query_until,
                    )
                )
                requested_qty = float(requested_qty)
                if not np.isfinite(requested_qty) or requested_qty <= 0:
                    continue
            except Exception as exc:
                self.logger.warning(
                    "Unable to query broker history for unresolved liquidation %s; keeping intent: %s",
                    symbol, exc,
                )
                continue

            # Index the returned history and validate each candidate's complete
            # replacement ancestry. Unrelated replacement chains must not suppress
            # an otherwise valid independent candidate; ambiguity is handled by the
            # existing unique-candidate requirement below.
            orders_by_id = {}
            duplicate_order_ids = set()
            replacement_parent_by_child = {}
            conflicting_replacement_children = set()
            for historical_order in orders:
                raw_parent_id = getattr(historical_order, "id", None)
                if raw_parent_id:
                    parent_id = str(raw_parent_id)
                    if parent_id in orders_by_id:
                        duplicate_order_ids.add(parent_id)
                    else:
                        orders_by_id[parent_id] = historical_order

                raw_child_id = getattr(historical_order, "replaced_by", None)
                if raw_parent_id and raw_child_id:
                    parent_id = str(raw_parent_id)
                    child_id = str(raw_child_id)
                    previous_parent = replacement_parent_by_child.get(child_id)
                    if previous_parent is not None and previous_parent != parent_id:
                        conflicting_replacement_children.add(child_id)
                    else:
                        replacement_parent_by_child[child_id] = parent_id

            def has_valid_replacement_ancestry(
                order,
                *,
                order_index,
                duplicate_ids,
                parent_by_child,
                conflicting_children,
            ):
                """Require every child->parent and parent->child edge to agree."""
                visited = set()
                current = order
                while True:
                    current_id_raw = getattr(current, "id", None)
                    if not current_id_raw:
                        return False
                    current_id = str(current_id_raw)
                    if current_id in visited or current_id in duplicate_ids:
                        return False
                    visited.add(current_id)

                    raw_parent_id = getattr(current, "replaces", None)
                    expected_parent_id = parent_by_child.get(current_id)
                    if current_id in conflicting_children:
                        return False
                    if not raw_parent_id:
                        # A purported child must not be accepted if the history
                        # says that a parent points to it but its back-link is absent.
                        return expected_parent_id is None
                    parent_id = str(raw_parent_id)
                    if expected_parent_id != parent_id:
                        return False
                    parent = order_index.get(parent_id)
                    if parent is None:
                        return False
                    if str(getattr(parent, "replaced_by", "") or "") != current_id:
                        return False
                    current = parent

            candidates = []
            for order in orders:
                order_symbol = str(getattr(order, "symbol", "") or "").strip()
                raw_side = getattr(order, "side", "")
                order_side = str(getattr(raw_side, "value", raw_side)).lower()
                raw_qty = getattr(order, "qty", None)
                raw_created_at = getattr(order, "created_at", None)
                order_id = getattr(order, "id", None)
                # Replaced parents are not live candidates. A terminal candidate
                # with a replaces link is accepted only when its entire ancestry
                # is present and bidirectionally consistent.
                if not order_id or order_symbol != symbol or order_side != expected_side:
                    continue
                if getattr(order, "replaced_by", None):
                    continue
                if not has_valid_replacement_ancestry(
                    order,
                    order_index=orders_by_id,
                    duplicate_ids=duplicate_order_ids,
                    parent_by_child=replacement_parent_by_child,
                    conflicting_children=conflicting_replacement_children,
                ):
                    continue
                try:
                    order_qty = float(raw_qty)
                    order_created_at = (
                        raw_created_at if isinstance(raw_created_at, datetime)
                        else datetime.fromisoformat(str(raw_created_at))
                    )
                    # Broker history timestamps must be timezone-aware;
                    # ambiguous timestamps are not reconciliation evidence.
                    if (
                        order_created_at.tzinfo is None
                        or order_created_at.utcoffset() is None
                    ):
                        continue
                except (TypeError, ValueError):
                    continue
                if (
                    np.isfinite(order_qty)
                    and np.isclose(order_qty, requested_qty, rtol=1e-6, atol=1e-8)
                    and query_after <= order_created_at <= query_until
                ):
                    candidates.append(order)

            if len(candidates) != 1:
                if len(candidates) > 1:
                    self.logger.error(
                        "Multiple broker orders match unresolved liquidation for %s; manual reconciliation required",
                        symbol,
                    )
                continue

            matched = candidates[0]
            order_id = str(matched.id)
            raw_status = getattr(matched, "status", "")
            status = str(getattr(raw_status, "value", raw_status)).lower()
            if status in terminal_rejections:
                # Record the terminal order as pending for one reconciliation cycle.
                # The positions snapshot passed into this method predates this
                # broker-history lookup and may still contain the pre-fill quantity.
                # The next invocation will refresh positions before allowing a retry.
                unresolved.pop(symbol, None)
                active_pending.setdefault(symbol, order_id)
            elif status == "replaced":
                # Without a usable replacement child, the intent is still ambiguous.
                # Do not clear it or submit another close based on the parent order.
                continue
            else:
                # Filled orders remain pending until a later positions snapshot confirms
                # flat; all other/unknown statuses are also preserved fail-closed.
                active_pending[symbol] = order_id
                unresolved.pop(symbol, None)
            changed = True

        if changed:
            self.state["emergency_liquidation_unresolved"] = unresolved
            self.state["emergency_liquidation_pending"] = active_pending
            self._save_state()
        return unresolved

    def _liquidate_all_positions(self, open_orders) -> List[Dict]:
        """Cancel unrelated open orders and safely resume an interrupted liquidation.

        Active close orders recorded in state are preserved on retry instead of
        being cancelled and resubmitted. Broker/API errors abort the operation;
        a returned order is persisted immediately so a later invocation can
        reconcile it against the broker's current open-order snapshot.
        """
        def validate_positions_snapshot(snapshot):
            if not isinstance(snapshot, (list, tuple)):
                raise RuntimeError(
                    "Cannot safely liquidate with an invalid broker positions snapshot"
                )
            seen_symbols = set()
            for position in snapshot:
                symbol = str(getattr(position, "symbol", "") or "").strip()
                if not symbol or symbol in seen_symbols:
                    raise RuntimeError(
                        "Cannot safely liquidate with missing or duplicate position symbols"
                    )
                seen_symbols.add(symbol)
                try:
                    quantity = float(getattr(position, "qty", None))
                except (TypeError, ValueError) as exc:
                    raise RuntimeError(
                        "Cannot safely liquidate a position with invalid symbol/quantity"
                    ) from exc
                if not np.isfinite(quantity):
                    raise RuntimeError(
                        "Cannot safely liquidate a position with invalid symbol/quantity"
                    )
            return list(snapshot)

        pending = dict(self.state.get("emergency_liquidation_pending", {}))
        pending_order_symbols = {}
        for symbol, order_id in pending.items():
            normalized_symbol = str(symbol or "").strip()
            normalized_order_id = str(order_id or "").strip()
            if not normalized_symbol or not normalized_order_id:
                raise RuntimeError(
                    "Cannot safely liquidate with an invalid persisted pending order mapping"
                )
            if normalized_order_id in pending_order_symbols:
                raise RuntimeError(
                    "Cannot safely liquidate when one pending order ID maps to multiple symbols"
                )
            pending_order_symbols[normalized_order_id] = normalized_symbol

        for order in open_orders:
            order_id = getattr(order, "id", None)
            if order_id is None:
                raise RuntimeError("Cannot safely liquidate while an open order has no ID")
            normalized_order_id = str(order_id)
            if normalized_order_id in pending_order_symbols:
                order_symbol = str(getattr(order, "symbol", "") or "").strip()
                expected_symbol = pending_order_symbols[normalized_order_id]
                if order_symbol != expected_symbol:
                    raise RuntimeError(
                        "Persisted pending liquidation order ID does not match broker order symbol"
                    )
                continue
            self._assert_paper_mutation_allowed()
            self.trading_client.cancel_order_by_id(order_id)

        remaining_orders = self._get_open_orders()
        for order in remaining_orders:
            order_id = str(getattr(order, "id", "") or "").strip()
            if order_id in pending_order_symbols:
                order_symbol = str(getattr(order, "symbol", "") or "").strip()
                if order_symbol != pending_order_symbols[order_id]:
                    raise RuntimeError(
                        "Persisted pending liquidation order ID does not match broker order symbol"
                    )
        remaining_ids = {str(getattr(order, "id", "")) for order in remaining_orders}
        unexpected_orders = [
            order for order in remaining_orders
            if str(getattr(order, "id", "")) not in pending_order_symbols
        ]
        if unexpected_orders:
            raise RuntimeError(
                "Cannot safely liquidate until broker confirms unrelated open orders are cancelled"
            )

        # Query directly so an API failure cannot be mistaken for an empty portfolio.
        positions = validate_positions_snapshot(self.trading_client.get_all_positions())
        liquidations = []
        active_pending = {}
        # Only confirmed terminal outcomes permit a new close. A replaced order
        # may have a live child, while suspended/done_for_day can resume.
        terminal_statuses = {
            "filled", "canceled", "cancelled", "expired", "rejected",
        }
        filled_pending = {}
        refresh_positions = False
        for symbol, order_id in pending.items():
            if str(order_id) in remaining_ids:
                active_pending[symbol] = order_id
                continue

            # Open-order snapshots can lag behind order acceptance. Reconcile by
            # ID before clearing a pending close; if the lookup fails, keep the
            # pending marker rather than risk submitting a duplicate close.
            try:
                recorded_order = self.trading_client.get_order_by_id(order_id)
                recorded_status = self._status_text(getattr(recorded_order, "status", ""))
            except Exception as exc:
                self.logger.warning(
                    "Unable to reconcile pending emergency close %s for %s; keeping it pending: %s",
                    order_id, symbol, exc,
                )
                active_pending[symbol] = order_id
            else:
                recorded_symbol = str(getattr(recorded_order, "symbol", "") or "").strip()
                if recorded_symbol != str(symbol).strip():
                    raise RuntimeError(
                        "Pending liquidation order ID does not match broker order symbol"
                    )
                if recorded_status == "filled":
                    # A terminal "filled" status is not sufficient evidence by
                    # itself: reject contradictory or incomplete broker fill data
                    # before a missing position snapshot can be treated as flat.
                    self._validate_broker_execution_state(
                        recorded_status,
                        getattr(recorded_order, "filled_qty", None),
                        getattr(recorded_order, "qty", None),
                    )
                    # Defer the flat-position decision until a fresh positions
                    # snapshot is fetched below. The first snapshot may predate
                    # the fill or omit a residual position due to propagation lag.
                    filled_pending[symbol] = order_id
                    refresh_positions = True
                elif recorded_status in terminal_statuses:
                    # A canceled/expired/rejected order can still have partial
                    # fills. Refresh positions before deciding whether to retry.
                    refresh_positions = True
                else:
                    active_pending[symbol] = order_id

        if refresh_positions:
            # Do not use the snapshot fetched before order reconciliation to decide
            # whether a residual position remains after a terminal close order.
            positions = validate_positions_snapshot(self.trading_client.get_all_positions())

        for symbol, order_id in filled_pending.items():
            matching_positions = [
                position for position in positions
                if str(getattr(position, "symbol", "")).strip() == symbol
            ]
            if len(matching_positions) > 1:
                # Duplicate symbol rows are not a trustworthy flat-position proof.
                active_pending[symbol] = order_id
                continue
            if not matching_positions:
                # Absence from a fresh broker snapshot confirms no open position.
                continue
            try:
                remaining_qty = float(getattr(matching_positions[0], "qty", None))
            except (TypeError, ValueError):
                active_pending[symbol] = order_id
            else:
                if not np.isfinite(remaining_qty) or remaining_qty != 0:
                    active_pending[symbol] = order_id

        if active_pending != pending:
            self.state["emergency_liquidation_pending"] = active_pending
        # Persist both active pending orders and removals of terminal orders.
        # Otherwise a restart can resurrect a stale pending marker from disk.
        if active_pending or active_pending != pending:
            self._save_state()
        unresolved = self._reconcile_unresolved_liquidation_intents(positions, active_pending)
        # Include orders reconciled during this invocation in the allowlist;
        # otherwise a later per-position safety pass could cancel our own close.
        for symbol, order_id in active_pending.items():
            normalized_order_id = str(order_id or "").strip()
            normalized_symbol = str(symbol or "").strip()
            if not normalized_order_id or not normalized_symbol:
                raise RuntimeError(
                    "Cannot safely liquidate with an invalid reconciled pending order mapping"
                )
            existing_symbol = pending_order_symbols.get(normalized_order_id)
            if existing_symbol is not None and existing_symbol != normalized_symbol:
                raise RuntimeError(
                    "Pending liquidation order ID maps to conflicting symbols"
                )
            pending_order_symbols[normalized_order_id] = normalized_symbol

        processed_symbols = set()
        refresh_before_next_candidate = False
        while True:
            # Reuse the already-validated snapshot for the first candidate. After
            # each close, fetch a new snapshot before selecting the next symbol.
            if refresh_before_next_candidate:
                positions = validate_positions_snapshot(
                    self.trading_client.get_all_positions()
                )
                refresh_before_next_candidate = False
            candidate_symbol = None
            for position in positions:
                symbol = str(getattr(position, "symbol", "")).strip()
                try:
                    quantity = float(getattr(position, "qty", 0) or 0)
                except (TypeError, ValueError) as exc:
                    raise RuntimeError(
                        "Cannot safely liquidate a position with invalid symbol/quantity"
                    ) from exc
                if not symbol or not np.isfinite(quantity):
                    raise RuntimeError(
                        "Cannot safely liquidate a position with invalid symbol/quantity"
                    )
                if (
                    quantity != 0
                    and symbol not in processed_symbols
                    and symbol not in active_pending
                    and symbol not in unresolved
                ):
                    candidate_symbol = symbol
                    break

            if candidate_symbol is None:
                break

            # A new/manual opening order may have appeared after the initial
            # cancellation pass. Cancel it and require a second broker snapshot
            # to prove that no unrelated active order remains before closing.
            current_open_orders = self._get_open_orders()
            cancelled_new_order = False
            for order in current_open_orders:
                order_id = str(getattr(order, "id", "") or "").strip()
                if not order_id:
                    raise RuntimeError(
                        "Cannot safely liquidate while an open order has no ID"
                    )
                if order_id in pending_order_symbols:
                    order_symbol = str(getattr(order, "symbol", "") or "").strip()
                    if order_symbol != pending_order_symbols[order_id]:
                        raise RuntimeError(
                            "Persisted pending liquidation order ID does not match broker order symbol"
                        )
                    continue
                self._assert_paper_mutation_allowed()
                self.trading_client.cancel_order_by_id(order_id)
                cancelled_new_order = True

            verified_open_orders = self._get_open_orders()
            for order in verified_open_orders:
                order_id = str(getattr(order, "id", "") or "").strip()
                if order_id not in pending_order_symbols:
                    raise RuntimeError(
                        "Cannot safely liquidate until broker confirms unrelated open orders are cancelled"
                    )
                order_symbol = str(getattr(order, "symbol", "") or "").strip()
                if order_symbol != pending_order_symbols[order_id]:
                    raise RuntimeError(
                        "Persisted pending liquidation order ID does not match broker order symbol"
                    )

            # Cancellation can race with fills. Re-read positions after the
            # cancellation check and derive the close intent from this snapshot.
            if cancelled_new_order:
                # A cancellation may race with fills, so only a post-cancel
                # position snapshot can authorize the close in this branch.
                positions = validate_positions_snapshot(
                    self.trading_client.get_all_positions()
                )
            matching_positions = [
                position for position in positions
                if str(getattr(position, "symbol", "")).strip() == candidate_symbol
            ]
            if len(matching_positions) > 1:
                raise RuntimeError(
                    "Cannot safely liquidate with duplicate broker position rows"
                )
            if not matching_positions:
                processed_symbols.add(candidate_symbol)
                continue
            try:
                quantity = float(getattr(matching_positions[0], "qty", None))
            except (TypeError, ValueError) as exc:
                raise RuntimeError(
                    "Cannot safely liquidate a position with invalid symbol/quantity"
                ) from exc
            if not np.isfinite(quantity):
                raise RuntimeError(
                    "Cannot safely liquidate a position with invalid symbol/quantity"
                )
            if quantity == 0 or candidate_symbol in active_pending or candidate_symbol in unresolved:
                processed_symbols.add(candidate_symbol)
                continue

            # Persist the exact latest quantity/side before sending the request.
            # The close-position endpoint closes the broker's current full
            # position; this quantity is evidence for later reconciliation.
            unresolved[candidate_symbol] = {
                "created_at": datetime.now(timezone.utc).isoformat(),
                "requested_qty": abs(quantity),
                "expected_side": "sell" if quantity > 0 else "buy",
                "reason": "close_submission_outcome_unconfirmed",
            }
            self.state["emergency_liquidation_unresolved"] = unresolved
            self._save_state()

            self._assert_paper_mutation_allowed()
            order = self.trading_client.close_position(candidate_symbol)
            order_id = getattr(order, "id", None)
            if order_id is None:
                raise RuntimeError(
                    f"Broker close request for {candidate_symbol} returned no order ID; manual reconciliation required"
                )
            status = self._status_text(getattr(order, "status", None), default="submitted")
            liquidations.append({
                "ticker": candidate_symbol,
                "side": "close_position",
                "quantity": abs(quantity),
                "order_id": str(order_id),
                "status": status,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "price": None,
            })
            unresolved.pop(candidate_symbol, None)
            self.state["emergency_liquidation_unresolved"] = unresolved
            # Every acknowledged close remains pending through at least one
            # later reconciliation cycle, even if its initial status is terminal.
            active_pending[candidate_symbol] = str(order_id)
            pending_order_symbols[str(order_id)] = candidate_symbol
            self.state["emergency_liquidation_pending"] = active_pending
            self._save_state()
            processed_symbols.add(candidate_symbol)
            refresh_before_next_candidate = True
        return liquidations

    def get_positions(self) -> Dict[str, Dict]:
        """Return verified broker positions or fail closed when they are unreadable.

        An API failure must never be represented as an empty portfolio: callers
        use this result to calculate exposure and position-level risk limits.
        """
        try:
            positions = self.trading_client.get_all_positions()
            parsed_positions = {}
            for position in positions:
                raw_symbol = getattr(position, "symbol", None)
                symbol = str(raw_symbol).strip().upper() if raw_symbol is not None else ""
                if not symbol:
                    raise ValueError("Broker returned a position without a valid symbol")
                if symbol in parsed_positions:
                    raise ValueError(
                        f"Broker returned duplicate position rows for {symbol}"
                    )
                values = {
                    "qty": float(position.qty),
                    "market_value": float(position.market_value),
                    "avg_entry_price": float(position.avg_entry_price),
                    "unrealized_pl": float(position.unrealized_pl),
                }
                if not all(np.isfinite(value) for value in values.values()):
                    raise ValueError(f"Broker returned non-finite position data for {symbol}")
                quantity = values["qty"]
                market_value = values["market_value"]
                # Reject internally inconsistent broker rows before risk sizing.
                if (quantity == 0) != (market_value == 0):
                    raise ValueError(
                        f"Broker returned inconsistent quantity/market value for {symbol}"
                    )
                if quantity != 0 and values["avg_entry_price"] <= 0:
                    raise ValueError(
                        f"Broker returned invalid average entry price for open position {symbol}"
                    )
                parsed_positions[symbol] = values
            return parsed_positions
        except Exception as e:
            self.logger.error("get_positions error; refusing to trade without verified positions: %s", e)
            raise RuntimeError(
                "Unable to retrieve broker positions; refusing to execute trades"
            ) from e

    @staticmethod
    def _count_submitted_orders(orders) -> int:
        """Count broker-acknowledged orders, not local failure records."""
        return sum(
            1 for order in orders
            if order.get("order_id")
            and str(order.get("status", "")).strip().lower() != "failed"
        )
    def _record_confirmed_submission(self, ticker: str, order_info: Dict) -> None:
        """Atomically archive broker acknowledgement and clear its unresolved intent."""
        client_order_id = str(order_info["client_order_id"])
        previous_state = dict(self.state)
        confirmed = dict(self.state.get("order_submission_confirmed", {}))
        confirmed[client_order_id] = dict(order_info)
        unresolved = dict(self.state.get("order_submission_unresolved", {}))
        unresolved.pop(ticker, None)
        self.state["order_submission_confirmed"] = confirmed
        self.state["order_submission_unresolved"] = unresolved
        try:
            self._save_state()
        except Exception:
            # Keep the in-memory state aligned with the last durable unresolved intent.
            self.state.clear()
            self.state.update(previous_state)
            raise

    def _reconcile_unresolved_submissions(self) -> None:
        """Resolve persisted intents by client ID; never retry an absent/unknown order."""
        unresolved = dict(self.state.get("order_submission_unresolved", {}))
        if not unresolved:
            return
        for ticker, intent in unresolved.items():
            client_order_id = intent.get("client_order_id")
            if not client_order_id:
                continue
            try:
                order = self.trading_client.get_order_by_client_id(client_order_id)
            except Exception:
                # Broker reads can be eventually consistent or temporarily unavailable.
                # The unresolved intent remains durable and continues to block execution.
                continue
            order_id = getattr(order, "id", None)
            if not order_id:
                continue

            # A lookup by client ID is not sufficient by itself: verify the
            # returned broker object agrees with the durable intent before
            # clearing it. Missing or mismatched identity remains unresolved.
            broker_client_order_id = str(
                getattr(order, "client_order_id", "") or ""
            ).strip()
            broker_symbol = str(getattr(order, "symbol", "") or "").strip()
            raw_side = getattr(order, "side", "")
            broker_side = str(getattr(raw_side, "value", raw_side)).lower()
            try:
                broker_qty = float(getattr(order, "qty", None))
                intent_qty = float(intent.get("quantity"))
            except (TypeError, ValueError):
                continue
            expected_side = str(intent.get("side", "")).lower()
            if (
                broker_client_order_id != str(client_order_id).strip()
                or broker_symbol != str(ticker).strip()
                or broker_side != expected_side
                or not np.isfinite(broker_qty)
                or not np.isfinite(intent_qty)
                or broker_qty <= 0
                or intent_qty <= 0
                or not np.isclose(broker_qty, intent_qty, rtol=1e-6, atol=1e-8)
            ):
                self.logger.error(
                    "Broker order identity does not match unresolved intent for %s; "
                    "retaining fail-closed state for manual reconciliation",
                    ticker,
                )
                continue

            expected_order_type = str(intent.get("order_type") or "").strip().lower()
            if expected_order_type:
                actual_order_type = str(
                    getattr(getattr(order, "type", None), "value", getattr(order, "type", None)) or ""
                ).strip().lower()
                if actual_order_type != expected_order_type:
                    self.logger.error(
                        "Broker order type differs from unresolved intent for %s; retaining fail-closed state",
                        ticker,
                    )
                    continue
                if expected_order_type == "limit":
                    try:
                        broker_limit_price = Decimal(str(getattr(order, "limit_price", None)))
                        intent_limit_price = Decimal(str(intent.get("limit_price")))
                    except (InvalidOperation, TypeError, ValueError):
                        continue
                    if (
                        not broker_limit_price.is_finite()
                        or not intent_limit_price.is_finite()
                        or broker_limit_price != intent_limit_price
                    ):
                        self.logger.error(
                            "Broker limit price differs from unresolved intent for %s; retaining fail-closed state",
                            ticker,
                        )
                        continue

            broker_status = self._status_text(
                getattr(order, "status", "unknown"), default="unknown"
            )
            # An identity match alone is not enough to resolve an ambiguous
            # submission: an absent/unknown status or malformed fill quantity
            # cannot establish the broker order's execution state.
            if not broker_status or broker_status == "unknown":
                self.logger.error(
                    "Broker order status is missing or unknown for unresolved intent %s; "
                    "retaining fail-closed state",
                    ticker,
                )
                continue
            raw_filled_qty = getattr(order, "filled_qty", None)
            try:
                broker_filled_qty = float(raw_filled_qty)
            except (TypeError, ValueError, OverflowError):
                self.logger.error(
                    "Broker order fill quantity is missing or invalid for unresolved intent %s; "
                    "retaining fail-closed state",
                    ticker,
                )
                continue
            if (
                not np.isfinite(broker_filled_qty)
                or broker_filled_qty < 0
                or broker_filled_qty > broker_qty + 1e-8
            ):
                self.logger.error(
                    "Broker order fill quantity is inconsistent for unresolved intent %s; "
                    "retaining fail-closed state",
                    ticker,
                )
                continue

            try:
                broker_filled_qty = self._validate_broker_execution_state(
                    broker_status, broker_filled_qty, broker_qty
                )
            except RuntimeError as exc:
                self.logger.error(
                    "Broker execution status/fill contradiction for unresolved intent %s; "
                    "retaining fail-closed state: %s",
                    ticker,
                    exc,
                )
                continue

            order_info = {
                "ticker": ticker,
                "side": intent.get("side"),
                "quantity": intent.get("quantity"),
                "order_id": str(order_id),
                "client_order_id": str(client_order_id),
                "status": self._status_text(getattr(order, "status", "unknown"), default="unknown"),
                "timestamp": intent.get("created_at", datetime.now(timezone.utc).isoformat()),
                "price": intent.get("price"),
                "order_type": intent.get("order_type"),
                "limit_price": intent.get("limit_price"),
                "pre_order_position_qty": intent.get("pre_order_position_qty"),
                "filled_qty": broker_filled_qty,
                "reconciled": True,
            }
            self._record_confirmed_submission(ticker, order_info)

    def _refresh_confirmed_submission_statuses(self) -> None:
        """Refresh broker status and fill quantity before reconciling positions."""
        confirmed = dict(self.state.get("order_submission_confirmed", {}))
        changed = False
        terminal_statuses = {"filled", "canceled", "cancelled", "expired", "rejected"}
        for client_order_id, stored_order in confirmed.items():
            status = self._status_text(stored_order.get("status", "unknown"), default="unknown")
            if status in terminal_statuses and stored_order.get("position_reconciled") is True:
                continue
            order_id = stored_order.get("order_id")
            if not order_id:
                if status in terminal_statuses:
                    raise RuntimeError(
                        "Terminal broker order lacks an ID for position reconciliation; manual reconciliation required"
                    )
                continue
            try:
                order = self.trading_client.get_order_by_id(order_id)
            except Exception as exc:
                # A terminal status without a fresh order/fill read cannot prove
                # that the position snapshot includes the fill.
                self.logger.warning(
                    "Unable to refresh acknowledged order %s (%s); retaining fail-closed guard: %s",
                    client_order_id, order_id, exc,
                )
                if status in terminal_statuses:
                    raise RuntimeError(
                        "Unable to refresh terminal broker order before position reconciliation"
                    ) from exc
                continue
            broker_status = self._status_text(getattr(order, "status", "unknown"), default="unknown")
            raw_filled_qty = getattr(order, "filled_qty", None)
            updated = dict(stored_order)
            # Validate broker identity for every refreshed status, not only terminal
            # orders. An unexpected response must never mutate persisted order state.
            broker_symbol = str(getattr(order, "symbol", "") or "").strip()
            raw_side = getattr(order, "side", "")
            broker_side = str(getattr(raw_side, "value", raw_side)).lower()
            try:
                broker_qty = float(getattr(order, "qty", None))
                expected_order_qty = float(stored_order.get("quantity"))
            except (TypeError, ValueError) as exc:
                raise RuntimeError(
                    "Broker order has invalid identity/quantity; manual reconciliation required"
                ) from exc
            broker_order_id = str(getattr(order, "id", "") or "").strip()
            broker_client_order_id = str(
                getattr(order, "client_order_id", "") or ""
            ).strip()
            if (
                broker_order_id != str(order_id).strip()
                or broker_client_order_id != str(client_order_id).strip()
                or broker_symbol != str(stored_order.get("ticker", "")).strip()
                or broker_side != str(stored_order.get("side", "")).lower()
                or not np.isfinite(broker_qty)
                or not np.isfinite(expected_order_qty)
                or not np.isclose(broker_qty, expected_order_qty, rtol=1e-6, atol=1e-8)
            ):
                status_label = "Terminal" if broker_status in terminal_statuses else "Non-terminal"
                raise RuntimeError(
                    f"{status_label} broker order identity differs from persisted intent; "
                    "manual reconciliation required"
                )
            expected_order_type = str(stored_order.get("order_type") or "").strip().lower()
            if expected_order_type:
                actual_order_type = str(
                    getattr(getattr(order, "type", None), "value", getattr(order, "type", None)) or ""
                ).strip().lower()
                if actual_order_type != expected_order_type:
                    raise RuntimeError(
                        "Broker order type differs from persisted intent; manual reconciliation required"
                    )
                if expected_order_type == "limit":
                    try:
                        broker_limit_price = Decimal(str(getattr(order, "limit_price", None)))
                        expected_limit_price = Decimal(str(stored_order.get("limit_price")))
                    except (InvalidOperation, TypeError, ValueError) as exc:
                        raise RuntimeError(
                            "Broker order limit price is invalid; manual reconciliation required"
                        ) from exc
                    if (
                        not broker_limit_price.is_finite()
                        or not expected_limit_price.is_finite()
                        or broker_limit_price != expected_limit_price
                    ):
                        raise RuntimeError(
                            "Broker order limit price differs from persisted intent; manual reconciliation required"
                        )
            if broker_status != stored_order.get("status"):
                updated["status"] = broker_status
                changed = True
            if raw_filled_qty in (None, ""):
                raise RuntimeError(
                    "Broker order has no verified filled quantity; manual reconciliation required"
                )
            filled_qty = self._validate_broker_execution_state(
                broker_status, raw_filled_qty, broker_qty
            )
            if updated.get("filled_qty") != filled_qty:
                updated["filled_qty"] = filled_qty
                changed = True
            confirmed[client_order_id] = updated

        if changed:
            previous_state = dict(self.state)
            self.state["order_submission_confirmed"] = confirmed
            try:
                self._save_state()
            except Exception:
                self.state.clear()
                self.state.update(previous_state)
                raise

    def _reconcile_terminal_order_positions(self) -> None:
        """Reconcile terminal fills against broker positions, including sequential fills."""
        confirmed = dict(self.state.get("order_submission_confirmed", {}))
        terminal_statuses = {"filled", "canceled", "cancelled", "expired", "rejected"}
        pending = [
            (client_order_id, order)
            for client_order_id, order in confirmed.items()
            if self._status_text(order.get("status", "unknown"), default="unknown") in terminal_statuses
            and order.get("position_reconciled") is not True
        ]
        if not pending:
            return

        # A single pending order can be checked directly against its expected
        # post-fill quantity. Multiple pending orders on one symbol require a
        # timestamped, continuous baseline chain; the final broker snapshot can
        # then validate the whole chain without comparing each order to the same
        # final quantity (which would incorrectly reject earlier fills).
        positions = self.get_positions()
        grouped = {}
        for client_order_id, order in pending:
            ticker = str(order.get("ticker", "")).strip()
            if not ticker or order.get("pre_order_position_qty") is None:
                raise RuntimeError(
                    "Terminal broker order lacks a pre-order position baseline; manual reconciliation required"
                )
            try:
                baseline_qty = float(order["pre_order_position_qty"])
                filled_qty = float(order["filled_qty"])
            except (TypeError, ValueError) as exc:
                raise RuntimeError(
                    "Terminal broker order has invalid quantity provenance; manual reconciliation required"
                ) from exc
            if (
                not np.isfinite(baseline_qty)
                or not np.isfinite(filled_qty)
                or filled_qty < 0
            ):
                raise RuntimeError(
                    "Terminal broker order has invalid quantity provenance; manual reconciliation required"
                )
            side = str(order.get("side", "")).lower()
            if side == "buy":
                expected_qty = baseline_qty + filled_qty
            elif side == "sell":
                expected_qty = baseline_qty - filled_qty
            else:
                raise RuntimeError(
                    "Terminal broker order has unknown side; manual reconciliation required"
                )
            if not np.isfinite(expected_qty) or expected_qty < -1e-6:
                raise RuntimeError(
                    "Terminal broker order implies an invalid position quantity; manual reconciliation required"
                )
            grouped.setdefault(ticker, []).append(
                (client_order_id, order, baseline_qty, expected_qty)
            )

        reconciled_ids = []
        for ticker, orders in grouped.items():
            if len(orders) > 1:
                timestamped = []
                for item in orders:
                    client_order_id, order, _, _ = item
                    raw_timestamp = order.get("timestamp")
                    if not raw_timestamp:
                        raise RuntimeError(
                            f"Multiple terminal orders for {ticker} lack timestamps; manual reconciliation required"
                        )
                    try:
                        timestamp = datetime.fromisoformat(
                            str(raw_timestamp).replace("Z", "+00:00")
                        )
                    except (TypeError, ValueError) as exc:
                        raise RuntimeError(
                            f"Multiple terminal orders for {ticker} have invalid timestamps; manual reconciliation required"
                        ) from exc
                    if timestamp.tzinfo is None:
                        raise RuntimeError(
                            f"Multiple terminal orders for {ticker} have timezone-naive timestamps; manual reconciliation required"
                        )
                    timestamped.append((timestamp.astimezone(timezone.utc), item))
                timestamped.sort(key=lambda entry: entry[0])
                if any(
                    timestamped[index][0] == timestamped[index - 1][0]
                    for index in range(1, len(timestamped))
                ):
                    raise RuntimeError(
                        f"Multiple terminal orders for {ticker} have ambiguous timestamps; manual reconciliation required"
                    )
                ordered = [item for _, item in timestamped]
                for previous, current in zip(ordered, ordered[1:]):
                    previous_expected = previous[3]
                    current_baseline = current[2]
                    chain_tolerance = max(
                        1e-6,
                        1e-5 * max(1.0, abs(previous_expected), abs(current_baseline)),
                    )
                    if abs(previous_expected - current_baseline) > chain_tolerance:
                        raise RuntimeError(
                            f"Terminal order quantity chain is inconsistent for {ticker}; manual reconciliation required"
                        )
                expected_qty = ordered[-1][3]
            else:
                expected_qty = orders[0][3]

            actual_qty = float(positions.get(ticker, {}).get("qty", 0.0) or 0.0)
            if not np.isfinite(actual_qty):
                raise RuntimeError("Broker position quantity is invalid during order reconciliation")
            tolerance = max(1e-6, 1e-5 * max(1.0, abs(expected_qty), abs(actual_qty)))
            if abs(actual_qty - expected_qty) > tolerance:
                order_ids = ", ".join(item[0] for item in orders)
                raise RuntimeError(
                    f"Broker position snapshot has not reconciled terminal order(s) {order_ids} "
                    f"for {ticker}; expected quantity {expected_qty}, observed {actual_qty}"
                )
            reconciled_ids.extend(item[0] for item in orders)

        for client_order_id in reconciled_ids:
            updated = dict(confirmed[client_order_id])
            updated["position_reconciled"] = True
            confirmed[client_order_id] = updated

        previous_state = dict(self.state)
        self.state["order_submission_confirmed"] = confirmed
        try:
            self._save_state()
        except Exception:
            self.state.clear()
            self.state.update(previous_state)
            raise


    @staticmethod
    def _opg_status(order, default="accepted"):
        value = getattr(order, "status", default)
        return str(getattr(value, "value", value) or default).strip().lower()

    def _persist_opg_batch(self, batch_id, batch):
        previous_batches = copy.deepcopy(self.state.get("opg_batches", {}))
        batches = dict(previous_batches)
        batch["updated_at"] = datetime.now(timezone.utc).isoformat()
        batches[batch_id] = batch
        self.state["opg_batches"] = batches
        try:
            self._save_state()
        except Exception:
            self.state["opg_batches"] = previous_batches
            raise

    @staticmethod
    def _validate_opg_broker_order(order, expected):
        expected_side = str(expected.get("side") or "").strip().lower()
        if expected_side not in {"buy", "sell"}:
            raise RuntimeError("OPG persisted order intent has an invalid side")
        expected_order_type = str(expected.get("order_type", "market")).strip().lower()
        if expected_order_type not in {"market", "limit"}:
            raise RuntimeError("OPG persisted order intent has an invalid order type")
        if str(expected.get("time_in_force", "opg")).strip().lower() != "opg":
            raise RuntimeError("OPG persisted order intent has an invalid time-in-force")
        try:
            expected_quantity = Decimal(str(expected.get("quantity")))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise RuntimeError("OPG persisted order intent has an invalid quantity") from exc
        if not expected_quantity.is_finite() or expected_quantity <= 0 or expected_quantity != expected_quantity.to_integral_value():
            raise RuntimeError("OPG persisted order intent quantity must be a positive integer")
        checks = {
            "client_order_id": expected["client_order_id"],
            "symbol": expected["symbol"],
            "side": expected["side"],
            "qty": str(expected["quantity"]),
        }
        for field, expected_value in checks.items():
            raw = getattr(order, field, None)
            if raw is None:
                raise RuntimeError(f"OPG broker acknowledgement is missing {field}")
            actual = getattr(raw, "value", raw)
            if field == "qty":
                try:
                    if Decimal(str(actual)) != Decimal(expected_value):
                        raise RuntimeError("OPG broker order identity mismatch")
                except (InvalidOperation, TypeError, ValueError) as exc:
                    raise RuntimeError("OPG broker order identity mismatch") from exc
            elif str(actual).strip().lower() != str(expected_value).strip().lower():
                raise RuntimeError("OPG broker order identity mismatch")
        order_id = str(getattr(order, "id", "") or "").strip()
        if not order_id:
            raise RuntimeError("OPG broker acknowledgement has no order ID")
        order_type = getattr(order, "type", None)
        tif = getattr(order, "time_in_force", None)
        actual_order_type = str(getattr(order_type, "value", order_type) or "").strip().lower()
        if order_type is None or actual_order_type != expected_order_type:
            raise RuntimeError("OPG broker order type mismatch")
        if tif is None or str(getattr(tif, "value", tif)).lower() != "opg":
            raise RuntimeError("OPG broker time-in-force mismatch")
        if expected_order_type == "limit":
            try:
                expected_limit_price = Decimal(str(expected.get("limit_price")))
                actual_limit_price = Decimal(str(getattr(order, "limit_price", None)))
            except (InvalidOperation, TypeError, ValueError) as exc:
                raise RuntimeError("OPG broker limit price is missing or invalid") from exc
            if (
                not expected_limit_price.is_finite()
                or expected_limit_price <= 0
                or not actual_limit_price.is_finite()
                or actual_limit_price != expected_limit_price
            ):
                raise RuntimeError("OPG broker limit price mismatch")
        raw_filled = getattr(order, "filled_qty", None)
        try:
            filled = Decimal(str(raw_filled))
            requested = Decimal(str(expected["quantity"]))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise RuntimeError("OPG broker fill snapshot is missing or invalid") from exc
        if not filled.is_finite() or filled < 0 or filled > requested or filled != filled.to_integral_value():
            raise RuntimeError("OPG broker fill quantity is invalid")
        average_price = getattr(order, "filled_avg_price", None)
        if average_price not in (None, ""):
            try:
                average = Decimal(str(average_price))
            except (InvalidOperation, TypeError, ValueError) as exc:
                raise RuntimeError("OPG broker average fill price is invalid") from exc
            if not average.is_finite() or average <= 0:
                raise RuntimeError("OPG broker average fill price is invalid")
        return order_id

    @staticmethod
    def _opg_terminal_statuses():
        return {"filled", "canceled", "cancelled", "expired", "rejected"}

    @staticmethod
    def _opg_active_statuses():
        return {
            "new", "accepted", "pending_new", "partially_filled", "done_for_day",
            "pending_cancel", "pending_replace", "accepted_for_bidding", "stopped",
            "suspended", "held", "calculated",
        }

    def _opg_reconciliation_snapshot(self, broker_order, expected):
        order_id = self._validate_opg_broker_order(broker_order, expected)
        expected_order_id = str(expected.get("order_id") or "").strip()
        if expected_order_id and order_id != expected_order_id:
            raise RuntimeError("OPG broker order identity mismatch: broker order ID changed")
        status = self._opg_status(broker_order, default="unknown")
        if status not in self._opg_terminal_statuses() | self._opg_active_statuses():
            raise RuntimeError(f"OPG broker order status is unknown: {status}")
        try:
            requested = Decimal(str(expected["quantity"]))
            filled = Decimal(str(getattr(broker_order, "filled_qty", None)))
        except (KeyError, InvalidOperation, TypeError, ValueError) as exc:
            raise RuntimeError("OPG broker fill quantity is missing or invalid") from exc
        if (
            not requested.is_finite() or requested <= 0
            or requested != requested.to_integral_value()
            or not filled.is_finite() or filled < 0
            or filled > requested or filled != filled.to_integral_value()
        ):
            raise RuntimeError("OPG broker fill quantity is invalid")
        if status == "filled" and filled != requested:
            raise RuntimeError("OPG filled status contradicts filled quantity")
        if status == "partially_filled" and not (0 < filled < requested):
            raise RuntimeError("OPG partially_filled status contradicts filled quantity")
        if status in {"new", "accepted", "pending_new"} and filled != 0:
            raise RuntimeError("OPG unfilled status contradicts positive filled quantity")
        if status in self._opg_terminal_statuses() - {"filled"} and filled >= requested:
            raise RuntimeError("OPG terminal non-filled status contradicts complete fill quantity")
        average = getattr(broker_order, "filled_avg_price", None)
        if average in (None, ""):
            average = None
        else:
            try:
                average_decimal = Decimal(str(average))
            except (InvalidOperation, TypeError, ValueError) as exc:
                raise RuntimeError("OPG broker average fill price is invalid") from exc
            if not average_decimal.is_finite() or average_decimal <= 0:
                raise RuntimeError("OPG broker average fill price is invalid")
            average = str(average_decimal)
        return {
            "order_id": order_id,
            "side": str(expected["side"]).strip().lower(),
            "symbol": str(expected["symbol"]).strip().upper(),
            "status": status,
            "filled_qty": int(filled),
            "average_fill_price": average,
            "broker_updated_at": str(getattr(broker_order, "updated_at", "") or "") or None,
        }

    def _recover_opg_batches(self):
        """Recover and reconcile persisted OPG batches using broker reads only.

        Missing orders are never resubmitted. Orders not reached before a crash
        are marked skipped, not submitted as catch-up orders. Active and partial
        orders remain blocking until the broker reports a terminal state.
        """
        batches = self.state.get("opg_batches", {})
        if not isinstance(batches, dict) or any(not isinstance(v, dict) for v in batches.values()):
            raise RuntimeError("Persisted OPG batch state is invalid; refusing recovery")
        if not batches:
            return {"batches_checked": 0, "batches_completed": 0, "batches_blocked": 0}
        if self.state.get("account_mode") != "paper" or getattr(self, "paper_trading", True) is False:
            raise RuntimeError("OPG recovery is Paper-only; refusing broker reconciliation")

        checked = completed = blocked = 0
        for batch_id in sorted(batches):
            batch = copy.deepcopy(batches[batch_id])
            batch_status = str(batch.get("status", "unknown")).strip().lower()
            if batch_status in {"completed", "no_orders"}:
                continue
            checked += 1
            batch_account = str(batch.get("account_id") or "").strip()
            current_account = str(self.state.get("account_id") or "").strip()
            if (
                not batch_account
                or not current_account
                or getattr(self, "_current_account_identity_verified", False) is not True
                or current_account != batch_account
            ):
                raise RuntimeError("Current verified broker account identity does not match OPG batch")
            if batch.get("account_mode") != "paper":
                raise RuntimeError("Persisted OPG batch is not explicitly Paper; refusing recovery")
            orders = batch.get("orders")
            if not isinstance(orders, dict):
                raise RuntimeError("Persisted OPG batch orders are malformed; manual reconciliation required")
            if not orders and batch_status == "unresolved":
                blocked += 1
                continue

            terminal_symbols = []
            active_symbols = []
            skipped_symbols = []
            for symbol in sorted(orders):
                order_record = dict(orders[symbol]) if isinstance(orders[symbol], dict) else None
                if order_record is None:
                    raise RuntimeError("Persisted OPG order record is malformed; manual reconciliation required")
                symbol = str(order_record.get("symbol") or symbol).strip().upper()
                order_status = str(order_record.get("status", "unknown")).strip().lower()
                if order_status in {"not_submitted", "skipped_after_restart"}:
                    order_record["status"] = "skipped_after_restart"
                    order_record["blocking_reason"] = "submission_not_attempted_before_restart_no_catch_up"
                    orders[symbol] = order_record
                    skipped_symbols.append(symbol)
                    batch["orders"] = orders
                    batch["status"] = "unresolved"
                    batch["blocking_reason"] = "one_or_more_orders_were_not_submitted_before_restart"
                    self._persist_opg_batch(batch_id, batch)
                    continue

                expected = dict(order_record)
                expected["symbol"] = symbol
                client_order_id = str(expected.get("client_order_id") or "").strip()
                if not client_order_id:
                    raise RuntimeError("Persisted OPG order has no client ID; manual reconciliation required")
                try:
                    if expected.get("order_id"):
                        broker_order = self.trading_client.get_order_by_id(expected["order_id"])
                    else:
                        broker_order = self.trading_client.get_order_by_client_id(client_order_id)
                    snapshot = self._opg_reconciliation_snapshot(broker_order, expected)
                except Exception as exc:
                    order_record["status"] = "unresolved"
                    order_record["blocking_reason"] = "broker_order_not_verifiably_recovered"
                    batch["orders"] = orders
                    batch["status"] = "unresolved"
                    batch["blocking_reason"] = str(exc)[:500]
                    self._persist_opg_batch(batch_id, batch)
                    raise RuntimeError(
                        f"OPG recovery unresolved for {symbol}: {exc}; no resubmission or catch-up performed"
                    ) from exc

                order_record.update(snapshot)
                order_record["recovered_at"] = datetime.now(timezone.utc).isoformat()
                order_record["blocking_reason"] = None
                orders[symbol] = order_record
                batch["orders"] = orders
                if snapshot["status"] in self._opg_terminal_statuses():
                    terminal_symbols.append(symbol)
                else:
                    active_symbols.append(symbol)
                self._persist_opg_batch(batch_id, batch)

            if terminal_symbols and not active_symbols and not skipped_symbols:
                try:
                    positions = self.get_positions()
                    position_checked_at = datetime.now(timezone.utc).isoformat()
                    for symbol in terminal_symbols:
                        order_record = orders[symbol]
                        baseline = order_record.get("pre_order_position_qty")
                        if baseline is None:
                            raise RuntimeError(
                                f"OPG terminal order {symbol} lacks pre-order position baseline; manual reconciliation required"
                            )
                        try:
                            baseline_qty = Decimal(str(baseline))
                            filled_qty = Decimal(str(order_record["filled_qty"]))
                            actual_qty = Decimal(str(positions.get(symbol, {}).get("qty", 0)))
                        except (InvalidOperation, TypeError, ValueError) as exc:
                            raise RuntimeError(f"OPG position snapshot is invalid for {symbol}") from exc
                        if not all(v.is_finite() for v in (baseline_qty, filled_qty, actual_qty)):
                            raise RuntimeError(f"OPG position snapshot is non-finite for {symbol}")
                        expected_qty = baseline_qty + filled_qty if order_record["side"] == "buy" else baseline_qty - filled_qty
                        if expected_qty < 0:
                            raise RuntimeError(f"OPG sell fill implies a negative position for {symbol}")
                        tolerance = max(Decimal("0.000001"), max(abs(expected_qty), abs(actual_qty), Decimal(1)) * Decimal("0.00001"))
                        if abs(actual_qty - expected_qty) > tolerance:
                            raise RuntimeError(
                                f"OPG broker position has not reconciled {symbol}: expected {expected_qty}, observed {actual_qty}"
                            )
                        order_record["position_reconciled"] = True
                        order_record["position_reconciled_at"] = position_checked_at
                        orders[symbol] = order_record
                    batch["orders"] = orders
                    batch["status"] = "completed"
                    batch["blocking_reason"] = None
                    batch["position_snapshot_at"] = position_checked_at
                    completed += 1
                except Exception as exc:
                    batch["status"] = "unresolved"
                    batch["blocking_reason"] = str(exc)[:500]
                    self._persist_opg_batch(batch_id, batch)
                    raise RuntimeError(
                        f"OPG position reconciliation unresolved for batch {batch_id}: {exc}"
                    ) from exc
            elif skipped_symbols:
                batch["status"] = "unresolved"
                batch["blocking_reason"] = "one_or_more_orders_were_not_submitted_before_restart_no_catch_up"
            elif active_symbols:
                batch["status"] = "reconciliation_pending"
                batch["blocking_reason"] = "broker_orders_not_terminal"
            else:
                batch["status"] = "unresolved"
                batch["blocking_reason"] = "batch_contains_orders_without_reconciliation_evidence"
            if batch["status"] != "completed":
                blocked += 1
            self._persist_opg_batch(batch_id, batch)
        return {"batches_checked": checked, "batches_completed": completed, "batches_blocked": blocked}

    def reconcile_opg_batches_read_only(self):
        """Public, read-only recovery entry point; requires a fresh account identity."""
        with self.execution_lock():
            if self.state.get("opg_batches") and getattr(self, "_current_account_identity_verified", False) is not True:
                raise RuntimeError("A fresh verified broker account identity is required for OPG recovery")
            return self._recover_opg_batches()


    @staticmethod
    def _opg_decimal(value, field):
        """Parse a finite Decimal without accepting booleans or lossy fallbacks."""
        if isinstance(value, bool) or value in (None, ""):
            raise RuntimeError(f"OPG preflight {field} is missing or invalid")
        try:
            parsed = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise RuntimeError(f"OPG preflight {field} is not numeric") from exc
        if not parsed.is_finite():
            raise RuntimeError(f"OPG preflight {field} must be finite")
        return parsed

    @staticmethod
    def _opg_metadata_value(value):
        raw = getattr(value, "value", value)
        return str(raw or "").strip().lower().rsplit(".", 1)[-1]

    def preflight_opg_plan(self, plan):
        """Read-only, fail-closed account/position/asset/budget gate for one OPG plan.

        The caller must hold execution_lock. This method performs broker reads only;
        it never persists OPG intent or mutates broker orders or positions.
        """
        if not isinstance(getattr(self, "state", None), dict):
            raise RuntimeError("OPG preflight requires initialized broker state")
        if self.state.get("account_mode") != "paper" or getattr(self, "paper_trading", False) is not True:
            raise RuntimeError("OPG preflight is Paper-only")
        configured_url = str(getattr(self, "base_url", "") or "").lower()
        if configured_url and "paper-api.alpaca.markets" not in configured_url:
            raise RuntimeError("OPG preflight TradingClient endpoint is not Alpaca Paper")
        if not callable(getattr(plan, "to_dict", None)) or not isinstance(getattr(plan, "assets", None), dict):
            raise RuntimeError("OPG preflight requires a valid immutable sizing plan")

        account = self.get_account_status()
        if not isinstance(account, dict):
            raise RuntimeError("OPG preflight account snapshot is unavailable")
        if account.get("account_data_fresh") is not True or account.get("account_data_source") != "alpaca_account":
            raise RuntimeError("OPG preflight requires a fresh Alpaca account snapshot")
        if getattr(self, "_current_account_identity_verified", False) is not True or account.get("account_identity_verified") is not True:
            raise RuntimeError("OPG preflight account identity is not currently verified")
        account_id = str(account.get("account_id") or "").strip()
        persisted_account_id = str(self.state.get("account_id") or "").strip()
        if not account_id or not persisted_account_id or account_id != persisted_account_id:
            raise RuntimeError("OPG preflight account ID does not match persisted Paper state")
        if self.state.get("account_mode") != "paper":
            raise RuntimeError("OPG preflight persisted account mode is not Paper")
        if self._opg_metadata_value(account.get("status")) != "active":
            raise RuntimeError("OPG preflight account status is not active")
        if account.get("trading_blocked") is not False:
            raise RuntimeError("OPG preflight account trading is blocked or unknown")
        snapshot_at = account.get("account_snapshot_at")
        if not isinstance(snapshot_at, str) or not snapshot_at.strip():
            raise RuntimeError("OPG preflight account snapshot timestamp is missing")
        try:
            parsed_snapshot_at = datetime.fromisoformat(snapshot_at.strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise RuntimeError("OPG preflight account snapshot timestamp is invalid") from exc
        if parsed_snapshot_at.tzinfo is None:
            raise RuntimeError("OPG preflight account snapshot timestamp must be timezone-aware")

        equity = self._opg_decimal(account.get("equity"), "account equity")
        buying_power = self._opg_decimal(account.get("buying_power"), "buying power")
        if equity <= 0 or buying_power < 0:
            raise RuntimeError("OPG preflight account equity or buying power is outside valid bounds")

        # The plan must be based on the same verified account equity and on
        # the smaller of that equity or the shared operational notional cap.
        plan_equity = self._opg_decimal(
            getattr(plan, "equity", None), "planned account equity"
        )
        sizing_capital = self._opg_decimal(
            getattr(plan, "sizing_capital", None), "planned sizing capital"
        )
        expected_sizing_capital = min(equity, MAX_GROSS_NOTIONAL_USD)
        if abs(plan_equity - equity) > Decimal("0.01"):
            raise RuntimeError(
                "OPG preflight account equity changed after target sizing; rebuild the plan"
            )
        if abs(sizing_capital - expected_sizing_capital) > Decimal("0.01"):
            raise RuntimeError(
                "OPG preflight sizing capital does not match the verified operational budget"
            )

        plan_assets = plan.assets
        normalized_assets = {}
        for raw_symbol, item in plan_assets.items():
            if not isinstance(raw_symbol, str) or not raw_symbol.strip():
                raise RuntimeError("OPG preflight plan contains an invalid symbol")
            symbol = raw_symbol.strip().upper()
            if symbol in normalized_assets:
                raise RuntimeError("OPG preflight plan contains duplicate normalized symbols")
            if str(getattr(item, "symbol", symbol) or "").strip().upper() != symbol:
                raise RuntimeError(f"OPG preflight plan symbol identity mismatch for {symbol}")
            normalized_assets[symbol] = item
        if not normalized_assets:
            raise RuntimeError("OPG preflight sizing plan has no assets")

        positions = self.get_positions()
        if not isinstance(positions, dict):
            raise RuntimeError("OPG preflight broker positions snapshot is invalid")
        broker_positions = {}
        for raw_symbol, position in positions.items():
            if not isinstance(raw_symbol, str) or not raw_symbol.strip() or not isinstance(position, dict):
                raise RuntimeError("OPG preflight broker position row is malformed")
            symbol = raw_symbol.strip().upper()
            if symbol in broker_positions:
                raise RuntimeError(f"OPG preflight duplicate broker position for {symbol}")
            quantity = self._opg_decimal(position.get("qty"), f"position quantity for {symbol}")
            if quantity < 0 or quantity != quantity.to_integral_value():
                raise RuntimeError(f"OPG preflight position for {symbol} is fractional or short")
            broker_positions[symbol] = quantity
        for symbol, item in normalized_assets.items():
            expected = self._opg_decimal(getattr(item, "current_quantity", None), f"planned position for {symbol}")
            if expected < 0 or expected != expected.to_integral_value():
                raise RuntimeError(f"OPG preflight planned position for {symbol} is not a non-negative whole share quantity")
            actual = broker_positions.get(symbol, Decimal("0"))
            if actual != expected:
                raise RuntimeError(f"OPG preflight position drift for {symbol}: broker={actual}, plan={expected}")
        for symbol, quantity in broker_positions.items():
            if symbol not in normalized_assets and quantity != 0:
                raise RuntimeError(f"OPG preflight found unmodeled broker position outside plan: {symbol}")

        # The OPG target is a complete account target. Mark every projected
        # position at its validated signal-bar Close, then add pending BUY
        # reserves from the broker. Existing SELL orders are not new exposure.
        opg_prices = {
            symbol: float(self._opg_decimal(
                getattr(item, "close_price", None), f"signal-bar Close for {symbol}"
            ))
            for symbol, item in normalized_assets.items()
        }
        projected_position_notional = Decimal("0")
        for symbol, item in normalized_assets.items():
            target_quantity = self._opg_decimal(
                getattr(item, "target_quantity", None), f"target quantity for {symbol}"
            )
            close_price = self._opg_decimal(
                getattr(item, "close_price", None), f"signal-bar Close for {symbol}"
            )
            if (
                target_quantity < 0
                or target_quantity != target_quantity.to_integral_value()
                or close_price <= 0
            ):
                raise RuntimeError(f"OPG preflight target exposure is invalid for {symbol}")
            projected_position_notional += target_quantity * close_price
        current_position_notional = Decimal("0")
        for symbol, quantity in broker_positions.items():
            if quantity == 0:
                continue
            if symbol not in opg_prices:
                raise RuntimeError(
                    f"OPG preflight cannot mark broker position outside the target price map: {symbol}"
                )
            current_position_notional += quantity * self._opg_decimal(
                opg_prices[symbol], f"signal-bar Close for broker position {symbol}"
            )

        active_orders = self._get_open_orders()
        pending_buy_notional = self._opg_decimal(
            self._get_reserved_buy_notional(active_orders, prices=opg_prices),
            "aggregate pending BUY notional",
        )
        # Do not credit planned SELLs before they actually fill. A mixed OPG
        # batch may submit BUYs only if current positions + pending BUYs +
        # proposed BUYs are already within the cap.
        planned_deltas = {
            symbol: getattr(item, "delta_quantity", None)
            for symbol, item in normalized_assets.items()
        }
        for order in active_orders:
            order_symbol = str(getattr(order, "symbol", "") or "").strip().upper()
            if not order_symbol:
                raise RuntimeError("OPG preflight found an active order without a symbol")
            if order_symbol in planned_deltas and planned_deltas[order_symbol] not in (0, None):
                raise RuntimeError(
                    f"OPG preflight active broker order already exists for planned symbol {order_symbol}"
                )

        # Persisted DAY submission intents with unknown outcomes are not
        # necessarily visible in the active-order endpoint. Block OPG until
        # their broker state has been reconciled after restart/reconnect.
        unresolved_day = self.state.get("order_submission_unresolved", {})
        confirmed_day = self.state.get("order_submission_confirmed", {})
        terminal_day_statuses = {"filled", "canceled", "cancelled", "expired", "rejected"}
        active_day = {
            client_id: order
            for client_id, order in confirmed_day.items()
            if (
                self._status_text(order.get("status", "unknown"), default="unknown")
                not in terminal_day_statuses
                or order.get("position_reconciled") is not True
            )
        } if isinstance(confirmed_day, dict) else {"invalid_state": {}}
        if unresolved_day or active_day:
            raise RuntimeError(
                "OPG preflight requires DAY order reconciliation before new exposure"
            )

        buy_orders = 0
        sell_orders = 0
        estimated_buy_notional = Decimal("0")
        asset_checks = []
        for symbol, item in sorted(normalized_assets.items()):
            delta = getattr(item, "delta_quantity", None)
            if isinstance(delta, bool) or not isinstance(delta, int):
                raise RuntimeError(f"OPG preflight delta quantity for {symbol} must be an integer")
            if delta == 0:
                continue
            side = "buy" if delta > 0 else "sell"
            quantity = abs(delta)
            current_quantity = broker_positions.get(symbol, Decimal("0"))
            if side == "buy":
                buy_orders += 1
                close_price = self._opg_decimal(getattr(item, "close_price", None), f"signal-bar Close for {symbol}")
                if close_price <= 0:
                    raise RuntimeError(f"OPG preflight signal-bar Close for {symbol} must be positive")
                estimated_buy_notional += Decimal(quantity) * close_price
            else:
                sell_orders += 1
                if current_quantity <= 0 or Decimal(quantity) > current_quantity:
                    raise RuntimeError(f"OPG preflight SELL for {symbol} exceeds the verified long position")

            try:
                asset = self.trading_client.get_asset(symbol)
            except Exception as exc:
                raise RuntimeError(f"OPG preflight could not retrieve asset metadata for {symbol}") from exc
            asset_symbol = str(getattr(asset, "symbol", "") or "").strip().upper()
            asset_class = self._opg_metadata_value(getattr(asset, "asset_class", None))
            asset_status = self._opg_metadata_value(getattr(asset, "status", None))
            tradable = getattr(asset, "tradable", None)
            attributes = getattr(asset, "attributes", None)
            if asset_symbol != symbol or asset_class != "us_equity" or asset_status != "active":
                raise RuntimeError(f"OPG preflight asset identity, class, or status is invalid for {symbol}")
            if type(tradable) is not bool:
                raise RuntimeError(f"OPG preflight tradability metadata is missing or invalid for {symbol}")
            if not isinstance(attributes, (list, tuple, set)):
                raise RuntimeError(f"OPG preflight asset attributes are missing or invalid for {symbol}")
            normalized_attributes = {self._opg_metadata_value(value) for value in attributes}
            if "ipo" in normalized_attributes:
                raise RuntimeError(f"OPG preflight blocks IPO asset {symbol} for MOO market orders")
            if side == "buy" and tradable is not True:
                raise RuntimeError(f"OPG preflight BUY asset {symbol} is not tradable")
            if side == "sell" and tradable is not True:
                if tradable is not False or current_quantity <= 0 or Decimal(quantity) > current_quantity:
                    raise RuntimeError(f"OPG preflight non-tradable asset {symbol} is not eligible for close-only SELL")
            asset_checks.append({
                "symbol": symbol,
                "side": side,
                "quantity": quantity,
                "asset_class": asset_class,
                "status": asset_status,
                "tradable": tradable,
                "close_only": side == "sell" and tradable is False,
            })

        if not estimated_buy_notional.is_finite() or estimated_buy_notional < 0:
            raise RuntimeError("OPG preflight aggregate BUY estimate is invalid")
        projected_gross_notional = calculate_aggregate_gross_notional(
            position_notional=current_position_notional,
            pending_buy_notional=pending_buy_notional,
            additional_buy_notional=estimated_buy_notional,
        )
        cap_check_applied = buy_orders > 0
        if cap_check_applied:
            enforce_aggregate_gross_notional_cap(
                position_notional=current_position_notional,
                pending_buy_notional=pending_buy_notional,
                additional_buy_notional=estimated_buy_notional,
            )
        if buy_orders and buying_power < estimated_buy_notional:
            raise RuntimeError(
                f"OPG preflight estimated BUY budget {estimated_buy_notional} exceeds buying power {buying_power}"
            )
        headroom = buying_power - estimated_buy_notional
        return {
            "approved": True,
            "source": "alpaca_account_and_positions_plus_alpaca_asset_metadata",
            "account_id": account_id,
            "account_snapshot_at": snapshot_at,
            "equity": float(equity),
            "buying_power": float(buying_power),
            "asset_checks": asset_checks,
            "buy_orders": buy_orders,
            "sell_orders": sell_orders,
            "estimated_buy_notional": float(estimated_buy_notional),
            "projected_position_notional": float(projected_position_notional),
            "current_position_notional": float(current_position_notional),
            "pending_buy_notional": float(pending_buy_notional),
            "planned_buy_notional": float(estimated_buy_notional),
            "projected_gross_notional": float(projected_gross_notional),
            "cap_check_applied": cap_check_applied,
            "max_gross_notional_usd": float(MAX_GROSS_NOTIONAL_USD),
            "buying_power_headroom": float(headroom),
        }

    def execute_opg_plan(self, plan, *, signal_bar_at: str, target_session: str):
        """Persist and submit one deterministic Paper-only OPG batch sequentially.

        This path is deliberately separate from execute_weights/DAY. Recovery is
        read-only and runs before idempotency/blocking checks; no residual retry or
        catch-up submission is ever performed.
        """
        lock = self.execution_lock() if hasattr(self, "state_file") else nullcontext()
        with lock:
            if getattr(self, "_current_account_identity_verified", False) is not True:
                raise RuntimeError("Current broker account identity is not verified; refusing OPG execution")
            batches = self.state.setdefault("opg_batches", {})
            if not isinstance(batches, dict) or any(
                not isinstance(batch, dict) for batch in batches.values()
            ):
                raise RuntimeError("Persisted OPG batch state is invalid; refusing execution")
            if batches:
                self._recover_opg_batches()
                batches = self.state.get("opg_batches", {})
            blocking = [
                key for key, value in batches.items()
                if value.get("status") not in {"completed", "no_orders"}
            ]
            account_id = str(self.state.get("account_id") or "").strip()
            if not account_id:
                raise RuntimeError("Verified broker account identity is required for OPG execution")
            from src.execution.opg_execution import build_opg_batch_record, build_opg_order_request

            record = build_opg_batch_record(
                account_id=account_id,
                signal_bar_at=signal_bar_at,
                target_session=target_session,
                plan=plan,
            )
            batch_id = record["batch_id"]
            if batch_id in batches:
                return copy.deepcopy(batches[batch_id])
            if blocking:
                raise RuntimeError(
                    "An unresolved or unreconciled OPG batch blocks a new batch: " + ", ".join(sorted(blocking))
                )
            if record["account_mode"] != self.state.get("account_mode"):
                raise RuntimeError("OPG batch account mode differs from persisted broker state")

            # Reconcile DAY state under the same broker execution lock before
            # OPG preflight. Terminal DAY fills are not safe to forget until the
            # broker position snapshot confirms their quantity effect.
            self._reconcile_unresolved_submissions()
            self._refresh_confirmed_submission_statuses()
            self._reconcile_terminal_order_positions()
            preflight_report = self.preflight_opg_plan(plan)
            record["preflight"] = copy.deepcopy(preflight_report)
            self._persist_opg_batch(batch_id, record)
            if record["status"] == "no_orders":
                return copy.deepcopy(record)

            for symbol in sorted(record["orders"]):
                order_record = record["orders"][symbol]
                order_record["status"] = "submission_pending"
                record["status"] = "submission_pending"
                self._persist_opg_batch(batch_id, record)
                request = build_opg_order_request(
                    symbol,
                    order_record["quantity"],
                    order_record["client_order_id"],
                    order_record["side"],
                    limit_price=order_record.get("limit_price"),
                )
                try:
                    self._assert_paper_mutation_allowed()
                    broker_order = self.trading_client.submit_order(request)
                    order_id = self._validate_opg_broker_order(broker_order, order_record)
                except Exception as submit_error:
                    # A failed/ambiguous POST is never repeated. Query the stable ID
                    # once; only a fully matching visible order can be acknowledged.
                    try:
                        broker_order = self.trading_client.get_order_by_client_id(
                            order_record["client_order_id"]
                        )
                        order_id = self._validate_opg_broker_order(broker_order, order_record)
                    except Exception as lookup_error:
                        order_record["status"] = "unresolved"
                        order_record["blocking_reason"] = "submission_outcome_unconfirmed"
                        record["status"] = "unresolved"
                        record["blocking_reason"] = str(lookup_error)[:500]
                        self._persist_opg_batch(batch_id, record)
                        raise RuntimeError(
                            f"OPG submission outcome unresolved for {symbol}; no resubmission performed"
                        ) from submit_error

                order_record.update({
                    "order_id": order_id,
                    "status": self._opg_status(broker_order),
                    "filled_qty": str(getattr(broker_order, "filled_qty", "0") or "0"),
                    "average_fill_price": (
                        str(getattr(broker_order, "filled_avg_price", None))
                        if getattr(broker_order, "filled_avg_price", None) is not None else None
                    ),
                    "acknowledged_at": datetime.now(timezone.utc).isoformat(),
                })
                self._persist_opg_batch(batch_id, record)

            record["status"] = "submission_complete"
            self._persist_opg_batch(batch_id, record)
            return copy.deepcopy(record)

    @contextmanager
    def execution_lock(self):
        """Serialize broker mutations and state refreshes for this state-file path.

        The lock is reentrant within one thread and executor instance so methods
        such as get_account_status can safely be called from an already-locked
        execution cycle. Across processes it remains an OS file lock: all
        participants must share this state-file path on a filesystem that
        supports advisory flock locks. It is not a distributed lock.
        """
        local = getattr(self, "_execution_lock_local", None)
        if local is None:
            # Keep __new__-constructed test executors compatible while ensuring
            # nested calls on the same instance/thread do not reacquire flock.
            local = threading.local()
            self._execution_lock_local = local

        depth = getattr(local, "depth", 0)
        if depth:
            local.depth = depth + 1
            try:
                yield
            finally:
                local.depth = depth
            return

        lock_path = self.state_file.with_name(
            self.state_file.name + ".execution.lock"
        )
        try:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            lock_file = open(lock_path, "a+", encoding="utf-8")
        except OSError as exc:
            raise RuntimeError(
                "Unable to open broker execution lock; refusing execution"
            ) from exc

        with lock_file:
            timeout = getattr(self, "execution_lock_timeout_seconds", 30.0)
            deadline = time.monotonic() + timeout
            acquired = False
            while not acquired:
                try:
                    fcntl.flock(
                        lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB
                    )
                    acquired = True
                except BlockingIOError as exc:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise RuntimeError(
                            "Timed out acquiring broker execution lock; refusing execution"
                        ) from exc
                    time.sleep(min(0.05, remaining))
                except OSError as exc:
                    raise RuntimeError(
                        "Unable to acquire broker execution lock; refusing execution"
                    ) from exc
            local.depth = 1
            try:
                yield
            finally:
                local.depth = 0
                try:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
                except OSError as exc:
                    raise RuntimeError(
                        "Unable to release broker execution lock; execution state requires review"
                    ) from exc

    @staticmethod
    def _positions_snapshot_signature(positions: Dict[str, Dict]) -> Dict[str, float]:
        """Canonicalize broker position quantities for pre-submit comparison."""
        if not isinstance(positions, dict):
            raise RuntimeError("Cannot verify broker position snapshot before order submission")
        signature = {}
        for raw_symbol, position in positions.items():
            symbol = str(raw_symbol).strip().upper()
            if not symbol or symbol in signature or not isinstance(position, dict):
                raise RuntimeError("Broker position snapshot is ambiguous before order submission")
            try:
                quantity = float(position.get("qty"))
            except (TypeError, ValueError, OverflowError) as exc:
                raise RuntimeError("Broker position quantity is invalid before order submission") from exc
            if not np.isfinite(quantity):
                raise RuntimeError("Broker position quantity is non-finite before order submission")
            signature[symbol] = quantity
        return signature

    def _open_orders_snapshot_signature(self, orders) -> tuple:
        """Canonicalize active broker orders, including fields affecting exposure."""
        if not isinstance(orders, (list, tuple)):
            raise RuntimeError("Cannot verify broker open-order snapshot before order submission")
        signature = []
        seen_ids = set()
        for order in orders:
            order_id = str(getattr(order, "id", "") or "").strip()
            symbol = str(getattr(order, "symbol", "") or "").strip().upper()
            side_value = getattr(order, "side", "")
            side = str(getattr(side_value, "value", side_value) or "").strip().lower()
            status = self._status_text(getattr(order, "status", None), default="")
            if not order_id or not symbol or not status or order_id in seen_ids:
                raise RuntimeError("Broker open-order snapshot is ambiguous before order submission")
            seen_ids.add(order_id)
            raw_qty = getattr(order, "qty", None)
            raw_filled = getattr(order, "filled_qty", None)
            signature.append((order_id, symbol, side, str(raw_qty), str(raw_filled), status))
        return tuple(sorted(signature))

    def _verify_execution_snapshot(
        self, account: Dict, positions: Dict[str, Dict], open_orders
    ) -> None:
        """Re-read broker state immediately before the first order and fail on drift.

        Alpaca exposes account, positions, and orders through separate reads rather
        than one atomic portfolio snapshot. This detects observable changes between
        sizing and submission; it cannot eliminate the final API/network race.
        """
        latest_account = self.get_account_status()
        if not isinstance(latest_account, dict) or latest_account.get("account_data_fresh") is not True:
            raise RuntimeError("Broker account snapshot became stale before order submission")
        for field in ("equity", "portfolio_value", "buying_power"):
            try:
                before = float(account[field])
                after = float(latest_account[field])
            except (KeyError, TypeError, ValueError, OverflowError) as exc:
                raise RuntimeError(
                    f"Cannot verify broker {field} before order submission"
                ) from exc
            if not np.isfinite(before) or not np.isfinite(after) or abs(before - after) > 0.01:
                raise RuntimeError(
                    f"Broker {field} changed after sizing; refusing order submission"
                )
        before_account_id = str(account.get("account_id") or "").strip()
        after_account_id = str(latest_account.get("account_id") or "").strip()
        if before_account_id != after_account_id:
            raise RuntimeError("Broker account identity changed before order submission")
        if account.get("account_identity_verified") is True and latest_account.get(
            "account_identity_verified"
        ) is not True:
            raise RuntimeError("Broker account identity is no longer verified before order submission")

        latest_positions = self.get_positions()
        if self._positions_snapshot_signature(positions) != self._positions_snapshot_signature(
            latest_positions
        ):
            raise RuntimeError(
                "Broker positions changed after sizing; refusing order submission"
            )

        latest_orders = self._get_open_orders()
        if self._open_orders_snapshot_signature(
            open_orders
        ) != self._open_orders_snapshot_signature(latest_orders):
            raise RuntimeError(
                "Broker open orders changed after sizing; refusing order submission"
            )

    def _verify_quote_before_submission(self, ticker: str, expected_price: float) -> float:
        """Refresh the quote after broker preflight and reject stale or changed sizing data."""
        latest_prices = self._get_prices_from_bars([ticker])
        latest_price = latest_prices.get(ticker) if isinstance(latest_prices, dict) else None
        try:
            latest_price = float(latest_price)
            expected_price = float(expected_price)
        except (TypeError, ValueError, OverflowError) as exc:
            raise RuntimeError(
                f"Cannot verify a fresh market quote for {ticker} immediately before submission"
            ) from exc
        if (
            not np.isfinite(latest_price)
            or latest_price <= 0
            or not np.isfinite(expected_price)
            or expected_price <= 0
        ):
            raise RuntimeError(
                f"Cannot verify a fresh market quote for {ticker} immediately before submission"
            )
        # A new quote must still support the price used to calculate exposure and
        # quantity. If it moved, abort and let the next cycle rebuild all sizing.
        if not np.isclose(latest_price, expected_price, rtol=1e-9, atol=1e-8):
            raise RuntimeError(
                f"Market quote changed after sizing for {ticker}; refusing order submission"
            )
        return latest_price

    def execute_weights(self, weights: PortfolioWeights, min_notional: float = 1.0) -> Dict:
        """Run the complete broker execution cycle under the shared OS lock."""
        with self.execution_lock():
            return self._execute_weights_locked(weights, min_notional)

    def _execute_weights_locked(
        self, weights: PortfolioWeights, min_notional: float = 1.0
    ) -> Dict:
        """Run reconciliation, risk checks, and broker mutations under the execution lock."""
        self._reconcile_unresolved_submissions()
        self._refresh_confirmed_submission_statuses()
        self._reconcile_terminal_order_positions()
        opg_batches = self.state.get("opg_batches", {})
        blocking_opg = [
            batch_id for batch_id, batch in opg_batches.items()
            if isinstance(batch, dict) and batch.get("status") not in {"completed", "no_orders"}
        ] if isinstance(opg_batches, dict) else ["invalid_state"]
        if blocking_opg:
            raise RuntimeError(
                "Persisted OPG batch requires read-only recovery before DAY execution: "
                + ", ".join(sorted(blocking_opg))
            )
        unresolved_submissions = self.state.get("order_submission_unresolved", {})
        confirmed_submissions = self.state.get("order_submission_confirmed", {})
        active_confirmed = {
            ticker: order
            for ticker, order in confirmed_submissions.items()
            if self._status_text(order.get("status", "unknown"), default="unknown") not in {
                "filled", "canceled", "cancelled", "expired", "rejected"
            }
        }
        if unresolved_submissions or active_confirmed:
            symbols = sorted(set(unresolved_submissions) | {
                str(order.get("ticker", client_id))
                for client_id, order in active_confirmed.items()
            })
            raise RuntimeError(
                "Broker order submission requires reconciliation or terminal status; refusing "
                f"further orders for: {', '.join(symbols)}"
            )

        target_weights = np.asarray(weights.weights, dtype=float)
        if target_weights.shape != (len(weights.tickers),) or not np.isfinite(target_weights).all():
            raise ValueError("Target weights must be a finite vector matching tickers")
        if any(not isinstance(ticker, str) or not ticker.strip() for ticker in weights.tickers):
            raise ValueError("Target tickers must be non-empty strings")
        normalized_tickers = [ticker.strip().upper() for ticker in weights.tickers]
        if len(set(normalized_tickers)) != len(normalized_tickers):
            raise ValueError("Target tickers must be unique; duplicate symbols make allocation ambiguous")
        # Use the same canonical symbols for quote lookup, position matching,
        # risk sizing, and order submission—not only for duplicate detection.
        weights.tickers = normalized_tickers
        if (target_weights < 0).any():
            raise ValueError("Negative target weights are unsupported; refusing to open or increase short positions")
        if float(target_weights.sum()) > 1.0 + 1e-6:
            raise ValueError("Target weights gross allocation must not exceed 1")

        self.logger.info(f"📤 Executing weights for {len(weights.tickers)} assets")

        account = self.get_account_status()
        if account.get("account_data_fresh") is not True:
            raise RuntimeError("Cannot verify current broker account equity; refusing to execute target weights")
        broker_portfolio_value = float(account["portfolio_value"])
        portfolio_value = float(account["equity"])
        if not np.isfinite(portfolio_value) or portfolio_value <= 0:
            raise RuntimeError("Cannot execute weights with invalid broker equity")
        buying_power = float(account.get("buying_power", 0.0) or 0.0)
        open_orders = self._get_open_orders()
        metadata = getattr(weights, "metadata", None) or {}
        circuit_breaker_triggered = bool(metadata.get("circuit_breaker_triggered", False))
        liquidation_requested = bool(metadata.get("liquidate_on_circuit_breaker", False))

        # Explicit emergency liquidation is a distinct execution path. It must not
        # depend on price-based exposure estimates, parsed position market values,
        # or allocation gates. _liquidate_all_positions still cancels and verifies
        # open orders before asking the broker to close every position.
        if circuit_breaker_triggered and liquidation_requested:
            self.logger.warning(
                "Drawdown circuit breaker: bypassing allocation gates for explicit liquidation"
            )
            # Establish a verified pre-execution baseline before mutating the broker.
            self._ensure_verified_metrics_baseline(portfolio_value)
            # Mark the pre-liquidation snapshot stale before any broker mutation;
            # liquidation may raise midway through a sequence of close requests.
            self.state["account_data_fresh"] = False
            self._save_state()
            orders_executed = self._liquidate_all_positions(open_orders)
            # The liquidation orders and their pending IDs are persisted by
            # _liquidate_all_positions. Treat bookkeeping as a separate commit:
            # if its save fails, restore the in-memory counters to the last durable
            # values while preserving the already-persisted liquidation outcome.
            previous_execution_time = self.state.get("last_execution_time")
            previous_total_orders = self.state.get("total_orders", 0)
            self.state["last_execution_time"] = datetime.now().isoformat()
            self.state["total_orders"] = previous_total_orders + self._count_submitted_orders(orders_executed)
            try:
                self._save_state()
            except Exception:
                self.state["last_execution_time"] = previous_execution_time
                self.state["total_orders"] = previous_total_orders
                raise
            metrics_refresh_error = None
            metrics_refreshed = False
            post_account = None
            metrics_before_refresh = None
            try:
                # Metrics must use a broker snapshot acquired after liquidation,
                # paired with positions from the post-execution phase.
                post_account = self.get_account_status()
                if post_account.get("account_data_fresh") is not True:
                    raise RuntimeError("Post-liquidation broker equity is not fresh")
                post_equity = float(post_account["equity"])
                if not np.isfinite(post_equity) or post_equity <= 0:
                    raise RuntimeError("Post-liquidation broker equity is invalid")
                post_liquidation_positions = self.get_positions()
                # Snapshot after the broker read, which may re-anchor metrics if
                # the current account response lacks a verifiable account ID.
                metrics_before_refresh = copy.deepcopy(self.metrics)
                self.metrics.update(post_equity, post_liquidation_positions)
                self._persist_metrics_history()
                metrics_refreshed = True
            except Exception as exc:
                # Do not export an in-memory equity observation that was not durably
                # committed to broker_state.json.
                if metrics_before_refresh is not None:
                    self.metrics = metrics_before_refresh
                metrics_refresh_error = str(exc)
                self.logger.error("Emergency liquidation submitted but metrics refresh failed: %s", exc)
            metrics = self.metrics.get_all_metrics()
            metrics_export_error = None
            try:
                self.metrics.write_metrics_json(metrics, "logs/metrics.json")
            except Exception as exc:
                # Reporting failure must not hide the outcome of broker operations.
                metrics_export_error = str(exc)
                self.logger.error("Emergency liquidation completed but metrics export failed: %s", exc)
            post_snapshot_fresh = bool(post_account and post_account.get("account_data_fresh") is True)
            result = {
                "orders": orders_executed,
                "portfolio_value": post_account.get("portfolio_value") if post_snapshot_fresh else None,
                "equity": post_account.get("equity") if post_snapshot_fresh else None,
                "pre_execution_portfolio_value": broker_portfolio_value,
                "pre_execution_equity": portfolio_value,
                "account_data_fresh": post_snapshot_fresh,
                "account_snapshot_at": post_account.get("account_snapshot_at") if post_snapshot_fresh else None,
                "metrics_equity": post_account.get("equity") if metrics_refreshed else None,
                "circuit_breaker_triggered": True,
                "liquidation_requested": True,
                "timestamp": datetime.now().isoformat(),
                "metrics": metrics,
            }
            if metrics_refresh_error is not None:
                result["metrics_refresh_error"] = metrics_refresh_error
            if metrics_export_error is not None:
                result["metrics_export_error"] = metrics_export_error
            return result

        # A drawdown breaker blocks new target execution by default. Zero weights
        # otherwise mean "close this position" in the normal rebalance path, which
        # would silently turn a non-liquidating breaker into a full liquidation.
        if circuit_breaker_triggered and not liquidation_requested:
            self.logger.warning(
                "Drawdown circuit breaker active; skipping target execution because liquidation was not requested"
            )
            return {
                "orders": [],
                "portfolio_value": broker_portfolio_value,
                "equity": portfolio_value,
                "pre_execution_portfolio_value": broker_portfolio_value,
                "pre_execution_equity": portfolio_value,
                "account_data_fresh": True,
                "account_snapshot_at": account.get("account_snapshot_at"),
                "metrics_equity": None,
                "circuit_breaker_triggered": True,
                "liquidation_requested": False,
                "timestamp": datetime.now().isoformat(),
                "metrics": self.metrics.get_all_metrics(),
            }

        # Financial freshness and account identity are separate guarantees.
        # A fresh account response without its ID may support unlinked metrics,
        # but it must not authorize a new allocation against persisted broker state.
        # Explicit emergency liquidation above remains a distinct risk-reducing path.
        if account.get("account_identity_verified") is False:
            raise RuntimeError(
                "Cannot verify current broker account identity; refusing new target execution"
            )

        positions = self.get_positions()
        # Enforce corporate-action reconciliation at the broker execution
        # boundary, not only in PPO inference. This protects direct executor
        # callers while retaining partial target vectors: the configured model
        # universe is supplied separately from the requested target symbols.
        if not self.configured_symbols:
            raise RuntimeError(
                "Configured corporate-action universe is unavailable; refusing broker execution"
            )
        configured_universe = set(self.configured_symbols)
        unconfigured_targets = sorted(set(weights.tickers) - configured_universe)
        if unconfigured_targets:
            raise RuntimeError(
                "Target weights contain symbols outside the configured PPO universe; "
                "refusing broker execution: " + ", ".join(unconfigured_targets)
            )
        self.reconcile_corporate_actions(
            self.configured_symbols,
            positions=positions,
        )
        # Direct executor callers must receive the same held-symbol protection
        # as PPO inference. Do not silently interpret an external holding as a
        # zero target merely because it is absent from a partial target vector.
        unconfigured_positions = []
        for symbol, position in positions.items():
            if str(symbol).strip().upper() in configured_universe:
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
                unconfigured_positions.append(str(symbol).strip().upper())
        if unconfigured_positions:
            raise RuntimeError(
                "Broker holds positions outside the configured PPO universe; "
                "refusing execution: " + ", ".join(sorted(unconfigured_positions))
            )
        # A missing symbol in the target vector means a zero target, so include
        # existing holdings in the execution universe for explicit rebalancing.
        execution_tickers = list(dict.fromkeys([*weights.tickers, *positions.keys()]))
        prices = self._get_prices_from_bars(execution_tickers)

        # Risk sizing must use broker quantity marked to a recent quote, not the
        # broker's market_value field alone (which can lag the quote or be
        # internally inconsistent with quantity). If any existing position
        # cannot be marked reliably, fail closed before submitting any order.
        marked_positions = {}
        for ticker, position in positions.items():
            try:
                quantity = float(position.get("qty"))
            except (TypeError, ValueError) as exc:
                raise RuntimeError(
                    f"Cannot safely calculate exposure: invalid broker quantity for {ticker}"
                ) from exc
            price = prices.get(ticker)
            if (
                not np.isfinite(quantity)
                or price is None
                or not np.isfinite(float(price))
                or float(price) <= 0
            ):
                raise RuntimeError(
                    f"Cannot safely calculate exposure: missing or invalid recent price for held position {ticker}"
                )
            marked_positions[ticker] = {
                **position,
                "market_value": quantity * float(price),
                "risk_mark_price": float(price),
            }
        positions = marked_positions
        # This is the shared hard USD cap used by both DAY and OPG. Existing
        # BUY orders reserve their full notional (including partial fills);
        # pending SELL orders do not increase gross exposure.
        gross_exposure, pending_buy_notional = self._get_aggregate_gross_notional(
            positions, open_orders, prices=prices
        )
        reserved_notional = self._get_reserved_notional(open_orders, prices=prices)
        if reserved_notional > 0:
            self.logger.warning(
                f"Notional già impegnato dagli ordini aperti: {reserved_notional:.2f}"
            )

        # Buying power limits new purchases, not risk-reducing sells. Keep it
        # separate from the drawdown/exposure gate so zero cash cannot suppress sells.
        available_buying_power = (
            max(0.0, buying_power - reserved_notional) if buying_power > 0 else 0.0
        )
        tradable_budget, risk_block = self._risk_gate(portfolio_value, portfolio_value)
        if risk_block:
            self.logger.warning(f"Risk gate attivo: {risk_block}")

        self.logger.info(
            f"Budget esecuzione: portfolio_value={portfolio_value:.2f}, "
            f"buying_power={buying_power:.2f}, tradable_budget={tradable_budget:.2f}"
        )
        risk_engine = RiskEngine(
            max_exposure_pct=RISK_LIMITS["max_portfolio_exposure"]
        )
        portfolio_manager = PortfolioManager()
        weights, pm_block = portfolio_manager.allocate(weights, portfolio_value, positions, reserved_notional=reserved_notional)
        if pm_block:
            self.logger.warning(f"Portfolio manager block: {pm_block}")

        metadata = getattr(weights, "metadata", None) or {}
        circuit_breaker_triggered = bool(metadata.get("circuit_breaker_triggered", False))
        liquidation_requested = bool(metadata.get("liquidate_on_circuit_breaker", False))
        # Anchor the metric series to verified pre-execution equity. The next
        # observation must come from a new post-execution broker snapshot.
        self._ensure_verified_metrics_baseline(portfolio_value)
        orders_executed = []
        target_weight_by_ticker = dict(zip(weights.tickers, weights.weights))
        open_order_symbols = {
            str(getattr(order, "symbol", "") or "").strip().upper()
            for order in open_orders
            if str(getattr(order, "symbol", "") or "").strip()
        }
        if circuit_breaker_triggered:
            if liquidation_requested:
                self.logger.warning("Drawdown circuit breaker: liquidating all open positions by explicit policy")
                orders_executed = self._liquidate_all_positions(open_orders)
            else:
                self.logger.warning(
                    "Drawdown circuit breaker: blocking new orders; existing positions are retained"
                )
        elif risk_block:
            self.logger.warning(
                "Risk gate blocks rebalancing; existing positions are retained"
            )
        elif pm_block:
            self.logger.warning(
                "Portfolio manager blocked the allocation; no target orders will be submitted"
            )
        else:
            if not np.isfinite(min_notional) or min_notional < 0:
                raise ValueError("min_notional must be finite and non-negative")

            # Preflight every required quote before submitting any order. Skipping a
            # missing quote can leave a target position open while other symbols trade.
            required_price_tickers = []
            for ticker in execution_tickers:
                if ticker in open_order_symbols:
                    continue
                target_weight = float(target_weight_by_ticker.get(ticker, 0.0))
                target_notional = portfolio_value * target_weight
                current_notional = float(
                    positions.get(ticker, {}).get("market_value", 0.0) or 0.0
                )
                bounded_target, symbol_risk_block = risk_engine.allow(
                    ticker,
                    target_notional,
                    portfolio_value,
                    positions,
                    reserved_notional=reserved_notional,
                )
                if symbol_risk_block:
                    bounded_target = 0.0
                delta_notional = abs(bounded_target - current_notional)
                if delta_notional > 1e-8 and delta_notional >= min_notional:
                    required_price_tickers.append(ticker)

            missing_prices = [
                ticker for ticker in required_price_tickers
                if ticker not in prices
                or not np.isfinite(prices[ticker])
                or prices[ticker] <= 0
            ]
            if missing_prices:
                raise RuntimeError(
                    "Cannot safely rebalance; valid prices unavailable for: "
                    + ", ".join(sorted(missing_prices))
                )

            for ticker in execution_tickers:
                target_weight = float(target_weight_by_ticker.get(ticker, 0.0))
                if ticker in open_order_symbols:
                    self.logger.warning(
                        "Open order already exists for %s; skipping to avoid duplicate orders",
                        ticker,
                    )
                    continue

                # Weights describe desired positions as fractions of account equity.
                target_notional = portfolio_value * float(target_weight)
                current_notional = float(
                    positions.get(ticker, {}).get("market_value", 0.0) or 0.0
                )
                bounded_target, symbol_risk_block = risk_engine.allow(
                    ticker,
                    target_notional,
                    portfolio_value,
                    positions,
                    reserved_notional=reserved_notional,
                )
                if symbol_risk_block:
                    self.logger.warning(
                        "Risk engine block %s: %s; reducing target to flat",
                        ticker,
                        symbol_risk_block,
                    )
                    bounded_target = 0.0

                # Submit only the signed delta needed to reach the bounded target.
                order_notional = bounded_target - current_notional
                if order_notional > 0:
                    cover_notional = (
                        min(order_notional, abs(current_notional))
                        if current_notional < 0
                        else 0.0
                    )
                    additional_exposure = order_notional - cover_notional
                    if additional_exposure > available_buying_power:
                        order_notional = cover_notional + available_buying_power
                    if order_notional <= 0:
                        continue
                if abs(order_notional) < min_notional:
                    continue

                side = OrderSide.BUY if order_notional > 0 else OrderSide.SELL
                price = prices.get(ticker, 0)
                if price <= 0:
                    self.logger.warning(f"Prezzo non disponibile per {ticker}, skip")
                    continue

                qty = round(abs(order_notional) / price, 6)
                if qty < 0.000001:
                    continue

                # Every rebalance cycle submits at most one order. The next cycle
                # must rebuild account, positions, open orders, and prices from fresh
                # broker snapshots before another symbol can be submitted.
                self._verify_execution_snapshot(account, positions, open_orders)
                # Broker preflight can take time. Re-fetch and revalidate the quote
                # after those reads so stale or moved pricing cannot authorize an
                # order sized from the earlier snapshot.
                price = self._verify_quote_before_submission(ticker, price)
                qty = round(abs(order_notional) / price, 6)
                if side == OrderSide.BUY:
                    raw_position = positions.get(ticker, {})
                    try:
                        signed_position_qty = Decimal(str(raw_position.get("qty", 0)))
                        verified_price = Decimal(str(price))
                    except (InvalidOperation, TypeError, ValueError) as exc:
                        raise RuntimeError(
                            f"Cannot determine BUY exposure for {ticker}: invalid position quantity or price"
                        ) from exc
                    if not signed_position_qty.is_finite() or not verified_price.is_finite() or verified_price <= 0:
                        raise RuntimeError(
                            f"Cannot determine BUY exposure for {ticker}: invalid position quantity or price"
                        )
                    # A BUY that covers an existing short reduces gross exposure.
                    # Permit that cover even when the account is already above cap;
                    # only the residual BUY that opens/increases a long is capped.
                    cover_qty = min(Decimal(str(qty)), max(Decimal("0"), -signed_position_qty))
                    cover_notional_decimal = cover_qty * verified_price
                    cover_notional = float(cover_notional_decimal)
                    gross_after_cover = max(0.0, gross_exposure - cover_notional)
                    available_gross_notional = max(
                        0.0, float(MAX_GROSS_NOTIONAL_USD) - gross_after_cover
                    )
                    cap_quantity = float(cover_qty) + (
                        np.floor((available_gross_notional / price) * 1_000_000) / 1_000_000
                    )
                    qty = min(qty, cap_quantity)
                    if qty < 0.000001:
                        self.logger.warning(
                            "Aggregate gross-notional cap blocks new BUY for %s "
                            "(gross=%.2f USD, cap=%.2f USD)",
                            ticker, gross_exposure, float(MAX_GROSS_NOTIONAL_USD),
                        )
                        continue
                    new_long_qty = max(0.0, qty - float(cover_qty))
                    if new_long_qty > 0:
                        position_notional = calculate_position_gross_notional(positions, prices)
                        enforce_aggregate_gross_notional_cap(
                            position_notional=max(
                                Decimal("0"), position_notional - cover_notional_decimal
                            ),
                            pending_buy_notional=pending_buy_notional,
                            additional_buy_notional=new_long_qty * price,
                        )
                if qty < 0.000001:
                    continue

                client_order_id = str(uuid.uuid4())
                # Persist intent before calling the broker: a lost response must not
                # cause an automatic duplicate order on this or a later process run.
                unresolved = dict(self.state.get("order_submission_unresolved", {}))
                order_type = "limit" if side == OrderSide.BUY else "market"
                limit_price = price if side == OrderSide.BUY else None
                unresolved[ticker] = {
                    "client_order_id": client_order_id,
                    "side": side.value,
                    "quantity": qty,
                    "price": price,
                    "order_type": order_type,
                    "limit_price": limit_price,
                    "pre_order_position_qty": float(
                        positions.get(ticker, {}).get("qty", 0.0) or 0.0
                    ),
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "reason": "submission_outcome_unconfirmed",
                }
                self.state["order_submission_unresolved"] = unresolved
                self._save_state()
                try:
                    if side == OrderSide.BUY:
                        order_req = LimitOrderRequest(
                            symbol=ticker,
                            qty=qty,
                            side=side,
                            time_in_force=TimeInForce.DAY,
                            limit_price=price,
                            client_order_id=client_order_id,
                        )
                    else:
                        order_req = MarketOrderRequest(
                            symbol=ticker,
                            qty=qty,
                            side=side,
                            time_in_force=TimeInForce.DAY,
                            client_order_id=client_order_id,
                        )
                    self._assert_paper_mutation_allowed()
                    order = self.trading_client.submit_order(order_req)
                    order_id = getattr(order, "id", None)
                    if not order_id:
                        raise RuntimeError("Broker response did not include an order ID")
                    broker_order_type = str(
                        getattr(getattr(order, "type", None), "value", getattr(order, "type", None)) or ""
                    ).strip().lower()
                    if broker_order_type != order_type:
                        raise RuntimeError("Broker acknowledged order type differs from persisted intent")
                    if order_type == "limit":
                        try:
                            broker_limit_price = Decimal(str(getattr(order, "limit_price", None)))
                        except (InvalidOperation, TypeError, ValueError) as exc:
                            raise RuntimeError("Broker acknowledgement limit price is missing or invalid") from exc
                        if not broker_limit_price.is_finite() or broker_limit_price != Decimal(str(limit_price)):
                            raise RuntimeError("Broker acknowledged limit price differs from persisted intent")
                    order_info = {
                        "ticker": ticker,
                        "side": side.value,
                        "quantity": qty,
                        "order_id": str(order_id),
                        "client_order_id": client_order_id,
                        "status": self._status_text(order.status, default="unknown"),
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "price": price,
                        "order_type": order_type,
                        "limit_price": limit_price,
                        "pre_order_position_qty": float(
                            positions.get(ticker, {}).get("qty", 0.0) or 0.0
                        ),
                    }
                    # Only report an acknowledged order after its confirmed state
                    # has been durably persisted. If persistence fails, the exception
                    # path below records a single unresolved outcome instead.
                    self._record_confirmed_submission(ticker, order_info)
                    orders_executed.append(order_info)
                    self.logger.info(f"  {side.value.upper()} {qty:.4f} {ticker} @ ~${price:.2f}")
                    # A successful submission changes broker exposure/order state.
                    # Stop here; a later rebalance cycle will use fresh broker data.
                    break
                except Exception as e:
                    # An exception does not prove that the broker rejected the order.
                    # Restore the durable intent even if a post-submit state write failed.
                    unresolved[ticker] = {
                        "client_order_id": client_order_id,
                        "side": side.value,
                        "quantity": qty,
                        "price": price,
                        "order_type": order_type,
                        "limit_price": limit_price,
                        "pre_order_position_qty": float(
                            positions.get(ticker, {}).get("qty", 0.0) or 0.0
                        ),
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "reason": "submission_outcome_unconfirmed",
                    }
                    self.state["order_submission_unresolved"] = unresolved
                    try:
                        self._save_state()
                    except Exception as persist_exc:
                        self.logger.critical(
                            "Unable to persist ambiguous order intent for %s: %s",
                            ticker, persist_exc,
                        )
                    self.logger.error("Ambiguous submission outcome for %s; halting rebalance: %s", ticker, e)
                    orders_executed.append({
                        "ticker": ticker,
                        "side": side.value,
                        "quantity": qty,
                        "order_id": None,
                        "client_order_id": client_order_id,
                        "status": "unresolved",
                        "error": str(e),
                        "timestamp": datetime.now().isoformat(),
                        "price": price,
                    })
                    # Do not attempt any further symbol after an ambiguous submit.
                    break

        # Orders may have changed broker holdings after the captured account
        # snapshot. Keep its values as historical evidence, not as current state.
        self.state["account_data_fresh"] = False
        self.state["last_execution_time"] = datetime.now().isoformat()
        self.state["total_orders"] += self._count_submitted_orders(orders_executed)
        self._save_state()

        # Refresh broker equity after order submission. Never combine pre-order
        # equity with post-order positions in a single metric observation.
        metrics_refresh_error = None
        metrics_refreshed = False
        post_account = None
        try:
            post_account = self.get_account_status()
            if post_account.get("account_data_fresh") is not True:
                raise RuntimeError("Post-execution broker equity is not fresh")
            post_equity = float(post_account["equity"])
            if not np.isfinite(post_equity) or post_equity <= 0:
                raise RuntimeError("Post-execution broker equity is invalid")
            new_positions = self.get_positions()
            self.metrics.update(post_equity, new_positions)
            self._persist_metrics_history()
            metrics_refreshed = True
        except Exception as exc:
            metrics_refresh_error = str(exc)
            self.logger.error("Orders submitted but post-execution metrics refresh failed: %s", exc)
        metrics = self.metrics.get_all_metrics()
        self.metrics.write_metrics_json(metrics, "logs/metrics.json")
        post_snapshot_fresh = bool(post_account and post_account.get("account_data_fresh") is True)
        result = {
            "orders": orders_executed,
            "portfolio_value": post_account.get("portfolio_value") if post_snapshot_fresh else None,
            "equity": post_account.get("equity") if post_snapshot_fresh else None,
            "pre_execution_portfolio_value": broker_portfolio_value,
            "pre_execution_equity": portfolio_value,
            "account_data_fresh": post_snapshot_fresh,
            "account_snapshot_at": post_account.get("account_snapshot_at") if post_snapshot_fresh else None,
            "metrics_equity": post_account.get("equity") if metrics_refreshed else None,
            "circuit_breaker_triggered": circuit_breaker_triggered,
            "liquidation_requested": circuit_breaker_triggered and liquidation_requested,
            "timestamp": datetime.now().isoformat(),
            "metrics": metrics,
        }
        if metrics_refresh_error is not None:
            result["metrics_refresh_error"] = metrics_refresh_error

        self.logger.info(f"✅ Eseguiti {len(orders_executed)} ordini")
        self.logger.info(
    f"📊 Metrics: DD={metrics.get('max_drawdown', 0.0):.2%}, "
    f"Sharpe={metrics.get('sharpe_ratio', metrics.get('sharpe', 0.0)):.2f}, "
    f"Regime={metrics.get('regime', 'n/a')}"
)
        return result

    def _get_prices_from_bars(self, tickers: List[str]) -> Dict[str, float]:
        """Return only positive prices backed by recent, timestamped quotes.

        Daily bar closes can be hours old during a live session and are not a
        safe fallback for sizing market orders. Missing, malformed, stale, or
        implausibly future-dated quotes are omitted; the execution preflight
        then blocks the rebalance before submitting any order.
        """
        max_quote_age = timedelta(minutes=5)
        future_tolerance = timedelta(seconds=30)
        now = datetime.now(timezone.utc)
        try:
            quotes = self.alpaca_feed.get_latest_quotes(tickers)
            if not isinstance(quotes, dict):
                raise RuntimeError("Market data provider returned an invalid quote snapshot")
            prices = {}
            for ticker in tickers:
                quote = quotes.get(ticker)
                if not isinstance(quote, dict):
                    continue
                try:
                    price = float(quote.get("price"))
                    raw_timestamp = quote.get("timestamp")
                    if isinstance(raw_timestamp, datetime):
                        quote_timestamp = raw_timestamp
                    elif raw_timestamp:
                        quote_timestamp = datetime.fromisoformat(
                            str(raw_timestamp).replace("Z", "+00:00")
                        )
                    else:
                        continue
                    if quote_timestamp.tzinfo is None:
                        continue
                    quote_timestamp = quote_timestamp.astimezone(timezone.utc)
                    age = now - quote_timestamp
                    if (
                        not np.isfinite(price)
                        or price <= 0
                        or age > max_quote_age
                        or age < -future_tolerance
                    ):
                        self.logger.warning(
                            "Rejecting invalid or stale market quote for %s (age=%s)",
                            ticker, age,
                        )
                        continue
                    prices[ticker] = price
                except (TypeError, ValueError, OverflowError):
                    self.logger.warning("Rejecting malformed market quote for %s", ticker)
            return prices
        except Exception as e:
            self.logger.warning(f"Latest market quote read failed; refusing stale-bar fallback: {e}")
            return {}
