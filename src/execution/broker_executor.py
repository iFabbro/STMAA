"""Broker Executor per Alpaca (paper/live trading) – alpaca-py."""
import os
import json
import logging
import numpy as np
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime

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
        self.max_position_pct = max_position_pct
        self.max_exposure_pct = max_exposure_pct
        self.max_symbol_concentration_pct = max_symbol_concentration_pct

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
            "portfolio_value": initial_capital,
        }
        self._load_state()
        self.logger.info(f"✅ BrokerExecutor inizializzato – paper={paper_trading}")

    def _load_state(self):
        if self.state_file.exists():
            try:
                with open(self.state_file) as f:
                    self.state = json.load(f)
                self.logger.info("✅ Stato broker caricato da persistenza")
            except Exception as e:
                self.logger.warning(f"State load error: {e}")
        else:
            self._save_state()

    def _save_state(self):
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.state_file, "w") as f:
            json.dump(self.state, f)

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
            }
            self.state["portfolio_value"] = data["portfolio_value"]
            self._save_state()
            return data
        except Exception as e:
            self.logger.error(f"get_account_status error: {e}")
            return {
                "cash": 0.0,
                "portfolio_value": self.state.get("portfolio_value", self.initial_capital),
                "buying_power": 0.0,
                "equity": self.state.get("portfolio_value", self.initial_capital),
                "account_data_fresh": False,
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
            "replaced", "done_for_day", "suspended",
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
        # Prefer the overlay's verified high-water mark. The legacy peak_equity
        # field may have been initialized from configured capital by older versions;
        # trust it only when this version has recorded its verified provenance.
        verified_peak = self.state.get("risk_peak_portfolio_value")
        if verified_peak is None and self.state.get("peak_equity_source") == "verified_account":
            verified_peak = self.state.get("peak_equity")
        if verified_peak is not None:
            value = float(verified_peak)
            if not np.isfinite(value) or value <= 0:
                raise RuntimeError("Persisted drawdown peak is invalid")
            return value
        if portfolio_value is None:
            stored_value = self.state.get("portfolio_value")
            if stored_value is None:
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

    def _liquidate_all_positions(self, open_orders) -> List[Dict]:
        """Cancel open orders, then request broker-side closure of every open position.

        This path is called only when the caller explicitly marks a drawdown
        circuit breaker and opts into liquidation. Any cancellation/position API
        error aborts the operation instead of reporting a successful liquidation.
        """
        for order in open_orders:
            order_id = getattr(order, "id", None)
            if order_id is None:
                raise RuntimeError("Cannot safely liquidate while an open order has no ID")
            self.trading_client.cancel_order_by_id(order_id)
        remaining_orders = self._get_open_orders()
        if remaining_orders:
            raise RuntimeError(
                "Cannot safely liquidate until broker confirms all open orders are cancelled"
            )

        # Query directly so an API failure cannot be mistaken for an empty portfolio.
        positions = self.trading_client.get_all_positions()
        liquidations = []
        for position in positions:
            symbol = str(getattr(position, "symbol", "")).strip()
            quantity = float(getattr(position, "qty", 0) or 0)
            if not symbol or not np.isfinite(quantity):
                raise RuntimeError("Cannot safely liquidate a position with invalid symbol/quantity")
            if quantity == 0:
                continue
            order = self.trading_client.close_position(symbol)
            liquidations.append({
                "ticker": symbol,
                "side": "close_position",
                "quantity": abs(quantity),
                "order_id": str(getattr(order, "id", "")),
                "status": str(getattr(order, "status", "submitted")),
                "timestamp": datetime.now().isoformat(),
                "price": None,
            })
        return liquidations

    def get_positions(self) -> Dict[str, Dict]:
        """Return verified broker positions or fail closed when they are unreadable.

        An API failure must never be represented as an empty portfolio: callers
        use this result to calculate exposure and position-level risk limits.
        """
        try:
            positions = self.trading_client.get_all_positions()
            return {
                p.symbol: {
                    "qty": float(p.qty),
                    "market_value": float(p.market_value),
                    "avg_entry_price": float(p.avg_entry_price),
                    "unrealized_pl": float(p.unrealized_pl),
                }
                for p in positions
            }
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
        if account.get("account_data_fresh") is False:
            raise RuntimeError("Cannot verify current broker account value; refusing to execute target weights")
        portfolio_value = float(account["portfolio_value"])
        if not np.isfinite(portfolio_value) or portfolio_value <= 0:
            raise RuntimeError("Cannot execute weights with invalid broker account value")
        buying_power = float(account.get("buying_power", 0.0) or 0.0)
        open_orders = self._get_open_orders()
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
                if abs(target_notional - current_notional) >= min_notional:
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
                        "replaced", "done_for_day", "suspended",
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

        self.state["last_execution_time"] = datetime.now().isoformat()
        self.state["total_orders"] += len(orders_executed)
        self._save_state()

        # Aggiorna metrics
        new_positions = self.get_positions()
        self.metrics.update(portfolio_value, new_positions)
        metrics = self.metrics.get_all_metrics()
        self.metrics.write_metrics_json(metrics, "logs/metrics.json")

        result = {
            "orders": orders_executed,
            "portfolio_value": portfolio_value,
            "circuit_breaker_triggered": circuit_breaker_triggered,
            "liquidation_requested": circuit_breaker_triggered and liquidation_requested,
            "timestamp": datetime.now().isoformat(),
            "metrics": metrics,
        }

        try:
            hub = Path("logs/hub_state.json")
            hub.parent.mkdir(parents=True, exist_ok=True)
            hub.write_text(
                json.dumps(
                    {
                        "timestamp": result["timestamp"],
                        "orders": orders_executed,
                        "portfolio_value": portfolio_value,
                        "cash": account.get("cash", 0),
                        "equity": account.get("equity", portfolio_value),
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
