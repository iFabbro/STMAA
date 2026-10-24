
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
            "account_data_fresh": True,
        }

    def get_positions(self):
        return {}


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


def test_trade_fails_closed_when_ppo_import_is_unavailable(tmp_path, monkeypatch, fake_env):
    monkeypatch.chdir(tmp_path)
    submitted = []

    class TrackingExecutor(DummyExecutor):
        def execute_weights(self, weights):
            submitted.append(weights)
            return super().execute_weights(weights)

    monkeypatch.setattr(trade, "BrokerExecutor", TrackingExecutor)
    monkeypatch.setattr(trade, "AlpacaFeed", DummyFeed)
    monkeypatch.setattr(trade, "DataProcessor", DummyProcessor)
    monkeypatch.setattr(trade, "WeightCentricPipeline", DummyPipeline)
    monkeypatch.setattr(trade, "RiskOverlayModule", DummyRisk)
    monkeypatch.setattr(trade, "PPO_AVAILABLE", False)
    monkeypatch.setattr(trade, "PPOAgent", None)

    bot = PaperTradingBot(tickers=["AAPL", "MSFT"], initial_capital=100000.0)
    market_data = bot.get_market_data()

    with pytest.raises(RuntimeError, match="PPO unavailable"):
        bot.generate_weights(market_data)

    assert submitted == []


def test_get_vix_value_returns_latest_valid_close(monkeypatch):
    history = pd.DataFrame(
        {"Close": [18.5, float("nan"), 24.2]},
        index=pd.date_range("2026-10-19", periods=3, freq="D"),
    )

    class FakeTicker:
        def __init__(self, symbol):
            assert symbol == "^VIX"

        def history(self, period, interval):
            assert period == "10d"
            assert interval == "1d"
            return history

    monkeypatch.setattr(trade.yf, "Ticker", FakeTicker)
    bot = PaperTradingBot.__new__(PaperTradingBot)
    monkeypatch.setattr(bot, "_latest_completed_vix_session_date", lambda: history.index[-1].date())
    assert bot.get_vix_value() == pytest.approx(24.2)



def test_get_vix_value_fails_closed_when_latest_close_is_stale(monkeypatch):
    history = pd.DataFrame(
        {"Close": [20.0]},
        index=pd.DatetimeIndex(["2026-10-20"]),
    )

    class FakeTicker:
        def __init__(self, symbol):
            pass

        def history(self, period, interval):
            return history

    monkeypatch.setattr(trade.yf, "Ticker", FakeTicker)
    bot = PaperTradingBot.__new__(PaperTradingBot)
    monkeypatch.setattr(
        bot, "_latest_completed_vix_session_date", lambda: pd.Timestamp("2026-10-21").date()
    )
    with pytest.raises(RuntimeError, match="valid VIX close"):
        bot.get_vix_value()


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
            return {"portfolio_value": 10000.0, "cash": 5000.0, "equity": 10000.0, "account_data_fresh": True}

        def execute_weights(self, weights):
            self.submitted_weights = weights
            return {"orders": [], "portfolio_value": 10000.0, "timestamp": "2026-10-22", "metrics": {}}

    risk = CapturingRisk()
    executor = CapturingExecutor()
    bot = PaperTradingBot.__new__(PaperTradingBot)
    bot.initial_capital = 10000.0
    bot.pipeline = SimpleNamespace(risk_module=risk, current_portfolio_value=10000.0)
    bot.executor = executor
    bot.tickers = ["AAPL", "MSFT"]
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
    assert risk.kwargs["peak_portfolio_value"] == pytest.approx(10000.0)
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
            return {"portfolio_value": 10000.0, "cash": 5000.0, "equity": 10000.0, "account_data_fresh": True}

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



def test_execute_trade_persists_actual_account_drawdown_peak(monkeypatch):
    from types import SimpleNamespace

    class CapturingRisk:
        def __init__(self):
            self.kwargs = None

        def apply(self, market_data, weights, tickers, **kwargs):
            self.kwargs = kwargs
            return np.asarray(weights, dtype=float)

    class StatefulExecutor(DummyExecutor):
        def __init__(self):
            self.paper_trading = True
            # Legacy numeric peak may have come from configured capital;
            # the risk overlay must re-anchor it to this verified broker equity.
            self.state = {"risk_peak_portfolio_value": 500000.0}
            self.saved = False
            self.submitted_weights = None

        def get_account_status(self):
            return {"portfolio_value": 100000.0, "cash": 50000.0, "equity": 100000.0, "account_data_fresh": True}

        def _save_state(self):
            self.saved = True

        def execute_weights(self, weights):
            self.submitted_weights = weights
            return {"orders": [], "portfolio_value": 100000.0, "timestamp": "2026-10-22", "metrics": {}}

    risk = CapturingRisk()
    executor = StatefulExecutor()
    bot = PaperTradingBot.__new__(PaperTradingBot)
    bot.initial_capital = 500000.0
    bot.pipeline = SimpleNamespace(risk_module=risk, current_portfolio_value=500000.0)
    bot.executor = executor
    bot.tickers = ["AAPL"]
    monkeypatch.setattr(bot, "get_vix_value", lambda: 12.0)
    monkeypatch.setattr(trade, "write_snapshot", lambda *args, **kwargs: None)
    monkeypatch.setattr(trade, "log_event", lambda *args, **kwargs: None)

    bot.execute_trade(PortfolioWeights(weights=np.array([0.1]), tickers=["AAPL"]))

    assert risk.kwargs["portfolio_value"] == pytest.approx(100000.0)
    assert risk.kwargs["peak_portfolio_value"] == pytest.approx(100000.0)
    assert executor.state["risk_peak_portfolio_value"] == pytest.approx(100000.0)
    assert executor.saved is True


def test_execute_trade_preserves_persisted_drawdown_peak(monkeypatch):
    from types import SimpleNamespace

    class CapturingRisk:
        def __init__(self):
            self.kwargs = None
            self.max_drawdown_threshold = 0.15

        def apply(self, market_data, weights, tickers, **kwargs):
            self.kwargs = kwargs
            return np.zeros(len(tickers), dtype=float)

    class StatefulExecutor(DummyExecutor):
        def __init__(self):
            self.paper_trading = True
            self.state = {
                "risk_peak_portfolio_value": 120000.0,
                "risk_peak_equity_source": "alpaca_account",
                "risk_peak_account_mode": "paper",
                "risk_peak_account_id": "account-A",
                "account_id": "account-A",
            }
            self.submitted_weights = None

        def get_account_status(self):
            return {"portfolio_value": 100000.0, "cash": 50000.0, "equity": 100000.0, "account_data_fresh": True, "account_id": "account-A"}

        def _save_state(self):
            pass

        def execute_weights(self, weights):
            self.submitted_weights = weights
            return {"orders": [], "portfolio_value": 100000.0, "timestamp": "2026-10-22", "metrics": {}}

    risk = CapturingRisk()
    executor = StatefulExecutor()
    bot = PaperTradingBot.__new__(PaperTradingBot)
    bot.initial_capital = 500000.0
    bot.pipeline = SimpleNamespace(risk_module=risk, current_portfolio_value=500000.0)
    bot.executor = executor
    bot.tickers = ["AAPL"]
    monkeypatch.setattr(bot, "get_vix_value", lambda: 12.0)
    monkeypatch.setattr(trade, "write_snapshot", lambda *args, **kwargs: None)
    monkeypatch.setattr(trade, "log_event", lambda *args, **kwargs: None)

    result = bot.execute_trade(PortfolioWeights(weights=np.array([0.1]), tickers=["AAPL"]))

    assert risk.kwargs["peak_portfolio_value"] == pytest.approx(120000.0)
    assert executor.state["risk_peak_portfolio_value"] == pytest.approx(120000.0)
    assert executor.submitted_weights is None
    assert result["target_execution_skipped"] is True
    assert result["circuit_breaker_triggered"] is True
    assert result["liquidate_on_circuit_breaker"] is False

    monkeypatch.setenv("LIQUIDATE_ON_DRAWDOWN", "true")
    bot.execute_trade(PortfolioWeights(weights=np.array([0.1]), tickers=["AAPL"]))
    assert executor.submitted_weights.metadata["liquidate_on_circuit_breaker"] is True



def test_execute_trade_reanchors_drawdown_peak_without_account_identity(monkeypatch):
    from types import SimpleNamespace

    class CapturingRisk:
        max_drawdown_threshold = 0.15

        def __init__(self):
            self.kwargs = None
            # Simulate an in-memory peak left over from a different/unverified account.
            self.peak_portfolio_value = 120000.0

        def apply(self, market_data, weights, tickers, **kwargs):
            self.kwargs = kwargs
            portfolio_value = float(kwargs["portfolio_value"])
            peak = max(
                portfolio_value,
                float(kwargs["peak_portfolio_value"]),
                self.peak_portfolio_value or portfolio_value,
            )
            self.peak_portfolio_value = peak
            if (peak - portfolio_value) / peak >= self.max_drawdown_threshold:
                return np.zeros(len(tickers), dtype=float)
            return np.asarray(weights, dtype=float)

    class StatefulExecutor(DummyExecutor):
        def __init__(self):
            self.paper_trading = True
            self.state = {
                "risk_peak_portfolio_value": 120000.0,
                "risk_peak_equity_source": "alpaca_account",
                "risk_peak_account_mode": "paper",
                "risk_peak_account_id": None,
            }
            self.submitted_weights = None

        def get_account_status(self):
            return {
                "portfolio_value": 100000.0, "cash": 50000.0, "equity": 100000.0,
                "account_data_fresh": True, "account_id": None,
            }

        def _save_state(self):
            pass

        def execute_weights(self, weights):
            self.submitted_weights = weights
            return {"orders": [], "portfolio_value": 100000.0, "timestamp": "2026-10-22", "metrics": {}}

    risk = CapturingRisk()
    executor = StatefulExecutor()
    bot = PaperTradingBot.__new__(PaperTradingBot)
    bot.initial_capital = 500000.0
    bot.pipeline = SimpleNamespace(risk_module=risk, current_portfolio_value=500000.0)
    bot.executor = executor
    bot.tickers = ["AAPL"]
    monkeypatch.setattr(bot, "get_vix_value", lambda: 12.0)
    monkeypatch.setattr(trade, "write_snapshot", lambda *args, **kwargs: None)
    monkeypatch.setattr(trade, "log_event", lambda *args, **kwargs: None)

    bot.execute_trade(PortfolioWeights(weights=np.array([0.1]), tickers=["AAPL"]))

    assert risk.kwargs["peak_portfolio_value"] == pytest.approx(100000.0)
    assert executor.state["risk_peak_portfolio_value"] == pytest.approx(100000.0)
    assert executor.state["risk_peak_account_id"] is None
    assert risk.peak_portfolio_value == pytest.approx(100000.0)
    assert executor.submitted_weights.metadata["circuit_breaker_triggered"] is False
    np.testing.assert_allclose(executor.submitted_weights.weights, np.array([0.1]))


def test_execute_trade_fails_closed_when_account_value_is_stale(monkeypatch):
    from types import SimpleNamespace

    class CapturingRisk:
        def apply(self, *args, **kwargs):
            raise AssertionError("Risk overlay must not run with stale account data")

    class StaleExecutor(DummyExecutor):
        def __init__(self):
            self.paper_trading = True
            self.submitted = False

        def get_account_status(self):
            return {
                "portfolio_value": 100000.0,
                "cash": 50000.0,
                "equity": 100000.0,
                "account_data_fresh": False,
            }

        def execute_weights(self, weights):
            self.submitted = True
            return {"orders": [], "portfolio_value": 100000.0, "timestamp": "2026-10-22", "metrics": {}}

    executor = StaleExecutor()
    bot = PaperTradingBot.__new__(PaperTradingBot)
    bot.initial_capital = 500000.0
    bot.pipeline = SimpleNamespace(risk_module=CapturingRisk(), current_portfolio_value=500000.0)
    bot.executor = executor
    with pytest.raises(RuntimeError, match="Cannot verify current broker account equity"):
        bot.execute_trade(PortfolioWeights(weights=np.array([0.1]), tickers=["AAPL"]))
    assert executor.submitted is False


@pytest.mark.parametrize(
    "invalid_weights",
    [
        np.array([0.5]),
        np.array([0.5, np.nan]),
        np.array([-0.1, 1.1]),
        np.array([0.4, 0.4]),
    ],
)
def test_generate_weights_rejects_invalid_ppo_output(monkeypatch, invalid_weights):
    class FakePPO:
        def predict_weights(self, market_data, current_weights=None):
            return invalid_weights

    bot = PaperTradingBot.__new__(PaperTradingBot)
    bot.tickers = ["AAPL", "MSFT"]
    bot.ppo_agent = FakePPO()
    bot.executor = DummyExecutor()
    monkeypatch.setattr(trade, "PPO_AVAILABLE", True)

    with pytest.raises(RuntimeError, match="PPO returned"):
        bot.generate_weights(pd.DataFrame({"Close_AAPL": [100.0], "Close_MSFT": [100.0]}))


def test_generate_weights_accepts_valid_ppo_target_weights(monkeypatch):
    class FakePPO:
        def predict_weights(self, market_data, current_weights=None):
            return np.array([0.25, 0.75])

    bot = PaperTradingBot.__new__(PaperTradingBot)
    bot.tickers = ["AAPL", "MSFT"]
    bot.ppo_agent = FakePPO()
    bot.executor = DummyExecutor()
    monkeypatch.setattr(trade, "PPO_AVAILABLE", True)

    weights = bot.generate_weights(pd.DataFrame({"Close_AAPL": [100.0], "Close_MSFT": [100.0]}))

    assert weights.tickers == ["AAPL", "MSFT"]
    np.testing.assert_allclose(weights.weights, np.array([0.25, 0.75]))


def test_generate_weights_passes_current_holdings_to_ppo(monkeypatch):
    observed = {}

    class FakePPO:
        def predict_weights(self, market_data, current_weights=None):
            observed["current_weights"] = np.asarray(current_weights, dtype=float)
            return np.array([0.25, 0.75])

    class CurrentPortfolioExecutor:
        def get_account_status(self):
            return {
                "portfolio_value": 10000.0,
                "equity": 10000.0,
                "account_data_fresh": True,
            }

        def get_positions(self):
            return {
                "AAPL": {"market_value": 2500.0},
                "MSFT": {"market_value": 5000.0},
            }

    bot = PaperTradingBot.__new__(PaperTradingBot)
    bot.tickers = ["AAPL", "MSFT"]
    bot.ppo_agent = FakePPO()
    bot.executor = CurrentPortfolioExecutor()
    monkeypatch.setattr(trade, "PPO_AVAILABLE", True)

    result = bot.generate_weights(pd.DataFrame({
        "Close_AAPL": [100.0], "Close_MSFT": [100.0]
    }))

    np.testing.assert_allclose(observed["current_weights"], np.array([0.25, 0.5]))
    np.testing.assert_allclose(result.weights, np.array([0.25, 0.75]))


def test_generate_weights_fails_closed_on_existing_short_position(monkeypatch):
    class FakePPO:
        def predict_weights(self, market_data, current_weights=None):
            raise AssertionError("PPO inference must not run with an existing short")

    class ShortPortfolioExecutor:
        def get_account_status(self):
            return {"portfolio_value": 10000.0, "equity": 10000.0, "account_data_fresh": True}

        def get_positions(self):
            return {"AAPL": {"market_value": -100.0}}

    bot = PaperTradingBot.__new__(PaperTradingBot)
    bot.tickers = ["AAPL", "MSFT"]
    bot.ppo_agent = FakePPO()
    bot.executor = ShortPortfolioExecutor()
    monkeypatch.setattr(trade, "PPO_AVAILABLE", True)

    with pytest.raises(RuntimeError, match="existing short position"):
        bot.generate_weights(pd.DataFrame({
            "Close_AAPL": [100.0], "Close_MSFT": [100.0]
        }))


def test_execute_trade_explicit_drawdown_liquidation_bypasses_unavailable_vix(
    monkeypatch,
):
    from types import SimpleNamespace

    class CapturingRisk:
        max_drawdown_threshold = 0.15

        def __init__(self):
            self.kwargs = None

        def apply(self, market_data, weights, tickers, **kwargs):
            self.kwargs = kwargs
            return np.zeros(len(tickers), dtype=float)

    class StatefulExecutor(DummyExecutor):
        def __init__(self):
            self.paper_trading = True
            self.state = {
                "risk_peak_portfolio_value": 120000.0,
                "risk_peak_equity_source": "alpaca_account",
                "risk_peak_account_mode": "paper",
                "risk_peak_account_id": "account-A",
            }
            self.submitted_weights = None

        def get_account_status(self):
            return {
                "portfolio_value": 100000.0,
                "cash": 50000.0,
                "equity": 100000.0,
                "account_data_fresh": True,
                "account_id": "account-A",
            }

        def _save_state(self):
            pass

        def execute_weights(self, weights):
            self.submitted_weights = weights
            return {
                "orders": [], "portfolio_value": 100000.0,
                "timestamp": "2026-10-23", "metrics": {},
            }

    monkeypatch.setenv("LIQUIDATE_ON_DRAWDOWN", "true")
    risk = CapturingRisk()
    executor = StatefulExecutor()
    bot = PaperTradingBot.__new__(PaperTradingBot)
    bot.initial_capital = 100000.0
    bot.pipeline = SimpleNamespace(
        risk_module=risk, current_portfolio_value=100000.0
    )
    bot.executor = executor
    bot.tickers = ["AAPL"]

    def unavailable_vix():
        raise AssertionError("VIX must not gate explicit emergency liquidation")

    monkeypatch.setattr(bot, "get_vix_value", unavailable_vix)
    monkeypatch.setattr(trade, "write_snapshot", lambda *args, **kwargs: None)
    monkeypatch.setattr(trade, "log_event", lambda *args, **kwargs: None)

    bot.execute_trade(PortfolioWeights(
        weights=np.array([0.2]), tickers=["AAPL"]
    ))

    assert risk.kwargs["vix"] is None
    assert executor.submitted_weights.metadata["circuit_breaker_triggered"] is True
    assert executor.submitted_weights.metadata["liquidate_on_circuit_breaker"] is True
    np.testing.assert_array_equal(executor.submitted_weights.weights, np.zeros(1))
