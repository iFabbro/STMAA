import numpy as np
import pytest

from src.execution.broker_executor import BrokerExecutor
from src.strategy.weight_interface import PortfolioWeights


@pytest.fixture
def alpaca_env(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "test-secret")


def test_executor_creation_structure(alpaca_env):
    executor = BrokerExecutor(paper_trading=True)

    assert executor.paper_trading is True
    assert executor.state_file.exists()


def test_execute_weights_mock(alpaca_env, monkeypatch):
    executor = BrokerExecutor()

    monkeypatch.setattr(
        executor,
        "get_account_status",
        lambda: {
            "cash": 500000.0,
            "portfolio_value": 500000.0,
            "equity": 500000.0,
        },
    )
    monkeypatch.setattr(executor, "get_positions", lambda: {})
    monkeypatch.setattr(executor, "_get_open_orders", lambda: [])
    monkeypatch.setattr(
        executor,
        "_get_prices_from_bars",
        lambda tickers: {
            "AAPL": 100.0,
            "GOOGL": 200.0,
            "MSFT": 300.0,
        },
    )

    weights = PortfolioWeights(
        weights=np.array([0.4, 0.3, 0.3]),
        tickers=["AAPL", "GOOGL", "MSFT"],
    )

    result = executor.execute_weights(weights)

    assert "orders" in result
    assert len(result["orders"]) == 3
    assert result["timestamp"] is not None
