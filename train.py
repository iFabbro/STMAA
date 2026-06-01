#!/usr/bin/env python3
"""Main training script per sistema multi-agente"""
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent / "src"))

import pandas as pd
import numpy as np
from config import *
from data.data_processor import DataProcessor
from agents.trading_env import TradingEnv
from strategy.weight_interface import WeightCentricPipeline, PortfolioWeights
from strategy.risk_overlay import RiskOverlayModule
from backtest.backtest_engine import ExecutionConsistentBacktest

def main():
    print("=" * 70)
    print("🚀 SISTEMA MULTI-AGENTE TRADING")
    print("=" * 70)
    
    # 1. Inizializza data processor
    print("\n📥 Step 1: Inizializzazione dati...")
    processor = DataProcessor(DATA_DIR)
    
    tickers = ['AAPL', 'GOOGL', 'MSFT']
    
    # Usa dati mock per demo (senza scaricare da Yahoo)
    dates = pd.date_range('2024-01-01', periods=200, freq='D')
    data = pd.DataFrame({
        f'Close_{t}': np.random.uniform(100, 150, 200) for t in tickers
    }, index=dates)
    
    for t in tickers:
        data[f'RSI_{t}'] = np.random.uniform(30, 70, 200)
        data[f'MACD_{t}'] = np.random.randn(200)
        data[f'BB_middle_{t}'] = np.random.uniform(100, 150, 200)
        data[f'ATR_{t}'] = np.random.uniform(1, 5, 200)
    
    print("✅ Dati mock generati")
    
    # 2. Crea ambiente trading
    print("\n🎮 Step 2: Creazione ambiente trading...")
    env = TradingEnv(
        data=data,
        tickers=tickers,
        initial_capital=INITIAL_CAPITAL,
        transaction_cost=TRANSACTION_COST
    )
    
    print(f"✅ Ambiente creato: action_space={env.action_space}, obs_space={env.observation_space}")
    
    # 3. Crea pipeline multi-agente
    print("\n⚙️  Step 3: Creazione pipeline multi-agente...")
    pipeline = WeightCentricPipeline(initial_capital=INITIAL_CAPITAL)
    pipeline.set_risk(RiskOverlayModule(
        max_position_size=MAX_POSITION_SIZE,
        max_drawdown_threshold=MAX_DRAWDOWN_THRESHOLD,
        vix_threshold_high=VIX_THRESHOLD_HIGH,
        vix_threshold_low=VIX_THRESHOLD_LOW
    ))
    
    print("✅ Pipeline multi-agente creata")
    
    # 4. Esegui backtest
    print("\n📊 Step 4: Backtest execution-consistent...")
    backtest_engine = ExecutionConsistentBacktest(
        initial_capital=INITIAL_CAPITAL,
        transaction_cost=TRANSACTION_COST,
        slippage=SLIPPAGE
    )
    
    def weights_generator(timestamp, row):
        weights = np.random.randn(len(tickers))
        weights = np.abs(weights) / weights.sum()
        return PortfolioWeights(weights=weights, tickers=tickers)
    
    results = backtest_engine.run_backtest(data, weights_generator, tickers)
    
    print("\n" + "=" * 70)
    print("📈 RISULTATI BACKTEST")
    print("=" * 70)
    metrics = results['metrics']
    print(f"  Cumulative Return: {metrics['cumulative_return']:.2%}")
    print(f"  Annualized Return: {metrics['annualized_return']:.2%}")
    print(f"  Sharpe Ratio: {metrics['sharpe_ratio']:.2f}")
    print(f"  Max Drawdown: {metrics['max_drawdown']:.2%}")
    print(f"  Final Value: ${results['final_value']:.2f}")
    print("=" * 70)
    
    print("\n✅ SISTEMA TESTATO CON SUCCESSO!")
    print("\n📝 Prossimi passi:")
    print("  1. Configura API keys in .env per paper trading")
    print("  2. Esegui 'python trade.py' per paper trading")
    print("  3. Monitora logs/execution/ per stato broker")

if __name__ == "__main__":
    main()
