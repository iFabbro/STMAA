#!/usr/bin/env python3
"""Paper Trading Script - Sistema Multi-Agente STMAA

Collega il sistema trading con Alpaca Paper Trading per operatività reale.
Fase 1: Dati Alpaca reali
Fase 2: Agente PPO vero
"""
import os
import sys
import time
import signal
from pathlib import Path
from datetime import datetime
from typing import List, Dict

sys.path.append(str(Path(__file__).parent / "src"))

from dotenv import load_dotenv
load_dotenv()

from src.utils.sqlite_logger import start_run, log_event, write_snapshot
RUN_ID = start_run(mode="paper", meta={"component": "trade"})

import numpy as np
import pandas as pd
import json
import yfinance as yf
import pandas_market_calendars as mcal

from src.execution.broker_executor import BrokerExecutor
from src.strategy.weight_interface import WeightCentricPipeline, PortfolioWeights
from src.strategy.risk_overlay import RiskOverlayModule
from src.data.data_processor import DataProcessor
from src.data.alpaca_feed import AlpacaFeed
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


log_event(RUN_ID, "trade", "startup", "trade.py started")

class PaperTradingBot:
    """Bot trading per paper trading con Alpaca"""

    def __init__(self, tickers: List[str], initial_capital: float = 100000.0):
        self.tickers = tickers
        self.initial_capital = initial_capital
        self.equity_curve = [initial_capital]

        self.executor = BrokerExecutor(
            api_key=os.getenv('ALPACA_API_KEY'),
            api_secret=os.getenv('ALPACA_API_SECRET'),
            base_url=os.getenv('ALPACA_BASE_URL', 'https://paper-api.alpaca.markets'),
            paper_trading=True,
        )

        self.pipeline = WeightCentricPipeline(initial_capital=initial_capital)
        self.pipeline.set_risk(RiskOverlayModule(
            max_position_size=0.2,
            max_drawdown_threshold=0.15,
            vix_threshold_high=30,
            vix_threshold_low=15,
            initial_capital=initial_capital,
            max_gross_exposure=1.0,
        ))

        self.processor = DataProcessor(Path("data"))
        self.alpaca_feed = AlpacaFeed(
            api_key=os.getenv('ALPACA_API_KEY'),
            api_secret=os.getenv('ALPACA_API_SECRET'),
        )

        # Agente PPO (Fase 2)
        self.ppo_agent = PPOAgent(
            tickers=tickers,
            initial_capital=initial_capital,
        ) if PPO_AVAILABLE else None

        print("PaperTradingBot inizializzato (PPO disponibile)" if PPO_AVAILABLE else "PPO non disponibile: la generazione dei pesi e il trading sono disabilitati")

    def get_market_data(self) -> pd.DataFrame:
        """Ottieni dati di mercato reali da Alpaca (bars giornaliere)."""
        print("📊 Scaricamento dati da Alpaca...")
        data = self.alpaca_feed.get_bars(self.tickers, days=100)
        print(f"✅ Dati: {len(data)} righe, {len(self.tickers)} tickers")
        return data

    def generate_weights(self, market_data: pd.DataFrame) -> PortfolioWeights:
        """Generate target weights with PPO; fail closed when inference is unavailable."""
        if not PPO_AVAILABLE or self.ppo_agent is None:
            raise RuntimeError(
                "PPO unavailable; refusing to generate fallback weights or submit trades"
            )

        # The PPO observation includes current holdings so the policy can account
        # for turnover. Obtain a verified account snapshot and pass current weights.
        account = self.executor.get_account_status()
        if account.get("account_data_fresh") is False:
            raise RuntimeError("Cannot verify current broker account value for PPO inference")
        portfolio_value = float(account.get("portfolio_value", 0.0))
        if not np.isfinite(portfolio_value) or portfolio_value <= 0:
            raise RuntimeError("Cannot calculate current PPO holdings weights from invalid equity")

        positions = self.executor.get_positions()
        if not isinstance(positions, dict):
            raise RuntimeError("Cannot verify current broker positions for PPO inference")
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
        if not np.isclose(weights.sum(), 1.0, rtol=1e-6, atol=1e-8):
            raise RuntimeError("PPO returned target weights that do not sum to 1")

        portfolio_weights = PortfolioWeights(
            weights=weights,
            tickers=self.tickers,
            timestamp=datetime.now().isoformat(),
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

    def execute_trade(self, weights: PortfolioWeights):
        """Apply portfolio risk controls before submitting target weights."""
        print("🔄 Esecuzione trade...")
        risk_module = getattr(self.pipeline, "risk_module", None)
        if risk_module is not None:
            account = self.executor.get_account_status()
            if account.get("account_data_fresh") is False:
                raise RuntimeError("Cannot verify current broker account value; refusing to submit target weights")
            portfolio_value = float(account.get("portfolio_value", self.initial_capital))
            if not np.isfinite(portfolio_value) or portfolio_value <= 0:
                raise RuntimeError("Cannot obtain a valid positive account portfolio value")

            # Persist the high-water mark independently of configured initial capital.
            # A first verified account snapshot establishes the baseline; later runs
            # retain the historical peak and advance it only when a new high is reached.
            executor_state = getattr(self.executor, "state", None)
            if isinstance(executor_state, dict):
                stored_peak = executor_state.get("risk_peak_portfolio_value")
                if stored_peak is None:
                    stored_peak = portfolio_value
                else:
                    stored_peak = float(stored_peak)
                    if not np.isfinite(stored_peak) or stored_peak <= 0:
                        raise RuntimeError("Stored risk drawdown peak is invalid")
                peak_portfolio_value = max(stored_peak, portfolio_value)
                executor_state["risk_peak_portfolio_value"] = peak_portfolio_value
                save_state = getattr(self.executor, "_save_state", None)
                if callable(save_state):
                    save_state()
            else:
                module_peak = getattr(risk_module, "peak_portfolio_value", None)
                peak_portfolio_value = max(
                    float(module_peak) if module_peak is not None else portfolio_value,
                    portfolio_value,
                )

            self.pipeline.current_portfolio_value = portfolio_value
            drawdown = max(0.0, (peak_portfolio_value - portfolio_value) / peak_portfolio_value)
            circuit_breaker_triggered = drawdown >= float(
                getattr(risk_module, "max_drawdown_threshold", 1.0)
            )
            liquidate_on_breaker = os.getenv("LIQUIDATE_ON_DRAWDOWN", "false").strip().lower() in {
                "1", "true", "yes", "on"
            }
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
        result = self.executor.execute_weights(weights)
        print(f"✅ Trade eseguito: {len(result['orders'])} ordini")
        for order in result['orders']:
            print(f"  • {order['side']} {order['quantity']:.4f} {order['ticker']}")
        write_snapshot(RUN_ID, "trade", payload={"tickers": self.tickers, "rows": len(result)})
        log_event(RUN_ID, "trade", "bars_loaded", "bars loaded", payload={"rows": len(result), "tickers": self.tickers})
        return result

    def update_hub_state(self, result: Dict):
        status = self.executor.get_account_status()
        portfolio_value = float(status.get('portfolio_value', self.initial_capital))
        cash = float(status.get('cash', 0))
        equity = float(status.get('equity', portfolio_value))
        self.equity_curve.append(portfolio_value)

        orders = result.get('orders', [])

        hub_state = {
            'timestamp': datetime.now().isoformat(),
            'portfolio_value': portfolio_value,
            'cash': cash,
            'equity': equity,
            'equity_curve': self.equity_curve[-120:],
            'orders': orders,
            'last_result': result,
        }
        Path('logs').mkdir(parents=True, exist_ok=True)
        Path('logs/hub_state.json').write_text(json.dumps(hub_state, indent=2))

    def run_loop(self, interval_seconds: int = 60):
        """Run loop trading continuo"""
        print("\n" + "=" * 62)
        print("🚀 AVVIO PAPER TRADING BOT")
        print("=" * 62)
        print(f"Tickers: {self.tickers}")
        print(f"Intervallo: {interval_seconds} secondi")
        print(f"Paper Trading: {self.executor.paper_trading}")
        print("=" * 62 + "\n")

        iteration = 0

        while running:
            iteration += 1
            print(f"\n[ITERAZIONE {iteration}]")
            print("-" * 50)

            try:
                market_data = self.get_market_data()
                weights = self.generate_weights(market_data)
                result = self.execute_trade(weights)
                self.update_hub_state(result)

                status = self.executor.get_account_status()
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


def main():
    print("=" * 70)
    print("🚀 STMAA - Paper Trading Bot")
    print("=" * 70)

    api_key = os.getenv('ALPACA_API_KEY')
    api_secret = os.getenv('ALPACA_API_SECRET')

    if not api_key or not api_secret:
        print("\n❌ API keys non configurate!")
        print("\n📝 Configurazione:")
        print("  1. cp .env.example .env")
        print("  2. nano .env  # Inserisci le tue API keys")
        print("  3. python3 trade.py")
        return

    print("✅ API keys configurate")

    tickers = ['AAPL', 'GOOGL', 'MSFT', 'AMZN', 'TSLA', 'NVDA', 'META']
    bot = PaperTradingBot(tickers=tickers, initial_capital=500000.0)
    bot.run_loop(interval_seconds=300)


if __name__ == "__main__":
    main()
