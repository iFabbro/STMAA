#!/usr/bin/env python3
"""Paper Trading Script - Sistema Multi-Agente STMAA

Collega il sistema trading con Alpaca Paper Trading per operatività reale.
"""
import os
import sys
import time
import signal
from pathlib import Path
from datetime import datetime
from typing import List, Dict

# Add src to path
sys.path.append(str(Path(__file__).parent / "src"))

from dotenv import load_dotenv
load_dotenv()

import numpy as np
import pandas as pd
import json

from src.execution.broker_executor import BrokerExecutor
from src.strategy.weight_interface import WeightCentricPipeline, PortfolioWeights
from src.strategy.risk_overlay import RiskOverlayModule
from src.data.data_processor import DataProcessor
from src.agents.trading_env import TradingEnv

# Interruzione elegante
running = True

def signal_handler(sig, frame):
    print("\n🛑 Ricevuto segnale di stop, chiudendo...")
    running = False

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

class PaperTradingBot:
    """Bot trading per paper trading con Alpaca"""
    
    def __init__(self, tickers: List[str], initial_capital: float = 100000.0):
        self.tickers = tickers
        self.initial_capital = initial_capital
        self.equity_curve = [initial_capital]
        
        # Init broker executor
        self.executor = BrokerExecutor(
            api_key=os.getenv('ALPACA_API_KEY'),
            api_secret=os.getenv('ALPACA_API_SECRET'),
            base_url=os.getenv('ALPACA_BASE_URL', 'https://paper-api.alpaca.markets'),
            paper_trading=True
        )
        
        # Init pipeline
        self.pipeline = WeightCentricPipeline(initial_capital=initial_capital)
        self.pipeline.set_risk(RiskOverlayModule(
            max_position_size=0.2,
            max_drawdown_threshold=0.15,
            vix_threshold_high=30,
            vix_threshold_low=15
        ))
        
        # Data processor
        self.processor = DataProcessor(Path("data"))
        
        print("✅ PaperTradingBot inizializzato")
    
    def get_market_data(self) -> pd.DataFrame:
        """Ottieni dati di mercato (mock per demo)"""
        print("📊 Ottimazione dati di mercato...")
        
        # MOCK: Genera dati random (per demo senza scaricare da Yahoo)
        dates = pd.date_range('2024-01-01', periods=100, freq='D')
        data = pd.DataFrame({
            f'Close_{t}': np.random.uniform(100, 150, 100) for t in self.tickers
        }, index=dates)
        
        for t in self.tickers:
            data[f'RSI_{t}'] = np.random.uniform(30, 70, 100)
            data[f'MACD_{t}'] = np.random.randn(100)
            data[f'BB_middle_{t}'] = np.random.uniform(100, 150, 100)
            data[f'ATR_{t}'] = np.random.uniform(1, 5, 100)
        
        print(f"✅ Dati ottenuti: {len(data)} righe, {len(self.tickers)} tickers")
        return data
    
    def generate_weights(self, market_data: pd.DataFrame) -> PortfolioWeights:
        """Genera weights usando pipeline multi-agente"""
        print("🧠 Generazione weights con pipeline multi-agente...")
        
        # Mock: genera weights random (qui dovresti usare agente DRL reale)
        weights = np.random.randn(len(self.tickers))
        weights = np.abs(weights) / weights.sum()
        
        portfolio_weights = PortfolioWeights(
            weights=weights,
            tickers=self.tickers,
            timestamp=datetime.now().isoformat()
        )
        
        print(f"✅ Weights generati: {portfolio_weights.to_dict()}")
        return portfolio_weights
    
    def execute_trade(self, weights: PortfolioWeights):
        """Esegue trade sul broker"""
        print("🔄 Esecuzione trade...")
        
        result = self.executor.execute_weights(weights)
        
        print(f"✅ Trade eseguito: {len(result['orders'])} ordini")
        for order in result['orders']:
            print(f"  • {order['side']} {order['quantity']:.2f} {order['ticker']}")
        
        return result

    def update_hub_state(self, result: Dict):
        status = self.executor.get_account_status()
        portfolio_value = float(status.get('portfolio_value', self.initial_capital))
        cash = float(status.get('cash', 0))
        equity = float(status.get('equity', portfolio_value))
        self.equity_curve.append(portfolio_value)
        hub_state = {
            'timestamp': datetime.now().isoformat(),
            'portfolio_value': portfolio_value,
            'cash': cash,
            'equity': equity,
            'equity_curve': self.equity_curve[-120:],
            'orders': result.get('orders', []),
            'last_result': result
        }
        Path('logs').mkdir(parents=True, exist_ok=True)
        Path('logs/hub_state.json').write_text(json.dumps(hub_state, indent=2))
    
    def run_loop(self, interval_seconds: int = 60):
        """Run loop trading continuo"""
        print("\n============================================================")
        print("🚀 AVVIO PAPER TRADING BOT")
        print("============================================================")
        print(f"Tickers: {self.tickers}")
        print(f"Intervallo: {interval_seconds} secondi")
        print(f"Paper Trading: {self.executor.paper_trading}")
        print("============================================================\n")
        
        iteration = 0
        
        while running:
            iteration += 1
            print(f"\n[ITERAZIONE {iteration}]")
            print("-" * 50)
            
            try:
                # 1. Ottieni dati
                market_data = self.get_market_data()
                
                # 2. Genera weights
                weights = self.generate_weights(market_data)
                
                # 3. Esegui trade
                result = self.execute_trade(weights)
                self.update_hub_state(result)
                
                # 4. Status account
                status = self.executor.get_account_status()
                print(f"\n💰 Status Account:")
                print(f"  • Cash: ${status['cash']:.2f}")
                print(f"  • Portfolio Value: ${status['portfolio_value']:.2f}")
                print(f"  • Equity: ${status['equity']:.2f}")
                
                print(f"\n⏸️  Waiting {interval_seconds} seconds...")
                
            except Exception as e:
                print(f"\n❌ Errore: {e}")
            
            # Wait next iteration
            if running:
                time.sleep(interval_seconds)
        
        print("\n============================================================")
        print("🛑 PAPER TRADING BOT STOPPATO")
        print("============================================================")

def main():
    """Main entry point"""
    print("=" * 70)
    print("🚀 STMAA - Paper Trading Bot")
    print("=" * 70)
    
    # Check API keys
    api_key = os.getenv('ALPACA_API_KEY')
    api_secret = os.getenv('ALPACA_API_SECRET')
    
    if not api_key or not api_secret:
        print("\n❌ API keys non configurate!")
        print("\n📝 Configurazione:")
        print("  1. cp .env.example .env")
        print("  2. nano .env  # Inserisci le tue API keys")
        print("  3. python trade.py")
        return
    
    print("✅ API keys configurate")
    
    # Init bot
    tickers = ['AAPL', 'GOOGL', 'MSFT', 'AMZN', 'TSLA', 'NVDA', 'META']
    bot = PaperTradingBot(tickers=tickers, initial_capital=500000.0)
    
    # Run
    bot.run_loop(interval_seconds=300)

if __name__ == "__main__":
    main()
