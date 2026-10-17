"""Ambiente trading MDP per agenti DRL"""
import gymnasium as gym
from gymnasium import spaces
import numpy as np
import pandas as pd
from typing import List

class TradingEnv(gym.Env):
    """Ambiente trading per DRL"""
    
    metadata = {'render.mode': ['human']}
    
    def __init__(
        self,
        data: pd.DataFrame,
        tickers: List[str],
        initial_capital: float = 100000.0,
        transaction_cost: float = 0.001,
        slippage: float = 0.0005
    ):
        super(TradingEnv, self).__init__()
        
        self.data = data.reset_index()
        self.tickers = tickers
        self.initial_capital = initial_capital
        self.transaction_cost = transaction_cost
        self.slippage = slippage
        
        self.current_step = 0
        self.portfolio_value = initial_capital
        self.current_weights = np.zeros(len(tickers))
        self.positions = np.zeros(len(tickers))
        
        self.action_space = spaces.Box(
            low=-1, high=1, shape=(len(tickers),), dtype=np.float32
        )
        
        n_features = 10
        n_state_features = len(tickers) * n_features + 4
        
        self.observation_space = spaces.Box(
            low=-np.inf, high=np.inf, shape=(n_state_features,), dtype=np.float32
        )
    
    def reset(self, seed=None, options=None):
        super().reset(seed=seed)
        self.current_step = 0
        self.portfolio_value = self.initial_capital
        self.current_weights = np.zeros(len(self.tickers))
        self.positions = np.zeros(len(self.tickers))
        
        obs = self._get_observation()
        return obs, {}
    
    def step(self, action):
        action = action * (1 - self.slippage)
        turnover = np.abs(action - self.current_weights).sum()
        transaction_cost = turnover * self.transaction_cost
        
        self.current_weights = action.copy()
        
        current_data = self.data.iloc[self.current_step]
        returns = pd.Series([current_data.get(f'Close_{ticker}', 0) for ticker in self.tickers]).pct_change().fillna(0)
        step_return = np.dot(self.current_weights, returns.values)
        
        self.portfolio_value *= (1 + step_return - transaction_cost)
        
        reward = step_return - transaction_cost - 0.1 * np.abs(action).sum() * np.std(returns)
        
        self.current_step += 1
        done = self.current_step >= len(self.data) - 1
        
        obs = self._get_observation()
        
        info = {
            'portfolio_value': self.portfolio_value,
            'step_return': step_return,
            'transaction_cost': transaction_cost
        }
        
        return obs, reward, done, False, info
    
    def _get_observation(self) -> np.ndarray:
        obs = []
        
        for ticker in self.tickers:
            step_data = self.data.iloc[self.current_step]
            
            rsi = step_data.get(f'RSI_{ticker}', 50) / 100
            macd = step_data.get(f'MACD_{ticker}', 0)
            bb_pos = step_data.get(f'BB_middle_{ticker}', 0)
            atr_norm = step_data.get(f'ATR_{ticker}', 0) / max(step_data.get(f'Close_{ticker}', 1), 0.001)
            
            obs.extend([rsi, macd, bb_pos, atr_norm])
        
        obs.extend([
            self.portfolio_value / self.initial_capital,
            np.abs(self.current_weights).sum(),
            self.positions.sum(),
            len(self.tickers)
        ])
        
        return np.array(obs, dtype=np.float32)
    
    def render(self, mode='human'):
        print(f"Step: {self.current_step}, Portfolio: {self.portfolio_value:.2f}")
