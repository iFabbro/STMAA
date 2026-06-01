import pytest
import pandas as pd
import numpy as np
from pathlib import Path
import sys
sys.path.append(str(Path(__file__).parent.parent.parent))

from src.backtest.backtest_engine import ExecutionConsistentBacktest
from src.strategy.weight_interface import PortfolioWeights

def test_backtest_engine_creation():
    engine = ExecutionConsistentBacktest(
        initial_capital=100000,
        transaction_cost=0.001,
        slippage=0.0005
    )
    
    assert engine.initial_capital == 100000
    print("✅ Backtest engine creato")

def test_metrics_calculation():
    engine = ExecutionConsistentBacktest()
    
    dates = pd.date_range('2024-01-01', periods=100, freq='D')
    portfolio_values = pd.Series(
        100000 * np.exp(np.cumsum(np.random.randn(100) * 0.01)),
        index=dates
    )
    
    metrics = engine._calculate_metrics(portfolio_values)
    
    assert 'sharpe_ratio' in metrics
    assert 'max_drawdown' in metrics
    assert 'cumulative_return' in metrics
    print("✅ Metrics calcolati correttamente")

if __name__ == "__main__":
    test_backtest_engine_creation()
    test_metrics_calculation()
    print("\n🎉 Tutti i test backtest engine passati!")
