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



def test_training_dataset_sha256_is_stable_and_sensitive_to_data_and_timestamps():
    from train import training_dataset_sha256

    index = pd.date_range("2024-01-02", periods=3, freq="D")
    frame = pd.DataFrame({"Close_AAA": [100.0, 101.25, 99.5]}, index=index)

    first = training_dataset_sha256(frame)
    assert first == training_dataset_sha256(frame.copy())
    assert len(first) == 64
    assert all(character in "0123456789abcdef" for character in first)

    changed_value = frame.copy()
    changed_value.iloc[1, 0] += 0.01
    assert training_dataset_sha256(changed_value) != first

    changed_timestamp = frame.copy()
    changed_timestamp.index = changed_timestamp.index + pd.Timedelta(days=1)
    assert training_dataset_sha256(changed_timestamp) != first


def test_training_dataset_sha256_rejects_empty_or_non_dataframe_input():
    from train import training_dataset_sha256

    with pytest.raises(ValueError, match="non-empty DataFrame"):
        training_dataset_sha256(pd.DataFrame())
    with pytest.raises(ValueError, match="non-empty DataFrame"):
        training_dataset_sha256(None)


def test_training_git_commit_is_a_sha_or_none():
    from train import training_git_commit

    commit = training_git_commit()
    assert commit is None or (
        len(commit) == 40
        and all(character in "0123456789abcdef" for character in commit)
    )



def test_training_git_worktree_clean_is_boolean_or_none():
    from train import training_git_worktree_clean

    clean = training_git_worktree_clean()
    assert clean is None or isinstance(clean, bool)



def test_training_git_provenance_returns_none_when_git_is_unavailable(monkeypatch):
    def unavailable(*args, **kwargs):
        raise FileNotFoundError("git executable not found")

    monkeypatch.setattr(train.subprocess, "run", unavailable)

    from train import training_git_commit, training_git_worktree_clean

    assert training_git_commit() is None
    assert training_git_worktree_clean() is None


def _run_training_main_for_model_path(monkeypatch, model_path_args):
    from types import SimpleNamespace

    history = pd.DataFrame(
        {
            "Open_AAA": np.linspace(99.0, 110.0, 100),
            "Close_AAA": np.linspace(100.0, 111.0, 100),
            "RSI_AAA": np.full(100, 50.0),
            "MACD_AAA": np.zeros(100),
            "BB_middle_AAA": np.linspace(99.0, 110.0, 100),
            "ATR_AAA": np.ones(100),
        },
        index=pd.date_range("2024-01-01", periods=100, freq="D"),
    )
    config = SimpleNamespace(
        tickers=("AAA",), initial_capital=1_000.0, transaction_cost=0.001,
        slippage=0.0005, max_position_size=0.2, max_gross_exposure=0.5,
    )
    captured = {}

    class FakeAgent:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def train(self, *args, **kwargs):
            return {"final_value": 1_000.0}

    monkeypatch.setattr(train, "DataProcessor", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(train, "prepare_historical_data", lambda *_args, **_kwargs: history)
    monkeypatch.setattr(train, "PPOAgent", FakeAgent)
    monkeypatch.setattr(train, "load_training_config", lambda overrides=None: config)
    monkeypatch.setattr(train, "training_data_source", lambda: "synthetic_test_data")
    monkeypatch.setattr(train, "training_git_commit", lambda: None)
    monkeypatch.setattr(train, "training_git_worktree_clean", lambda: None)
    train.main([
        "--start-date", "2024-01-01", "--end-date", "2024-04-10",
        "--total-timesteps", "8", *model_path_args,
    ])
    return captured


def test_train_cli_uses_environment_model_path_when_option_is_omitted(tmp_path, monkeypatch):
    target = tmp_path / "environment-model.zip"
    monkeypatch.setenv("STMAA_MODEL_PATH", str(target))
    captured = _run_training_main_for_model_path(monkeypatch, [])
    assert captured["model_path"] == str(target)


def test_train_cli_explicit_model_path_overrides_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("STMAA_MODEL_PATH", str(tmp_path / "environment-model.zip"))
    explicit = tmp_path / "explicit-model.zip"
    captured = _run_training_main_for_model_path(
        monkeypatch, ["--model-path", str(explicit)]
    )
    assert captured["model_path"] == str(explicit)
