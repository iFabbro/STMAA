"""Broker Executor per Alpaca (paper/live trading) – alpaca-py."""
import os
import json
import logging
import tempfile
import numpy as np
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime, timedelta, timezone

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, GetOrdersRequest
from alpaca.trading.enums import OrderSide, TimeInForce, QueryOrderStatus

from src.strategy.weight_interface import PortfolioWeights
from src.data.alpaca_feed import AlpacaFeed
from src.utils.metrics import PortfolioMetrics

RISK_LIMITS = {
    "max_portfolio_exposure": float(os.getenv("MAX_PORTFOLIO_EXPOSURE", "0.50")),
    "max_drawdown": float(os.getenv("MAX_DRAWDOWN", "0.15")),
}


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

logs_dir = Path("logs/execution")
logs_dir.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(logs_dir / "broker_executor.log"),
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
        if portfolio_value <= 0:
            return 0.0, "portfolio_value_non_positive"
        if abs(target_notional) < 1e-12:
            # A zero target is an intentional request to close an existing position.
            return 0.0, None
        current = abs(float(positions.get(ticker, {}).get("market_value", 0.0) or 0.0))
        # Esposizione lorda attuale = ordini aperti riservati + tutte le posizioni.
        gross = self._portfolio_gross(positions, reserved_notional=reserved_notional)
        max_position = portfolio_value * self.max_position_pct
        # Budget residuo di esposizione totale. `current` viene restituito al budget
        # perche' target_notional e' la dimensione desiderata della posizione su
        # `ticker`, che sostituisce quella gia' presente in `gross`.
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
    ):
        self.api_key = api_key or os.getenv("ALPACA_API_KEY", "")
        self.api_secret = api_secret or os.getenv("ALPACA_API_SECRET", "")
        self.base_url = base_url
        self.paper_trading = paper_trading
        self.initial_capital = initial_capital
        self.logger = logging.getLogger(__name__)

        if not self.api_key or not self.api_secret:
            raise ValueError(
                "ALPACA_API_KEY e ALPACA_API_SECRET devono essere impostati nel .env"
            )

        self.trading_client = TradingClient(
            api_key=self.api_key,
            secret_key=self.api_secret,
            paper=self.paper_trading,
        )

        self.alpaca_feed = AlpacaFeed(
            api_key=self.api_key,
            api_secret=self.api_secret,
        )

        self.metrics = PortfolioMetrics(initial_capital=initial_capital)

        self.state_file = Path("logs/execution/broker_state.json")
        self.state = {
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
        }
        self._load_state()
        self._restore_metrics_history()
        # A persisted snapshot is historical evidence, never a fresh broker read.
        self.state["account_data_fresh"] = False
        self.logger.info(f"✅ BrokerExecutor inizializzato – paper={paper_trading}")

    def _load_state(self):
        if not self.state_file.exists():
            self._save_state()
            return

        try:
            with open(self.state_file, encoding="utf-8") as state_file:
                loaded_state = json.load(state_file)
            if not isinstance(loaded_state, dict):
                raise ValueError("persisted broker state must be a JSON object")
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
        except Exception as exc:
            # A corrupt/unreadable state may contain the only record of an accepted
            # close request. Never continue with an empty in-memory state: that could
            # cause a duplicate close after restart.
            raise RuntimeError(
                "Unable to load persisted broker state; refusing execution with unverified liquidation state"
            ) from exc

        self.state.update(loaded_state)
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
        """Anchor metrics to verified broker equity on first use, not configured capital."""
        if self.state.get("metrics_baseline_source") == "verified_account":
            return
        value = float(equity)
        if not np.isfinite(value) or value <= 0:
            raise RuntimeError("Cannot establish metrics baseline from invalid broker equity")
        self.metrics = PortfolioMetrics(initial_capital=value)
        self.state["equity_history"] = [value]
        self.state["metrics_baseline_source"] = "verified_account"
        self.state["metrics_account_mode"] = self.state.get("account_mode")
        self.state["metrics_account_id"] = self.state.get("account_id")
        self._save_state()

    def _persist_metrics_history(self):
        self.state["equity_history"] = list(self.metrics.equity_curve)
        self.state["metrics_baseline_source"] = "verified_account"
        self.state["metrics_account_mode"] = self.state.get("account_mode")
        self.state["metrics_account_id"] = self.state.get("account_id")
        self._save_state()

    def _save_state(self):
        """Persist state atomically so an interrupted write cannot truncate it."""
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.state_file.parent,
                prefix=f".{self.state_file.name}.",
                suffix=".tmp",
                delete=False,
            ) as state_file:
                temp_path = Path(state_file.name)
                json.dump(self.state, state_file)
                state_file.flush()
                os.fsync(state_file.fileno())
            os.replace(temp_path, self.state_file)
        except Exception:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    self.logger.exception("Unable to remove temporary broker state file %s", temp_path)
            raise

    def get_account_status(self) -> Dict:
        try:
            account = self.trading_client.get_account()
            data = {
                "cash": float(account.cash),
                "portfolio_value": float(account.portfolio_value),
                "buying_power": float(account.buying_power),
                "equity": float(account.equity),
                "daytrade_count": account.daytrade_count,
                "status": account.status,
                "account_data_fresh": True,
                "account_data_source": "alpaca_account",
                "account_snapshot_at": datetime.now(timezone.utc).isoformat(),
                "account_id": str(getattr(account, "id", "") or "") or None,
            }
            stored_account_id = self.state.get("account_id")
            if stored_account_id and data["account_id"] and stored_account_id != data["account_id"]:
                raise ValueError("Broker account ID differs from persisted state; refusing to mix account history")
            numeric_values = (data["cash"], data["portfolio_value"], data["buying_power"], data["equity"])
            if not all(np.isfinite(value) for value in numeric_values):
                raise ValueError("Broker account returned non-finite account values")
            if data["portfolio_value"] <= 0 or data["equity"] <= 0 or data["buying_power"] < 0:
                raise ValueError("Broker account returned invalid portfolio value, equity, or buying power")

            # Keep the broker's portfolio_value and equity separate. Risk budgets
            # use equity; persisted values carry provenance and a broker timestamp.
            self.state.update({
                "portfolio_value": data["portfolio_value"],
                "portfolio_value_source": "alpaca_account",
                "last_verified_portfolio_value": data["portfolio_value"],
                "account_id": data["account_id"],
                "equity": data["equity"],
                "equity_source": "alpaca_account",
                "last_verified_equity": data["equity"],
                "account_snapshot_at": data["account_snapshot_at"],
                "account_data_fresh": True,
            })
            self._save_state()
            return data
        except Exception as e:
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
        return self.trading_client.get_orders(
            filter=GetOrdersRequest(status=QueryOrderStatus.OPEN)
        )

    def _order_status_value(self, order) -> str:
        status = getattr(order, "status", "")
        return str(status).lower()

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
            return 0.0

        limit_price = getattr(order, "limit_price", None)
        if limit_price not in (None, ""):
            price = parse_positive(limit_price, "limit_price")
            return remaining_qty * price

        symbol = getattr(order, "symbol", None)
        if symbol and symbol in prices:
            price = parse_positive(prices[symbol], f"market price for {symbol}")
            return remaining_qty * price

        # Some broker responses provide the original order notional but no price.
        # Scale it by the remaining quantity only when total qty is known.
        if notional is not None:
            return notional * (remaining_qty / qty)

        raise RuntimeError(
            f"Cannot estimate exposure for active order {symbol or '<unknown symbol>'}: "
            "no valid limit price, market price, or notional"
        )

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
            peak_source == "alpaca_account"
            and peak_mode == account_mode
            and bool(account_id)
            and bool(peak_account_id)
            and account_id == peak_account_id
        )
        verified_peak = self.state.get("risk_peak_portfolio_value") if provenance_matches else None
        legacy_peak_account_id = self.state.get("peak_equity_account_id")
        legacy_peak_is_verified = (
            self.state.get("peak_equity_source") == "verified_account"
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
        self.state["peak_equity"] = peak
        self.state["risk_peak_portfolio_value"] = peak
        self.state["peak_equity_source"] = "verified_account"
        self.state["risk_peak_equity_source"] = "alpaca_account"
        self.state["risk_peak_account_mode"] = self.state.get("account_mode")
        self.state["risk_peak_account_id"] = self.state.get("account_id")
        self.state["peak_equity_account_mode"] = self.state.get("account_mode")
        self.state["peak_equity_account_id"] = self.state.get("account_id")
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
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=timezone.utc)
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

            candidates = []
            for order in orders:
                order_symbol = str(getattr(order, "symbol", "") or "").strip()
                raw_side = getattr(order, "side", "")
                order_side = str(getattr(raw_side, "value", raw_side)).lower()
                raw_qty = getattr(order, "qty", None)
                raw_created_at = getattr(order, "created_at", None)
                order_id = getattr(order, "id", None)
                # A replaced parent is not the live close request. Prefer its
                # replacement child when that child independently matches the
                # persisted intent; if it is absent, keep the intent unresolved.
                if getattr(order, "replaced_by", None):
                    continue
                if not order_id or order_symbol != symbol or order_side != expected_side:
                    continue
                try:
                    order_qty = float(raw_qty)
                    order_created_at = (
                        raw_created_at if isinstance(raw_created_at, datetime)
                        else datetime.fromisoformat(str(raw_created_at))
                    )
                    if order_created_at.tzinfo is None:
                        order_created_at = order_created_at.replace(tzinfo=timezone.utc)
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
        pending = dict(self.state.get("emergency_liquidation_pending", {}))
        pending_ids = {str(order_id) for order_id in pending.values() if order_id}
        for order in open_orders:
            order_id = getattr(order, "id", None)
            if order_id is None:
                raise RuntimeError("Cannot safely liquidate while an open order has no ID")
            if str(order_id) in pending_ids:
                continue
            self.trading_client.cancel_order_by_id(order_id)

        remaining_orders = self._get_open_orders()
        remaining_ids = {str(getattr(order, "id", "")) for order in remaining_orders}
        unexpected_orders = [
            order for order in remaining_orders
            if str(getattr(order, "id", "")) not in pending_ids
        ]
        if unexpected_orders:
            raise RuntimeError(
                "Cannot safely liquidate until broker confirms unrelated open orders are cancelled"
            )

        # Query directly so an API failure cannot be mistaken for an empty portfolio.
        positions = self.trading_client.get_all_positions()
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
                if recorded_status == "filled":
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
            positions = self.trading_client.get_all_positions()

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
        for position in positions:
            symbol = str(getattr(position, "symbol", "")).strip()
            try:
                quantity = float(getattr(position, "qty", 0) or 0)
            except (TypeError, ValueError) as exc:
                raise RuntimeError("Cannot safely liquidate a position with invalid symbol/quantity") from exc
            if not symbol or not np.isfinite(quantity):
                raise RuntimeError("Cannot safely liquidate a position with invalid symbol/quantity")
            # An unresolved intent is deliberately fail-closed across process restarts.
            # Without a broker order ID, automatically retrying could duplicate a close
            # that the broker accepted but whose response was lost or malformed.
            if quantity == 0 or symbol in active_pending or symbol in unresolved:
                continue

            # Persist intent before sending the broker request. If the process dies or
            # the response is ambiguous, the next run will require reconciliation
            # rather than blindly submitting a second close for this symbol.
            unresolved[symbol] = {
                "created_at": datetime.now(timezone.utc).isoformat(),
                "requested_qty": abs(quantity),
                "expected_side": "sell" if quantity > 0 else "buy",
                "reason": "close_submission_outcome_unconfirmed",
            }
            self.state["emergency_liquidation_unresolved"] = unresolved
            self._save_state()

            order = self.trading_client.close_position(symbol)
            order_id = getattr(order, "id", None)
            if order_id is None:
                raise RuntimeError(
                    f"Broker close request for {symbol} returned no order ID; manual reconciliation required"
                )
            status = self._status_text(getattr(order, "status", None), default="submitted")
            liquidations.append({
                "ticker": symbol,
                "side": "close_position",
                "quantity": abs(quantity),
                "order_id": str(order_id),
                "status": status,
                "timestamp": datetime.now().isoformat(),
                "price": None,
            })
            unresolved.pop(symbol, None)
            self.state["emergency_liquidation_unresolved"] = unresolved
            # Keep every returned order ID pending for at least one reconciliation
            # cycle, including terminal statuses. Canceled/expired/rejected orders
            # can contain partial fills, and the current positions snapshot predates
            # this close response. The next invocation must refresh positions before
            # deciding whether a residual close is needed.
            active_pending[symbol] = str(order_id)
            self.state["emergency_liquidation_pending"] = active_pending
            self._save_state()
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
                symbol = str(getattr(position, "symbol", "")).strip()
                if not symbol:
                    raise ValueError("Broker returned a position without a valid symbol")
                values = {
                    "qty": float(position.qty),
                    "market_value": float(position.market_value),
                    "avg_entry_price": float(position.avg_entry_price),
                    "unrealized_pl": float(position.unrealized_pl),
                }
                if not all(np.isfinite(value) for value in values.values()):
                    raise ValueError(f"Broker returned non-finite position data for {symbol}")
                parsed_positions[symbol] = values
            return parsed_positions
        except Exception as e:
            self.logger.error("get_positions error; refusing to trade without verified positions: %s", e)
            raise RuntimeError(
                "Unable to retrieve broker positions; refusing to execute trades"
            ) from e

    def execute_weights(self, weights: PortfolioWeights, min_notional: float = 1.0) -> Dict:
        target_weights = np.asarray(weights.weights, dtype=float)
        if target_weights.shape != (len(weights.tickers),) or not np.isfinite(target_weights).all():
            raise ValueError("Target weights must be a finite vector matching tickers")
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
            self.state["last_execution_time"] = datetime.now().isoformat()
            self.state["total_orders"] += len(orders_executed)
            self._save_state()
            metrics_refresh_error = None
            metrics_refreshed = False
            post_account = None
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
                self.metrics.update(post_equity, post_liquidation_positions)
                self._persist_metrics_history()
                metrics_refreshed = True
            except Exception as exc:
                metrics_refresh_error = str(exc)
                self.logger.error("Emergency liquidation submitted but metrics refresh failed: %s", exc)
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
                "circuit_breaker_triggered": True,
                "liquidation_requested": True,
                "timestamp": datetime.now().isoformat(),
                "metrics": metrics,
            }
            if metrics_refresh_error is not None:
                result["metrics_refresh_error"] = metrics_refresh_error
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

        positions = self.get_positions()
        # A missing symbol in the target vector means a zero target, so include
        # existing holdings in the execution universe for explicit rebalancing.
        execution_tickers = list(dict.fromkeys([*weights.tickers, *positions.keys()]))
        prices = self._get_prices_from_bars(execution_tickers)
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
            str(getattr(order, "symbol", "")).strip()
            for order in open_orders
            if str(getattr(order, "symbol", "")).strip()
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

            pending_reduction = False
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
                risk_reducing_target = (
                    abs(bounded_target) < abs(current_notional) - 1e-8
                )
                if order_notional > 0:
                    if pending_reduction and not risk_reducing_target:
                        self.logger.warning(
                            "Skipping additional buy for %s until pending reductions are confirmed",
                            ticker,
                        )
                        continue
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

                try:
                    order_req = MarketOrderRequest(
                        symbol=ticker,
                        qty=qty,
                        side=side,
                        time_in_force=TimeInForce.DAY,
                    )
                    order = self.trading_client.submit_order(order_req)
                    order_info = {
                        "ticker": ticker,
                        "side": side.value,
                        "quantity": qty,
                        "order_id": str(order.id),
                        "status": str(order.status),
                        "timestamp": datetime.now().isoformat(),
                        "price": price,
                    }
                    orders_executed.append(order_info)
                    if self._order_status_value(order) not in {
                        "canceled", "cancelled", "expired", "rejected",
                    }:
                        # Project accepted/filled deltas because the original broker
                        # position snapshot is stale for the rest of this loop.
                        positions[ticker] = {
                            **positions.get(ticker, {}),
                            "market_value": current_notional + order_notional,
                        }
                        if abs(current_notional + order_notional) < abs(current_notional) - 1e-8:
                            pending_reduction = True
                        if side == OrderSide.BUY:
                            available_buying_power = max(
                                0.0, available_buying_power - abs(order_notional)
                            )
                    self.logger.info(f"  ✅ {side.value.upper()} {qty:.4f} {ticker} @ ~${price:.2f}")
                except Exception as e:
                    self.logger.error(f"  ❌ Ordine {ticker} fallito: {e}")
                    orders_executed.append({
                        "ticker": ticker,
                        "side": side.value,
                        "quantity": qty,
                        "order_id": None,
                        "status": "failed",
                        "error": str(e),
                        "timestamp": datetime.now().isoformat(),
                        "price": price,
                    })

        # Orders may have changed broker holdings after the captured account
        # snapshot. Keep its values as historical evidence, not as current state.
        self.state["account_data_fresh"] = False
        self.state["last_execution_time"] = datetime.now().isoformat()
        self.state["total_orders"] += len(orders_executed)
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

        try:
            hub = Path("logs/hub_state.json")
            hub.parent.mkdir(parents=True, exist_ok=True)
            hub.write_text(
                json.dumps(
                    {
                        "timestamp": result["timestamp"],
                        "orders": orders_executed,
                        "portfolio_value": result["portfolio_value"],
                        "pre_execution_portfolio_value": broker_portfolio_value,
                        "cash": post_account.get("cash") if post_snapshot_fresh else None,
                        "equity": result["equity"],
                        "pre_execution_equity": portfolio_value,
                        "account_data_fresh": post_snapshot_fresh,
                        "account_snapshot_at": result["account_snapshot_at"],
                        "account_data_source": post_account.get("account_data_source") if post_snapshot_fresh else None,
                        "metrics_equity": result["metrics_equity"],
                        "metrics_refresh_error": metrics_refresh_error,
                        "metrics": metrics,
                    },
                    indent=2,
                    default=str,
                )
            )
        except Exception as e:
            self.logger.error(f"Hub state write failed: {e}")

        self.logger.info(f"✅ Eseguiti {len(orders_executed)} ordini")
        self.logger.info(
    f"📊 Metrics: DD={metrics.get('max_drawdown', 0.0):.2%}, "
    f"Sharpe={metrics.get('sharpe_ratio', metrics.get('sharpe', 0.0)):.2f}, "
    f"Regime={metrics.get('regime', 'n/a')}"
)
        return result

    def _get_prices_from_bars(self, tickers: List[str]) -> Dict[str, float]:
        try:
            bars_df = self.alpaca_feed.get_bars(tickers, days=2)
            prices = {}
            for ticker in tickers:
                close_col = f"Close_{ticker}"
                if close_col in bars_df.columns:
                    prices[ticker] = float(bars_df[close_col].iloc[-1])
            return prices
        except Exception as e:
            self.logger.warning(f"_get_prices_from_bars fallback: {e}")
            return {}
