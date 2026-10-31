import multiprocessing

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


def test_model_load_rejects_missing_artifact_digest_before_deserialization(tmp_path, monkeypatch):
    import json
    from src.agents import ppo_agent

    model_path = tmp_path / "missing-digest.zip"
    model_path.write_bytes(b"model-bytes")
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
    (tmp_path / "missing-digest.zip.metadata.json").write_text(json.dumps(metadata))
    monkeypatch.setattr(
        ppo_agent.PPO,
        "load",
        lambda *_args, **_kwargs: pytest.fail("must not deserialize"),
    )
    with pytest.raises(ValueError, match="no valid artifact SHA-256"):
        PPOAgent(tickers=["A"], model_path=str(model_path))


def test_model_load_rejects_artifact_digest_mismatch_before_deserialization(tmp_path, monkeypatch):
    import json
    from src.agents import ppo_agent

    model_path = tmp_path / "tampered.zip"
    model_path.write_bytes(b"model-bytes")
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
        "model_sha256": "0" * 64,
    }
    (tmp_path / "tampered.zip.metadata.json").write_text(json.dumps(metadata))
    monkeypatch.setattr(ppo_agent.PPO, "load", lambda *_args, **_kwargs: pytest.fail("must not deserialize"))
    with pytest.raises(ValueError, match="artifact SHA-256 mismatch"):
        PPOAgent(tickers=["A"], model_path=str(model_path))


def test_model_save_publishes_matching_artifact_digest_and_metadata(tmp_path, monkeypatch):
    import hashlib
    import json

    model_path = tmp_path / "saved.zip"
    agent = PPOAgent(tickers=["A"], model_path=str(model_path), load_existing_model=False)

    class FakeModel:
        def save(self, path):
            Path(path).write_bytes(b"verified-model-artifact")

    from pathlib import Path
    agent.model = FakeModel()
    agent.save(training_metadata={"seed": 42})

    metadata = json.loads((tmp_path / "saved.zip.metadata.json").read_text())
    artifact_path = tmp_path / metadata["artifact_file"]
    assert metadata["model_sha256"] == hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    assert metadata["training"] == {"seed": 42}
    assert not model_path.exists()
    assert not list(tmp_path.glob(".*.tmp"))
    assert not list(tmp_path.glob(".*.zip"))


def test_model_save_failure_preserves_existing_published_artifacts(tmp_path):
    import json
    from pathlib import Path

    model_path = tmp_path / "existing.zip"
    metadata_path = tmp_path / "existing.zip.metadata.json"
    model_path.write_bytes(b"previous-model")
    metadata_path.write_text('{"previous": true}')
    agent = PPOAgent(tickers=["A"], model_path=str(model_path), load_existing_model=False)

    class FailingModel:
        def save(self, path):
            Path(path).write_bytes(b"partial-model")
            raise OSError("simulated model serialization failure")

    agent.model = FailingModel()
    with pytest.raises(OSError, match="simulated"):
        agent.save()
    assert model_path.read_bytes() == b"previous-model"
    assert json.loads(metadata_path.read_text()) == {"previous": True}
    assert not list(tmp_path.glob(".*.tmp"))
    assert not list(tmp_path.glob(".*.zip"))



def test_model_save_interruption_before_manifest_replace_preserves_previous_generation(
    tmp_path, monkeypatch
):
    import json
    from pathlib import Path
    from src.agents import ppo_agent

    model_path = tmp_path / "versioned.zip"
    first = PPOAgent(tickers=["A"], model_path=str(model_path), load_existing_model=False)

    class FirstModel:
        def save(self, path):
            Path(path).write_bytes(b"first-model-generation")

    first.model = FirstModel()
    first.save()
    manifest_path = tmp_path / "versioned.zip.metadata.json"
    previous_metadata = json.loads(manifest_path.read_text())
    previous_artifact = tmp_path / previous_metadata["artifact_file"]
    assert previous_artifact.is_file()

    second = PPOAgent(tickers=["A"], model_path=str(model_path), load_existing_model=False)

    class SecondModel:
        def save(self, path):
            Path(path).write_bytes(b"second-model-generation")

    second.model = SecondModel()
    real_replace = ppo_agent.os.replace

    def fail_manifest_replace(source, destination):
        if Path(destination) == manifest_path:
            raise OSError("simulated interruption before manifest publication")
        return real_replace(source, destination)

    monkeypatch.setattr(ppo_agent.os, "replace", fail_manifest_replace)
    with pytest.raises(OSError, match="simulated interruption"):
        second.save()

    # The active manifest remains unchanged and still resolves to the old,
    # checksum-valid immutable generation; the new generation is only an orphan.
    assert json.loads(manifest_path.read_text()) == previous_metadata
    assert previous_artifact.read_bytes() == b"first-model-generation"
    loaded = PPOAgent(tickers=["A"], model_path=str(model_path), load_existing_model=False)
    captured = {}

    class LoadedModel:
        pass

    monkeypatch.setattr(
        ppo_agent.PPO,
        "load",
        lambda path: captured.setdefault("path", path) or LoadedModel(),
    )
    loaded.load()
    assert Path(captured["path"]) == previous_artifact


def test_model_load_rejects_artifact_filename_path_traversal(tmp_path):
    import json

    model_path = tmp_path / "unsafe.zip"
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
        "model_sha256": "0" * 64,
        "artifact_file": "../outside.zip",
    }
    (tmp_path / "unsafe.zip.metadata.json").write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match="invalid artifact filename"):
        PPOAgent(tickers=["A"], model_path=str(model_path))


def test_model_load_rejects_non_content_addressed_artifact_filename(tmp_path):
    import hashlib
    import json

    model_path = tmp_path / "named.zip"
    payload = b"valid model bytes"
    digest = hashlib.sha256(payload).hexdigest()
    (tmp_path / "arbitrary.zip").write_bytes(payload)
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
        "model_sha256": digest,
        "artifact_file": "arbitrary.zip",
    }
    (tmp_path / "named.zip.metadata.json").write_text(json.dumps(metadata))

    with pytest.raises(ValueError, match="filename does not match its SHA-256"):
        PPOAgent(tickers=["A"], model_path=str(model_path))


def test_model_load_rejects_symlinked_content_addressed_artifact(tmp_path):
    import hashlib
    import json

    model_path = tmp_path / "linked.zip"
    external_artifact = tmp_path / "external-model.zip"
    payload = b"valid model bytes"
    external_artifact.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    artifact_name = f"linked.{digest}.zip"
    (tmp_path / artifact_name).symlink_to(external_artifact)
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
        "model_sha256": digest,
        "artifact_file": artifact_name,
    }
    (tmp_path / "linked.zip.metadata.json").write_text(json.dumps(metadata))

    with pytest.raises(ValueError, match="must not be a symbolic link"):
        PPOAgent(tickers=["A"], model_path=str(model_path))


def test_model_restart_loads_manifest_generation(tmp_path, monkeypatch):
    from pathlib import Path
    from src.agents import ppo_agent

    model_path = tmp_path / "restart.zip"
    writer = PPOAgent(
        tickers=["A"], model_path=str(model_path), load_existing_model=False
    )

    class SavedModel:
        def save(self, path):
            Path(path).write_bytes(b"restartable-model-generation")

    writer.model = SavedModel()
    writer.save()

    loaded_model = object()
    monkeypatch.setattr(ppo_agent.PPO, "load", lambda _path: loaded_model)
    restarted = PPOAgent(tickers=["A"], model_path=str(model_path))
    assert restarted.model is loaded_model


def test_model_save_cleans_first_temp_file_if_second_temp_creation_fails(
    tmp_path, monkeypatch
):
    from src.agents import ppo_agent

    model_path = tmp_path / "staging-error.zip"
    agent = PPOAgent(
        tickers=["A"], model_path=str(model_path), load_existing_model=False
    )
    agent.model = object()
    real_mkstemp = ppo_agent.tempfile.mkstemp
    calls = 0

    def fail_second_mkstemp(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated metadata staging failure")
        return real_mkstemp(*args, **kwargs)

    monkeypatch.setattr(ppo_agent.tempfile, "mkstemp", fail_second_mkstemp)
    with pytest.raises(OSError, match="metadata staging failure"):
        agent.save()

    assert not list(tmp_path.glob(".*.zip"))
    assert not list(tmp_path.glob(".*.tmp"))



def test_ppo_artifact_retention_keeps_active_and_requested_generations(tmp_path):
    import json
    from pathlib import Path

    model_path = tmp_path / "retained.zip"
    agent = PPOAgent(tickers=["A"], model_path=str(model_path), load_existing_model=False)

    class SavedModel:
        def __init__(self, content):
            self.content = content

        def save(self, path):
            Path(path).write_bytes(self.content)

    for payload in (b"generation-one", b"generation-two", b"generation-three"):
        agent.model = SavedModel(payload)
        agent.save()

    manifest_path = tmp_path / "retained.zip.metadata.json"
    active = tmp_path / json.loads(manifest_path.read_text())["artifact_file"]
    preview = agent.prune_artifacts(
        retain_generations=2, dry_run=True, min_generation_age_seconds=0
    )
    assert len(preview) == 1
    assert active.exists()
    assert Path(preview[0]).exists()

    removed = agent.prune_artifacts(
        retain_generations=2, dry_run=False, min_generation_age_seconds=0
    )
    assert removed == preview
    assert active.exists()
    assert not Path(removed[0]).exists()
    assert len(list(tmp_path.glob("retained.*.zip"))) == 2


def test_ppo_artifact_pruning_removes_only_stale_staging_files(tmp_path):
    import os
    import time
    from pathlib import Path

    model_path = tmp_path / "staging.zip"
    agent = PPOAgent(tickers=["A"], model_path=str(model_path), load_existing_model=False)

    class SavedModel:
        def save(self, path):
            Path(path).write_bytes(b"active-generation")

    agent.model = SavedModel()
    agent.save()
    stale_model_temp = tmp_path / ".staging.zip.abandoned.zip"
    stale_metadata_temp = tmp_path / ".staging.zip.metadata.json.abandoned.tmp"
    recent_temp = tmp_path / ".staging.zip.recent.zip"
    for candidate in (stale_model_temp, stale_metadata_temp, recent_temp):
        candidate.write_bytes(b"temporary")
    old = time.time() - 7200
    os.utime(stale_model_temp, (old, old))
    os.utime(stale_metadata_temp, (old, old))

    preview = agent.prune_artifacts(min_temp_age_seconds=3600, dry_run=True)
    assert set(preview) == {str(stale_model_temp), str(stale_metadata_temp)}
    assert stale_model_temp.exists() and stale_metadata_temp.exists()

    removed = agent.prune_artifacts(min_temp_age_seconds=3600, dry_run=False)
    assert set(removed) == set(preview)
    assert not stale_model_temp.exists()
    assert not stale_metadata_temp.exists()
    assert recent_temp.exists()


def test_ppo_artifact_pruning_fails_closed_on_invalid_active_manifest(tmp_path):
    from pathlib import Path

    model_path = tmp_path / "invalid-manifest.zip"
    agent = PPOAgent(tickers=["A"], model_path=str(model_path), load_existing_model=False)

    class SavedModel:
        def save(self, path):
            Path(path).write_bytes(b"active-generation")

    agent.model = SavedModel()
    agent.save()
    orphan = tmp_path / ("invalid-manifest." + "a" * 64 + ".zip")
    orphan.write_bytes(b"orphan")
    (tmp_path / "invalid-manifest.zip.metadata.json").write_text("{broken")

    with pytest.raises(RuntimeError, match="active manifest is unreadable"):
        agent.prune_artifacts(dry_run=False)
    assert orphan.exists()


def test_ppo_artifact_operations_fail_closed_when_lock_cannot_be_acquired(
    tmp_path, monkeypatch
):
    from src.agents import ppo_agent

    agent = PPOAgent(
        tickers=["A"], model_path=str(tmp_path / "locked.zip"), load_existing_model=False
    )

    def fail_lock(*_args, **_kwargs):
        raise OSError("simulated flock failure")

    monkeypatch.setattr(ppo_agent.fcntl, "flock", fail_lock)
    with pytest.raises(RuntimeError, match="Unable to acquire PPO artifact lock"):
        agent.prune_artifacts()



def _artifact_lock_process_worker(model_path, timeout_seconds, result_queue):
    from pathlib import Path
    from src.agents.ppo_agent import _artifact_lock

    try:
        with _artifact_lock(Path(model_path), timeout_seconds=timeout_seconds):
            result_queue.put("acquired")
    except RuntimeError as exc:
        result_queue.put(str(exc))


def test_ppo_artifact_lock_serializes_independent_processes_and_releases(tmp_path):
    from src.agents.ppo_agent import _artifact_lock

    model_path = tmp_path / "concurrent.zip"
    context = multiprocessing.get_context("fork")
    result_queue = context.Queue()

    # A second process must time out while the first process owns the lock.
    with _artifact_lock(model_path):
        blocked = context.Process(
            target=_artifact_lock_process_worker,
            args=(str(model_path), 0.2, result_queue),
        )
        blocked.start()
        blocked.join(timeout=5)
        if blocked.is_alive():
            blocked.terminate()
            blocked.join(timeout=2)
            pytest.fail("child process did not finish while testing lock contention")
        assert blocked.exitcode == 0
        assert result_queue.get(timeout=1) == (
            "Timed out acquiring PPO artifact lock; refusing artifact operation"
        )

    # Once the owner exits, a fresh process must be able to acquire the same lock.
    acquired = context.Process(
        target=_artifact_lock_process_worker,
        args=(str(model_path), 1.0, result_queue),
    )
    acquired.start()
    acquired.join(timeout=5)
    if acquired.is_alive():
        acquired.terminate()
        acquired.join(timeout=2)
        pytest.fail("child process did not finish after the lock owner released it")
    assert acquired.exitcode == 0
    assert result_queue.get(timeout=1) == "acquired"
    result_queue.close()
    result_queue.join_thread()



@pytest.mark.parametrize("operation", ["save", "load", "prune_artifacts"])
def test_all_public_ppo_artifact_operations_fail_closed_without_lock(
    tmp_path, monkeypatch, operation
):
    from pathlib import Path
    from src.agents import ppo_agent

    model_path = tmp_path / f"{operation}.zip"
    agent = PPOAgent(
        tickers=["A"], model_path=str(model_path), load_existing_model=False
    )
    if operation == "save":
        class SavedModel:
            def save(self, path):
                Path(path).write_bytes(b"model")

        agent.model = SavedModel()

    def fail_lock(*_args, **_kwargs):
        raise OSError("simulated flock failure")

    monkeypatch.setattr(ppo_agent.fcntl, "flock", fail_lock)
    with pytest.raises(RuntimeError, match="Unable to acquire PPO artifact lock"):
        getattr(agent, operation)()


def test_ppo_artifact_lock_rejects_symlinked_lock_file(tmp_path):
    from src.agents.ppo_agent import _artifact_lock

    model_path = tmp_path / "symlinked-lock.zip"
    lock_path = tmp_path / ".symlinked-lock.zip.artifact.lock"
    external_target = tmp_path / "external-lock-target"
    external_target.write_text("must remain untouched")
    lock_path.symlink_to(external_target)

    with pytest.raises(RuntimeError, match="Unable to open PPO artifact lock"):
        with _artifact_lock(model_path):
            pytest.fail("symlinked lock file must never be followed")

    assert lock_path.is_symlink()
    assert external_target.read_text() == "must remain untouched"



def test_ppo_load_rejects_symlinked_metadata_manifest(tmp_path, monkeypatch):
    from pathlib import Path
    from src.agents import ppo_agent

    model_path = tmp_path / "manifest-link.zip"
    agent = PPOAgent(
        tickers=["A"], model_path=str(model_path), load_existing_model=False
    )

    class SavedModel:
        def save(self, path):
            Path(path).write_bytes(b"manifest-link-model")

    agent.model = SavedModel()
    agent.save()
    manifest_path = Path(str(model_path) + ".metadata.json")
    external_manifest = tmp_path / "external-metadata.json"
    external_manifest.write_bytes(manifest_path.read_bytes())
    manifest_path.unlink()
    manifest_path.symlink_to(external_manifest)

    monkeypatch.setattr(
        ppo_agent.PPO, "load",
        lambda _path: pytest.fail("PPO.load must not run for a symlinked manifest"),
    )
    with pytest.raises((OSError, ValueError)):
        PPOAgent(tickers=["A"], model_path=str(model_path))


def test_ppo_prune_rejects_symlinked_manifest_without_deleting_artifacts(tmp_path):
    from pathlib import Path

    model_path = tmp_path / "prune-manifest-link.zip"
    agent = PPOAgent(
        tickers=["A"], model_path=str(model_path), load_existing_model=False
    )

    class SavedModel:
        def save(self, path):
            Path(path).write_bytes(b"prune-manifest-link-model")

    agent.model = SavedModel()
    agent.save()
    manifest_path = Path(str(model_path) + ".metadata.json")
    external_manifest = tmp_path / "external-prune-metadata.json"
    external_manifest.write_bytes(manifest_path.read_bytes())
    manifest_path.unlink()
    manifest_path.symlink_to(external_manifest)

    with pytest.raises(RuntimeError, match="active manifest is unreadable"):
        agent.prune_artifacts(dry_run=False)

    assert manifest_path.is_symlink()
    assert external_manifest.exists()
    assert list(tmp_path.glob("prune-manifest-link.*.zip"))


def test_ppo_prune_rejects_symlinked_active_generation(tmp_path):
    import json
    from pathlib import Path

    model_path = tmp_path / "prune-artifact-link.zip"
    agent = PPOAgent(
        tickers=["A"], model_path=str(model_path), load_existing_model=False
    )

    class SavedModel:
        def save(self, path):
            Path(path).write_bytes(b"prune-artifact-link-model")

    agent.model = SavedModel()
    agent.save()
    manifest_path = Path(str(model_path) + ".metadata.json")
    manifest = json.loads(manifest_path.read_text())
    active_path = tmp_path / manifest["artifact_file"]
    external_artifact = tmp_path / "external-active-model.zip"
    external_artifact.write_bytes(active_path.read_bytes())
    active_path.unlink()
    active_path.symlink_to(external_artifact)

    with pytest.raises(RuntimeError, match="active generation is missing or corrupt"):
        agent.prune_artifacts(dry_run=False)

    assert active_path.is_symlink()
    assert external_artifact.exists()



@pytest.mark.parametrize(
    "failure_point",
    ["artifact_replace", "artifact_directory_fsync", "manifest_replace"],
)
def test_ppo_save_publication_failures_before_manifest_replace_preserve_previous_generation(
    tmp_path, monkeypatch, failure_point
):
    import json
    from pathlib import Path
    from src.agents import ppo_agent

    model_path = tmp_path / "crash-recovery.zip"
    manifest_path = Path(str(model_path) + ".metadata.json")

    class SavedModel:
        def __init__(self, payload):
            self.payload = payload

        def save(self, path):
            Path(path).write_bytes(self.payload)

    writer = PPOAgent(
        tickers=["A"], model_path=str(model_path), load_existing_model=False
    )
    writer.model = SavedModel(b"previous-generation")
    writer.save()
    previous_manifest = json.loads(manifest_path.read_text())
    previous_artifact = tmp_path / previous_manifest["artifact_file"]

    writer.model = SavedModel(b"candidate-generation")
    real_replace = ppo_agent.os.replace
    if failure_point in ("artifact_replace", "manifest_replace"):
        def fail_selected_replace(source, destination):
            destination = Path(destination)
            if failure_point == "artifact_replace" and destination != manifest_path:
                raise OSError("simulated artifact publication interruption")
            if failure_point == "manifest_replace" and destination == manifest_path:
                raise OSError("simulated manifest publication interruption")
            return real_replace(source, destination)

        monkeypatch.setattr(ppo_agent.os, "replace", fail_selected_replace)
    else:
        def fail_artifact_directory_fsync(path):
            raise OSError("simulated artifact directory fsync interruption")

        monkeypatch.setattr(
            ppo_agent, "_fsync_directory", fail_artifact_directory_fsync
        )

    expected_message = {
        "artifact_replace": "artifact publication interruption",
        "artifact_directory_fsync": "artifact directory fsync interruption",
        "manifest_replace": "manifest publication interruption",
    }[failure_point]
    with pytest.raises(OSError, match=expected_message):
        writer.save()

    assert json.loads(manifest_path.read_text()) == previous_manifest
    assert previous_artifact.read_bytes() == b"previous-generation"

    captured = {}
    monkeypatch.setattr(
        ppo_agent.PPO, "load",
        lambda path: captured.setdefault("path", path) or object(),
    )
    restarted = PPOAgent(
        tickers=["A"], model_path=str(model_path), load_existing_model=False
    )
    restarted.load()
    assert Path(captured["path"]) == previous_artifact


def test_ppo_save_post_manifest_fsync_failure_leaves_new_generation_loadable(
    tmp_path, monkeypatch
):
    import hashlib
    import json
    from pathlib import Path
    from src.agents import ppo_agent

    model_path = tmp_path / "post-commit-fsync.zip"
    manifest_path = Path(str(model_path) + ".metadata.json")

    class SavedModel:
        def __init__(self, payload):
            self.payload = payload

        def save(self, path):
            Path(path).write_bytes(self.payload)

    writer = PPOAgent(
        tickers=["A"], model_path=str(model_path), load_existing_model=False
    )
    writer.model = SavedModel(b"previous-generation")
    writer.save()
    previous_manifest = json.loads(manifest_path.read_text())

    writer.model = SavedModel(b"committed-generation")
    real_fsync_directory = ppo_agent._fsync_directory
    calls = 0

    def fail_after_manifest_replace(path):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated post-manifest directory fsync failure")
        return real_fsync_directory(path)

    monkeypatch.setattr(
        ppo_agent, "_fsync_directory", fail_after_manifest_replace
    )
    with pytest.raises(OSError, match="post-manifest directory fsync failure"):
        writer.save()

    current_manifest = json.loads(manifest_path.read_text())
    assert current_manifest != previous_manifest
    active_artifact = tmp_path / current_manifest["artifact_file"]
    assert active_artifact.read_bytes() == b"committed-generation"
    assert current_manifest["model_sha256"] == hashlib.sha256(
        active_artifact.read_bytes()
    ).hexdigest()

    captured = {}
    monkeypatch.setattr(
        ppo_agent.PPO, "load",
        lambda path: captured.setdefault("path", path) or object(),
    )
    restarted = PPOAgent(
        tickers=["A"], model_path=str(model_path), load_existing_model=False
    )
    restarted.load()
    assert Path(captured["path"]) == active_artifact



def test_ppo_prune_preserves_recent_orphan_generation_until_recovery_grace_expires(
    tmp_path,
):
    import hashlib
    import os
    import time
    from pathlib import Path

    model_path = tmp_path / "orphan-grace.zip"
    agent = PPOAgent(
        tickers=["A"], model_path=str(model_path), load_existing_model=False
    )

    class SavedModel:
        def save(self, path):
            Path(path).write_bytes(b"active-generation")

    agent.model = SavedModel()
    agent.save()

    orphan_payload = b"published-before-manifest-crash"
    orphan_digest = hashlib.sha256(orphan_payload).hexdigest()
    orphan_path = tmp_path / f"orphan-grace.{orphan_digest}.zip"
    orphan_path.write_bytes(orphan_payload)

    # retain_generations=1 must not override the recovery grace period.
    removed = agent.prune_artifacts(
        retain_generations=1,
        dry_run=False,
        min_generation_age_seconds=3600,
    )
    assert str(orphan_path) not in removed
    assert orphan_path.read_bytes() == orphan_payload

    old = time.time() - 7200
    os.utime(orphan_path, (old, old))
    removed = agent.prune_artifacts(
        retain_generations=1,
        dry_run=False,
        min_generation_age_seconds=3600,
    )
    assert str(orphan_path) in removed
    assert not orphan_path.exists()


def _ppo_save_with_blocking_model_process(model_path, started, release, result_queue):
    from pathlib import Path

    agent = PPOAgent(
        tickers=["A"], model_path=model_path, load_existing_model=False
    )

    class BlockingModel:
        def save(self, path):
            started.set()
            if not release.wait(timeout=10):
                raise RuntimeError("timed out waiting to publish test generation")
            Path(path).write_bytes(b"concurrent-generation")

    agent.model = BlockingModel()
    try:
        agent.save()
        result_queue.put("save:success")
    except Exception as exc:
        result_queue.put(f"save:error:{exc}")


def _ppo_prune_process(model_path, started, result_queue):
    agent = PPOAgent(
        tickers=["A"], model_path=model_path, load_existing_model=False
    )
    started.set()
    try:
        removed = agent.prune_artifacts(
            retain_generations=1,
            dry_run=False,
            min_generation_age_seconds=0,
        )
        result_queue.put(("prune:success", removed))
    except Exception as exc:
        result_queue.put(("prune:error", str(exc)))


def test_ppo_save_and_prune_are_serialized_across_processes(tmp_path):
    from pathlib import Path
    import json

    model_path = tmp_path / "save-prune-race.zip"
    initial = PPOAgent(
        tickers=["A"], model_path=str(model_path), load_existing_model=False
    )

    class InitialModel:
        def save(self, path):
            Path(path).write_bytes(b"initial-generation")

    initial.model = InitialModel()
    initial.save()
    manifest_path = Path(str(model_path) + ".metadata.json")
    initial_manifest = json.loads(manifest_path.read_text())
    initial_artifact = tmp_path / initial_manifest["artifact_file"]

    context = multiprocessing.get_context("fork")
    started = context.Event()
    release = context.Event()
    prune_started = context.Event()
    result_queue = context.Queue()
    saver = context.Process(
        target=_ppo_save_with_blocking_model_process,
        args=(str(model_path), started, release, result_queue),
    )
    pruner = None
    saver.start()
    try:
        assert started.wait(timeout=5), "save process did not enter model publication"
        pruner = context.Process(
            target=_ppo_prune_process,
            args=(str(model_path), prune_started, result_queue),
        )
        pruner.start()
        assert prune_started.wait(timeout=5), "prune process did not start"
        pruner.join(timeout=0.25)
        assert pruner.is_alive(), "prune must wait while save owns the artifact lock"
    finally:
        release.set()

    saver.join(timeout=10)
    assert not saver.is_alive(), "save process did not finish"
    assert saver.exitcode == 0
    assert pruner is not None
    pruner.join(timeout=10)
    assert not pruner.is_alive(), "prune process did not finish after save released lock"
    assert pruner.exitcode == 0

    outcomes = [result_queue.get(timeout=2), result_queue.get(timeout=2)]
    assert "save:success" in outcomes
    prune_result = next(item for item in outcomes if isinstance(item, tuple))
    assert prune_result[0] == "prune:success"

    final_manifest = json.loads(manifest_path.read_text())
    final_artifact = tmp_path / final_manifest["artifact_file"]
    assert final_artifact.read_bytes() == b"concurrent-generation"
    assert final_artifact.exists()
    assert not initial_artifact.exists()
    result_queue.close()
    result_queue.join_thread()


@pytest.mark.parametrize("manifest_payload", ["null", "[]", '"not-an-object"'])
def test_ppo_prune_rejects_non_object_manifest_without_deleting_artifacts(
    tmp_path, manifest_payload
):
    import hashlib
    from pathlib import Path

    model_path = tmp_path / "non-object-manifest.zip"
    agent = PPOAgent(
        tickers=["A"], model_path=str(model_path), load_existing_model=False
    )

    class SavedModel:
        def save(self, path):
            Path(path).write_bytes(b"active-generation")

    agent.model = SavedModel()
    agent.save()

    orphan_payload = b"orphan-generation"
    orphan_digest = hashlib.sha256(orphan_payload).hexdigest()
    orphan_path = tmp_path / f"non-object-manifest.{orphan_digest}.zip"
    orphan_path.write_bytes(orphan_payload)
    manifest_path = Path(str(model_path) + ".metadata.json")
    manifest_path.write_text(manifest_payload)

    with pytest.raises(RuntimeError, match="active manifest is invalid"):
        agent.prune_artifacts(dry_run=False, min_generation_age_seconds=0)

    assert orphan_path.read_bytes() == orphan_payload
    assert manifest_path.read_text() == manifest_payload


def test_ppo_prune_preserves_future_dated_generations_and_staging_files(tmp_path):
    """Clock skew must never make a future-dated artifact eligible for deletion."""
    import hashlib
    import json
    import os
    import time
    from pathlib import Path

    model_path = tmp_path / "clock-skew.zip"
    agent = PPOAgent(
        tickers=["A"], model_path=str(model_path), load_existing_model=False
    )

    class SavedModel:
        def save(self, path):
            Path(path).write_bytes(b"active-generation")

    agent.model = SavedModel()
    agent.save()

    future = time.time() + 24 * 60 * 60
    orphan_payload = b"future-dated-orphan-generation"
    orphan_digest = hashlib.sha256(orphan_payload).hexdigest()
    orphan_path = tmp_path / f"clock-skew.{orphan_digest}.zip"
    orphan_path.write_bytes(orphan_payload)
    os.utime(orphan_path, (future, future))

    staging_path = tmp_path / ".clock-skew.zip.future-staging.zip"
    staging_path.write_bytes(b"future-dated-staging")
    os.utime(staging_path, (future, future))

    manifest_path = Path(str(model_path) + ".metadata.json")
    active_path = tmp_path / json.loads(manifest_path.read_text())["artifact_file"]

    removed = agent.prune_artifacts(
        retain_generations=1,
        dry_run=False,
        min_temp_age_seconds=3600,
        min_generation_age_seconds=3600,
    )

    assert str(orphan_path) not in removed
    assert str(staging_path) not in removed
    assert orphan_path.read_bytes() == orphan_payload
    assert staging_path.read_bytes() == b"future-dated-staging"
    assert active_path.is_file()


@pytest.mark.parametrize(
    "manifest_mutation",
    [
        "missing_contract_version",
        "wrong_contract_version",
        "missing_tickers",
        "wrong_state_dim",
        "missing_action_contract",
        "invalid_market_data_contract",
        "wrong_cost_parameter",
    ],
)
def test_ppo_prune_rejects_incompatible_manifest_without_deleting_recovery_generation(
    tmp_path, manifest_mutation
):
    import json
    import os
    import time
    from pathlib import Path

    model_path = tmp_path / "incompatible-manifest.zip"
    agent = PPOAgent(
        tickers=["A"], model_path=str(model_path), load_existing_model=False
    )

    class SavedModel:
        def save(self, path):
            Path(path).write_bytes(b"active-generation")

    agent.model = SavedModel()
    agent.save()

    manifest_path = Path(str(model_path) + ".metadata.json")
    manifest = json.loads(manifest_path.read_text())
    if manifest_mutation == "missing_contract_version":
        manifest.pop("contract_version")
    elif manifest_mutation == "wrong_contract_version":
        manifest["contract_version"] = 999
    elif manifest_mutation == "missing_tickers":
        manifest.pop("tickers")
    elif manifest_mutation == "wrong_state_dim":
        manifest["state_dim"] = 999
    elif manifest_mutation == "missing_action_contract":
        manifest.pop("action_contract")
    elif manifest_mutation == "invalid_market_data_contract":
        manifest["market_data_contract"] = {}
    elif manifest_mutation == "wrong_cost_parameter":
        manifest["transaction_cost"] = 0.25
    manifest_path.write_text(json.dumps(manifest))

    recovery_payload = b"previous-recoverable-generation"
    import hashlib

    recovery_digest = hashlib.sha256(recovery_payload).hexdigest()
    recovery_path = tmp_path / f"incompatible-manifest.{recovery_digest}.zip"
    recovery_path.write_bytes(recovery_payload)
    old = time.time() - 7200
    os.utime(recovery_path, (old, old))

    with pytest.raises(RuntimeError, match="active manifest is invalid"):
        agent.prune_artifacts(
            retain_generations=1,
            dry_run=False,
            min_generation_age_seconds=0,
        )

    assert recovery_path.read_bytes() == recovery_payload
    assert manifest_path.exists()


def _create_old_ppo_prune_generations(tmp_path, model_path, agent):
    import hashlib
    import json
    import os
    import time
    from pathlib import Path

    class SavedModel:
        def save(self, path):
            Path(path).write_bytes(b"active-generation")

    agent.model = SavedModel()
    agent.save()
    manifest = json.loads(Path(str(model_path) + ".metadata.json").read_text())
    active_path = tmp_path / manifest["artifact_file"]

    old_generations = []
    for index in range(2):
        payload = f"old-orphan-generation-{index}".encode()
        digest = hashlib.sha256(payload).hexdigest()
        artifact = tmp_path / f"{model_path.stem}.{digest}{model_path.suffix}"
        artifact.write_bytes(payload)
        old = time.time() - 7200 - index
        os.utime(artifact, (old, old))
        old_generations.append(artifact)
    return active_path, old_generations


def test_ppo_prune_fsyncs_partial_deletions_before_reporting_unlink_error(
    tmp_path, monkeypatch
):
    from pathlib import Path
    from src.agents import ppo_agent

    model_path = tmp_path / "partial-unlink.zip"
    agent = PPOAgent(
        tickers=["A"], model_path=str(model_path), load_existing_model=False
    )
    active_path, generations = _create_old_ppo_prune_generations(
        tmp_path, model_path, agent
    )
    generations.sort(key=lambda path: path.stat().st_mtime_ns, reverse=True)
    real_unlink = Path.unlink
    unlink_calls = []
    fsync_calls = []

    def fail_second_unlink(path, *args, **kwargs):
        if path in generations:
            unlink_calls.append(path)
            if len(unlink_calls) == 2:
                raise OSError("simulated disk unlink failure")
        return real_unlink(path, *args, **kwargs)

    def record_fsync(path):
        fsync_calls.append(path)

    monkeypatch.setattr(Path, "unlink", fail_second_unlink)
    monkeypatch.setattr(ppo_agent, "_fsync_directory", record_fsync)

    with pytest.raises(RuntimeError, match="partial cleanup"):
        agent.prune_artifacts(
            retain_generations=1,
            dry_run=False,
            min_generation_age_seconds=0,
        )

    assert len(unlink_calls) == 2
    assert not generations[0].exists()
    assert generations[1].exists()
    assert active_path.read_bytes() == b"active-generation"
    assert fsync_calls == [tmp_path]


def test_ppo_prune_reports_directory_fsync_failure_after_cleanup(tmp_path, monkeypatch):
    from src.agents import ppo_agent

    model_path = tmp_path / "fsync-failure.zip"
    agent = PPOAgent(
        tickers=["A"], model_path=str(model_path), load_existing_model=False
    )
    active_path, generations = _create_old_ppo_prune_generations(
        tmp_path, model_path, agent
    )

    def fail_fsync(_path):
        raise OSError("simulated directory fsync failure")

    monkeypatch.setattr(ppo_agent, "_fsync_directory", fail_fsync)

    with pytest.raises(RuntimeError, match="directory fsync failed"):
        agent.prune_artifacts(
            retain_generations=1,
            dry_run=False,
            min_generation_age_seconds=0,
        )

    assert all(not path.exists() for path in generations)
    assert active_path.read_bytes() == b"active-generation"
