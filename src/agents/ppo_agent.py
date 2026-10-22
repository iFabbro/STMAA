"""PPO agent with a consistent target-weight action contract."""
import json
import logging
from pathlib import Path
from typing import List, Optional

import gymnasium as gym
import numpy as np
import pandas as pd
from gymnasium import spaces
from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import DummyVecEnv

logger = logging.getLogger(__name__)
MODEL_CONTRACT_VERSION = 2


class StockTradingEnv(gym.Env):
    """Long-only target-weight environment with close-to-close accounting.

    Actions are allocation scores in [-1, 1]. They are transformed into
    non-negative target weights summing to one, matching PPOAgent.predict_weights.
    An action at close t is executed at close t and earns the return to close t+1.
    """

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
        if not isinstance(df, pd.DataFrame) or df.empty:
            raise ValueError("df must be a non-empty DataFrame")
        if not tickers or len(set(tickers)) != len(tickers):
            raise ValueError("tickers must be a non-empty list of unique symbols")
        if not np.isfinite(initial_capital) or initial_capital <= 0:
            raise ValueError("initial_capital must be finite and positive")
        if not np.isfinite(transaction_cost) or not 0 <= transaction_cost < 1:
            raise ValueError("transaction_cost must be finite and in [0, 1)")
        if not isinstance(lookback, int) or lookback < 0 or lookback >= len(df) - 1:
            raise ValueError("lookback must be >= 0 and leave at least two usable rows")

        self.tickers = list(tickers)
        self.feature_columns = []
        for ticker in self.tickers:
            self.feature_columns.extend([
                f"Close_{ticker}", f"RSI_{ticker}", f"MACD_{ticker}",
                f"BB_middle_{ticker}", f"ATR_{ticker}",
            ])
        missing = [column for column in self.feature_columns if column not in df.columns]
        if missing:
            raise ValueError(f"Missing market feature columns: {missing}")

        self.df = df.reset_index(drop=True).copy()
        features = self.df[self.feature_columns].to_numpy(dtype=float)
        if not np.isfinite(features).all():
            raise ValueError("Market features must be finite")
        close_columns = [f"Close_{ticker}" for ticker in self.tickers]
        prices = self.df[close_columns].to_numpy(dtype=float)
        if (prices <= 0).any():
            raise ValueError("Close prices must be strictly positive")

        self.n_assets = len(self.tickers)
        self.initial_capital = float(initial_capital)
        self.transaction_cost = float(transaction_cost)
        self.lookback = lookback
        self.current_step = lookback
        self.balance = self.initial_capital
        self.stocks = np.zeros(self.n_assets, dtype=float)
        self.prev_total_asset = self.initial_capital
        self.total_asset = self.initial_capital

        # State includes market features and current holdings weights, so turnover
        # costs are not based on hidden portfolio state.
        self.n_features = 5
        self.state_dim = 1 + self.n_assets * self.n_features + self.n_assets
        self.action_space = spaces.Box(
            low=-1.0, high=1.0, shape=(self.n_assets,), dtype=np.float32
        )
        self.observation_space = spaces.Box(
            low=-np.inf, high=np.inf, shape=(self.state_dim,), dtype=np.float32
        )

    @staticmethod
    def action_to_weights(action: np.ndarray, n_assets: int) -> np.ndarray:
        action = np.asarray(action, dtype=float)
        if action.shape != (n_assets,):
            raise ValueError(f"Expected action shape {(n_assets,)}, got {action.shape}")
        if not np.isfinite(action).all():
            raise ValueError("Action values must be finite")
        scores = np.clip(action, -1.0, 1.0) + 1.0
        total = float(scores.sum())
        if total <= np.finfo(float).eps:
            return np.full(n_assets, 1.0 / n_assets, dtype=float)
        return scores / total

    def reset(self, seed=None, options=None):
        super().reset(seed=seed)
        options = options or {}
        self.current_step = len(self.df) - 1 if options.get("start_at_latest") else self.lookback
        self.balance = self.initial_capital
        self.stocks = np.zeros(self.n_assets, dtype=float)
        self.prev_total_asset = self.initial_capital
        self.total_asset = self.initial_capital
        return self._get_state(), {}

    def step(self, action: np.ndarray):
        if self.current_step >= len(self.df) - 1:
            raise RuntimeError("Episode is terminated; call reset() before step()")

        target_weights = self.action_to_weights(action, self.n_assets)
        current_prices = self._get_current_prices()
        equity_before_trade = float(self.balance + np.dot(self.stocks, current_prices))
        if not np.isfinite(equity_before_trade) or equity_before_trade <= 0:
            raise RuntimeError("Portfolio equity is not finite and positive")

        current_weights = self.stocks * current_prices / equity_before_trade
        turnover = float(np.abs(target_weights - current_weights).sum())
        cost = equity_before_trade * turnover * self.transaction_cost
        equity_after_cost = equity_before_trade - cost
        if not np.isfinite(equity_after_cost) or equity_after_cost <= 0:
            self.balance = 0.0
            self.stocks = np.zeros(self.n_assets, dtype=float)
            self.total_asset = 0.0
            self.current_step = len(self.df) - 1
            self.prev_total_asset = max(equity_before_trade, np.finfo(float).tiny)
            return self._get_state(), -1.0, True, False, {
                "total_asset": 0.0, "transaction_cost": float(cost),
                "turnover": turnover, "bankrupt": True,
            }

        # Rebalance at close t, using post-cost equity; all positions are long-only.
        self.stocks = target_weights * equity_after_cost / current_prices
        self.balance = 0.0
        self.current_step += 1
        next_prices = self._get_current_prices()
        self.total_asset = float(self.balance + np.dot(self.stocks, next_prices))
        if not np.isfinite(self.total_asset) or self.total_asset <= 0:
            self.total_asset = 0.0
            reward = -1.0
            terminated = True
        else:
            reward = self.total_asset / self.prev_total_asset - 1.0
            terminated = self.current_step >= len(self.df) - 1
        self.prev_total_asset = self.total_asset
        return self._get_state(), float(reward), terminated, False, {
            "total_asset": float(self.total_asset),
            "transaction_cost": float(cost),
            "turnover": turnover,
            "target_weights": target_weights.copy(),
            "bankrupt": self.total_asset <= 0,
        }

    def _get_state(self) -> np.ndarray:
        row = self.df.iloc[self.current_step]
        market_features = [float(row[column]) for column in self.feature_columns]
        prices = self._get_current_prices()
        equity = float(self.balance + np.dot(self.stocks, prices))
        weights = self.stocks * prices / equity if equity > 0 else np.zeros(self.n_assets)
        state = np.asarray([self.balance, *market_features, *weights], dtype=np.float32)
        if not np.isfinite(state).all():
            raise ValueError("Observation contains non-finite values")
        return state

    def _get_current_prices(self) -> np.ndarray:
        return self.df[[f"Close_{ticker}" for ticker in self.tickers]].iloc[
            self.current_step
        ].to_numpy(dtype=float)

    def render(self):
        print(f"Step: {self.current_step}, Portfolio: {self.total_asset:.2f}")


class PPOAgent:
    """PPO policy whose actions consistently represent target allocation scores."""

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
        if not tickers or len(set(tickers)) != len(tickers):
            raise ValueError("tickers must be a non-empty list of unique symbols")
        self.tickers = list(tickers)
        self.initial_capital = float(initial_capital)
        if not np.isfinite(self.initial_capital) or self.initial_capital <= 0:
            raise ValueError("initial_capital must be finite and positive")
        self.model_path = model_path or "models/ppo_trading_model_v2.zip"
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
            logger.info("PPOAgent loaded from %s", self.model_path)
        else:
            logger.info("No compatible PPO v2 model found; equal-weight fallback remains active")

    def train(self, df: pd.DataFrame, total_timesteps: int = 50000):
        if total_timesteps <= 0:
            raise ValueError("total_timesteps must be positive")

        def make_env():
            return StockTradingEnv(
                df=df.copy(),
                tickers=self.tickers,
                initial_capital=self.initial_capital,
            )

        self.env = DummyVecEnv([make_env])
        if self.model is None:
            self.model = PPO(
                "MlpPolicy", self.env, learning_rate=self.learning_rate,
                n_steps=self.n_steps, batch_size=self.batch_size,
                n_epochs=self.n_epochs, gamma=self.gamma, verbose=self.verbose,
            )
        else:
            self.model.set_env(self.env)
        self.model.learn(total_timesteps=total_timesteps)
        self.save()
        logger.info("PPO training completed")

    def predict_weights(self, market_data: pd.DataFrame) -> np.ndarray:
        if self.model is None:
            logger.warning("PPO model unavailable; using equal-weight fallback")
            return np.full(len(self.tickers), 1.0 / len(self.tickers), dtype=float)

        env = StockTradingEnv(
            df=market_data.reset_index(drop=True).copy(),
            tickers=self.tickers,
            initial_capital=self.initial_capital,
        )
        observation, _ = env.reset(options={"start_at_latest": True})
        action, _ = self.model.predict(observation, deterministic=True)
        return StockTradingEnv.action_to_weights(action, len(self.tickers))

    def save(self):
        if self.model is None:
            raise RuntimeError("Cannot save PPO model before training")
        model_path = Path(self.model_path)
        model_path.parent.mkdir(parents=True, exist_ok=True)
        self.model.save(str(model_path))
        metadata_path = Path(str(model_path) + ".metadata.json")
        metadata_path.write_text(json.dumps({
            "contract_version": MODEL_CONTRACT_VERSION,
            "tickers": self.tickers,
            "state_dim": 1 + len(self.tickers) * 6,
            "action_contract": "long_only_target_weights",
        }, indent=2))

    def load(self):
        model_path = Path(self.model_path)
        metadata_path = Path(str(model_path) + ".metadata.json")
        if not model_path.exists():
            raise FileNotFoundError(f"Model not found: {model_path}")
        if not metadata_path.exists():
            raise ValueError(
                "PPO model has no contract metadata; retrain it with the current action/state contract"
            )
        metadata = json.loads(metadata_path.read_text())
        expected_dim = 1 + len(self.tickers) * 6
        if metadata.get("contract_version") != MODEL_CONTRACT_VERSION:
            raise ValueError("PPO model contract version is incompatible; retrain the model")
        if metadata.get("tickers") != self.tickers or metadata.get("state_dim") != expected_dim:
            raise ValueError("PPO model ticker/state contract is incompatible; retrain the model")
        if metadata.get("action_contract") != "long_only_target_weights":
            raise ValueError("PPO model action contract is incompatible; retrain the model")
        self.model = PPO.load(str(model_path))
