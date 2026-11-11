"""Deterministic semantic replay for PPO shadow ledgers."""
import pandas_market_calendars as mcal

from src.agents.ppo_shadow_ledger import PPOShadowLedger
from src.agents.ppo_shadow_portfolio import PPOShadowPortfolio


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
    if any(not isinstance(value, (int, float)) for value in values):
        raise ValueError("Shadow decision weights must be numeric")
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
    if metrics.get("session") != session or benchmark_metrics.get("session") != session:
        raise ValueError("Portfolio/benchmark transition session mismatch")
    if benchmark_metrics.get("ticker") != benchmark.get("ticker") or not isinstance(benchmark.get("ticker"), str):
        raise ValueError("Benchmark transition ticker mismatch")
    if (not isinstance(benchmark.get("shares"), (int, float)) or benchmark["shares"] <= 0
            or not isinstance(benchmark.get("last_equity"), (int, float))
            or benchmark["last_equity"] <= 0):
        raise ValueError("Invalid persisted benchmark state")
    if abs(float(metrics.get("equity_close", float("nan"))) - portfolio.last_equity) > 1e-7:
        raise ValueError("Portfolio transition metrics disagree with persisted portfolio state")
    if abs(float(benchmark_metrics.get("equity_close", float("nan"))) - benchmark["last_equity"]) > 1e-7:
        raise ValueError("Benchmark metrics disagree with persisted benchmark state")
    return portfolio, benchmark, benchmark_metrics


def replay_shadow_ledger(path):
    records = PPOShadowLedger(path).read()
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
            portfolio, benchmark, metrics = _validate_transition(event, pending, config)
            benchmark_metrics.append(metrics)
            last_transition_session = event["session"]
            pending = None
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
