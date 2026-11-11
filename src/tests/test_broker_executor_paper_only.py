"""Regression tests for the BrokerExecutor Paper-only safety boundary."""

import pytest

import src.execution.broker_executor as broker_executor_module
from src.execution.broker_executor import BrokerExecutor, PAPER_API_BASE_URL


class FakeTradingClient:
    def __init__(self, *, api_key, secret_key, paper, url_override):
        self.paper = paper
        self._base_url = url_override
        self.calls = []

    def submit_order(self, *args, **kwargs):
        self.calls.append(("submit_order", args, kwargs))
        raise AssertionError("broker mutation must not be called by this test")

    def cancel_order_by_id(self, *args, **kwargs):
        self.calls.append(("cancel_order_by_id", args, kwargs))
        raise AssertionError("broker mutation must not be called by this test")

    def close_position(self, *args, **kwargs):
        self.calls.append(("close_position", args, kwargs))
        raise AssertionError("broker mutation must not be called by this test")


class FakeAlpacaFeed:
    def __init__(self, **kwargs):
        pass


@pytest.fixture
def paper_executor_factory(monkeypatch, tmp_path):
    monkeypatch.setenv("ALPACA_API_KEY", "paper-test-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "paper-test-secret")
    monkeypatch.setenv("STMAA_BROKER_STATE_FILE", str(tmp_path / "broker_state.json"))
    monkeypatch.setattr(broker_executor_module, "TradingClient", FakeTradingClient)
    monkeypatch.setattr(broker_executor_module, "AlpacaFeed", FakeAlpacaFeed)
    return lambda **kwargs: BrokerExecutor(**kwargs)


def test_executor_rejects_non_paper_mode_before_client_creation(paper_executor_factory):
    with pytest.raises(ValueError, match="Paper-only"):
        paper_executor_factory(paper_trading=False)


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://api.alpaca.markets",
        "http://paper-api.alpaca.markets",
        "https://paper-api.alpaca.markets.evil.example",
        "https://paper-api.alpaca.markets:443",
        "https://paper-api.alpaca.markets/other",
        "",
    ],
)
def test_executor_rejects_live_or_ambiguous_endpoints(paper_executor_factory, endpoint):
    with pytest.raises(ValueError, match="exact Alpaca Paper endpoint"):
        paper_executor_factory(base_url=endpoint)


def test_executor_pins_sdk_client_to_exact_paper_endpoint(paper_executor_factory):
    executor = paper_executor_factory()

    assert executor.paper_trading is True
    assert executor.base_url == PAPER_API_BASE_URL
    assert executor.trading_client.paper is True
    assert executor.trading_client._base_url == PAPER_API_BASE_URL
    executor._assert_paper_mutation_allowed()


def test_non_paper_sdk_endpoint_is_rejected_by_mutation_guard(paper_executor_factory):
    executor = paper_executor_factory()
    executor.trading_client._base_url = "https://api.alpaca.markets"

    with pytest.raises(RuntimeError, match="SDK client endpoint is not Alpaca Paper"):
        executor._assert_paper_mutation_allowed()

    assert executor.trading_client.calls == []


def test_replaced_sdk_client_is_rejected(paper_executor_factory):
    executor = paper_executor_factory()
    executor.trading_client = FakeTradingClient(
        api_key="paper-test-key",
        secret_key="paper-test-secret",
        paper=False,
        url_override="https://api.alpaca.markets",
    )

    with pytest.raises(RuntimeError, match="Paper client identity is not verified"):
        executor._assert_paper_mutation_allowed()

    assert executor.trading_client.calls == []
