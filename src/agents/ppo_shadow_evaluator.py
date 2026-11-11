"""Fail-closed, one-shot evaluator for the prospective PPO shadow holdout."""
import hashlib
import json
import math
import os
import tempfile
from pathlib import Path

import numpy as np

from src.agents.ppo_shadow_ledger import PPOShadowLedger
from src.agents.ppo_shadow_replay import replay_shadow_ledger
from src.agents.ppo_shadow_snapshots import PPOShadowSnapshotStore


HOLDOUT_RETURN_SESSIONS = 252
HOLDOUT_DECISION_COUNT = HOLDOUT_RETURN_SESSIONS + 1


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _metrics(equity_values, returns):
    equity = np.asarray(equity_values, dtype=float)
    daily = np.asarray(returns, dtype=float)
    if (len(equity) != HOLDOUT_RETURN_SESSIONS + 1 or len(daily) != HOLDOUT_RETURN_SESSIONS
            or not np.isfinite(equity).all() or not np.isfinite(daily).all()
            or (equity <= 0).any()):
        raise ValueError("Holdout series is incomplete or contains invalid values")
    peaks = np.maximum.accumulate(equity)
    drawdowns = equity / peaks - 1.0
    volatility = float(np.std(daily, ddof=1) * math.sqrt(252))
    downside_deviation = float(math.sqrt(float(np.mean(np.square(np.minimum(daily, 0.0)))) * 252))
    mean_daily = float(np.mean(daily))
    return {
        "initial_equity": float(equity[0]),
        "final_equity": float(equity[-1]),
        "cumulative_return": float(equity[-1] / equity[0] - 1.0),
        "max_drawdown": float(np.min(drawdowns)),
        "annualized_volatility": volatility,
        "sharpe_ratio": float(mean_daily / np.std(daily, ddof=1) * math.sqrt(252))
            if np.std(daily, ddof=1) > 0 else None,
        "sortino_ratio": float(mean_daily * 252 / downside_deviation)
            if downside_deviation > 0 else None,
    }


def evaluate_shadow_holdout(ledger_path, report_path=None):
    """Write exactly one final report after 252 return-bearing NYSE sessions.

    The first decision is a warm-up signal; each of the following 252 decisions
    must be paired with a next-session Open-to-Close transition.
    """
    ledger_path = Path(ledger_path)
    output = Path(report_path) if report_path is not None else ledger_path.with_name(
        ledger_path.stem + ".final_report.json"
    )
    records = PPOShadowLedger(ledger_path).read()
    replay = replay_shadow_ledger(ledger_path)
    if replay["transition_count"] != HOLDOUT_RETURN_SESSIONS:
        raise ValueError(
            f"Final evaluation requires exactly {HOLDOUT_RETURN_SESSIONS} completed return-bearing sessions; "
            f"found {replay['transition_count']}"
        )
    if replay["decision_count"] != HOLDOUT_DECISION_COUNT or replay["pending_decision"] is not None:
        raise ValueError(
            f"Final evaluation requires {HOLDOUT_DECISION_COUNT} decisions (one warm-up plus "
            f"{HOLDOUT_RETURN_SESSIONS} return sessions) and no pending decision"
        )
    events = [record["payload"] for record in records[1:]]
    decisions = [event for event in events if event.get("type") == "decision"]
    transitions = [event for event in events if event.get("type") == "portfolio_transition"]
    if len(decisions) != HOLDOUT_DECISION_COUNT or len(transitions) != HOLDOUT_RETURN_SESSIONS:
        raise ValueError("Ledger event counts changed during final evaluation")
    snapshots = PPOShadowSnapshotStore(ledger_path)
    evidence = []
    for event in decisions:
        refs = event.get("input_snapshots")
        if not isinstance(refs, dict) or set(refs) != {"model_inference", "benchmark"}:
            raise ValueError("A decision is missing its complete immutable inference-input snapshots")
        for role in ("model_inference", "benchmark"):
            reference = refs[role]
            if reference.get("role") != role:
                raise ValueError("Decision snapshot role mismatch")
            payload = snapshots.verify(reference)
            if payload.get("market_data_fingerprint") != event.get(
                    "market_data_fingerprint" if role == "model_inference" else "benchmark_data_fingerprint"):
                raise ValueError("Decision snapshot fingerprint differs from the ledger event")
            if role == "model_inference":
                context = payload.get("inference_context")
                current_weights = event.get("current_weights")
                if (not isinstance(context, dict) or context.get("inference_contract")
                        != "ppo_predict_weights_current_weights_v1"
                        or not isinstance(current_weights, dict)
                        or context.get("tickers") != list(records[0]["payload"]["config"]["tickers"])
                        or context.get("current_weights") != [
                            float(current_weights[t]) for t in records[0]["payload"]["config"]["tickers"]
                        ]):
                    raise ValueError("Persisted inference context differs from the decision's current weights")
            evidence.append({"session": event["session"], "role": role, "sha256": reference["sha256"]})
    for event in transitions:
        refs = event.get("input_snapshots")
        if not isinstance(refs, dict) or set(refs) != {"model_inference", "benchmark"}:
            raise ValueError("A transition is missing its complete market-data snapshots")
        for role in ("model_inference", "benchmark"):
            payload = snapshots.verify(refs[role])
            expected_fingerprint = event.get(
                "market_data_fingerprint" if role == "model_inference" else "benchmark_data_fingerprint"
            )
            if payload.get("market_data_fingerprint") != expected_fingerprint:
                raise ValueError("Transition snapshot fingerprint differs from the ledger event")
    initial_capital = float(records[0]["payload"]["config"]["initial_capital"])
    strategy_equity = [initial_capital] + [float(event["metrics"]["equity_close"]) for event in transitions]
    strategy_returns = [float(event["metrics"]["step_return"]) for event in transitions]
    benchmark_equity = [initial_capital] + [float(event["benchmark_metrics"]["equity_close"]) for event in transitions]
    benchmark_returns = [float(event["benchmark_metrics"]["step_return"]) for event in transitions]
    strategy = _metrics(strategy_equity, strategy_returns)
    benchmark = _metrics(benchmark_equity, benchmark_returns)
    report = {
        "schema": "stmaa_ppo_shadow_final_evaluation_v1",
        "status": "FINAL",
        "protocol": {
            "return_bearing_sessions": HOLDOUT_RETURN_SESSIONS,
            "decision_count": HOLDOUT_DECISION_COUNT,
            "warmup_decisions": 1,
            "decision_rule": "completed_session_close_signal_next_nyse_session_open_fill",
            "return_rule": "next_session_open_to_close",
            "intermediate_performance_evaluation": "prohibited",
            "report_policy": "single_create_no_overwrite",
        },
        "start_decision_session": decisions[0]["session"],
        "first_return_session": transitions[0]["session"],
        "final_return_session": transitions[-1]["session"],
        "strategy": strategy,
        "benchmark_spy_buy_and_hold": benchmark,
        "acceptance": {
            "positive_net_return": strategy["cumulative_return"] > 0.0,
            "max_drawdown_within_15pct": strategy["max_drawdown"] >= -0.15,
            "benchmark_outperformance_required": False,
            "overall_pass": strategy["cumulative_return"] > 0.0
                and strategy["max_drawdown"] >= -0.15,
        },
        "ledger": {
            "sha256_head": replay["head_hash"],
            "record_count": replay["record_count"],
            "decision_count": replay["decision_count"],
            "transition_count": replay["transition_count"],
        },
        "input_snapshot_evidence": {
            "verified_references": len(evidence),
            "sha256": hashlib.sha256(_canonical(evidence)).hexdigest(),
        },
    }
    raw = _canonical(report) + b"\n"
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() or output.is_symlink():
        raise FileExistsError("Final evaluation report already exists; it is never overwritten")
    fd, temporary_name = tempfile.mkstemp(prefix=f".{output.name}.", dir=output.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        # Hard-link publication is atomic and fails if another evaluator won the race.
        os.link(temporary_name, output)
        directory_fd = os.open(output.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except FileExistsError:
        raise FileExistsError("Final evaluation report already exists; it is never overwritten")
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
    return report
