import pytest
import trade
from src.utils.startup_config import load_startup_config, validate_broker_preflight


def test_preflight_only_passes_validated_config_and_never_starts_trading_loop(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "paper-key-example")
    monkeypatch.setenv("ALPACA_API_SECRET", "paper-secret-example")
    monkeypatch.setenv("MAX_PORTFOLIO_EXPOSURE", "0.42")
    monkeypatch.setenv("MAX_DRAWDOWN", "0.12")
    monkeypatch.setenv("LIQUIDATE_ON_DRAWDOWN", "true")

    observed = {}

    class FakeExecutor:
        paper_trading = True

        def get_account_status(self):
            return {
                "account_data_fresh": True,
                "account_id": "paper-account-123",
                "equity": 10000.0,
                "cash": 9000.0,
                "buying_power": 18000.0,
            }

        def execute_weights(self, *_args, **_kwargs):
            raise AssertionError("preflight-only must never submit target weights")

    class FakeBot:
        def __init__(self, **kwargs):
            observed["kwargs"] = kwargs
            self.executor = FakeExecutor()

        def run_loop(self, **_kwargs):
            raise AssertionError("preflight-only must not start the trading loop")

    monkeypatch.setattr(trade, "PaperTradingBot", FakeBot)

    assert trade.main(["--preflight-only"]) == 0
    assert observed["kwargs"]["max_drawdown_threshold"] == 0.12
    assert observed["kwargs"]["max_gross_exposure"] == 0.42
    assert observed["kwargs"]["liquidate_on_drawdown"] is True
    assert observed["kwargs"]["api_key"] == "paper-key-example"
    assert observed["kwargs"]["api_secret"] == "paper-secret-example"
    assert observed["kwargs"]["base_url"] == "https://paper-api.alpaca.markets"


def test_invalid_startup_config_stops_before_bot_construction(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "paper-key-example")
    monkeypatch.setenv("ALPACA_API_SECRET", "paper-secret-example")
    monkeypatch.setenv("STMAA_TRADING_MODE", "live")

    class ForbiddenBot:
        def __init__(self, **_kwargs):
            raise AssertionError("invalid config must be rejected before bot construction")

    monkeypatch.setattr(trade, "PaperTradingBot", ForbiddenBot)

    assert trade.main(["--preflight-only"]) == 1


def test_validated_limits_and_credentials_reach_real_bot_components(monkeypatch):
    observed = {}

    class FakeExecutor:
        def __init__(self, **kwargs):
            observed["executor"] = kwargs
            self.paper_trading = kwargs["paper_trading"]

    class FakePipeline:
        def __init__(self, **kwargs):
            observed["pipeline"] = kwargs

        def set_risk(self, risk):
            observed["risk"] = risk

    class FakeRiskOverlay:
        def __init__(self, **kwargs):
            observed["risk_kwargs"] = kwargs

    class FakeProcessor:
        def __init__(self, path):
            observed["processor_path"] = str(path)

    class FakeFeed:
        def __init__(self, **kwargs):
            observed["feed"] = kwargs

    monkeypatch.setattr(trade, "BrokerExecutor", FakeExecutor)
    monkeypatch.setattr(trade, "WeightCentricPipeline", FakePipeline)
    monkeypatch.setattr(trade, "RiskOverlayModule", FakeRiskOverlay)
    monkeypatch.setattr(trade, "DataProcessor", FakeProcessor)
    monkeypatch.setattr(trade, "AlpacaFeed", FakeFeed)
    monkeypatch.setattr(trade, "PPO_AVAILABLE", False)

    trade.PaperTradingBot(
        tickers=["AAPL"],
        initial_capital=10000.0,
        max_drawdown_threshold=0.12,
        max_gross_exposure=0.42,
        liquidate_on_drawdown=True,
        api_key="paper-key-example",
        api_secret="paper-secret-example",
        base_url="https://paper-api.alpaca.markets",
    )

    assert observed["executor"] == {
        "api_key": "paper-key-example",
        "api_secret": "paper-secret-example",
        "base_url": "https://paper-api.alpaca.markets",
        "paper_trading": True,
        "initial_capital": 10000.0,
    }
    assert observed["feed"] == {
        "api_key": "paper-key-example",
        "api_secret": "paper-secret-example",
    }
    assert observed["risk_kwargs"]["max_drawdown_threshold"] == 0.12
    assert observed["risk_kwargs"]["max_gross_exposure"] == 0.42
    assert observed["risk_kwargs"]["initial_capital"] == 10000.0


@pytest.mark.parametrize(
    "overrides",
    [
        {"STMAA_TRADING_MODE": "live"},
        {"ALPACA_BASE_URL": "https://api.alpaca.markets"},
        {"MAX_PORTFOLIO_EXPOSURE": "nan"},
        {"MAX_PORTFOLIO_EXPOSURE": "0"},
        {"MAX_PORTFOLIO_EXPOSURE": "1.01"},
        {"MAX_DRAWDOWN": "inf"},
        {"MAX_DRAWDOWN": "1"},
        {"LIQUIDATE_ON_DRAWDOWN": "sometimes"},
        {"ALPACA_API_KEY": "your_api_key_here"},
    ],
)
def test_invalid_startup_configuration_is_rejected(overrides):
    env = {
        "ALPACA_API_KEY": "valid-paper-key",
        "ALPACA_API_SECRET": "valid-paper-secret",
        **overrides,
    }
    with pytest.raises(ValueError):
        load_startup_config(env)


@pytest.mark.parametrize(
    "status",
    [
        {"account_data_fresh": False, "account_id": "paper-1", "equity": 10000, "cash": 9000, "buying_power": 18000},
        {"account_data_fresh": True, "equity": 10000, "cash": 9000, "buying_power": 18000},
        {"account_data_fresh": True, "account_id": "paper-1", "equity": 0, "cash": 0, "buying_power": 0},
        {"account_data_fresh": True, "account_id": "paper-1", "equity": 10000, "cash": -1, "buying_power": 18000},
        {"account_data_fresh": True, "account_id": "paper-1", "equity": 10000, "cash": 9000, "buying_power": float("inf")},
        {"account_data_fresh": True, "account_id": "paper-1", "equity": "invalid", "cash": 9000, "buying_power": 18000},
    ],
)
def test_broker_preflight_rejects_untrusted_or_invalid_account_snapshot(status):
    class FakeExecutor:
        paper_trading = True

        def get_account_status(self):
            return status

    with pytest.raises(RuntimeError):
        validate_broker_preflight(FakeExecutor())


def test_broker_preflight_rejects_executor_not_explicitly_in_paper_mode():
    class FakeExecutor:
        paper_trading = False

        def get_account_status(self):
            raise AssertionError("must reject mode before requesting account data")

    with pytest.raises(RuntimeError, match="not explicitly in paper mode"):
        validate_broker_preflight(FakeExecutor())


def test_invalid_account_snapshot_blocks_startup_without_loop_or_orders(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "paper-key-example")
    monkeypatch.setenv("ALPACA_API_SECRET", "paper-secret-example")

    class FakeExecutor:
        paper_trading = True

        def get_account_status(self):
            return {
                "account_data_fresh": False,
                "account_id": "paper-account-123",
                "equity": 10000.0,
                "cash": 9000.0,
                "buying_power": 18000.0,
            }

        def execute_weights(self, *_args, **_kwargs):
            raise AssertionError("failed preflight must never submit target weights")

    class FakeBot:
        def __init__(self, **_kwargs):
            self.executor = FakeExecutor()

        def run_loop(self, **_kwargs):
            raise AssertionError("failed preflight must never start the trading loop")

    monkeypatch.setattr(trade, "PaperTradingBot", FakeBot)
    assert trade.main(["--preflight-only"]) == 1
