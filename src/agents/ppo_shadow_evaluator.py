"""Fail-closed, one-shot evaluator for the prospective PPO shadow holdout."""
import hashlib
import json
import math
import os
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from src.agents.ppo_shadow_ledger import PPOShadowLedger
from src.agents.ppo_shadow_replay import replay_shadow_records
from src.agents.ppo_shadow_snapshots import PPOShadowSnapshotStore
from src.data.market_data_contract import validate_market_data_contract


HOLDOUT_RETURN_SESSIONS = 252
HOLDOUT_DECISION_COUNT = HOLDOUT_RETURN_SESSIONS


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


def _verify_snapshot(store, reference, role, session, expected_fingerprint):
    if not isinstance(reference, dict) or reference.get("role") != role:
        raise ValueError("Input snapshot role does not match its ledger field")
    payload = store.verify(reference)
    validate_market_data_contract(payload.get("market_data_contract"))
    if payload.get("market_data_fingerprint") != expected_fingerprint:
        raise ValueError("Snapshot fingerprint differs from the ledger event")
    frame = payload.get("dataframe")
    if not isinstance(frame, dict):
        raise ValueError("Snapshot is missing its persisted DataFrame")
    columns, index, data = frame.get("columns"), frame.get("index"), frame.get("data")
    if (not isinstance(columns, list) or not columns or len(set(columns)) != len(columns)
            or not isinstance(index, list) or not index
            or not isinstance(data, list) or len(data) != len(index)
            or any(not isinstance(row, list) or len(row) != len(columns) for row in data)):
        raise ValueError("Persisted DataFrame shape is invalid")
    dates = [pd.Timestamp(value) for value in index]
    if (len(set(dates)) != len(dates) or dates != sorted(dates)
            or dates[-1].date().isoformat() != session):
        raise ValueError("Persisted DataFrame sessions do not match the ledger event")
    values = np.asarray(data, dtype=float)
    if not np.isfinite(values).all():
        raise ValueError("Persisted DataFrame contains non-finite values")
    return payload


def evaluate_shadow_holdout(ledger_path, report_path=None):
    """Write exactly one final report after 252 return-bearing NYSE sessions.

    The first decision is a warm-up signal. A terminal collection call records the final
    next-session Open-to-Close transition without creating another pending decision.
    """
    ledger_path = Path(ledger_path)
    output = Path(report_path) if report_path is not None else ledger_path.with_name(
        ledger_path.stem + ".final_report.json"
    )
    with PPOShadowLedger(ledger_path).locked_read() as records:
        return _evaluate_locked(ledger_path, output, records)


def _evaluate_locked(ledger_path, output, records):
    replay = replay_shadow_records(records)
    if replay["transition_count"] != HOLDOUT_RETURN_SESSIONS:
        raise ValueError(
            f"Final evaluation requires exactly {HOLDOUT_RETURN_SESSIONS} completed return-bearing sessions; "
            f"found {replay['transition_count']}"
        )
    if replay["decision_count"] != HOLDOUT_DECISION_COUNT or replay["pending_decision"] is not None:
        raise ValueError(
            f"Final evaluation requires {HOLDOUT_DECISION_COUNT} decisions (one warm-up plus "
            f"{HOLDOUT_RETURN_SESSIONS - 1} subsequent signals) and no pending decision"
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
            payload = _verify_snapshot(
                snapshots, reference, role, event["session"],
                event.get("market_data_fingerprint" if role == "model_inference"
                          else "benchmark_data_fingerprint"),
            )
            if role == "model_inference":
                context = payload.get("inference_context")
                current_weights = event.get("current_weights")
                tickers = records[0]["payload"]["config"]["tickers"]
                if (not isinstance(context, dict) or context.get("inference_contract")
                        != "ppo_predict_weights_current_weights_v1"
                        or not isinstance(current_weights, dict) or set(current_weights) != set(tickers)
                        or context.get("tickers") != tickers):
                    raise ValueError("Persisted inference context differs from the decision's current weights")
                weight_values = [float(current_weights[t]) for t in tickers]
                # Current holdings are marked at Close and can drift beyond target caps
                # after price moves. Caps apply to PPO target weights, not observed state.
                if (not np.isfinite(weight_values).all() or any(value < 0 for value in weight_values)
                        or context.get("current_weights") != weight_values):
                    raise ValueError("Persisted PPO current weights differ from the frozen inference context")
            evidence.append({"session": event["session"], "role": role, "sha256": reference["sha256"]})
    for event in transitions:
        refs = event.get("input_snapshots")
        if not isinstance(refs, dict) or set(refs) != {"model_inference", "benchmark"}:
            raise ValueError("A transition is missing its complete market-data snapshots")
        for role in ("model_inference", "benchmark"):
            _verify_snapshot(
                snapshots, refs[role], role, event["session"],
                event.get("market_data_fingerprint" if role == "model_inference"
                          else "benchmark_data_fingerprint"),
            )
            evidence.append({"session": event["session"], "role": role, "sha256": refs[role]["sha256"]})
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
            "return_rule": "prior_close_to_current_close_equity_with_next_open_rebalance",
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
        raise FileExistsError("Final evaluation report already exists; it is never overwritten") from None
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
    return report
