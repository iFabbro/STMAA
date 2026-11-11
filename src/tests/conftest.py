"""Test-wide isolation for durable broker state and external network access.

Production defaults to a repository-anchored path; tests use a per-test file so
restarts are reproducible and tests never share broker state.
"""
import socket

import pytest


@pytest.fixture(autouse=True)
def block_network_access(monkeypatch):
    """Prevent test code from opening or using external network sockets."""

    def deny_network(*args, **kwargs):
        raise RuntimeError("Network access is disabled during STMAA tests")

    monkeypatch.setattr(socket.socket, "connect", deny_network)
    monkeypatch.setattr(socket.socket, "connect_ex", deny_network)
    monkeypatch.setattr(socket.socket, "send", deny_network)
    monkeypatch.setattr(socket.socket, "sendall", deny_network)
    monkeypatch.setattr(socket.socket, "sendto", deny_network)
    monkeypatch.setattr(socket, "create_connection", deny_network)
    monkeypatch.setattr(socket, "getaddrinfo", deny_network)

    if hasattr(socket.socket, "sendmsg"):
        monkeypatch.setattr(socket.socket, "sendmsg", deny_network)


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
        "test_broker_corporate_action_restart_pending_orders_e2e.py",
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


@pytest.fixture(autouse=True)
def register_mock_broker_clients_as_paper(monkeypatch, request):
    """Give injected offline broker doubles explicit Paper provenance.

    The dedicated Paper-boundary tests are excluded: they must exercise client
    replacement and endpoint tampering without this test adapter.
    """
    if request.node.path.name == "test_broker_executor_paper_only.py":
        return

    from src.execution.broker_executor import BrokerExecutor, PAPER_API_BASE_URL

    def test_setattr(self, name, value):
        object.__setattr__(self, name, value)
        if name != "trading_client" or value is None:
            return
        # A test-injected fake broker is an explicitly Paper-scoped adapter.
        # Preserve any endpoint it already declares so Live-tampering tests
        # continue to fail closed.
        if not hasattr(value, "_base_url"):
            try:
                value._base_url = PAPER_API_BASE_URL
            except (AttributeError, TypeError):
                return
        object.__setattr__(self, "_paper_client_identity", id(value))
        object.__setattr__(self, "_paper_endpoint_verified", True)
        if not hasattr(self, "paper_trading"):
            object.__setattr__(self, "paper_trading", True)
        if not hasattr(self, "base_url"):
            object.__setattr__(self, "base_url", PAPER_API_BASE_URL)

    monkeypatch.setattr(BrokerExecutor, "__setattr__", test_setattr)
