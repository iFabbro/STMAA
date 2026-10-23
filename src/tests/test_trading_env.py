import numpy as np
import pandas as pd
import pytest

from src.agents.trading_env import TradingEnv


def make_data(prices_a=(100.0, 110.0, 121.0), prices_b=None):
    dates = pd.date_range("2024-01-01", periods=len(prices_a), freq="D")
    data = {
        "Close_A": prices_a,
        "RSI_A": np.full(len(prices_a), 50.0),
        "MACD_A": np.zeros(len(prices_a)),
        "BB_middle_A": np.asarray(prices_a, dtype=float),
        "ATR_A": np.ones(len(prices_a)),
    }
    tickers = ["A"]
    if prices_b is not None:
        data.update({
            "Close_B": prices_b,
            "RSI_B": np.full(len(prices_a), 50.0),
            "MACD_B": np.zeros(len(prices_a)),
            "BB_middle_B": np.asarray(prices_b, dtype=float),
            "ATR_B": np.ones(len(prices_a)),
        })
        tickers.append("B")
    return pd.DataFrame(data, index=dates), tickers


def test_observation_shape_matches_observation_space():
    data, tickers = make_data(prices_b=(100.0, 90.0, 81.0))
    env = TradingEnv(data, tickers)
    observation, _ = env.reset(seed=1)
    assert observation.shape == env.observation_space.shape
    assert env.observation_space.contains(observation)


def test_action_at_t_earns_return_from_t_to_t_plus_one():
    data, tickers = make_data()
    env = TradingEnv(data, tickers, initial_capital=10000, transaction_cost=0, slippage=0)
    env.reset()
    observation, reward, terminated, truncated, info = env.step(np.array([1.0]))
    assert info["portfolio_value"] == pytest.approx(11000.0)
    assert reward == pytest.approx(0.10)
    assert terminated is False
    assert truncated is False
    assert observation.shape == env.observation_space.shape


def test_rebalance_cost_is_charged_and_reflected_in_reward():
    data, tickers = make_data(prices_a=(100.0, 100.0))
    env = TradingEnv(data, tickers, initial_capital=10000, transaction_cost=0.01, slippage=0.02)
    env.reset()
    _, reward, terminated, _, info = env.step(np.array([0.5]))
    assert info["transaction_cost"] == pytest.approx(150.0)
    assert info["portfolio_value"] == pytest.approx(9850.0)
    assert reward == pytest.approx(-0.015)
    assert terminated is True


def test_invalid_inputs_are_rejected():
    data, tickers = make_data()
    with pytest.raises(ValueError, match="at least two rows"):
        TradingEnv(data.iloc[:1], tickers)
    with pytest.raises(ValueError, match="unique"):
        TradingEnv(data, ["A", "A"])
    with pytest.raises(ValueError, match="Missing close"):
        TradingEnv(data, ["MISSING"])


def test_invalid_action_shape_and_non_finite_action_are_rejected():
    data, tickers = make_data()
    env = TradingEnv(data, tickers)
    env.reset()
    with pytest.raises(ValueError, match="action shape"):
        env.step(np.array([0.5, 0.5]))
    with pytest.raises(ValueError, match="finite"):
        env.step(np.array([np.nan]))


def test_action_is_clipped_and_episode_cannot_be_stepped_after_termination():
    data, tickers = make_data(prices_a=(100.0, 100.0))
    env = TradingEnv(data, tickers, transaction_cost=0, slippage=0)
    env.reset()
    _, _, terminated, _, _ = env.step(np.array([2.0]))
    assert terminated is True
    assert np.abs(env.current_weights).sum() <= 1.0
    with pytest.raises(RuntimeError, match="terminated"):
        env.step(np.array([0.0]))


def test_bad_prices_are_rejected():
    data, tickers = make_data(prices_a=(100.0, np.nan, 120.0))
    with pytest.raises(ValueError, match="finite and strictly positive"):
        TradingEnv(data, tickers)

def test_non_finite_market_features_are_rejected():
    data, tickers = make_data()
    data.loc[data.index[0], "MACD_A"] = np.nan
    with pytest.raises(ValueError, match="Market features"):
        TradingEnv(data, tickers)

