import numpy as np
import pandas as pd
import pytest

from src.agents.ppo_agent import PPOAgent, StockTradingEnv


def market_data(prices_a=(100.0, 110.0, 121.0), prices_b=None):
    size = len(prices_a)
    data = {}
    tickers = ["A"]
    for ticker, prices in [("A", prices_a)]:
        data[f"Close_{ticker}"] = prices
        data[f"RSI_{ticker}"] = np.full(size, 50.0)
        data[f"MACD_{ticker}"] = np.zeros(size)
        data[f"BB_middle_{ticker}"] = np.asarray(prices, dtype=float)
        data[f"ATR_{ticker}"] = np.ones(size)
    if prices_b is not None:
        tickers.append("B")
        data["Close_B"] = prices_b
        data["RSI_B"] = np.full(size, 50.0)
        data["MACD_B"] = np.zeros(size)
        data["BB_middle_B"] = np.asarray(prices_b, dtype=float)
        data["ATR_B"] = np.ones(size)
    return pd.DataFrame(data), tickers


def test_environment_observation_matches_declared_space():
    data, tickers = market_data(prices_b=(100.0, 90.0, 81.0))
    env = StockTradingEnv(data, tickers, lookback=0)
    observation, _ = env.reset()
    assert observation.shape == env.observation_space.shape
    assert env.observation_space.contains(observation)


def test_target_weight_action_earns_next_close_to_close_return():
    data, tickers = market_data()
    env = StockTradingEnv(
        data, tickers, initial_capital=1000.0, transaction_cost=0.0, lookback=0
    )
    env.reset()
    _, reward, terminated, truncated, info = env.step(np.array([1.0]))
    assert info["total_asset"] == pytest.approx(1100.0)
    assert reward == pytest.approx(0.10)
    assert terminated is False
    assert truncated is False

    _, reward, terminated, _, info = env.step(np.array([1.0]))
    assert info["total_asset"] == pytest.approx(1210.0)
    assert reward == pytest.approx(0.10)
    assert terminated is True


def test_action_contract_is_long_only_and_handles_all_negative_scores():
    weights = StockTradingEnv.action_to_weights(np.array([-1.0, -1.0]), 2)
    np.testing.assert_allclose(weights, np.array([0.5, 0.5]))
    assert np.all(weights >= 0)
    assert weights.sum() == pytest.approx(1.0)


def test_invalid_market_data_and_actions_fail_explicitly():
    data, tickers = market_data()
    with pytest.raises(ValueError, match="lookback"):
        StockTradingEnv(data, tickers, lookback=2)
    env = StockTradingEnv(data, tickers, lookback=0)
    env.reset()
    with pytest.raises(ValueError, match="action shape"):
        env.step(np.array([0.2, 0.3]))
    with pytest.raises(ValueError, match="finite"):
        env.step(np.array([np.nan]))


def test_prediction_uses_latest_market_row_and_same_action_contract():
    data, tickers = market_data(prices_a=(100.0, 110.0, 121.0))
    agent = PPOAgent(tickers=tickers, model_path="/tmp/nonexistent-ppo-model.zip")
    captured = {}

    class FakeModel:
        def predict(self, observation, deterministic=True):
            captured["observation"] = observation
            return np.array([1.0]), None

    agent.model = FakeModel()
    weights = agent.predict_weights(data)
    assert captured["observation"][1] == pytest.approx(121.0)
    np.testing.assert_allclose(weights, np.array([1.0]))


def test_legacy_model_without_contract_metadata_is_rejected(tmp_path):
    model_path = tmp_path / "legacy.zip"
    model_path.write_bytes(b"placeholder")
    with pytest.raises(ValueError, match="no contract metadata"):
        PPOAgent(tickers=["A"], model_path=str(model_path))
