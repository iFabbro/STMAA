"""PPO agent with a consistent target-weight action contract."""
import fcntl
import hashlib
import hmac
import json
import logging
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import List, Optional

import gymnasium as gym
import numpy as np
import pandas as pd
from gymnasium import spaces
from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import DummyVecEnv

try:
    from src.data.market_data_contract import (
        market_data_contract,
        validate_market_data_contract,
    )
except ModuleNotFoundError:
    from data.market_data_contract import (
        market_data_contract,
        validate_market_data_contract,
    )

logger = logging.getLogger(__name__)


def _sha256_file(path: Path) -> str:
    """Return a streaming SHA-256 digest for a model artifact."""
    digest = hashlib.sha256()
    with path.open("rb") as artifact:
        for chunk in iter(lambda: artifact.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()



@contextmanager
def _artifact_lock(model_path: Path, timeout_seconds: float = 30.0):
    """Serialize PPO artifact readers, writers, and maintenance across processes."""
    lock_path = model_path.parent.resolve() / f".{model_path.name}.artifact.lock"
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_file = lock_path.open("a+", encoding="utf-8")
    except OSError as exc:
        raise RuntimeError("Unable to open PPO artifact lock; refusing artifact operation") from exc
    with lock_file:
        deadline = time.monotonic() + timeout_seconds
        while True:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError as exc:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError("Timed out acquiring PPO artifact lock; refusing artifact operation") from exc
                time.sleep(min(0.05, remaining))
            except OSError as exc:
                raise RuntimeError("Unable to acquire PPO artifact lock; refusing artifact operation") from exc
        try:
            yield
        finally:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            except OSError as exc:
                raise RuntimeError("Unable to release PPO artifact lock; artifact state requires review") from exc


def _fsync_directory(path: Path) -> None:
    """Persist directory-entry updates after atomic artifact publication."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    directory_fd = os.open(path, flags)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)

MODEL_CONTRACT_VERSION = 5

class StockTradingEnv(gym.Env):
    """Long-only target-weight environment with close-to-close accounting.

    Actions are allocation scores in [-1, 1]. Only positive scores allocate
    capital; zero or negative scores remain in cash. Target weights are bounded
    by per-position and gross-exposure caps.
    An action at close t is executed at close t and earns the return to close t+1.
    """

    metadata = {"render_modes": ["human"]}

    def __init__(
        self,
        df: pd.DataFrame,
        tickers: List[str],
        initial_capital: float = 100000.0,
        transaction_cost: float = 0.001,
        slippage: float = 0.0005,
        max_position_size: float = 0.2,
        max_gross_exposure: float = 0.5,
        lookback: int = 20,
    ):
        super().__init__()
        if not isinstance(df, pd.DataFrame) or df.empty:
            raise ValueError("df must be a non-empty DataFrame")
        if not tickers or len(set(tickers)) != len(tickers):
            raise ValueError("tickers must be a non-empty list of unique symbols")
        if not np.isfinite(initial_capital) or initial_capital <= 0:
            raise ValueError("initial_capital must be finite and positive")
        costs = np.asarray([transaction_cost, slippage, max_position_size, max_gross_exposure], dtype=float)
        if not np.isfinite(costs).all():
            raise ValueError("Trading costs and allocation limits must be finite")
        if transaction_cost < 0 or slippage < 0 or transaction_cost + slippage >= 1:
            raise ValueError("transaction_cost and slippage must be non-negative and sum to less than 1")
        if max_position_size <= 0 or max_gross_exposure <= 0:
            raise ValueError("Allocation limits must be positive")
        if max_position_size > 1 or max_gross_exposure > 1:
            raise ValueError("Allocation limits cannot exceed 1")
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
        self.slippage = float(slippage)
        self.max_position_size = float(max_position_size)
        self.max_gross_exposure = float(max_gross_exposure)
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
    def action_to_weights(
        action: np.ndarray,
        n_assets: int,
        max_position_size: float = 0.2,
        max_gross_exposure: float = 0.5,
    ) -> np.ndarray:
        action = np.asarray(action, dtype=float)
        if action.shape != (n_assets,):
            raise ValueError(f"Expected action shape {(n_assets,)}, got {action.shape}")
        if not np.isfinite(action).all():
            raise ValueError("Action values must be finite")
        limits = np.asarray([max_position_size, max_gross_exposure], dtype=float)
        if not np.isfinite(limits).all() or (limits <= 0).any() or (limits > 1).any():
            raise ValueError("Allocation limits must be finite and in (0, 1]")
        scores = np.clip(action, 0.0, 1.0)
        total = float(scores.sum())
        if total <= np.finfo(float).eps:
            # A uniformly minimum action is an explicit all-cash target.
            return np.zeros(n_assets, dtype=float)
        normalized = scores / total
        # Deliberately do not redistribute after clipping: any unused allocation
        # remains cash and can never push a name or gross exposure above its cap.
        return np.minimum(normalized * max_gross_exposure, max_position_size)

    def reset(self, seed=None, options=None):
        super().reset(seed=seed)
        options = options or {}
        self.current_step = len(self.df) - 1 if options.get("start_at_latest") else self.lookback

        initial_weights = np.asarray(
            options.get("initial_weights", np.zeros(self.n_assets)), dtype=float
        )
        if initial_weights.shape != (self.n_assets,):
            raise ValueError(f"Expected initial_weights shape {(self.n_assets,)}, got {initial_weights.shape}")
        if not np.isfinite(initial_weights).all() or (initial_weights < 0).any():
            raise ValueError("initial_weights must be finite and non-negative")
        if float(initial_weights.sum()) > 1.0 + 1e-8:
            raise ValueError("initial_weights gross allocation must not exceed 1")

        prices = self._get_current_prices()
        self.balance = self.initial_capital * max(0.0, 1.0 - float(initial_weights.sum()))
        self.stocks = initial_weights * self.initial_capital / prices
        self.prev_total_asset = self.initial_capital
        self.total_asset = self.initial_capital
        return self._get_state(), {}

    def step(self, action: np.ndarray):
        if self.current_step >= len(self.df) - 1:
            raise RuntimeError("Episode is terminated; call reset() before step()")

        target_weights = self.action_to_weights(
            action, self.n_assets, self.max_position_size, self.max_gross_exposure
        )
        current_prices = self._get_current_prices()
        equity_before_trade = float(self.balance + np.dot(self.stocks, current_prices))
        if not np.isfinite(equity_before_trade) or equity_before_trade <= 0:
            raise RuntimeError("Portfolio equity is not finite and positive")

        current_weights = self.stocks * current_prices / equity_before_trade
        turnover = float(np.abs(target_weights - current_weights).sum())
        cost = equity_before_trade * turnover * (self.transaction_cost + self.slippage)
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
        self.balance = equity_after_cost * max(0.0, 1.0 - float(target_weights.sum()))
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
        state = np.asarray(
            [self.balance / self.initial_capital, *market_features, *weights],
            dtype=np.float32,
        )
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
        transaction_cost: float = 0.001,
        slippage: float = 0.0005,
        max_position_size: float = 0.2,
        max_gross_exposure: float = 0.5,
        load_existing_model: bool = True,
        seed: Optional[int] = None,
    ):
        if not tickers or len(set(tickers)) != len(tickers):
            raise ValueError("tickers must be a non-empty list of unique symbols")
        self.tickers = list(tickers)
        self.initial_capital = float(initial_capital)
        if not np.isfinite(self.initial_capital) or self.initial_capital <= 0:
            raise ValueError("initial_capital must be finite and positive")
        self.model_path = model_path or "models/ppo_trading_model_v3.zip"
        self.transaction_cost = float(transaction_cost)
        self.slippage = float(slippage)
        self.max_position_size = float(max_position_size)
        self.max_gross_exposure = float(max_gross_exposure)
        limits = np.asarray([
            self.transaction_cost, self.slippage,
            self.max_position_size, self.max_gross_exposure,
        ], dtype=float)
        if not np.isfinite(limits).all():
            raise ValueError("PPO costs and allocation limits must be finite")
        if self.transaction_cost < 0 or self.slippage < 0 or self.transaction_cost + self.slippage >= 1:
            raise ValueError("PPO costs must be non-negative and sum to less than 1")
        if not 0 < self.max_position_size <= 1 or not 0 < self.max_gross_exposure <= 1:
            raise ValueError("PPO allocation limits must be in (0, 1]")
        self.learning_rate = learning_rate
        self.n_steps = n_steps
        self.batch_size = batch_size
        self.n_epochs = n_epochs
        self.gamma = gamma
        self.verbose = verbose
        self.model: Optional[PPO] = None
        self.env = None
        self.seed = seed

        metadata_path = Path(str(self.model_path) + ".metadata.json")
        if load_existing_model and (Path(self.model_path).exists() or metadata_path.exists()):
            self.load()
            logger.info("PPOAgent loaded from %s", self.model_path)
        else:
            logger.info("PPO model not loaded; inference will fail closed until a compatible model is trained or loaded")

    def train(
        self,
        df: pd.DataFrame,
        total_timesteps: int = 50000,
        validation_df: Optional[pd.DataFrame] = None,
        training_metadata: Optional[dict] = None,
    ):
        if total_timesteps <= 0:
            raise ValueError("total_timesteps must be positive")

        def make_env():
            return StockTradingEnv(
                df=df.copy(),
                tickers=self.tickers,
                initial_capital=self.initial_capital,
                transaction_cost=self.transaction_cost,
                slippage=self.slippage,
                max_position_size=self.max_position_size,
                max_gross_exposure=self.max_gross_exposure,
            )

        self.env = DummyVecEnv([make_env])
        if self.model is None:
            self.model = PPO(
                "MlpPolicy", self.env, learning_rate=self.learning_rate,
                n_steps=self.n_steps, batch_size=self.batch_size,
                n_epochs=self.n_epochs, gamma=self.gamma, verbose=self.verbose,
                seed=self.seed,
            )
        else:
            self.model.set_env(self.env)
        self.model.learn(total_timesteps=total_timesteps)
        validation_metrics = self.evaluate(validation_df) if validation_df is not None else None
        metadata = dict(training_metadata or {})
        if validation_metrics is not None:
            metadata["validation_metrics"] = validation_metrics
        self.save(training_metadata=metadata)
        logger.info("PPO training completed")
        return validation_metrics

    def evaluate(self, df: pd.DataFrame) -> dict:
        """Evaluate the deterministic policy on a separate chronological holdout."""
        if self.model is None:
            raise RuntimeError("Cannot evaluate PPO policy before training or loading")
        env = StockTradingEnv(
            df=df.reset_index(drop=True).copy(),
            tickers=self.tickers,
            initial_capital=self.initial_capital,
            transaction_cost=self.transaction_cost,
            slippage=self.slippage,
            max_position_size=self.max_position_size,
            max_gross_exposure=self.max_gross_exposure,
            lookback=0,
        )
        observation, _ = env.reset()
        equity = [self.initial_capital]
        terminated = False
        while not terminated:
            action, _ = self.model.predict(observation, deterministic=True)
            observation, _, terminated, truncated, info = env.step(action)
            if truncated:
                raise RuntimeError("Unexpected truncation during PPO validation")
            equity.append(float(info["total_asset"]))
        values = np.asarray(equity, dtype=float)
        if not np.isfinite(values).all() or (values <= 0).any():
            raise RuntimeError("PPO validation produced invalid portfolio equity")
        peaks = np.maximum.accumulate(values)
        return {
            "observations": int(len(df)),
            "steps": int(len(values) - 1),
            "initial_value": float(values[0]),
            "final_value": float(values[-1]),
            "cumulative_return": float(values[-1] / values[0] - 1.0),
            "max_drawdown": float(np.min(values / peaks - 1.0)),
        }

    def predict_weights(
        self,
        market_data: pd.DataFrame,
        current_weights: Optional[np.ndarray] = None,
    ) -> np.ndarray:
        if current_weights is None:
            current_weights = np.zeros(len(self.tickers), dtype=float)
        else:
            current_weights = np.asarray(current_weights, dtype=float)
        if current_weights.shape != (len(self.tickers),):
            raise ValueError(
                f"Expected current_weights shape {(len(self.tickers),)}, got {current_weights.shape}"
            )
        if not np.isfinite(current_weights).all() or (current_weights < 0).any():
            raise ValueError("current_weights must be finite and non-negative")
        if float(current_weights.sum()) > 1.0 + 1e-8:
            raise ValueError("current_weights gross allocation must not exceed 1")
        if self.model is None:
            raise RuntimeError(
                "PPO model unavailable; train or load a compatible model before inference"
            )

        env = StockTradingEnv(
            df=market_data.reset_index(drop=True).copy(),
            tickers=self.tickers,
            initial_capital=self.initial_capital,
            transaction_cost=self.transaction_cost,
            slippage=self.slippage,
            max_position_size=self.max_position_size,
            max_gross_exposure=self.max_gross_exposure,
            lookback=0,
        )
        observation, _ = env.reset(options={
            "start_at_latest": True,
            "initial_weights": current_weights,
        })
        action, _ = self.model.predict(observation, deterministic=True)
        return StockTradingEnv.action_to_weights(
            action, len(self.tickers), self.max_position_size, self.max_gross_exposure
        )

    def save(self, training_metadata: Optional[dict] = None):
        with _artifact_lock(Path(self.model_path)):
            return self._save_locked(training_metadata=training_metadata)

    def _save_locked(self, training_metadata: Optional[dict] = None):
        if self.model is None:
            raise RuntimeError("Cannot save PPO model before training")
        model_path = Path(self.model_path)
        model_path.parent.mkdir(parents=True, exist_ok=True)
        metadata_path = Path(str(model_path) + ".metadata.json")
        model_temp_path = None
        metadata_temp_path = None
        try:
            model_fd, model_temp_name = tempfile.mkstemp(
                prefix=f".{model_path.name}.", suffix=".zip", dir=model_path.parent
            )
            os.close(model_fd)
            model_temp_path = Path(model_temp_name)
            metadata_fd, metadata_temp_name = tempfile.mkstemp(
                prefix=f".{metadata_path.name}.", suffix=".tmp", dir=model_path.parent
            )
            os.close(metadata_fd)
            metadata_temp_path = Path(metadata_temp_name)
            # Publish each model under an immutable content-addressed name. The
            # metadata file is the sole atomic pointer to the active generation.
            self.model.save(str(model_temp_path))
            with model_temp_path.open("rb") as model_file:
                os.fsync(model_file.fileno())
            model_digest = _sha256_file(model_temp_path)
            published_model_path = model_path.with_name(
                f"{model_path.stem}.{model_digest}{model_path.suffix or '.zip'}"
            )
            metadata = {
                "contract_version": MODEL_CONTRACT_VERSION,
                "tickers": self.tickers,
                "state_dim": 1 + len(self.tickers) * 6,
                "action_contract": "long_only_capped_target_weights_zero_floor",
                "transaction_cost": self.transaction_cost,
                "slippage": self.slippage,
                "max_position_size": self.max_position_size,
                "max_gross_exposure": self.max_gross_exposure,
                "market_data_contract": market_data_contract(),
                "model_sha256": model_digest,
                "artifact_file": published_model_path.name,
                "training": training_metadata or {},
            }
            with metadata_temp_path.open("w", encoding="utf-8") as metadata_file:
                metadata_file.write(json.dumps(metadata, indent=2))
                metadata_file.flush()
                os.fsync(metadata_file.fileno())
            # If publication is interrupted before the metadata replace, the
            # previous manifest still points to its intact immutable artifact.
            os.replace(model_temp_path, published_model_path)
            _fsync_directory(model_path.parent)
            os.replace(metadata_temp_path, metadata_path)
            _fsync_directory(model_path.parent)
        finally:
            if model_temp_path is not None:
                model_temp_path.unlink(missing_ok=True)
            if metadata_temp_path is not None:
                metadata_temp_path.unlink(missing_ok=True)

    def load(self):
        with _artifact_lock(Path(self.model_path)):
            return self._load_locked()

    def prune_artifacts(
        self,
        retain_generations: int = 3,
        dry_run: bool = True,
        min_temp_age_seconds: float = 3600.0,
    ) -> List[str]:
        """List or remove unreferenced PPO generations and stale staging files.

        Maintenance is explicit and dry-run by default. The same process-shared
        lock protects readers, publishers, and pruning; an invalid active manifest
        aborts cleanup rather than risking deletion of the only recoverable model.
        """
        if not isinstance(retain_generations, int) or isinstance(retain_generations, bool) or retain_generations < 1:
            raise ValueError("retain_generations must be a positive integer")
        if not np.isfinite(min_temp_age_seconds) or min_temp_age_seconds < 0:
            raise ValueError("min_temp_age_seconds must be finite and non-negative")

        model_path = Path(self.model_path)
        with _artifact_lock(model_path):
            manifest_path = Path(str(model_path) + ".metadata.json")
            if not manifest_path.is_file():
                return []
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise RuntimeError("Cannot prune PPO artifacts: active manifest is unreadable") from exc
            active_name = manifest.get("artifact_file")
            active_digest = manifest.get("model_sha256")
            expected_name = f"{model_path.stem}.{active_digest}{model_path.suffix or '.zip'}"
            if (
                not isinstance(active_digest, str)
                or len(active_digest) != 64
                or any(ch not in "0123456789abcdef" for ch in active_digest)
                or active_name != expected_name
            ):
                raise RuntimeError("Cannot prune PPO artifacts: active manifest is invalid")
            active_path = model_path.parent / active_name
            if not active_path.is_file() or not hmac.compare_digest(_sha256_file(active_path), active_digest):
                raise RuntimeError("Cannot prune PPO artifacts: active generation is missing or corrupt")

            import re
            pattern = re.compile(
                rf"^{re.escape(model_path.stem)}\.([0-9a-f]{{64}}){re.escape(model_path.suffix or '.zip')}$"
            )
            generations = []
            for candidate in model_path.parent.iterdir():
                match = pattern.fullmatch(candidate.name)
                if match and candidate.is_file():
                    digest = match.group(1)
                    try:
                        if hmac.compare_digest(_sha256_file(candidate), digest):
                            generations.append(candidate)
                    except OSError:
                        continue
            generations.sort(key=lambda item: item.stat().st_mtime_ns, reverse=True)
            retained = {active_path}
            for candidate in generations:
                if len(retained) >= retain_generations:
                    break
                retained.add(candidate)
            remove = [item for item in generations if item not in retained]

            now = time.time()
            temp_candidates = list(model_path.parent.glob(f".{model_path.name}.*.zip"))
            temp_candidates += list(model_path.parent.glob(f".{manifest_path.name}.*.tmp"))
            for candidate in temp_candidates:
                try:
                    if candidate.is_file() and now - candidate.stat().st_mtime >= min_temp_age_seconds:
                        remove.append(candidate)
                except OSError:
                    continue
            # De-duplicate while preserving deterministic output order.
            remove = list(dict.fromkeys(remove))
            if not dry_run and remove:
                for candidate in remove:
                    candidate.unlink(missing_ok=True)
                _fsync_directory(model_path.parent)
            return [str(candidate) for candidate in remove]

    def _load_locked(self):
        model_path = Path(self.model_path)
        metadata_path = Path(str(model_path) + ".metadata.json")
        if not metadata_path.exists():
            if not model_path.exists():
                raise FileNotFoundError(f"Model not found: {model_path}")
            raise ValueError(
                "PPO model has no contract metadata; retrain it with the current action/state contract"
            )
        metadata = json.loads(metadata_path.read_text())
        expected_dim = 1 + len(self.tickers) * 6
        if metadata.get("contract_version") != MODEL_CONTRACT_VERSION:
            raise ValueError("PPO model contract version is incompatible; retrain the model")
        validate_market_data_contract(metadata.get("market_data_contract"))
        if metadata.get("tickers") != self.tickers or metadata.get("state_dim") != expected_dim:
            raise ValueError("PPO model ticker/state contract is incompatible; retrain the model")
        if metadata.get("action_contract") != "long_only_capped_target_weights_zero_floor":
            raise ValueError("PPO model action contract is incompatible; retrain the model")
        expected_parameters = {
            "transaction_cost": self.transaction_cost,
            "slippage": self.slippage,
            "max_position_size": self.max_position_size,
            "max_gross_exposure": self.max_gross_exposure,
        }
        for key, expected in expected_parameters.items():
            actual = metadata.get(key)
            if not isinstance(actual, (int, float)) or not np.isclose(actual, expected, rtol=0, atol=1e-12):
                raise ValueError(f"PPO model {key} is incompatible; retrain the model")
        expected_digest = metadata.get("model_sha256")
        if (
            not isinstance(expected_digest, str)
            or len(expected_digest) != 64
            or any(character not in "0123456789abcdef" for character in expected_digest)
        ):
            raise ValueError("PPO model metadata has no valid artifact SHA-256; retrain the model")
        artifact_name = metadata.get("artifact_file")
        if artifact_name is None:
            # Backward compatibility for the immediately preceding fixed-path
            # format, which already required a verified digest.
            artifact_path = model_path
        elif (
            not isinstance(artifact_name, str)
            or not artifact_name
            or Path(artifact_name).name != artifact_name
        ):
            raise ValueError("PPO model metadata has an invalid artifact filename")
        else:
            artifact_path = model_path.parent / artifact_name
        if not artifact_path.is_file():
            raise FileNotFoundError(f"PPO model artifact not found: {artifact_path}")
        actual_digest = _sha256_file(artifact_path)
        if not hmac.compare_digest(expected_digest, actual_digest):
            raise ValueError("PPO model artifact SHA-256 mismatch; refusing to load")
        self.model = PPO.load(str(artifact_path))
