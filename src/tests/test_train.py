import numpy as np
import pandas as pd
import pytest

import train
from train import chronological_split, flatten_processed_data, load_training_config, training_data_source
from src.agents.ppo_agent import PPOAgent
from src.data.market_data_contract import market_data_contract


def test_flatten_processed_alpaca_field_ticker_columns_to_ppo_feature_contract():
    fields = ["Open", "High", "Low", "Close", "Volume", "RSI", "MACD", "BB_middle", "ATR"]
    tickers = ["AAA", "BBB"]
    columns = pd.MultiIndex.from_product([fields, tickers])
    values = np.arange(5 * len(columns), dtype=float).reshape(5, len(columns)) + 1
    data = pd.DataFrame(
        values,
        index=pd.date_range("2024-01-01", periods=5, freq="D"),
        columns=columns,
    )

    flattened = flatten_processed_data(data, tickers)

    assert list(flattened.columns) == [
        f"{feature}_{ticker}"
        for ticker in tickers
        for feature in ("Open", "Close", "RSI", "MACD", "BB_middle", "ATR")
    ]
    np.testing.assert_allclose(flattened["Close_AAA"], data[("Close", "AAA")])


def test_flatten_rejects_missing_ticker_or_feature():
    columns = pd.MultiIndex.from_tuples([
        ("Close", "AAA"), ("RSI", "AAA"), ("MACD", "AAA"),
        ("BB_middle", "AAA"), ("ATR", "AAA"),
    ])
    data = pd.DataFrame([[100, 50, 0, 100, 1]], columns=columns)
    with pytest.raises(ValueError, match="missing tickers"):
        flatten_processed_data(data, ["AAA", "BBB"])


def test_chronological_split_keeps_later_holdout_and_no_overlap():
    data = pd.DataFrame(
        {"Close_AAA": np.arange(100, dtype=float)},
        index=pd.date_range("2020-01-01", periods=100, freq="D"),
    )

    training, validation = chronological_split(data, validation_fraction=0.2)

    assert len(training) == 80
    assert len(validation) == 20
    assert training.index.max() < validation.index.min()
    assert training.iloc[-1, 0] == 79
    assert validation.iloc[0, 0] == 80


def test_chronological_split_rejects_too_small_or_invalid_fraction():
    data = pd.DataFrame({"Close_AAA": np.arange(40, dtype=float)})
    with pytest.raises(ValueError, match="between 0.1 and 0.4"):
        chronological_split(data, validation_fraction=0.5)
    with pytest.raises(ValueError, match="at least 30 training"):
        chronological_split(data, validation_fraction=0.2)



def test_flatten_processed_single_ticker_flat_columns():
    data = pd.DataFrame({
        "Open": [99.5, 100.5],
        "Close": [100.0, 101.0],
        "RSI": [50.0, 55.0],
        "MACD": [0.0, 0.2],
        "BB_middle": [99.0, 100.0],
        "ATR": [1.0, 1.1],
    })
    flattened = flatten_processed_data(data, ["AAA"])
    assert list(flattened.columns) == [
        "Open_AAA", "Close_AAA", "RSI_AAA", "MACD_AAA", "BB_middle_AAA", "ATR_AAA"
    ]
    np.testing.assert_allclose(flattened["Close_AAA"], data["Close"])


def test_training_data_source_matches_active_sip_contract():
    contract = market_data_contract()
    assert training_data_source() == "alpaca_sip_adjusted_ohlcv"
    assert training_data_source() == (
        f"{contract['provider']}_{contract['feed']}_adjusted_ohlcv"
    )
    assert contract["feed"] == "sip"


def test_training_config_loads_project_root_dotenv_without_broker_credentials(monkeypatch):
    for key in (
        "ALPACA_API_KEY", "ALPACA_API_SECRET", "STMAA_TICKERS",
        "STMAA_INITIAL_CAPITAL", "MAX_PORTFOLIO_EXPOSURE",
        "MAX_POSITION_SIZE", "TRANSACTION_COST", "SLIPPAGE",
    ):
        monkeypatch.delenv(key, raising=False)

    calls = []
    def fake_load_dotenv(*, dotenv_path, override):
        calls.append((dotenv_path, override))
        monkeypatch.setenv("STMAA_TICKERS", "MSFT,AAPL")
        monkeypatch.setenv("STMAA_INITIAL_CAPITAL", "250000")
        monkeypatch.setenv("MAX_PORTFOLIO_EXPOSURE", "0.40")
        return True

    monkeypatch.setattr(train, "load_dotenv", fake_load_dotenv)
    config = load_training_config()

    assert calls == [(train.BASE_DIR / ".env", False)]
    assert config.tickers == ("MSFT", "AAPL")
    assert config.initial_capital == pytest.approx(250000.0)
    assert config.max_gross_exposure == pytest.approx(0.40)


def test_training_cli_overrides_use_shared_validation_and_do_not_require_credentials(monkeypatch):
    monkeypatch.setattr(train, "load_dotenv", lambda **kwargs: False)
    config = load_training_config(overrides={
        "STMAA_TICKERS": ["NVDA", "AAPL"],
        "STMAA_INITIAL_CAPITAL": 125000,
        "MAX_PORTFOLIO_EXPOSURE": 0.35,
        "MAX_POSITION_SIZE": 0.15,
        "TRANSACTION_COST": 0.002,
        "SLIPPAGE": 0.001,
    })
    assert config.tickers == ("NVDA", "AAPL")
    assert config.initial_capital == pytest.approx(125000.0)
    assert config.max_gross_exposure == pytest.approx(0.35)
    assert config.max_position_size == pytest.approx(0.15)
    assert config.transaction_cost == pytest.approx(0.002)
    assert config.slippage == pytest.approx(0.001)


def test_training_and_inference_share_neutral_ppo_artifact_base_path():
    agent = PPOAgent(tickers=["A"], load_existing_model=False)
    assert train.MODEL_PATH.name == "ppo_trading_model.zip"
    assert agent.model_path == "models/ppo_trading_model.zip"
