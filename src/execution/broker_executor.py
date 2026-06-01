"""Broker Executor per Alpaca (paper/live trading) – alpaca-py.

Sostituisce il precedente mock di get_account_status() e execute_weights()
con chiamate reali al Trading API di Alpaca via alpaca-py.
"""
import os
import json
import logging
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (
    MarketOrderRequest,
    GetAssetsRequest,
)
from alpaca.trading.enums import OrderSide, TimeInForce, AssetClass

from src.strategy.weight_interface import PortfolioWeights

# Setup logging
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


class BrokerExecutor:
    """Executor Alpaca deployment-aware (paper + live).

    Usa alpaca-py TradingClient per:
    - get_account_status()  → dati reali account
    - execute_weights()     → ordini reali a mercato
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        base_url: str = "https://paper-api.alpaca.markets",
        paper_trading: bool = True,
    ):
        self.api_key = api_key or os.getenv("ALPACA_API_KEY", "")
        self.api_secret = api_secret or os.getenv("ALPACA_API_SECRET", "")
        self.base_url = base_url
        self.paper_trading = paper_trading
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

        self.state_file = Path("logs/execution/broker_state.json")
        self.state = {
            "last_execution_time": None,
            "total_orders": 0,
            "portfolio_value": 100000,
        }
        self._load_state()
        self.logger.info(
            f"✅ BrokerExecutor inizializzato – paper={paper_trading}"
        )

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Account
    # ------------------------------------------------------------------

    def get_account_status(self) -> Dict:
        """Dati reali account Alpaca (no mock)."""
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
            # Fallback sui valori persistiti
            return {
                "cash": 0.0,
                "portfolio_value": self.state.get("portfolio_value", 100000),
                "buying_power": 0.0,
                "equity": self.state.get("portfolio_value", 100000),
            }

    # ------------------------------------------------------------------
    # Posizioni correnti
    # ------------------------------------------------------------------

    def get_positions(self) -> Dict[str, Dict]:
        """Restituisce {ticker: {qty, market_value, avg_entry_price}}."""
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

    # ------------------------------------------------------------------
    # Esecuzione ordini
    # ------------------------------------------------------------------

    def execute_weights(
        self,
        weights: PortfolioWeights,
        min_notional: float = 1.0,
    ) -> Dict:
        """Converte weights target in ordini a mercato su Alpaca.

        Calcola le quantità in dollari sulla base di portfolio_value.
        Invia solo ordini con valore nominale >= min_notional.
        """
        self.logger.info(
            f"📤 Executing weights for {len(weights.tickers)} assets"
        )

        account = self.get_account_status()
        portfolio_value = account["portfolio_value"]

        # Prezzi correnti per calcolare qty
        prices = self._get_current_prices(weights.tickers)

        orders_executed = []
        for ticker, target_weight in zip(weights.tickers, weights.weights):
            target_notional = portfolio_value * float(target_weight)
            if abs(target_notional) < min_notional:
                self.logger.debug(f"Skip {ticker}: notional {target_notional:.2f} < {min_notional}")
                continue

            side = OrderSide.BUY if target_notional > 0 else OrderSide.SELL
            price = prices.get(ticker, 0)
            if price <= 0:
                self.logger.warning(f"Prezzo non disponibile per {ticker}, skip")
                continue

            qty = round(abs(target_notional) / price, 6)
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
                self.logger.info(
                    f"  ✅ {side.value.upper()} {qty:.4f} {ticker} @ ~${price:.2f}"
                )
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

        result = {
            "orders": orders_executed,
            "portfolio_value": portfolio_value,
            "timestamp": datetime.now().isoformat(),
        }

        # Aggiorna hub_state.json
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
                    },
                    indent=2,
                )
            )
        except Exception as e:
            self.logger.error(f"Hub state write failed: {e}")

        self.logger.info(f"✅ Eseguiti {len(orders_executed)} ordini")
        return result

    # ------------------------------------------------------------------
    # Prezzi correnti (best-effort per calcolo qty)
    # ------------------------------------------------------------------

    def _get_current_prices(self, tickers: List[str]) -> Dict[str, float]:
        """Usa alpaca-py StockLatestQuoteRequest per prezzi mid."""
        try:
            from alpaca.data.historical import StockHistoricalDataClient
            from alpaca.data.requests import StockLatestQuoteRequest

            data_client = StockHistoricalDataClient(
                api_key=self.api_key, secret_key=self.api_secret
            )
            req = StockLatestQuoteRequest(symbol_or_symbols=tickers)
            quotes = data_client.get_stock_latest_quote(req)
            return {
                t: round((float(q.ask_price) + float(q.bid_price)) / 2, 4)
                for t, q in quotes.items()
                if q.ask_price and q.bid_price
            }
        except Exception as e:
            self.logger.warning(f"_get_current_prices fallback: {e}")
            return {}
