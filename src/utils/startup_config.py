"""Strict, read-only startup validation for the paper-trading process."""
from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass
from typing import Mapping


PAPER_BASE_URL = "https://paper-api.alpaca.markets"
DEFAULT_TICKERS = ("AAPL", "GOOGL", "MSFT", "AMZN", "TSLA", "NVDA", "META")
DEFAULT_INITIAL_CAPITAL = "100000"
DEFAULT_MAX_PORTFOLIO_EXPOSURE = "0.50"
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
class StrategyConfig:
    tickers: tuple[str, ...]
    initial_capital: float
    max_position_size: float
    max_gross_exposure: float
    transaction_cost: float
    slippage: float


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


def load_strategy_config(
    environ: Mapping[str, str] | None = None,
    overrides: Mapping[str, object] | None = None,
) -> StrategyConfig:
    """Load the shared, credential-free strategy settings used by training and runtime.

    Explicit CLI overrides take precedence over environment values. All values
    pass through the same validation used by the paper-runtime startup config.
    """
    env = dict(os.environ if environ is None else environ)
    allowed = {
        "STMAA_TICKERS",
        "STMAA_INITIAL_CAPITAL",
        "MAX_PORTFOLIO_EXPOSURE",
        "MAX_POSITION_SIZE",
        "TRANSACTION_COST",
        "SLIPPAGE",
    }
    for name, value in (overrides or {}).items():
        if name not in allowed:
            raise ValueError(f"Unsupported strategy configuration override: {name}")
        if name == "STMAA_TICKERS" and isinstance(value, (list, tuple)):
            value = ",".join(str(item) for item in value)
        env[name] = str(value)

    tickers = load_tickers(env)
    initial_capital = _risk_float(
        "STMAA_INITIAL_CAPITAL", env.get("STMAA_INITIAL_CAPITAL"), DEFAULT_INITIAL_CAPITAL
    )
    exposure = _risk_float(
        "MAX_PORTFOLIO_EXPOSURE", env.get("MAX_PORTFOLIO_EXPOSURE"),
        DEFAULT_MAX_PORTFOLIO_EXPOSURE,
    )
    max_position_size = _risk_float(
        "MAX_POSITION_SIZE", env.get("MAX_POSITION_SIZE"), DEFAULT_MAX_POSITION_SIZE
    )
    transaction_cost = _risk_float(
        "TRANSACTION_COST", env.get("TRANSACTION_COST"), DEFAULT_TRANSACTION_COST
    )
    slippage = _risk_float("SLIPPAGE", env.get("SLIPPAGE"), DEFAULT_SLIPPAGE)

    if initial_capital <= 0:
        raise ValueError("STMAA_INITIAL_CAPITAL must be positive")
    if not 0 < exposure <= 1:
        raise ValueError("MAX_PORTFOLIO_EXPOSURE must be in (0, 1]")
    if not 0 < max_position_size <= exposure:
        raise ValueError("MAX_POSITION_SIZE must be in (0, MAX_PORTFOLIO_EXPOSURE]")
    for name, value in (("TRANSACTION_COST", transaction_cost), ("SLIPPAGE", slippage)):
        if not 0 <= value < 1:
            raise ValueError(f"{name} must be in [0, 1)")

    return StrategyConfig(
        tickers=tickers,
        initial_capital=initial_capital,
        max_position_size=max_position_size,
        max_gross_exposure=exposure,
        transaction_cost=transaction_cost,
        slippage=slippage,
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

    strategy = load_strategy_config(env)
    exposure = strategy.max_gross_exposure
    drawdown = _risk_float("MAX_DRAWDOWN", env.get("MAX_DRAWDOWN"), "0.15")
    if not 0 < drawdown < 1:
        raise ValueError("MAX_DRAWDOWN must be in (0, 1)")

    liquidate = _parse_bool("LIQUIDATE_ON_DRAWDOWN", env.get("LIQUIDATE_ON_DRAWDOWN"), default=False)
    vix_high = _risk_float("VIX_THRESHOLD_HIGH", env.get("VIX_THRESHOLD_HIGH"), DEFAULT_VIX_THRESHOLD_HIGH)
    vix_low = _risk_float("VIX_THRESHOLD_LOW", env.get("VIX_THRESHOLD_LOW"), DEFAULT_VIX_THRESHOLD_LOW)
    interval_seconds = _positive_int("STMAA_INTERVAL_SECONDS", env.get("STMAA_INTERVAL_SECONDS"), DEFAULT_INTERVAL_SECONDS)
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
        tickers=strategy.tickers,
        initial_capital=strategy.initial_capital,
        max_position_size=strategy.max_position_size,
        transaction_cost=strategy.transaction_cost,
        slippage=strategy.slippage,
        vix_threshold_high=vix_high,
        vix_threshold_low=vix_low,
        interval_seconds=interval_seconds,
    )


def validate_ppo_preflight(agent) -> None:
    """Require every explicit PPO lifecycle state before an operational cycle."""
    if agent is None:
        raise RuntimeError(
            "PPO startup preflight failed: PPO agent is unavailable; "
            "install the declared ML dependencies before normal operation"
        )
    lifecycle = getattr(agent, "lifecycle_status", None)
    required_states = (
        "ppo_initialized", "model_discovered", "model_loaded",
        "model_validated", "inference_available",
    )
    if not isinstance(lifecycle, dict) or any(
        lifecycle.get(state) is not True for state in required_states
    ):
        states = lifecycle if isinstance(lifecycle, dict) else "unreported"
        raise RuntimeError(
            "PPO startup preflight failed: model lifecycle is not fully ready "
            f"(required={required_states}, actual={states}); train and validate a "
            "compatible artifact with train.py before normal operation"
        )
    model = getattr(agent, "model", None)
    if model is None or not callable(getattr(model, "predict", None)):
        raise RuntimeError(
            "PPO startup preflight failed: validated inference model is unavailable"
        )


def validate_broker_preflight(executor) -> dict:
    """Build a read-only Paper account/positions/orders/exposure preflight report."""
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

    # This reconciliation also recovers persisted OPG batches, using broker reads
    # only. It may update local recovery metadata, never broker orders.
    reconcile = getattr(executor, "reconcile_broker_state_read_only", None)
    positions_reader = getattr(executor, "get_positions", None)
    open_orders_reader = getattr(executor, "_get_open_orders", None)
    exposure_reader = getattr(executor, "_get_aggregate_gross_notional", None)
    if not all(callable(method) for method in (reconcile, positions_reader, open_orders_reader, exposure_reader)):
        raise RuntimeError("Broker preflight failed: read-only positions/orders/exposure diagnostics are unavailable")

    reconciliation = reconcile()
    if not isinstance(reconciliation, dict) or reconciliation.get("read_only") is not True:
        raise RuntimeError("Broker preflight failed: broker reconciliation report is invalid")

    positions = positions_reader()
    if not isinstance(positions, dict):
        raise RuntimeError("Broker preflight failed: broker positions snapshot is invalid")
    open_orders = open_orders_reader()
    if not isinstance(open_orders, (list, tuple)):
        raise RuntimeError("Broker preflight failed: broker open-orders snapshot is invalid")

    try:
        gross_notional, pending_buy_notional = exposure_reader(positions, open_orders)
        gross_notional = float(gross_notional)
        pending_buy_notional = float(pending_buy_notional)
    except Exception as exc:
        raise RuntimeError("Broker preflight failed: aggregate gross exposure cannot be verified") from exc

    if (not math.isfinite(gross_notional) or gross_notional < 0
            or not math.isfinite(pending_buy_notional) or pending_buy_notional < 0):
        raise RuntimeError("Broker preflight failed: aggregate exposure values are invalid")

    from src.execution.notional_cap import MAX_GROSS_NOTIONAL_USD

    cap_usd = float(MAX_GROSS_NOTIONAL_USD)
    cap_compliant = gross_notional <= cap_usd + 1e-8
    safe_for_new_decision = reconciliation.get("safe_for_new_decision") is True and cap_compliant
    status["preflight_report"] = {
        "read_only": True,
        "account_id": account_id.strip(),
        "positions": positions,
        "position_count": len(positions),
        "open_order_count": len(open_orders),
        "gross_notional_usd": gross_notional,
        "pending_buy_notional_usd": pending_buy_notional,
        "max_gross_notional_usd": cap_usd,
        "cap_compliant": cap_compliant,
        "reconciliation": reconciliation,
        "safe_for_new_decision": safe_for_new_decision,
    }
    return status
