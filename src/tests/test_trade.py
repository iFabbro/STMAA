import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import trade
from trade import PaperTradingBot
from src.strategy.weight_interface import PortfolioWeights


class DummyExecutor:
    def __init__(self, *args, **kwargs):
        self.paper_trading = True

    def execute_weights(self, weights):
        return {
            "orders": [
                {
                    "ticker": ticker,
                    "side": "buy",
                    "quantity": float(weight),
                    "timestamp": "2026-08-01T12:00:00",
                    "price": 100.0,
                }
                for ticker, weight in zip(weights.tickers, weights.weights)
            ],
            "portfolio_value": 123456.0,
            "timestamp": "2026-08-01T12:00:00",
            "metrics": {"max_drawdown": 0.0, "sharpe_ratio": 0.0, "regime": "sideways"},
        }

    def get_account_status(self):
        return {
            "cash": 1000.0,
            "portfolio_value": 123456.0,
            "equity": 123456.0,
        }


class DummyFeed:
    def __init__(self, *args, **kwargs):
        pass

    def get_bars(self, tickers, days=100):
        idx = pd.date_range("2026-01-01", periods=3, freq="D")
        data = {}
        for t in tickers:
            data[f"Close_{t}"] = [100.0, 101.0, 102.0]
        return pd.DataFrame(data, index=idx)


class DummyProcessor:
    def __init__(self, *args, **kwargs):
        pass


class DummyPipeline:
    def __init__(self, *args, **kwargs):
        pass

    def set_risk(self, *args, **kwargs):
        return None


class DummyRisk:
    def __init__(self, *args, **kwargs):
        pass


@pytest.fixture
def fake_env(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "test-secret")


def test_trade_fallback_writes_hub_state(tmp_path, monkeypatch, fake_env):
    monkeypatch.chdir(tmp_path)

    monkeypatch.setattr(trade, "BrokerExecutor", DummyExecutor)
    monkeypatch.setattr(trade, "AlpacaFeed", DummyFeed)
    monkeypatch.setattr(trade, "DataProcessor", DummyProcessor)
    monkeypatch.setattr(trade, "WeightCentricPipeline", DummyPipeline)
    monkeypatch.setattr(trade, "RiskOverlayModule", DummyRisk)
    monkeypatch.setattr(trade, "PPO_AVAILABLE", False)
    monkeypatch.setattr(trade, "PPOAgent", None)

    bot = PaperTradingBot(tickers=["AAPL", "MSFT"], initial_capital=100000.0)

    market_data = bot.get_market_data()
    weights = bot.generate_weights(market_data)

    assert isinstance(weights, PortfolioWeights)
    assert weights.tickers == ["AAPL", "MSFT"]
    assert np.allclose(weights.weights, np.array([0.5, 0.5]))
    assert np.isclose(weights.weights.sum(), 1.0)

    result = bot.execute_trade(weights)
    bot.update_hub_state(result)

    hub_file = Path("logs/hub_state.json")
    assert hub_file.exists()

    payload = json.loads(hub_file.read_text())
    assert payload["portfolio_value"] == 123456.0
    assert payload["cash"] == 1000.0
    assert payload["equity"] == 123456.0
    assert len(payload["orders"]) == 2
    assert payload["orders"][0]["ticker"] == "AAPL"
    assert payload["orders"][1]["ticker"] == "MSFT"



def test_get_vix_value_returns_latest_valid_close(monkeypatch):
    history = pd.DataFrame(
        {"Close": [18.5, float("nan"), 24.2]},
        index=pd.date_range("2026-10-19", periods=3, freq="D"),
    )

    class FakeTicker:
        def __init__(self, symbol):
            assert symbol == "^VIX"

        def history(self, period, interval):
            assert period == "5d"
            assert interval == "1d"
            return history

    monkeypatch.setattr(trade.yf, "Ticker", FakeTicker)
    bot = PaperTradingBot.__new__(PaperTradingBot)
    assert bot.get_vix_value() == pytest.approx(24.2)


@pytest.mark.parametrize(
    "history",
    [
        pd.DataFrame(),
        pd.DataFrame({"Close": [float("nan")]}),
        pd.DataFrame({"Close": [-1.0]}),
        pd.DataFrame({"Close": [float("inf")]}),
    ],
)
def test_get_vix_value_fails_closed_on_invalid_data(monkeypatch, history):
    class FakeTicker:
        def __init__(self, symbol):
            pass

        def history(self, period, interval):
            return history

    monkeypatch.setattr(trade.yf, "Ticker", FakeTicker)
    bot = PaperTradingBot.__new__(PaperTradingBot)
    with pytest.raises(RuntimeError, match="Cannot obtain a valid VIX"):
        bot.get_vix_value()



def test_execute_trade_passes_vix_to_risk_overlay(monkeypatch):
    from types import SimpleNamespace

    class CapturingRisk:
        def __init__(self):
            self.kwargs = None

        def apply(self, market_data, weights, tickers, **kwargs):
            self.kwargs = kwargs
            return np.asarray(weights, dtype=float) * 0.5

    class CapturingExecutor(DummyExecutor):
        def __init__(self):
            self.paper_trading = True
            self.submitted_weights = None

        def get_account_status(self):
            return {"portfolio_value": 10000.0, "cash": 5000.0, "equity": 10000.0}

        def execute_weights(self, weights):
            self.submitted_weights = weights
            return {"orders": [], "portfolio_value": 10000.0, "timestamp": "2026-10-22", "metrics": {}}

    risk = CapturingRisk()
    executor = CapturingExecutor()
    bot = PaperTradingBot.__new__(PaperTradingBot)
    bot.initial_capital = 10000.0
    bot.pipeline = SimpleNamespace(risk_module=risk, current_portfolio_value=10000.0)
    bot.executor = executor
    monkeypatch.setattr(bot, "get_vix_value", lambda: 32.0)
    monkeypatch.setattr(trade, "write_snapshot", lambda *args, **kwargs: None)
    monkeypatch.setattr(trade, "log_event", lambda *args, **kwargs: None)

    original = PortfolioWeights(
        weights=np.array([0.2, 0.2]),
        tickers=["AAPL", "MSFT"],
        timestamp="2026-10-22T10:00:00",
    )
    bot.execute_trade(original)

    assert risk.kwargs["vix"] == pytest.approx(32.0)
    assert risk.kwargs["portfolio_value"] == pytest.approx(10000.0)
    assert np.allclose(executor.submitted_weights.weights, np.array([0.1, 0.1]))
    assert executor.submitted_weights.metadata["vix"] == pytest.approx(32.0)
    assert executor.submitted_weights.metadata["risk_overlay_applied"] is True


def test_execute_trade_does_not_submit_orders_when_vix_unavailable(monkeypatch):
    from types import SimpleNamespace

    class CapturingRisk:
        def apply(self, *args, **kwargs):
            raise AssertionError("Risk overlay must not run without VIX")

    class CapturingExecutor(DummyExecutor):
        def __init__(self):
            self.paper_trading = True
            self.submitted = False

        def get_account_status(self):
            return {"portfolio_value": 10000.0, "cash": 5000.0, "equity": 10000.0}

        def execute_weights(self, weights):
            self.submitted = True
            return {"orders": [], "portfolio_value": 10000.0, "timestamp": "2026-10-22", "metrics": {}}

    executor = CapturingExecutor()
    bot = PaperTradingBot.__new__(PaperTradingBot)
    bot.initial_capital = 10000.0
    bot.pipeline = SimpleNamespace(risk_module=CapturingRisk(), current_portfolio_value=10000.0)
    bot.executor = executor

    def unavailable_vix():
        raise RuntimeError("Cannot obtain a valid VIX value")

    monkeypatch.setattr(bot, "get_vix_value", unavailable_vix)
    weights = PortfolioWeights(
        weights=np.array([0.2, 0.2]),
        tickers=["AAPL", "MSFT"],
        timestamp="2026-10-22T10:00:00",
    )
    with pytest.raises(RuntimeError, match="Cannot obtain a valid VIX"):
        bot.execute_trade(weights)
    assert executor.submitted is False
