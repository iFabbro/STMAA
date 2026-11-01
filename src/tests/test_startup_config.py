import pytest

from src.utils.startup_config import (
    load_hub_refresh_seconds,
    load_startup_config,
    load_tickers,
    validate_broker_preflight,
)


VALID_ENV = {
    "ALPACA_API_KEY": "paper-key-example",
    "ALPACA_API_SECRET": "paper-secret-example",
}


def test_startup_config_defaults_to_paper_and_safe_liquidation_setting():
    config = load_startup_config(VALID_ENV)
    assert config.trading_mode == "paper"
    assert config.base_url == "https://paper-api.alpaca.markets"
    assert config.liquidate_on_drawdown is False
    assert config.max_portfolio_exposure == pytest.approx(0.50)
    assert config.max_drawdown == pytest.approx(0.15)
    assert config.tickers == ("AAPL", "GOOGL", "MSFT", "AMZN", "TSLA", "NVDA", "META")
    assert config.initial_capital == pytest.approx(500000.0)
    assert config.max_position_size == pytest.approx(0.20)
    assert config.transaction_cost == pytest.approx(0.001)
    assert config.slippage == pytest.approx(0.0005)
    assert config.vix_threshold_high == pytest.approx(30.0)
    assert config.vix_threshold_low == pytest.approx(15.0)
    assert config.interval_seconds == 300


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("STMAA_INITIAL_CAPITAL", "0"),
        ("STMAA_INITIAL_CAPITAL", "nan"),
        ("MAX_POSITION_SIZE", "0"),
        ("MAX_POSITION_SIZE", "0.60"),
        ("TRANSACTION_COST", "-0.1"),
        ("TRANSACTION_COST", "1"),
        ("SLIPPAGE", "nan"),
        ("VIX_THRESHOLD_LOW", "-1"),
        ("VIX_THRESHOLD_HIGH", "15"),
        ("STMAA_INTERVAL_SECONDS", "0"),
        ("STMAA_INTERVAL_SECONDS", "1.5"),
    ],
)
def test_startup_config_rejects_invalid_runtime_settings(key, value):
    with pytest.raises(ValueError):
        load_startup_config({**VALID_ENV, key: value})


def test_startup_config_loads_runtime_overrides():
    config = load_startup_config({
        **VALID_ENV,
        "STMAA_TICKERS": "aapl, msft",
        "STMAA_INITIAL_CAPITAL": "250000",
        "MAX_POSITION_SIZE": "0.10",
        "TRANSACTION_COST": "0.002",
        "SLIPPAGE": "0.001",
        "VIX_THRESHOLD_LOW": "12",
        "VIX_THRESHOLD_HIGH": "28",
        "STMAA_INTERVAL_SECONDS": "120",
    })
    assert config.tickers == ("AAPL", "MSFT")
    assert config.initial_capital == pytest.approx(250000.0)
    assert config.max_position_size == pytest.approx(0.10)
    assert config.transaction_cost == pytest.approx(0.002)
    assert config.slippage == pytest.approx(0.001)
    assert config.vix_threshold_low == pytest.approx(12.0)
    assert config.vix_threshold_high == pytest.approx(28.0)
    assert config.interval_seconds == 120


@pytest.mark.parametrize("value", ["", "AAPL,,MSFT", "AAPL,AAPL", "bad ticker", "1AAPL"])
def test_ticker_loader_rejects_empty_invalid_or_duplicate_symbols(value):
    with pytest.raises(ValueError):
        load_tickers({"STMAA_TICKERS": value})


def test_hub_refresh_interval_is_positive_integer():
    assert load_hub_refresh_seconds({}) == 30
    assert load_hub_refresh_seconds({"STMAA_HUB_REFRESH_SECONDS": "15"}) == 15
    with pytest.raises(ValueError, match="STMAA_HUB_REFRESH_SECONDS"):
        load_hub_refresh_seconds({"STMAA_HUB_REFRESH_SECONDS": "0"})


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("ALPACA_API_KEY", ""),
        ("ALPACA_API_KEY", " your_api_key_here "),
        ("ALPACA_API_SECRET", "your_api_secret_here"),
        ("ALPACA_API_SECRET", "   "),
    ],
)
def test_startup_config_rejects_missing_or_placeholder_credentials(key, value):
    env = {**VALID_ENV, key: value}
    with pytest.raises(ValueError, match="missing|placeholder"):
        load_startup_config(env)


@pytest.mark.parametrize("mode", ["live", "LIVE", "paper-live", ""])
def test_startup_config_rejects_non_paper_mode(mode):
    env = {**VALID_ENV, "STMAA_TRADING_MODE": mode}
    with pytest.raises(ValueError, match="must be 'paper'"):
        load_startup_config(env)


@pytest.mark.parametrize(
    "url",
    [
        "https://api.alpaca.markets",
        "https://paper-api.alpaca.markets.evil.example",
        "https://paper-api.alpaca.markets/v2",
    ],
)
def test_startup_config_rejects_non_paper_or_custom_endpoint(url):
    env = {**VALID_ENV, "ALPACA_BASE_URL": url}
    with pytest.raises(ValueError, match="ALPACA_BASE_URL"):
        load_startup_config(env)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("MAX_PORTFOLIO_EXPOSURE", "nan"),
        ("MAX_PORTFOLIO_EXPOSURE", "0"),
        ("MAX_PORTFOLIO_EXPOSURE", "1.01"),
        ("MAX_PORTFOLIO_EXPOSURE", "-0.01"),
        ("MAX_DRAWDOWN", "inf"),
        ("MAX_DRAWDOWN", "0"),
        ("MAX_DRAWDOWN", "1"),
        ("MAX_DRAWDOWN", "abc"),
    ],
)
def test_startup_config_rejects_invalid_risk_limits(key, value):
    env = {**VALID_ENV, key: value}
    with pytest.raises(ValueError):
        load_startup_config(env)


@pytest.mark.parametrize("value", ["maybe", "2", "enabled"])
def test_startup_config_rejects_ambiguous_liquidation_boolean(value):
    env = {**VALID_ENV, "LIQUIDATE_ON_DRAWDOWN": value}
    with pytest.raises(ValueError, match="LIQUIDATE_ON_DRAWDOWN"):
        load_startup_config(env)


@pytest.mark.parametrize("value,expected", [("true", True), ("YES", True), ("1", True), ("off", False), ("0", False)])
def test_startup_config_parses_liquidation_boolean(value, expected):
    env = {**VALID_ENV, "LIQUIDATE_ON_DRAWDOWN": value}
    assert load_startup_config(env).liquidate_on_drawdown is expected


class FakeExecutor:
    def __init__(self, status, paper_trading=True):
        self.status = status
        self.paper_trading = paper_trading
        self.calls = 0

    def get_account_status(self):
        self.calls += 1
        return self.status


def good_status(**overrides):
    return {
        "account_data_fresh": True,
        "account_id": "paper-account-123",
        "equity": 10000.0,
        "cash": 9000.0,
        "buying_power": 18000.0,
        **overrides,
    }


def test_broker_preflight_uses_only_read_only_account_snapshot():
    executor = FakeExecutor(good_status())
    status = validate_broker_preflight(executor)
    assert status["account_id"] == "paper-account-123"
    assert executor.calls == 1


def test_broker_preflight_rejects_live_executor_before_account_call():
    executor = FakeExecutor(good_status(), paper_trading=False)
    with pytest.raises(RuntimeError, match="not explicitly in paper"):
        validate_broker_preflight(executor)
    assert executor.calls == 0


@pytest.mark.parametrize(
    "status",
    [
        {"account_data_fresh": False, "account_id": "paper-account-123", "equity": 10000, "cash": 9000, "buying_power": 18000},
        good_status(account_id=None),
        good_status(account_id="  "),
        good_status(equity=None),
        good_status(equity=float("nan")),
        good_status(cash=-1),
        good_status(buying_power=float("inf")),
        good_status(equity=0),
    ],
)
def test_broker_preflight_fails_closed_on_unverified_account(status):
    executor = FakeExecutor(status)
    with pytest.raises(RuntimeError):
        validate_broker_preflight(executor)
