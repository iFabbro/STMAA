"""Integration coverage for the complete experimental PPO training workflow."""

from types import SimpleNamespace

import numpy as np
import pandas as pd

import train
from src.agents.ppo_agent import PPOAgent
from src.data.market_data_contract import market_data_contract, market_data_fingerprint


def synthetic_market_data(size: int = 40, ticker: str = "AAA") -> pd.DataFrame:
    """Build deterministic, finite OHLC/indicator data without external providers."""
    close = 100.0 * np.cumprod(1.0 + np.linspace(-0.002, 0.003, size))
    frame = pd.DataFrame(
        {
            f"Open_{ticker}": np.concatenate(([close[0]], close[:-1])),
            f"Close_{ticker}": close,
            f"RSI_{ticker}": 50.0 + 5.0 * np.sin(np.linspace(0.0, 4.0, size)),
            f"MACD_{ticker}": 0.1 * np.sin(np.linspace(0.0, 3.0, size)),
            f"BB_middle_{ticker}": close * 0.995,
            f"ATR_{ticker}": np.full(size, 1.0),
        },
        index=pd.date_range("2024-01-01", periods=size, freq="D"),
    )
    return frame


def test_ppo_training_validation_save_reload_and_inference_cycle(tmp_path):
    """Exercise real SB3 learning and the persisted-artifact restart path on CPU."""
    data = synthetic_market_data(size=40)
    training = data.iloc[:30].copy()
    validation = data.iloc[30:].copy()
    model_path = tmp_path / "integration_model.zip"

    agent = PPOAgent(
        tickers=["AAA"],
        initial_capital=1_000.0,
        model_path=str(model_path),
        transaction_cost=0.0,
        slippage=0.0,
        max_position_size=0.2,
        max_gross_exposure=0.5,
        load_existing_model=False,
        seed=7,
        learning_rate=3e-4,
        n_steps=8,
        batch_size=4,
        n_epochs=1,
        verbose=0,
    )

    metrics = agent.train(
        training,
        total_timesteps=8,
        validation_df=validation,
        training_metadata={"test_run": True, "dataset_sha256": "a" * 64},
    )

    assert agent.model is not None
    assert metrics["observations"] == len(validation)
    assert metrics["steps"] == len(validation) - 1
    assert np.isfinite(metrics["final_value"])
    assert np.isfinite(metrics["cumulative_return"])
    assert np.isfinite(metrics["max_drawdown"])

    manifest_path = tmp_path / "integration_model.zip.metadata.json"
    assert manifest_path.is_file()
    manifest = __import__("json").loads(manifest_path.read_text(encoding="utf-8"))
    artifact_path = tmp_path / manifest["artifact_file"]
    assert artifact_path.is_file()
    assert manifest["training"]["test_run"] is True
    assert manifest["training"]["dataset_sha256"] == "a" * 64
    assert manifest["training"]["validation_metrics"] == metrics

    # Recreate the agent as a fresh process would: it must load the active
    # manifest generation and produce a valid target-weight prediction.
    inference_data = data.copy()
    inference_data.attrs["market_data_contract"] = market_data_contract()
    inference_data.attrs["market_data_fingerprint"] = market_data_fingerprint(
        inference_data
    )
    reloaded = PPOAgent(
        tickers=["AAA"],
        initial_capital=1_000.0,
        model_path=str(model_path),
        transaction_cost=0.0,
        slippage=0.0,
        max_position_size=0.2,
        max_gross_exposure=0.5,
        load_existing_model=True,
        seed=7,
        learning_rate=3e-4,
        n_steps=8,
        batch_size=4,
        n_epochs=1,
        verbose=0,
    )
    weights = reloaded.predict_weights(inference_data)

    assert reloaded.model is not None
    assert weights.shape == (1,)
    assert np.isfinite(weights).all()
    assert np.all(weights >= 0.0)
    assert np.all(weights <= 0.2 + 1e-12)
    assert float(weights.sum()) <= 0.5 + 1e-12


def test_training_cli_orchestrates_chronological_pipeline_without_external_services(
    tmp_path, monkeypatch, capsys
):
    """Verify train.main wiring while replacing data and training boundaries."""
    history = synthetic_market_data(size=100)
    config = SimpleNamespace(
        tickers=("AAA",),
        initial_capital=1_000.0,
        transaction_cost=0.001,
        slippage=0.0005,
        max_position_size=0.2,
        max_gross_exposure=0.5,
    )
    calls = {}

    class FakeProcessor:
        def __init__(self, _data_dir):
            pass

        def process(self, tickers, start_date, end_date):
            calls["data_request"] = (tickers, start_date, end_date)
            return history.copy()

    class FakeAgent:
        def __init__(self, **kwargs):
            calls["agent_config"] = kwargs

        def train(self, training, total_timesteps, validation_df, training_metadata):
            calls["training"] = training.copy()
            calls["validation"] = validation_df.copy()
            calls["timesteps"] = total_timesteps
            calls["metadata"] = training_metadata
            return {
                "observations": len(validation_df),
                "steps": len(validation_df) - 1,
                "final_value": 1_010.0,
                "cumulative_return": 0.01,
                "max_drawdown": -0.02,
            }

    monkeypatch.setattr(train, "DataProcessor", FakeProcessor)
    monkeypatch.setattr(train, "PPOAgent", FakeAgent)
    monkeypatch.setattr(train, "load_training_config", lambda overrides=None: config)
    monkeypatch.setattr(train, "training_data_source", lambda: "synthetic_test_data")
    monkeypatch.setattr(train, "training_git_commit", lambda: "b" * 40)
    monkeypatch.setattr(train, "training_git_worktree_clean", lambda: True)

    train.main(
        [
            "--start-date", "2024-01-01",
            "--end-date", "2024-04-10",
            "--total-timesteps", "8",
            "--seed", "7",
            "--model-path", str(tmp_path / "cli_model.zip"),
        ]
    )

    assert calls["data_request"] == (["AAA"], "2024-01-01", "2024-04-10")
    assert len(calls["training"]) == 80
    assert len(calls["validation"]) == 20
    assert calls["training"].index.max() < calls["validation"].index.min()
    assert calls["timesteps"] == 8
    assert calls["agent_config"]["load_existing_model"] is False
    assert calls["agent_config"]["model_path"] == str(tmp_path / "cli_model.zip")
    assert calls["metadata"]["data_source"] == "synthetic_test_data"
    assert calls["metadata"]["git_commit"] == "b" * 40
    assert calls["metadata"]["git_worktree_clean"] is True
    assert calls["metadata"]["dataset_sha256"] == train.training_dataset_sha256(history)

    output = capsys.readouterr().out
    assert "PPO training and chronological holdout evaluation completed." in output
    assert "not a guarantee of future performance" in output
