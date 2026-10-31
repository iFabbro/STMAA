import numpy as np
import pandas as pd
import pytest

from src.agents.ppo_agent import PPOAgent, StockTradingEnv
from src.backtest.backtest_engine import ExecutionConsistentBacktest
from src.data.market_data_contract import market_data_contract


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
        data, tickers, initial_capital=1000.0, transaction_cost=0.0,
        slippage=0.0, lookback=0,
    )
    env.reset()
    _, reward, terminated, truncated, info = env.step(np.array([1.0]))
    assert info["total_asset"] == pytest.approx(1020.0)
    assert reward == pytest.approx(0.02)
    assert terminated is False
    assert truncated is False

    _, reward, terminated, _, info = env.step(np.array([1.0]))
    assert info["total_asset"] == pytest.approx(1040.4)
    assert reward == pytest.approx(0.02)
    assert terminated is True


@pytest.mark.parametrize(
    "action",
    [np.array([-1.0, -1.0]), np.array([-0.2, -0.8]), np.array([0.0, 0.0])],
)
def test_action_contract_maps_non_positive_scores_to_cash(action):
    weights = StockTradingEnv.action_to_weights(action, 2)
    np.testing.assert_allclose(weights, np.zeros(2))
    assert np.all(weights >= 0)
    assert weights.sum() == pytest.approx(0.0)


def test_action_contract_keeps_positive_scores_and_cash_residual():
    weights = StockTradingEnv.action_to_weights(
        np.array([-1.0, 1.0]), 2,
        max_position_size=0.5, max_gross_exposure=0.5,
    )
    np.testing.assert_allclose(weights, np.array([0.0, 0.5]))
    assert weights.sum() == pytest.approx(0.5)


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
    weights = agent.predict_weights(data, current_weights=np.array([0.25]))
    assert captured["observation"][1] == pytest.approx(121.0)
    assert captured["observation"][6] == pytest.approx(0.25)
    np.testing.assert_allclose(weights, np.array([0.2]))


def test_prediction_fails_closed_when_model_is_unavailable():
    data, tickers = market_data()
    agent = PPOAgent(tickers=tickers, model_path="/tmp/nonexistent-ppo-model.zip")
    with pytest.raises(RuntimeError, match="model unavailable"):
        agent.predict_weights(data)


@pytest.mark.parametrize(
    "current_weights",
    [
        np.array([np.nan]),
        np.array([-0.1]),
        np.array([1.1]),
        np.array([0.2, 0.3]),
    ],
)
def test_prediction_rejects_invalid_current_weights(current_weights):
    data, tickers = market_data()
    agent = PPOAgent(tickers=tickers, model_path="/tmp/nonexistent-ppo-model.zip")
    agent.model = object()
    with pytest.raises(ValueError, match="current_weights"):
        agent.predict_weights(data, current_weights=current_weights)


def test_legacy_model_without_contract_metadata_is_rejected(tmp_path):
    model_path = tmp_path / "legacy.zip"
    model_path.write_bytes(b"placeholder")
    with pytest.raises(ValueError, match="no contract metadata"):
        PPOAgent(tickers=["A"], model_path=str(model_path))



def test_action_contract_never_exceeds_position_or_gross_caps():
    weights = StockTradingEnv.action_to_weights(
        np.array([1.0, 1.0, 1.0, 1.0]), 4,
        max_position_size=0.2, max_gross_exposure=0.5,
    )
    assert np.all(weights >= 0)
    assert np.all(weights <= 0.2)
    assert weights.sum() <= 0.5 + 1e-12


def test_environment_charges_transaction_cost_and_slippage_and_keeps_cash():
    data, tickers = market_data(prices_a=(100.0, 100.0))
    env = StockTradingEnv(data, tickers, initial_capital=1000.0, lookback=0)
    env.reset()
    _, _, terminated, _, info = env.step(np.array([1.0]))
    assert terminated is True
    assert info["target_weights"][0] == pytest.approx(0.2)
    assert info["transaction_cost"] == pytest.approx(0.3)
    assert env.balance == pytest.approx(799.76)
    assert env.stocks[0] * 100.0 == pytest.approx(199.94)


def test_model_contract_rejects_risk_configuration_mismatch(tmp_path):
    import json

    model_path = tmp_path / "model.zip"
    model_path.write_bytes(b"placeholder")
    metadata = {
        "contract_version": 5,
        "tickers": ["A"],
        "state_dim": 7,
        "action_contract": "long_only_capped_target_weights_zero_floor",
        "market_data_contract": market_data_contract(),
        "transaction_cost": 0.001,
        "slippage": 0.0005,
        "max_position_size": 0.2,
        "max_gross_exposure": 0.5,
    }
    (tmp_path / "model.zip.metadata.json").write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match="max_gross_exposure"):
        PPOAgent(
            tickers=["A"], model_path=str(model_path), max_gross_exposure=0.4
        )



def test_ppo_environment_execution_costs_match_backtest_engine():
    data, tickers = market_data(prices_a=(100.0, 110.0))
    env = StockTradingEnv(
        data,
        tickers,
        initial_capital=1000.0,
        transaction_cost=0.001,
        slippage=0.0005,
        max_position_size=1.0,
        max_gross_exposure=1.0,
        lookback=0,
    )
    env.reset()
    _, _, _, _, info = env.step(np.array([1.0]))

    backtest = ExecutionConsistentBacktest(
        initial_capital=1000.0, transaction_cost=0.001, slippage=0.0005
    )
    result = backtest.run_backtest(
        data[["Close_A"]],
        lambda _timestamp, _row: np.array([1.0]),
        tickers,
    )

    assert info["total_asset"] == pytest.approx(result["final_value"])



def test_model_rejects_previous_market_data_contract_version(tmp_path):
    import json

    model_path = tmp_path / "old-contract.zip"
    model_path.write_bytes(b"placeholder")
    previous_contract = market_data_contract()
    previous_contract["version"] = 1
    (tmp_path / "old-contract.zip.metadata.json").write_text(json.dumps({
        "contract_version": 5,
        "tickers": ["A"],
        "state_dim": 7,
        "action_contract": "long_only_capped_target_weights_zero_floor",
        "market_data_contract": previous_contract,
        "transaction_cost": 0.001,
        "slippage": 0.0005,
        "max_position_size": 0.2,
        "max_gross_exposure": 0.5,
    }))
    with pytest.raises(ValueError, match="market-data contract"):
        PPOAgent(tickers=["A"], model_path=str(model_path))
