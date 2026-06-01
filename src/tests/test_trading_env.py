import pytest
import pandas as pd
import numpy as np
from pathlib import Path
import sys
sys.path.append(str(Path(__file__).parent.parent.parent))

from src.agents.trading_env import TradingEnv

def test_env_creation():
    tickers = ['AAPL', 'GOOGL']
    dates = pd.date_range('2024-01-01', periods=100, freq='D')
    
    data = pd.DataFrame({
        'Close_AAPL': np.random.uniform(100, 150, 100),
        'Close_GOOGL': np.random.uniform(100, 150, 100),
        'RSI_AAPL': np.random.uniform(30, 70, 100),
        'RSI_GOOGL': np.random.uniform(30, 70, 100),
        'MACD_AAPL': np.random.randn(100),
        'MACD_GOOGL': np.random.randn(100),
        'BB_middle_AAPL': np.random.uniform(100, 150, 100),
        'BB_middle_GOOGL': np.random.uniform(100, 150, 100),
        'ATR_AAPL': np.random.uniform(1, 5, 100),
        'ATR_GOOGL': np.random.uniform(1, 5, 100),
    }, index=dates)
    
    env = TradingEnv(data, tickers, initial_capital=100000)
    
    assert env.initial_capital == 100000
    assert env.action_space.shape == (2,)
    print("✅ Ambiente trading creato correttamente")

def test_env_reset_step():
    tickers = ['AAPL']
    dates = pd.date_range('2024-01-01', periods=50, freq='D')
    
    data = pd.DataFrame({
        'Close_AAPL': np.random.uniform(100, 150, 50),
        'RSI_AAPL': np.random.uniform(30, 70, 50),
        'MACD_AAPL': np.random.randn(50),
        'BB_middle_AAPL': np.random.uniform(100, 150, 50),
        'ATR_AAPL': np.random.uniform(1, 5, 50),
    }, index=dates)
    
    env = TradingEnv(data, tickers)
    obs, _ = env.reset()
    
    assert obs.shape[0] > 0
    
    action = env.action_space.sample()
    obs, reward, done, truncated, info = env.step(action)
    
    assert 'portfolio_value' in info
    print("✅ Reset e step funzionanti")

if __name__ == "__main__":
    test_env_creation()
    test_env_reset_step()
    print("\n🎉 Tutti i test trading env passati!")
