"""Broker Executor per Alpaca (paper/live trading) – alpaca-py."""
import os
import json
import logging
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
            return type(weights)(weights=weights.weights * scale, tickers=weights.tickers), None
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
            }

    def _get_open_orders(self):
        return self.trading_client.get_orders(
            filter=GetOrdersRequest(status=QueryOrderStatus.OPEN)
        )

    def _order_status_value(self, order) -> str:
        status = getattr(order, "status", "")
        return str(status).lower()

    def _estimate_order_notional(self, order, prices: Optional[Dict[str, float]] = None) -> float:
        prices = prices or {}
        status = self._order_status_value(order)
        if status in {"filled", "canceled", "expired", "rejected", "replaced", "done_for_day", "suspended"}:
            return 0.0

        qty = float(getattr(order, "qty", 0) or 0)
        filled_qty = float(getattr(order, "filled_qty", 0) or 0)
        remaining_qty = max(qty - filled_qty, 0.0)
        if remaining_qty <= 0:
            return 0.0

        limit_price = getattr(order, "limit_price", None)
        if limit_price not in (None, "", 0):
            return remaining_qty * float(limit_price)

        symbol = getattr(order, "symbol", None)
        if symbol and symbol in prices and prices[symbol] > 0:
            return remaining_qty * float(prices[symbol])

        notional = getattr(order, "notional", None)
        if notional not in (None, "", 0):
            return float(notional)

        return 0.0

    def _get_reserved_notional(self, orders, prices: Optional[Dict[str, float]] = None) -> float:
        return sum(self._estimate_order_notional(order, prices=prices) for order in orders)

    def _get_peak_equity(self) -> float:
        return float(self.state.get("peak_equity", self.initial_capital) or self.initial_capital)

    def _update_peak_equity(self, portfolio_value: float) -> float:
        peak = max(self._get_peak_equity(), float(portfolio_value))
        self.state["peak_equity"] = peak
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

    def get_positions(self) -> Dict[str, Dict]:
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
            self.logger.error(f"get_positions error: {e}")
            return {}

    def execute_weights(self, weights: PortfolioWeights, min_notional: float = 1.0) -> Dict:
        self.logger.info(f"📤 Executing weights for {len(weights.tickers)} assets")

        account = self.get_account_status()
        portfolio_value = float(account["portfolio_value"])
        buying_power = float(account.get("buying_power", 0.0) or 0.0)
        prices = self._get_prices_from_bars(weights.tickers)
        open_orders = self._get_open_orders()
        reserved_notional = self._get_reserved_notional(open_orders, prices=prices)
        if reserved_notional > 0:
            self.logger.warning(
                f"Notional già impegnato dagli ordini aperti: {reserved_notional:.2f}"
            )

        tradable_budget = portfolio_value
        if buying_power > 0:
            tradable_budget = min(portfolio_value, max(0.0, buying_power - reserved_notional))
        else:
            tradable_budget = portfolio_value

        tradable_budget, risk_block = self._risk_gate(portfolio_value, tradable_budget)
        if risk_block:
            self.logger.warning(f"Risk gate attivo: {risk_block}")

        self.logger.info(
            f"Budget esecuzione: portfolio_value={portfolio_value:.2f}, "
            f"buying_power={buying_power:.2f}, tradable_budget={tradable_budget:.2f}"
        )
        positions = self.get_positions()
        risk_engine = RiskEngine()
        portfolio_manager = PortfolioManager()
        weights, pm_block = portfolio_manager.allocate(weights, portfolio_value, positions, reserved_notional=reserved_notional)
        if pm_block:
            self.logger.warning(f"Portfolio manager block: {pm_block}")

        orders_executed = []
        for ticker, target_weight in zip(weights.tickers, weights.weights):
            target_notional = tradable_budget * float(target_weight)
            current_notional = float(
                positions.get(ticker, {}).get("market_value", 0.0) or 0.0
            )
            bounded_target, risk_block = risk_engine.allow(
                ticker,
                target_notional,
                portfolio_value,
                positions,
                reserved_notional=reserved_notional,
            )
            if risk_block:
                self.logger.warning(f"Risk engine block {ticker}: {risk_block}")
                # If the desired exposure is blocked, target flat for this symbol.
                bounded_target = 0.0

            # Weights describe desired positions, not incremental orders. Submit
            # only the notional delta required to reach the bounded target.
            order_notional = bounded_target - current_notional
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
