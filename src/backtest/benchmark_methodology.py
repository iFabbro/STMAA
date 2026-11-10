"""Methodology checks for PPO/benchmark execution-data parity."""
from __future__ import annotations

from typing import Any

import pandas as pd

try:
    from src.data.market_data_contract import market_data_fingerprint
except ModuleNotFoundError:
    from data.market_data_contract import market_data_fingerprint


def execution_price_panel(frame: pd.DataFrame, tickers: list[str]) -> pd.DataFrame:
    """Return the canonical ordered Open/Close panel used for execution checks."""
    if not isinstance(frame, pd.DataFrame) or not isinstance(tickers, list) or not tickers:
        raise ValueError("frame and a non-empty ticker list are required")
    if len(set(tickers)) != len(tickers):
        raise ValueError("tickers must be unique")
    # Historical single-ticker feature frames keep raw OHLCV columns
    # unsuffixed (Open/High/Low/Close/Volume) while derived close features
    # may be ticker-suffixed. Normalize only this unambiguous one-ticker case.
    normalized = frame
    if len(tickers) == 1:
        ticker = tickers[0]
        aliases = {
            f"Open_{ticker}": "Open",
            f"Close_{ticker}": "Close",
        }
        if any(target not in normalized.columns and source in normalized.columns
               for target, source in aliases.items()):
            normalized = normalized.copy()
            for target, source in aliases.items():
                if target not in normalized.columns and source in normalized.columns:
                    normalized[target] = normalized[source]
    columns = [column for ticker in tickers for column in (f"Open_{ticker}", f"Close_{ticker}")]
    missing = [column for column in columns if column not in normalized.columns]
    if missing:
        raise ValueError(f"execution price panel is missing columns: {missing}")
    panel = normalized.loc[:, columns].copy()
    if not isinstance(panel.index, pd.DatetimeIndex) or panel.index.hasnans:
        raise ValueError("execution price panel requires a valid DatetimeIndex")
    if not panel.index.is_unique or not panel.index.is_monotonic_increasing:
        raise ValueError("execution price timestamps must be unique and chronological")
    try:
        values = panel.to_numpy(dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError("execution prices must be numeric") from exc
    if not pd.notna(values).all() or (values <= 0).any():
        raise ValueError("execution prices must be finite and strictly positive")
    return panel


def execution_data_fingerprint(frame: pd.DataFrame, tickers: list[str]) -> str:
    """SHA-256 fingerprint of ordered dates and exact execution Open/Close values."""
    return market_data_fingerprint(execution_price_panel(frame, tickers))


def audit_benchmark_methodology(
    *,
    ppo_fold: dict[str, Any],
    benchmark_fold: dict[str, Any],
    ppo_parameters: dict[str, Any],
    benchmark_parameters: dict[str, Any],
) -> dict[str, Any]:
    """Fail closed on known mismatches; report absent historical evidence explicitly."""
    checks: dict[str, str] = {}
    expected = ppo_fold.get("execution_data_fingerprint")
    actual = benchmark_fold.get("execution_data_fingerprint")
    if not isinstance(expected, str) or not isinstance(actual, str):
        checks["execution_data_identity"] = "INCONCLUSIVE"
    else:
        checks["execution_data_identity"] = (
            "PASS" if expected == actual else "FAIL"
        )

    for key in ("initial_capital", "transaction_cost", "slippage"):
        left, right = ppo_parameters.get(key), benchmark_parameters.get(key)
        checks[f"parameter_{key}"] = (
            "INCONCLUSIVE" if left is None or right is None
            else "PASS" if float(left) == float(right)
            else "FAIL"
        )

    checks["entry_execution"] = (
        "PASS"
        if ppo_fold.get("entry_execution") == "next_bar_open"
        and benchmark_fold.get("entry_execution") == "next_bar_open"
        else "INCONCLUSIVE"
    )
    statuses = set(checks.values())
    overall = "FAIL" if "FAIL" in statuses else (
        "INCONCLUSIVE" if "INCONCLUSIVE" in statuses else "PASS"
    )
    return {"status": overall, "checks": checks}
