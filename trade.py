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

        print("✅ PaperTradingBot inizializzato (Alpaca + PPO attivo)" if PPO_AVAILABLE else "✅ PaperTradingBot inizializzato (fallback senza PPO)")

    def get_market_data(self) -> pd.DataFrame:
        """Ottieni dati di mercato reali da Alpaca (bars giornaliere)."""
        print("📊 Scaricamento dati da Alpaca...")
        data = self.alpaca_feed.get_bars(self.tickers, days=100)
        print(f"✅ Dati: {len(data)} righe, {len(self.tickers)} tickers")
        return data

    def generate_weights(self, market_data: pd.DataFrame) -> PortfolioWeights:
        """Genera weights usando agente PPO reale (Fase 2)."""
        if not PPO_AVAILABLE or self.ppo_agent is None:
            print("⚠️ PPO non disponibile, uso pesi equal-weight di fallback...")
            n = len(self.tickers)
            weights = np.array([1.0 / n] * n, dtype=float)
            return PortfolioWeights(
                weights=weights,
                tickers=self.tickers,
                timestamp=datetime.now().isoformat(),
            )

        print("🧠 Generazione weights con PPO...")

        # Addestra PPO se non esiste modello
        model_path = "models/ppo_trading_model.zip"
        if not Path(model_path).exists():
            print("⚠️  Nessun modello PPO trovato, addestramento automatico...")
            self.ppo_agent.train(market_data, total_timesteps=10000)

        # PPO predice weights
        weights = self.ppo_agent.predict_weights(market_data)

        portfolio_weights = PortfolioWeights(
            weights=weights,
            tickers=self.tickers,
            timestamp=datetime.now().isoformat(),
        )

        print(f"✅ Weights PPO: {portfolio_weights.to_dict()}")
        return portfolio_weights

    def execute_trade(self, weights: PortfolioWeights):
        """Esegue trade sul broker"""
        print("🔄 Esecuzione trade...")
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
