"""Gymnasium environment for multi-asset trading research."""
from __future__ import annotations

from typing import List

import gymnasium as gym
import numpy as np
import pandas as pd
from gymnasium import spaces


class TradingEnv(gym.Env):
    """Target-weight environment with close-to-close portfolio accounting.

    An action selects target weights at the current close. Transaction costs are
    charged at that rebalance; the target portfolio then earns returns over the
    interval to the next row. The final observation is terminal and cannot be
    stepped from again.
    """

    metadata = {"render_modes": ["human"]}

    def __init__(
        self,
        data: pd.DataFrame,
        tickers: List[str],
        initial_capital: float = 100000.0,
        transaction_cost: float = 0.001,
        slippage: float = 0.0005,
    ):
        super().__init__()

        if not isinstance(data, pd.DataFrame) or data.empty:
            raise ValueError("data must be a non-empty DataFrame")
        if len(data) < 2:
            raise ValueError("data must contain at least two rows")
        if not tickers or len(set(tickers)) != len(tickers):
            raise ValueError("tickers must be a non-empty list of unique symbols")
        if not np.isfinite(initial_capital) or initial_capital <= 0:
            raise ValueError("initial_capital must be finite and positive")
        costs = np.asarray([transaction_cost, slippage], dtype=float)
        if not np.isfinite(costs).all() or (costs < 0).any():
            raise ValueError("transaction_cost and slippage must be finite and non-negative")
        if transaction_cost + slippage >= 1:
            raise ValueError("Combined transaction cost and slippage must be < 1")

        self.tickers = list(tickers)
        self.close_columns = [f"Close_{ticker}" for ticker in self.tickers]
        missing = [column for column in self.close_columns if column not in data.columns]
        if missing:
            raise ValueError(f"Missing close price columns: {missing}")

        self.data = data.copy().reset_index(drop=True)
        prices = self.data.loc[:, self.close_columns].to_numpy(dtype=float)
        if not np.isfinite(prices).all() or (prices <= 0).any():
            raise ValueError("Close prices must be finite and strictly positive")

        feature_columns = [
            f"{feature}_{ticker}" for ticker in self.tickers
            for feature in ("RSI", "MACD", "BB_middle", "ATR")
            if f"{feature}_{ticker}" in data.columns
        ]
        if feature_columns and not np.isfinite(
            data.loc[:, feature_columns].to_numpy(dtype=float)
        ).all():
            raise ValueError("Market features must be finite when provided")

        self.initial_capital = float(initial_capital)
        self.transaction_cost = float(transaction_cost)
        self.slippage = float(slippage)
        self.current_step = 0
        self.portfolio_value = self.initial_capital
        self.current_weights = np.zeros(len(self.tickers), dtype=float)
        self.positions = np.zeros(len(self.tickers), dtype=float)
        self._terminated = False

        self.action_space = spaces.Box(
            low=-1.0, high=1.0, shape=(len(self.tickers),), dtype=np.float32
        )
        # Four market features per ticker plus four portfolio/account features.
        self.n_features = 4
        observation_size = len(self.tickers) * self.n_features + 4
        self.observation_space = spaces.Box(
            low=-np.inf, high=np.inf, shape=(observation_size,), dtype=np.float32
        )

    def reset(self, seed=None, options=None):
        super().reset(seed=seed)
        self.current_step = 0
        self.portfolio_value = self.initial_capital
        self.current_weights = np.zeros(len(self.tickers), dtype=float)
        self.positions = np.zeros(len(self.tickers), dtype=float)
        self._terminated = False
        return self._get_observation(), {}

    def step(self, action):
        if self._terminated:
            raise RuntimeError("Episode is terminated; call reset() before step()")
        action = np.asarray(action, dtype=float)
        if action.shape != (len(self.tickers),):
            raise ValueError(
                f"Expected action shape {(len(self.tickers),)}, got {action.shape}"
            )
        if not np.isfinite(action).all():
            raise ValueError("Action weights must all be finite")
        target_weights = np.clip(action, -1.0, 1.0)

        previous_value = self.portfolio_value
        turnover = float(np.abs(target_weights - self.current_weights).sum())
        execution_cost = turnover * (self.transaction_cost + self.slippage)
        if execution_cost >= 1.0:
            self.portfolio_value = 0.0
            self.current_weights = np.zeros(len(self.tickers), dtype=float)
            self.current_step += 1
            self._terminated = True
            return self._get_observation(), -1.0, True, False, {
                "portfolio_value": self.portfolio_value,
                "step_return": -1.0,
                "transaction_cost": execution_cost,
                "turnover": turnover,
                "bankrupt": True,
            }

        self.portfolio_value *= 1.0 - execution_cost
        self.current_weights = target_weights.copy()

        # Holdings selected at row t earn the close-to-close return from t to t+1.
        current_prices = self.data.loc[self.current_step, self.close_columns].to_numpy(dtype=float)
        next_prices = self.data.loc[self.current_step + 1, self.close_columns].to_numpy(dtype=float)
        asset_returns = next_prices / current_prices - 1.0
        portfolio_return = float(np.dot(self.current_weights, asset_returns))
        if not np.isfinite(portfolio_return) or portfolio_return <= -1.0:
            self.portfolio_value = 0.0
            self.current_weights = np.zeros(len(self.tickers), dtype=float)
            self.current_step += 1
            self._terminated = True
            return self._get_observation(), -1.0, True, False, {
                "portfolio_value": self.portfolio_value,
                "step_return": -1.0,
                "transaction_cost": execution_cost,
                "turnover": turnover,
                "bankrupt": True,
            }

        self.portfolio_value *= 1.0 + portfolio_return
        self.current_step += 1

        # Update weights to reflect relative performance between rebalances.
        denominator = 1.0 + portfolio_return
        self.current_weights = (
            self.current_weights * (1.0 + asset_returns) / denominator
        )
        self.positions = self.current_weights.copy()
        self._terminated = self.current_step >= len(self.data) - 1

        step_return = self.portfolio_value / previous_value - 1.0
        reward = float(step_return)
        info = {
            "portfolio_value": float(self.portfolio_value),
            "step_return": float(step_return),
            "market_return": float(portfolio_return),
            "transaction_cost": float(execution_cost * previous_value),
            "turnover": turnover,
            "bankrupt": False,
        }
        return self._get_observation(), reward, self._terminated, False, info

    def _get_observation(self) -> np.ndarray:
        row = self.data.iloc[self.current_step]
        features = []
        for ticker in self.tickers:
            close = float(row[f"Close_{ticker}"])
            rsi = float(row.get(f"RSI_{ticker}", 50.0)) / 100.0
            macd = float(row.get(f"MACD_{ticker}", 0.0))
            bb_middle = float(row.get(f"BB_middle_{ticker}", close))
            bb_position = (close - bb_middle) / close
            atr = float(row.get(f"ATR_{ticker}", 0.0))
            atr_normalized = atr / close
            features.extend([rsi, macd, bb_position, atr_normalized])

        observation = np.asarray(
            [
                *features,
                self.portfolio_value / self.initial_capital,
                float(np.abs(self.current_weights).sum()),
                float(np.abs(self.positions).sum()),
                float(len(self.tickers)),
            ],
            dtype=np.float32,
        )
        if not np.isfinite(observation).all():
            raise ValueError("Observation contains non-finite values")
        return observation

    def render(self):
        print(f"Step: {self.current_step}, Portfolio: {self.portfolio_value:.2f}")
