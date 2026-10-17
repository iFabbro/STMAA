"""PPO Agent per trading multi-asset via FinRL/Stable-Baselines3.

Sostituisce il placeholder equal-weight in trade.py generate_weights().
"""
import logging
from pathlib import Path
from typing import List, Optional

import numpy as np
import pandas as pd
import gymnasium as gym
from gymnasium import spaces
from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import DummyVecEnv

logger = logging.getLogger(__name__)


class StockTradingEnv(gym.Env):
    """Ambiente gym custom per trading multi-asset (FinRL-style)."""

    metadata = {"render_modes": ["human"]}

    def __init__(
        self,
        df: pd.DataFrame,
        tickers: List[str],
        initial_capital: float = 100000.0,
        transaction_cost: float = 0.001,
        lookback: int = 20,
    ):
        super().__init__()

        self.df = df.reset_index(drop=True)
        self.tickers = tickers
        self.n_assets = len(tickers)
        self.initial_capital = initial_capital
        self.transaction_cost = transaction_cost
        self.lookback = lookback

        self.current_step = 0
        self.balance = initial_capital
        self.stocks = np.zeros(self.n_assets, dtype=np.float32)
        self.prev_total_asset = initial_capital

        self.n_features = 5
        self.state_dim = 1 + self.n_assets * self.n_features

        self.action_space = spaces.Box(
            low=-1, high=1, shape=(self.n_assets,), dtype=np.float32
        )

        self.observation_space = spaces.Box(
            low=-np.inf, high=np.inf, shape=(self.state_dim,), dtype=np.float32
        )

        logger.info(f"StockTradingEnv: {self.n_assets} assets, {self.state_dim} state dim")

    def reset(self, seed=None, options=None):
        super().reset(seed=seed)
        self.current_step = self.lookback
        self.balance = self.initial_capital
        self.stocks = np.zeros(self.n_assets, dtype=np.float32)
        self.prev_total_asset = self.initial_capital
        return self._get_state(), {}

    def step(self, action: np.ndarray):
        price = self._get_current_prices()

        for i, act in enumerate(action):
            if act > 0.1:
                target_qty = act * self.balance / (price[i] * (1 + self.transaction_cost))
                buy_qty = min(target_qty, self.balance / price[i])
                if buy_qty > 0:
                    self.stocks[i] += buy_qty
                    self.balance -= buy_qty * price[i] * (1 + self.transaction_cost)
            elif act < -0.1:
                sell_qty = min(abs(act) * self.stocks[i], self.stocks[i])
                if sell_qty > 0:
                    self.stocks[i] -= sell_qty
                    self.balance += sell_qty * price[i] * (1 - self.transaction_cost)

        self.current_step += 1

        total_asset = self.balance + (self.stocks * price).sum()
        reward = (total_asset - self.prev_total_asset) / self.prev_total_asset
        self.prev_total_asset = total_asset

        done = self.current_step >= len(self.df) - 1

        return self._get_state(), reward, done, False, {"total_asset": total_asset}

    def _get_state(self) -> np.ndarray:
        features = []
        for ticker in self.tickers:
            close = self.df[f"Close_{ticker}"].iloc[self.current_step]
            rsi = self.df[f"RSI_{ticker}"].iloc[self.current_step]
            macd = self.df[f"MACD_{ticker}"].iloc[self.current_step]
            bb = self.df[f"BB_middle_{ticker}"].iloc[self.current_step]
            atr = self.df[f"ATR_{ticker}"].iloc[self.current_step]
            features.extend([close, rsi, macd, bb, atr])

        state = np.array([self.balance] + features, dtype=np.float32)
        return state

    def _get_current_prices(self) -> np.ndarray:
        return np.array([
            self.df[f"Close_{t}"].iloc[self.current_step]
            for t in self.tickers
        ], dtype=np.float32)


class PPOAgent:
    """Agente PPO per trading multi-asset."""

    def __init__(
        self,
        tickers: List[str],
        initial_capital: float = 100000.0,
        model_path: Optional[str] = None,
        learning_rate: float = 3e-4,
        n_steps: int = 2048,
        batch_size: int = 64,
        n_epochs: int = 10,
        gamma: float = 0.99,
        verbose: int = 0,
    ):
        self.tickers = tickers
        self.initial_capital = initial_capital
        self.model_path = model_path or "models/ppo_trading_model.zip"
        self.learning_rate = learning_rate
        self.n_steps = n_steps
        self.batch_size = batch_size
        self.n_epochs = n_epochs
        self.gamma = gamma
        self.verbose = verbose

        self.model: Optional[PPO] = None
        self.env = None

        if Path(self.model_path).exists():
            self.load()
            logger.info(f"✅ PPOAgent caricato da {self.model_path}")
        else:
            logger.info("⚠️  Nessun modello PPO trovato, usi equal-weight")

    def train(self, df: pd.DataFrame, total_timesteps: int = 50000):
        logger.info(f"🚀 Addestramento PPO: {total_timesteps} timesteps")

        def make_env():
            return StockTradingEnv(
                df=df.copy(),
                tickers=self.tickers,
                initial_capital=self.initial_capital,
            )

        self.env = DummyVecEnv([make_env])

        if self.model is None:
            self.model = PPO(
                "MlpPolicy",
                self.env,
                learning_rate=self.learning_rate,
                n_steps=self.n_steps,
                batch_size=self.batch_size,
                n_epochs=self.n_epochs,
                gamma=self.gamma,
                verbose=self.verbose,
            )

        self.model.learn(total_timesteps=total_timesteps)
        self.save()

        logger.info("✅ Addestramento PPO completato")

    def predict_weights(self, market_data: pd.DataFrame) -> np.ndarray:
        if self.model is None:
            logger.warning("⚠️  PPO modello non disponibile, uso equal-weight")
            return np.ones(len(self.tickers)) / len(self.tickers)

        env = StockTradingEnv(
            df=market_data.reset_index(drop=True).copy(),
            tickers=self.tickers,
            initial_capital=self.initial_capital,
        )

        obs, _ = env.reset()
        action, _ = self.model.predict(obs, deterministic=True)

        weights = (action + 1) / 2
        weights = weights / weights.sum()

        return weights

    def save(self):
        Path(self.model_path).parent.mkdir(parents=True, exist_ok=True)
        self.model.save(self.model_path)
        logger.info(f"💾 Modello PPO salvato: {self.model_path}")

    def load(self):
        if not Path(self.model_path).exists():
            raise FileNotFoundError(f"Modello non trovato: {self.model_path}")

        def make_env():
            return StockTradingEnv(
                df=pd.DataFrame(),
                tickers=self.tickers,
                initial_capital=self.initial_capital,
            )

        env = DummyVecEnv([make_env])
        self.model = PPO.load(self.model_path, env=env)
