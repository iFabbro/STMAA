"""Test-wide isolation for durable broker state.

Production defaults to a repository-anchored path; tests use a per-test file so
restarts are reproducible and tests never share broker state.
"""
import pytest


@pytest.fixture(autouse=True)
def isolate_broker_state_file(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "STMAA_BROKER_STATE_FILE",
        str(tmp_path / "logs" / "execution" / "broker_state.json"),
    )
