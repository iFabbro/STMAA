"""Deterministic semantic replay for PPO shadow ledgers."""
import pandas_market_calendars as mcal

from src.agents.ppo_shadow_ledger import PPOShadowLedger
from src.agents.ppo_shadow_portfolio import PPOShadowPortfolio


def _next_nyse_session(previous, current):
    schedule = mcal.get_calendar("NYSE").schedule(start_date=previous, end_date=current)
    sessions = [value.date().isoformat() for value in schedule.index]
    return len(sessions) == 2 and sessions == [previous, current]


def _validate_decision(event):
    session = event.get("session")
    weights = event.get("target_weights")
    if not isinstance(session, str) or not isinstance(weights, dict) or not weights:
        raise ValueError("Malformed shadow decision event")
    if any(not isinstance(value, (int, float)) for value in weights.values()):
        raise ValueError("Shadow decision weights must be numeric")
    return session


def _validate_transition(event, pending):
    session = event.get("session")
    if not isinstance(session, str) or event.get("signal_session") != pending["session"]:
        raise ValueError("Shadow transition does not consume the immediately pending decision")
    if not _next_nyse_session(pending["session"], session):
        raise ValueError("Shadow transition is not on the next NYSE session")
    portfolio = PPOShadowPortfolio.from_state(event["portfolio"])
    benchmark = event.get("benchmark")
    metrics = event.get("metrics")
    benchmark_metrics = event.get("benchmark_metrics")
    if not isinstance(benchmark, dict) or not isinstance(metrics, dict) or not isinstance(benchmark_metrics, dict):
        raise ValueError("Shadow transition is missing portfolio or benchmark metrics")
    if benchmark_metrics.get("session") != session:
        raise ValueError("Benchmark transition session does not match portfolio transition")
    if benchmark_metrics.get("ticker") != benchmark.get("ticker"):
        raise ValueError("Benchmark transition ticker mismatch")
    return portfolio, benchmark, benchmark_metrics


def replay_shadow_ledger(path):
    records = PPOShadowLedger(path).read()
    if not records:
        raise ValueError("Shadow ledger is empty")
    decisions, portfolio = [], None
    benchmark, benchmark_metrics, provenance = None, [], None
    pending, last_transition_session = None, None
    for index, record in enumerate(records):
        event = record["payload"]
        kind = event.get("type")
        if index == 0:
            if kind != "run_metadata" or event.get("schema") != "ppo_shadow_v1":
                raise ValueError("Shadow ledger has no valid run metadata header")
            if not isinstance(event.get("provenance"), dict) or not isinstance(event.get("config"), dict):
                raise ValueError("Shadow ledger metadata is incomplete")
            provenance = event["provenance"]
            continue
        if kind == "decision":
            session = _validate_decision(event)
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
            portfolio, benchmark, metrics = _validate_transition(event, pending)
            benchmark_metrics.append(metrics)
            last_transition_session = event["session"]
            pending = None
        else:
            raise ValueError(f"Unsupported ledger event type: {kind!r}")
    if len(decisions) != len(benchmark_metrics) and len(decisions) != len(benchmark_metrics) + 1:
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
