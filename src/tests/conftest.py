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
    # The test suite's synthetic broker scenarios share a known test universe.
    # Production still requires an explicitly configured universe and remains
    # fail-closed when it is absent.
    monkeypatch.setenv(
        "STMAA_CONFIGURED_SYMBOLS",
        "AAPL,GOOGL,MSFT,SPY,QQQ,TSLA,NVDA,AMZN,META,NFLX,IBM,BTCUSD,XYZ,BRK.B,A,B,C,T0,T1,T2,T3,T4,T5,T6,T7,T8,T9,T10",
    )


@pytest.fixture(autouse=True)
def isolate_broker_executor_corporate_action_io(monkeypatch, request):
    """Keep executor unit tests offline; dedicated reconciler tests stay real."""
    dedicated_reconciler_tests = {
        "test_alpaca_corporate_actions_readonly_audit.py",
        "test_corporate_action_adjusted_ohlcv.py",
        "test_corporate_action_reconciliation.py",
        "test_broker_corporate_action_execution_e2e.py",
    }
    if request.node.path.name in dedicated_reconciler_tests:
        return

    from src.execution.broker_executor import BrokerExecutor

    monkeypatch.setattr(
        BrokerExecutor,
        "reconcile_corporate_actions",
        lambda self, configured_symbols, positions=None: {
            "new_activity_count": 0,
            "blocked_symbols": [],
            "unresolved_symbols": [],
        },
    )
