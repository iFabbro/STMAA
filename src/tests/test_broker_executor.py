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


class _FakeOrder:
    def __init__(self, status, qty=0, filled_qty=0, limit_price=None, notional=None, symbol="AAPL"):
        self.status = status
        self.qty = qty
        self.filled_qty = filled_qty
        self.limit_price = limit_price
        self.notional = notional
        self.symbol = symbol


def test_reserved_notional_counts_accepted(alpaca_env):
    executor = BrokerExecutor()
    orders = [_FakeOrder("accepted", qty=10, filled_qty=0, limit_price=100.0, symbol="AAPL")]
    reserved = executor._get_reserved_notional(orders, prices={"AAPL": 100.0})
    assert reserved == 1000.0


def test_reserved_notional_counts_partially_filled_residual(alpaca_env):
    executor = BrokerExecutor()
    orders = [_FakeOrder("partially_filled", qty=10, filled_qty=4, limit_price=50.0, symbol="MSFT")]
    reserved = executor._get_reserved_notional(orders, prices={"MSFT": 50.0})
    assert reserved == 300.0


def test_reserved_notional_ignores_filled(alpaca_env):
    executor = BrokerExecutor()
    orders = [_FakeOrder("filled", qty=10, filled_qty=10, limit_price=50.0, symbol="GOOGL")]
    reserved = executor._get_reserved_notional(orders, prices={"GOOGL": 50.0})
    assert reserved == 0.0


def test_execute_weights_uses_residual_budget(alpaca_env, monkeypatch):
    executor = BrokerExecutor()

    executor.state["peak_equity"] = 1000.0
    monkeypatch.setattr(
        executor,
        "get_account_status",
        lambda: {
            "cash": 500000.0,
            "portfolio_value": 1000.0,
            "buying_power": 1000.0,
            "equity": 1000.0,
        },
    )
    monkeypatch.setattr(executor, "get_positions", lambda: {})
    monkeypatch.setattr(
        executor,
        "_get_open_orders",
        lambda: [_FakeOrder("accepted", qty=2, filled_qty=0, limit_price=100.0, symbol="AAPL")],
    )
    monkeypatch.setattr(
        executor,
        "_get_prices_from_bars",
        lambda tickers: {"AAPL": 100.0},
    )

    weights = PortfolioWeights(weights=np.array([1.0]), tickers=["AAPL"])
    result = executor.execute_weights(weights)

    assert len(result["orders"]) == 1
    assert result["orders"][0]["status"] == "failed"


def test_risk_gate_blocks_on_drawdown(alpaca_env):
    executor = BrokerExecutor()
    executor.state["peak_equity"] = 1000.0
    capped, block = executor._risk_gate(800.0, 500.0)
    assert capped == 0.0
    assert block and block.startswith("max_drawdown")


def test_risk_gate_caps_exposure(alpaca_env):
    executor = BrokerExecutor()
    executor.state["peak_equity"] = 1000.0
    capped, block = executor._risk_gate(970.0, 800.0)
    assert capped <= 500.0
    assert block is None
