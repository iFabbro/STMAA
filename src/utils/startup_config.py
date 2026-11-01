"""Strict, read-only startup validation for the paper-trading process."""
from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass
from typing import Mapping


PAPER_BASE_URL = "https://paper-api.alpaca.markets"
DEFAULT_TICKERS = ("AAPL", "GOOGL", "MSFT", "AMZN", "TSLA", "NVDA", "META")
DEFAULT_INITIAL_CAPITAL = "500000"
DEFAULT_MAX_POSITION_SIZE = "0.20"
DEFAULT_TRANSACTION_COST = "0.001"
DEFAULT_SLIPPAGE = "0.0005"
DEFAULT_VIX_THRESHOLD_HIGH = "30"
DEFAULT_VIX_THRESHOLD_LOW = "15"
DEFAULT_INTERVAL_SECONDS = "300"
DEFAULT_HUB_REFRESH_SECONDS = "30"
_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}
_PLACEHOLDERS = {
    "",
    "your_api_key_here",
    "your_api_secret_here",
    "changeme",
    "change_me",
    "placeholder",
    "replace_me",
}


@dataclass(frozen=True)
class StartupConfig:
    api_key: str
    api_secret: str
    trading_mode: str
    base_url: str
    max_portfolio_exposure: float
    max_drawdown: float
    liquidate_on_drawdown: bool
    tickers: tuple[str, ...]
    initial_capital: float
    max_position_size: float
    transaction_cost: float
    slippage: float
    vix_threshold_high: float
    vix_threshold_low: float
    interval_seconds: int


def _required_secret(name: str, value: str | None) -> str:
    cleaned = (value or "").strip()
    if cleaned.lower() in _PLACEHOLDERS:
        raise ValueError(f"{name} is missing or still contains a placeholder")
    if any(marker in cleaned.lower() for marker in ("your_api_key", "your_api_secret", "replace_with")):
        raise ValueError(f"{name} is still a placeholder")
    return cleaned


def _parse_bool(name: str, value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise ValueError(f"{name} must be one of: true/false, yes/no, on/off, 1/0")


def _risk_float(name: str, value: str | None, default: str) -> float:
    try:
        parsed = float(default if value is None else value.strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a valid number") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"{name} must be finite")
    return parsed


def _positive_int(name: str, value: str | None, default: str) -> int:
    raw = default if value is None else value.strip()
    if not raw.isdigit() or int(raw) <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return int(raw)


def load_tickers(environ: Mapping[str, str] | None = None) -> tuple[str, ...]:
    """Load a validated, ordered ticker universe without requiring broker credentials."""
    env = os.environ if environ is None else environ
    raw = env.get("STMAA_TICKERS")
    values = DEFAULT_TICKERS if raw is None else tuple(item.strip().upper() for item in raw.split(","))
    if not values or any(not item for item in values):
        raise ValueError("STMAA_TICKERS must contain a non-empty comma-separated ticker list")
    if any(re.fullmatch(r"[A-Z][A-Z0-9.-]{0,14}", item) is None for item in values):
        raise ValueError("STMAA_TICKERS contains an invalid ticker symbol")
    if len(set(values)) != len(values):
        raise ValueError("STMAA_TICKERS must not contain duplicates")
    return tuple(values)


def load_hub_refresh_seconds(environ: Mapping[str, str] | None = None) -> int:
    """Load the dashboard refresh interval independently of broker startup validation."""
    env = os.environ if environ is None else environ
    return _positive_int(
        "STMAA_HUB_REFRESH_SECONDS",
        env.get("STMAA_HUB_REFRESH_SECONDS"),
        DEFAULT_HUB_REFRESH_SECONDS,
    )


def load_startup_config(environ: Mapping[str, str] | None = None) -> StartupConfig:
    """Validate all startup settings; this function performs no network calls."""
    env = os.environ if environ is None else environ
    api_key = _required_secret("ALPACA_API_KEY", env.get("ALPACA_API_KEY"))
    api_secret = _required_secret("ALPACA_API_SECRET", env.get("ALPACA_API_SECRET"))

    raw_mode = env.get("STMAA_TRADING_MODE")
    mode = ("paper" if raw_mode is None else raw_mode.strip()).lower()
    if mode != "paper":
        raise ValueError("STMAA_TRADING_MODE must be 'paper'; live trading is disabled by this entry point")

    base_url = (env.get("ALPACA_BASE_URL") or PAPER_BASE_URL).strip().rstrip("/")
    if base_url != PAPER_BASE_URL:
        raise ValueError(f"ALPACA_BASE_URL must be {PAPER_BASE_URL}; live/custom endpoints are not permitted")

    exposure = _risk_float("MAX_PORTFOLIO_EXPOSURE", env.get("MAX_PORTFOLIO_EXPOSURE"), "0.50")
    drawdown = _risk_float("MAX_DRAWDOWN", env.get("MAX_DRAWDOWN"), "0.15")
    if not 0 < exposure <= 1:
        raise ValueError("MAX_PORTFOLIO_EXPOSURE must be in (0, 1]")
    if not 0 < drawdown < 1:
        raise ValueError("MAX_DRAWDOWN must be in (0, 1)")

    liquidate = _parse_bool("LIQUIDATE_ON_DRAWDOWN", env.get("LIQUIDATE_ON_DRAWDOWN"), default=False)
    tickers = load_tickers(env)
    initial_capital = _risk_float("STMAA_INITIAL_CAPITAL", env.get("STMAA_INITIAL_CAPITAL"), DEFAULT_INITIAL_CAPITAL)
    max_position_size = _risk_float("MAX_POSITION_SIZE", env.get("MAX_POSITION_SIZE"), DEFAULT_MAX_POSITION_SIZE)
    transaction_cost = _risk_float("TRANSACTION_COST", env.get("TRANSACTION_COST"), DEFAULT_TRANSACTION_COST)
    slippage = _risk_float("SLIPPAGE", env.get("SLIPPAGE"), DEFAULT_SLIPPAGE)
    vix_high = _risk_float("VIX_THRESHOLD_HIGH", env.get("VIX_THRESHOLD_HIGH"), DEFAULT_VIX_THRESHOLD_HIGH)
    vix_low = _risk_float("VIX_THRESHOLD_LOW", env.get("VIX_THRESHOLD_LOW"), DEFAULT_VIX_THRESHOLD_LOW)
    interval_seconds = _positive_int("STMAA_INTERVAL_SECONDS", env.get("STMAA_INTERVAL_SECONDS"), DEFAULT_INTERVAL_SECONDS)

    if initial_capital <= 0:
        raise ValueError("STMAA_INITIAL_CAPITAL must be positive")
    if not 0 < max_position_size <= exposure:
        raise ValueError("MAX_POSITION_SIZE must be in (0, MAX_PORTFOLIO_EXPOSURE]")
    for name, value in (("TRANSACTION_COST", transaction_cost), ("SLIPPAGE", slippage)):
        if not 0 <= value < 1:
            raise ValueError(f"{name} must be in [0, 1)")
    if vix_low < 0 or vix_high <= vix_low:
        raise ValueError("VIX thresholds must satisfy 0 <= VIX_THRESHOLD_LOW < VIX_THRESHOLD_HIGH")

    return StartupConfig(
        api_key=api_key,
        api_secret=api_secret,
        trading_mode=mode,
        base_url=base_url,
        max_portfolio_exposure=exposure,
        max_drawdown=drawdown,
        liquidate_on_drawdown=liquidate,
        tickers=tickers,
        initial_capital=initial_capital,
        max_position_size=max_position_size,
        transaction_cost=transaction_cost,
        slippage=slippage,
        vix_threshold_high=vix_high,
        vix_threshold_low=vix_low,
        interval_seconds=interval_seconds,
    )


def validate_broker_preflight(executor) -> dict:
    """Require a fresh, identified, numerically valid account snapshot; read-only."""
    if getattr(executor, "paper_trading", None) is not True:
        raise RuntimeError("Broker preflight refused: executor is not explicitly in paper mode")

    status = executor.get_account_status()
    if not isinstance(status, dict) or status.get("account_data_fresh") is not True:
        raise RuntimeError("Broker preflight failed: no fresh account snapshot; refusing to start")

    account_id = status.get("account_id")
    if not isinstance(account_id, str) or not account_id.strip():
        raise RuntimeError("Broker preflight failed: current broker account identity is unverified")

    for field in ("equity", "cash", "buying_power"):
        value = status.get(field)
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"Broker preflight failed: invalid {field}") from exc
        if not math.isfinite(number) or number < 0:
            raise RuntimeError(f"Broker preflight failed: invalid {field}")

    if float(status["equity"]) <= 0:
        raise RuntimeError("Broker preflight failed: account equity must be positive")
    return status
