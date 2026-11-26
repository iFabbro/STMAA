"""Deterministic semantic replay for PPO shadow ledgers."""
import math

import pandas as pd
import pandas_market_calendars as mcal

from src.agents.ppo_shadow_ledger import PPOShadowLedger
from src.agents.ppo_shadow_portfolio import PPOShadowPortfolio
from src.agents.ppo_shadow_snapshots import PPOShadowSnapshotStore
from src.data.market_data_contract import validate_market_data_contract


def _is_finite_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, TypeError):
        return False


def _validate_finite_numeric_values(value, context):
    if isinstance(value, dict):
        for item in value.values():
            _validate_finite_numeric_values(item, context)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _validate_finite_numeric_values(item, context)
    elif isinstance(value, (int, float)) and not _is_finite_number(value):
        raise ValueError(f"{context} contains a non-finite numeric value")


def _is_nyse_session(value):
    schedule = mcal.get_calendar("NYSE").schedule(start_date=value, end_date=value)
    return len(schedule) == 1


def _next_nyse_session(previous, current):
    schedule = mcal.get_calendar("NYSE").schedule(start_date=previous, end_date=current)
    sessions = [value.date().isoformat() for value in schedule.index]
    return len(sessions) == 2 and sessions == [previous, current]


def _validate_decision(event, config):
    session, weights = event.get("session"), event.get("target_weights")
    tickers = config.get("tickers")
    if (not isinstance(session, str) or not isinstance(weights, dict)
            or not isinstance(tickers, list) or set(weights) != set(tickers)
            or not _is_nyse_session(session)):
        raise ValueError("Malformed shadow decision event or non-session date")
    values = list(weights.values())
    if any(not _is_finite_number(value) for value in values):
        raise ValueError("Shadow decision weights must be finite numeric values")
    if (any(value < 0 or value > config["max_position_size"] + 1e-8 for value in values)
            or sum(values) > config["max_gross_exposure"] + 1e-8):
        raise ValueError("Shadow decision violates frozen allocation caps")
    return session


def _validate_transition(event, pending, config):
    session = event.get("session")
    if not isinstance(session, str) or event.get("signal_session") != pending["session"]:
        raise ValueError("Shadow transition does not consume the immediately pending decision")
    if not _next_nyse_session(pending["session"], session):
        raise ValueError("Shadow transition is not on the next NYSE session")
    portfolio = PPOShadowPortfolio.from_state(event["portfolio"])
    if tuple(portfolio.tickers) != tuple(config["tickers"]):
        raise ValueError("Transition portfolio tickers differ from frozen run configuration")
    benchmark = event.get("benchmark")
    metrics = event.get("metrics")
    benchmark_metrics = event.get("benchmark_metrics")
    if not isinstance(benchmark, dict) or not isinstance(metrics, dict) or not isinstance(benchmark_metrics, dict):
        raise ValueError("Shadow transition is missing portfolio or benchmark metrics")
    _validate_finite_numeric_values(metrics, "Portfolio transition metrics")
    _validate_finite_numeric_values(benchmark_metrics, "Benchmark transition metrics")
    if metrics.get("session") != session or benchmark_metrics.get("session") != session:
        raise ValueError("Portfolio/benchmark transition session mismatch")
    if benchmark_metrics.get("ticker") != benchmark.get("ticker") or not isinstance(benchmark.get("ticker"), str):
        raise ValueError("Benchmark transition ticker mismatch")
    if (not _is_finite_number(benchmark.get("shares")) or benchmark["shares"] <= 0
            or not _is_finite_number(benchmark.get("last_equity"))
            or benchmark["last_equity"] <= 0):
        raise ValueError("Invalid persisted benchmark state")
    if (not _is_finite_number(metrics.get("equity_close"))
            or abs(float(metrics["equity_close"]) - portfolio.last_equity) > 1e-7):
        raise ValueError("Portfolio transition metrics disagree with persisted portfolio state")
    if (not _is_finite_number(benchmark_metrics.get("equity_close"))
            or abs(float(benchmark_metrics["equity_close"]) - benchmark["last_equity"]) > 1e-7):
        raise ValueError("Benchmark metrics disagree with persisted benchmark state")
    return portfolio, benchmark, benchmark_metrics


def _assert_number(actual, expected, label):
    if (not _is_finite_number(actual) or not _is_finite_number(expected)
            or not math.isclose(float(actual), float(expected), rel_tol=1e-9, abs_tol=1e-7)):
        raise ValueError(f"Replay accounting mismatch for {label}")


def _snapshot_session_row(store, reference, role, session, expected_fingerprint):
    if not isinstance(reference, dict) or reference.get("role") != role:
        raise ValueError(f"Replay accounting snapshot role mismatch: {role}")
    payload = store.verify(reference)
    validate_market_data_contract(payload.get("market_data_contract"))
    if payload.get("market_data_fingerprint") != expected_fingerprint:
        raise ValueError(f"Replay accounting snapshot fingerprint mismatch: {role}")
    frame = payload.get("dataframe")
    if not isinstance(frame, dict):
        raise ValueError(f"Replay accounting snapshot is missing its DataFrame: {role}")
    columns, index, data = frame.get("columns"), frame.get("index"), frame.get("data")
    if (not isinstance(columns, list) or not columns or len(set(columns)) != len(columns)
            or not isinstance(index, list) or not index
            or not isinstance(data, list) or len(data) != len(index)
            or any(not isinstance(row, list) or len(row) != len(columns) for row in data)):
        raise ValueError(f"Replay accounting snapshot DataFrame shape is invalid: {role}")
    dates = [pd.Timestamp(value).date().isoformat() for value in index]
    if len(set(dates)) != len(dates) or dates != sorted(dates):
        raise ValueError(f"Replay accounting snapshot sessions are invalid: {role}")
    matches = [position for position, value in enumerate(dates) if value == session]
    if len(matches) != 1:
        raise ValueError(f"Replay accounting snapshot does not contain exactly one {session} row: {role}")
    row = data[matches[0]]
    prices = {}
    for column, value in zip(columns, row, strict=True):
        if not _is_finite_number(value):
            raise ValueError(f"Replay accounting snapshot contains non-finite data: {role}")
        prices[column] = float(value)
    return prices


def _recompute_transition(event, pending, config, previous_portfolio, previous_benchmark, snapshot_store):
    if snapshot_store is None:
        raise ValueError("Semantic replay requires immutable transition input snapshots")
    references = event.get("input_snapshots")
    if not isinstance(references, dict) or set(references) != {"model_inference", "benchmark"}:
        raise ValueError("Semantic replay requires both model and benchmark input snapshots")
    model_row = _snapshot_session_row(
        snapshot_store, references["model_inference"], "model_inference",
        event["session"], event.get("market_data_fingerprint"),
    )
    benchmark_row = _snapshot_session_row(
        snapshot_store, references["benchmark"], "benchmark",
        event["session"], event.get("benchmark_data_fingerprint"),
    )
    tickers = tuple(config["tickers"])
    open_prices = {ticker: model_row[f"Open_{ticker}"] for ticker in tickers}
    close_prices = {ticker: model_row[f"Close_{ticker}"] for ticker in tickers}
    benchmark_ticker = config.get("benchmark_ticker", "SPY")
    benchmark_open = benchmark_row[f"Open_{benchmark_ticker}"]
    benchmark_close = benchmark_row[f"Close_{benchmark_ticker}"]

    if previous_portfolio is None:
        previous_portfolio = PPOShadowPortfolio(
            tickers,
            initial_capital=config["initial_capital"],
            transaction_cost=config["transaction_cost"],
            slippage=config["slippage"],
            max_position_size=config["max_position_size"],
            max_gross_exposure=config["max_gross_exposure"],
        )
    else:
        previous_portfolio = PPOShadowPortfolio.from_state(previous_portfolio.to_state())
    expected_metrics = previous_portfolio.rebalance_at_open(
        pending["target_weights"], open_prices, close_prices, event["session"]
    )
    expected_state = previous_portfolio.to_state()
    persisted_state = event["portfolio"]
    if persisted_state.get("tickers") != expected_state["tickers"]:
        raise ValueError("Replay accounting mismatch for portfolio tickers")
    for key in ("initial_capital", "transaction_cost", "slippage",
                "max_position_size", "max_gross_exposure", "cash", "last_equity"):
        _assert_number(persisted_state.get(key), expected_state[key], f"portfolio state {key}")
    for ticker in tickers:
        _assert_number(
            persisted_state.get("shares", {}).get(ticker),
            expected_state["shares"][ticker],
            f"portfolio state shares.{ticker}",
        )
    stored_metrics = event["metrics"]
    for key in ("equity_open", "equity_close", "step_return", "turnover",
                "turnover_notional", "transaction_cost", "cash"):
        _assert_number(stored_metrics.get(key), expected_metrics[key], f"portfolio metrics.{key}")
    for ticker in tickers:
        _assert_number(
            stored_metrics.get("shares", {}).get(ticker),
            expected_metrics["shares"][ticker],
            f"portfolio metrics shares.{ticker}",
        )
        _assert_number(
            stored_metrics.get("weights_at_close", {}).get(ticker),
            expected_metrics["weights_at_close"][ticker],
            f"portfolio metrics weights_at_close.{ticker}",
        )

    if previous_benchmark is None:
        benchmark_shares = float(config["initial_capital"]) / benchmark_open
        benchmark_previous_equity = float(config["initial_capital"])
        benchmark_start_session = event["session"]
    else:
        if previous_benchmark.get("ticker") != benchmark_ticker:
            raise ValueError("Replay accounting benchmark ticker changed between transitions")
        benchmark_shares = float(previous_benchmark["shares"])
        benchmark_previous_equity = float(previous_benchmark["last_equity"])
        benchmark_start_session = previous_benchmark["start_session"]
    benchmark_equity_open = benchmark_shares * benchmark_open
    benchmark_equity_close = benchmark_shares * benchmark_close
    expected_benchmark_metrics = {
        "session": event["session"],
        "ticker": benchmark_ticker,
        "equity_open": benchmark_equity_open,
        "equity_close": benchmark_equity_close,
        "step_return": benchmark_equity_close / benchmark_previous_equity - 1.0,
    }
    expected_benchmark = {
        "ticker": benchmark_ticker,
        "shares": benchmark_shares,
        "last_equity": benchmark_equity_close,
        "start_session": benchmark_start_session,
    }
    stored_benchmark = event["benchmark"]
    if stored_benchmark.get("ticker") != benchmark_ticker:
        raise ValueError("Replay accounting mismatch for benchmark ticker")
    for key in ("shares", "last_equity"):
        _assert_number(stored_benchmark.get(key), expected_benchmark[key], f"benchmark state {key}")
    if stored_benchmark.get("start_session") != benchmark_start_session:
        raise ValueError("Replay accounting mismatch for benchmark start_session")
    stored_benchmark_metrics = event["benchmark_metrics"]
    for key in ("equity_open", "equity_close", "step_return"):
        _assert_number(
            stored_benchmark_metrics.get(key), expected_benchmark_metrics[key],
            f"benchmark metrics.{key}",
        )
    return previous_portfolio, expected_benchmark


def replay_shadow_records(records, snapshot_store=None):
    if not records:
        raise ValueError("Shadow ledger is empty")
    decisions, portfolio = [], None
    benchmark, benchmark_metrics, provenance = None, [], None
    pending, last_transition_session = None, None
    config = None
    for index, record in enumerate(records):
        event = record["payload"]
        kind = event.get("type")
        if index == 0:
            if kind != "run_metadata" or event.get("schema") != "ppo_shadow_v1":
                raise ValueError("Shadow ledger has no valid run metadata header")
            config = event.get("config")
            provenance = event.get("provenance")
            if (not isinstance(config, dict) or not isinstance(provenance, dict)
                    or not isinstance(config.get("tickers"), list)
                    or not config["tickers"]):
                raise ValueError("Shadow ledger metadata is incomplete")
            continue
        if kind == "decision":
            session = _validate_decision(event, config)
            if pending is not None:
                raise ValueError("A second decision appeared before the pending decision was transitioned")
            if last_transition_session is not None and session != last_transition_session:
                raise ValueError("Decision session does not match the preceding transition session")
            if decisions and session <= decisions[-1]["session"]:
                raise ValueError("Shadow decision sessions are not strictly increasing")
            pending = event
            decisions.append(event)
        elif kind == "portfolio_transition":
            if pending is None:
                raise ValueError("Portfolio transition has no preceding pending decision")
            persisted_portfolio, persisted_benchmark, metrics = _validate_transition(event, pending, config)
            portfolio, benchmark = _recompute_transition(
                event, pending, config, portfolio, benchmark, snapshot_store
            )
            benchmark_metrics.append(metrics)
            last_transition_session = event["session"]
            pending = None
            if (portfolio.last_equity <= 0
                    or abs(portfolio.last_equity - persisted_portfolio.last_equity) > 1e-7
                    or abs(benchmark["last_equity"] - persisted_benchmark["last_equity"]) > 1e-7):
                raise ValueError("Replay accounting state differs from persisted transition state")
        else:
            raise ValueError(f"Unsupported ledger event type: {kind!r}")
    if len(decisions) not in (len(benchmark_metrics), len(benchmark_metrics) + 1):
        raise ValueError("Decision/transition counts violate the shadow state machine")
    return {
        "record_count": len(records),
        "decision_count": len(decisions),
        "transition_count": len(benchmark_metrics),
        "last_decision": decisions[-1] if decisions else None,
        "pending_decision": pending,
        "portfolio": portfolio.to_state() if portfolio else None,
        "benchmark": benchmark,
        "benchmark_metrics": benchmark_metrics,
        "provenance": provenance,
        "head_hash": records[-1]["hash"],
    }


def replay_shadow_ledger(path):
    """Read, integrity-check, and independently recompute a shadow ledger."""
    ledger = PPOShadowLedger(path)
    snapshots = PPOShadowSnapshotStore(path, create=False)
    return replay_shadow_records(ledger.read(), snapshots)
