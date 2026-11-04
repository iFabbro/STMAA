"""Persisted broker state must include every safety-critical order map."""
import json

import pytest

from src.execution.broker_executor import BrokerExecutor


@pytest.fixture
def isolated_state_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "test-secret")
    monkeypatch.setenv(
        "STMAA_BROKER_STATE_FILE",
        str(tmp_path / "state" / "broker_state.json"),
    )


@pytest.mark.parametrize(
    "missing_key",
    [
        "emergency_liquidation_pending",
        "emergency_liquidation_unresolved",
        "order_submission_unresolved",
        "order_submission_confirmed",
        "opg_batches",
    ],
)
def test_restart_fails_closed_if_safety_map_is_missing(
    isolated_state_env, missing_key
):
    first = BrokerExecutor()
    persisted = json.loads(first.state_file.read_text(encoding="utf-8"))
    persisted.pop(missing_key)
    first.state_file.write_text(json.dumps(persisted), encoding="utf-8")
    original = first.state_file.read_text(encoding="utf-8")

    with pytest.raises(
        RuntimeError,
        match="Unable to load persisted broker state",
    ):
        BrokerExecutor()

    # A failed startup must not rewrite or replace the only durable state copy.
    assert first.state_file.read_text(encoding="utf-8") == original


@pytest.mark.parametrize("schema_version", [None, 0, 3, "2"])
def test_restart_fails_closed_on_missing_or_unsupported_state_schema(
    isolated_state_env, schema_version
):
    first = BrokerExecutor()
    persisted = json.loads(first.state_file.read_text(encoding="utf-8"))
    if schema_version is None:
        persisted.pop("state_schema_version")
    else:
        persisted["state_schema_version"] = schema_version
    first.state_file.write_text(json.dumps(persisted), encoding="utf-8")
    original = first.state_file.read_text(encoding="utf-8")

    with pytest.raises(
        RuntimeError,
        match="Unable to load persisted broker state",
    ):
        BrokerExecutor()

    assert first.state_file.read_text(encoding="utf-8") == original


def test_new_state_initializes_schema_and_all_safety_maps(isolated_state_env):
    executor = BrokerExecutor()

    persisted = json.loads(executor.state_file.read_text(encoding="utf-8"))
    assert persisted["state_schema_version"] == 2
    for key in (
        "emergency_liquidation_pending",
        "emergency_liquidation_unresolved",
        "order_submission_unresolved",
        "order_submission_confirmed",
        "opg_batches",
    ):
        assert persisted[key] == {}


def test_restart_fails_closed_if_opg_batch_entry_is_not_an_object(isolated_state_env):
    first = BrokerExecutor()
    persisted = json.loads(first.state_file.read_text(encoding="utf-8"))
    persisted["opg_batches"] = {"batch-bad": "not-an-object"}
    first.state_file.write_text(json.dumps(persisted), encoding="utf-8")
    original = first.state_file.read_text(encoding="utf-8")

    with pytest.raises(RuntimeError, match="Unable to load persisted broker state"):
        BrokerExecutor()

    assert first.state_file.read_text(encoding="utf-8") == original
