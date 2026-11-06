import pytest
from unittest.mock import Mock

from src.utils.startup_config import (
    load_hub_refresh_seconds,
    load_startup_config,
    load_strategy_config,
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
    assert config.initial_capital == pytest.approx(100000.0)
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
        self.positions = {"AAPL": {"qty": 1.0, "market_value": 100.0}}
        self.open_orders = []

    def get_account_status(self):
        self.calls += 1
        return self.status

    def get_positions(self):
        return self.positions

    def _get_open_orders(self):
        return self.open_orders

    def _get_aggregate_gross_notional(self, positions, open_orders):
        return (sum(abs(float(p["market_value"])) for p in positions.values()), 0.0)

    def reconcile_broker_state_read_only(self):
        return {
            "safe_for_new_decision": not self.open_orders,
            "read_only": True,
            "unresolved_submissions": 0,
            "active_or_unreconciled_confirmed_orders": 0,
            "open_orders": len(self.open_orders),
        }


def good_status(**overrides):
    return {
        "account_data_fresh": True,
        "account_id": "paper-account-123",
        "equity": 10000.0,
        "cash": 9000.0,
        "buying_power": 18000.0,
        **overrides,
    }


def test_broker_preflight_returns_read_only_positions_orders_and_aggregate_cap_report():
    executor = FakeExecutor(good_status())
    status = validate_broker_preflight(executor)
    report = status["preflight_report"]
    assert status["account_id"] == "paper-account-123"
    assert report["read_only"] is True
    assert report["position_count"] == 1
    assert report["open_order_count"] == 0
    assert report["gross_notional_usd"] == pytest.approx(100.0)
    assert report["max_gross_notional_usd"] == pytest.approx(1000.0)
    assert report["cap_compliant"] is True
    assert report["safe_for_new_decision"] is True
    assert executor.calls == 1


def test_broker_preflight_marks_existing_exposure_above_cap_as_blocked():
    executor = FakeExecutor(good_status())
    executor.positions = {"AAPL": {"qty": 11.0, "market_value": 1100.0}}
    status = validate_broker_preflight(executor)
    report = status["preflight_report"]
    assert report["gross_notional_usd"] == pytest.approx(1100.0)
    assert report["cap_compliant"] is False
    assert report["safe_for_new_decision"] is False


def test_broker_preflight_marks_open_orders_as_blocked():
    executor = FakeExecutor(good_status())
    executor.open_orders = [object()]
    status = validate_broker_preflight(executor)
    assert status["preflight_report"]["open_order_count"] == 1
    assert status["preflight_report"]["safe_for_new_decision"] is False


def test_broker_preflight_recovers_persisted_opg_batches_after_account_identity():
    executor = FakeExecutor(good_status())
    executor.reconcile_opg_batches_read_only = Mock()
    status = validate_broker_preflight(executor)
    assert status["account_id"] == "paper-account-123"
    executor.reconcile_opg_batches_read_only.assert_called_once_with()


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


def test_shared_strategy_config_defaults_match_runtime_contract_without_credentials():
    config = load_strategy_config({})
    assert config.tickers == ("AAPL", "GOOGL", "MSFT", "AMZN", "TSLA", "NVDA", "META")
    assert config.initial_capital == pytest.approx(100000.0)
    assert config.max_gross_exposure == pytest.approx(0.50)
    assert config.max_position_size == pytest.approx(0.20)
    assert config.transaction_cost == pytest.approx(0.001)
    assert config.slippage == pytest.approx(0.0005)


def test_runtime_startup_uses_same_shared_strategy_config():
    env = {
        **VALID_ENV, "STMAA_TICKERS": "MSFT,AAPL",
        "STMAA_INITIAL_CAPITAL": "240000",
        "MAX_PORTFOLIO_EXPOSURE": "0.40",
        "MAX_POSITION_SIZE": "0.15",
        "TRANSACTION_COST": "0.002", "SLIPPAGE": "0.001",
    }
    strategy = load_strategy_config(env)
    runtime = load_startup_config(env)
    assert runtime.tickers == strategy.tickers
    assert runtime.initial_capital == strategy.initial_capital
    assert runtime.max_portfolio_exposure == strategy.max_gross_exposure
    assert runtime.max_position_size == strategy.max_position_size
    assert runtime.transaction_cost == strategy.transaction_cost
    assert runtime.slippage == strategy.slippage


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("MAX_PORTFOLIO_EXPOSURE", "0"),
        ("MAX_PORTFOLIO_EXPOSURE", "1.01"),
        ("MAX_POSITION_SIZE", "0.60"),
        ("STMAA_INITIAL_CAPITAL", "nan"),
        ("TRANSACTION_COST", "-0.1"),
        ("SLIPPAGE", "1"),
    ],
)
def test_shared_strategy_config_rejects_invalid_values(key, value):
    with pytest.raises(ValueError):
        load_strategy_config({key: value})
