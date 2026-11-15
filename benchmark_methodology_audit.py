#!/usr/bin/env python3
"""Audit whether saved PPO and benchmark reports are methodologically comparable."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Unable to read report: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Report must contain a JSON object: {path}")
    return value


def _select_ppo_folds(
    ppo: dict[str, Any], ppo_seed: int | None
) -> tuple[list[Any] | None, int | None, str, str | None]:
    """Select folds without ever choosing a multi-seed run implicitly."""
    if "runs" in ppo:
        runs = ppo.get("runs")
        if not isinstance(runs, list) or not runs:
            return None, None, "INCONCLUSIVE", "Multi-seed report has no valid runs list."
        if ppo_seed is None:
            return None, None, "INCONCLUSIVE", (
                "Multi-seed report requires an explicit --ppo-seed selection."
            )
        matches = [
            run for run in runs
            if isinstance(run, dict) and run.get("seed") == ppo_seed
        ]
        if len(matches) != 1:
            reason = (
                f"Seed {ppo_seed} is not present in the multi-seed report."
                if not matches else
                f"Seed {ppo_seed} occurs more than once; selection is ambiguous."
            )
            return None, None, "INCONCLUSIVE", reason
        folds = matches[0].get("folds")
        if not isinstance(folds, list):
            return None, ppo_seed, "INCONCLUSIVE", (
                f"Selected seed {ppo_seed} has no valid folds list."
            )
        return folds, ppo_seed, "PASS", None

    folds = ppo.get("folds")
    if ppo_seed is not None:
        return None, None, "INCONCLUSIVE", (
            "--ppo-seed was supplied, but the PPO report is legacy single-seed "
            "and contains no runs[] seed metadata."
        )
    if not isinstance(folds, list):
        return None, None, "INCONCLUSIVE", (
            "PPO report contains neither a valid legacy folds list nor a valid runs list."
        )
    return folds, None, "PASS", None


def _inconclusive_result(reason: str, selected_seed: int | None) -> dict[str, Any]:
    names = (
        "cost_and_cap_parameters", "fold_alignment", "execution_data_identity",
        "entry_execution", "ppo_daily_exposure_trace",
        "benchmark_daily_exposure_trace", "reported_cost_values",
    )
    checks = {"seed_selection": "INCONCLUSIVE"}
    checks.update({name: "INCONCLUSIVE" for name in names})
    return {
        "schema_version": 1,
        "status": "INCONCLUSIVE",
        "checks": checks,
        "fold_count": 0,
        "selected_seed": selected_seed,
        "reason": reason,
        "interpretation": (
            "INCONCLUSIVE means required seed selection or report evidence is absent "
            "or ambiguous; it must not be interpreted as parity."
        ),
    }


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def audit_reports(
    ppo: dict[str, Any],
    benchmark: dict[str, Any],
    ppo_seed: int | None = None,
) -> dict[str, Any]:
    """Return PASS/FAIL/INCONCLUSIVE checks; missing evidence is never treated as PASS."""
    ppo_folds, selected_seed, seed_status, seed_reason = _select_ppo_folds(ppo, ppo_seed)
    if ppo_folds is None:
        return _inconclusive_result(seed_reason or "PPO seed selection failed.", selected_seed)

    checks: dict[str, str] = {"seed_selection": seed_status}
    ppo_parameters = ppo.get("training_parameters")
    benchmark_parameters = benchmark.get("parameters")
    if not isinstance(ppo_parameters, dict) or not isinstance(benchmark_parameters, dict):
        checks["cost_and_cap_parameters"] = "INCONCLUSIVE"
    else:
        keys = (
            "initial_capital", "transaction_cost", "slippage",
            "max_position_size", "max_gross_exposure",
        )
        left_values = [_number(ppo_parameters.get(key)) for key in keys]
        right_values = [_number(benchmark_parameters.get(key)) for key in keys]
        if any(value is None for value in left_values + right_values):
            checks["cost_and_cap_parameters"] = "INCONCLUSIVE"
        else:
            checks["cost_and_cap_parameters"] = (
                "PASS" if left_values == right_values else "FAIL"
            )

    benchmark_folds = benchmark.get("folds")
    paired: list[tuple[dict[str, Any], dict[str, Any]]] = []
    if not isinstance(benchmark_folds, list):
        checks["fold_alignment"] = "INCONCLUSIVE"
    elif len(ppo_folds) != len(benchmark_folds):
        checks["fold_alignment"] = "FAIL"
    elif not all(isinstance(left, dict) and isinstance(right, dict)
                 for left, right in zip(ppo_folds, benchmark_folds)):
        checks["fold_alignment"] = "INCONCLUSIVE"
    else:
        paired = list(zip(ppo_folds, benchmark_folds))
        alignment = []
        for left, right in paired:
            left_observations = left.get("test_observations", left.get("observations"))
            right_observations = right.get("observations")
            required = (
                isinstance(left.get("test_start"), str) and bool(left["test_start"])
                and isinstance(left.get("test_end"), str) and bool(left["test_end"])
                and isinstance(right.get("test_start"), str) and bool(right["test_start"])
                and isinstance(right.get("test_end"), str) and bool(right["test_end"])
                and left_observations is not None and right_observations is not None
            )
            if not required:
                alignment.append("INCONCLUSIVE")
            else:
                alignment.append(
                    "PASS" if (
                        left["test_start"] == right["test_start"]
                        and left["test_end"] == right["test_end"]
                        and left_observations == right_observations
                    ) else "FAIL"
                )
        checks["fold_alignment"] = _aggregate(alignment)

    fingerprints = []
    execution = []
    ppo_timeseries = []
    benchmark_timeseries = []
    cost_records = []
    for left, right in paired:
        ppo_hash = left.get("execution_data_fingerprint")
        benchmark_hash = right.get("execution_data_fingerprint")
        fingerprints.append(
            "INCONCLUSIVE" if not ppo_hash or not benchmark_hash
            else "PASS" if ppo_hash == benchmark_hash else "FAIL"
        )
        benchmark_strategy = right.get("equal_weight_buy_and_hold")
        if not isinstance(benchmark_strategy, dict):
            benchmark_strategy = {}
        ppo_execution = left.get("entry_execution")
        benchmark_execution = benchmark_strategy.get("entry_execution")
        execution.append(
            "PASS" if ppo_execution == benchmark_execution == "next_bar_open"
            else "INCONCLUSIVE" if not ppo_execution or not benchmark_execution
            else "FAIL"
        )
        ppo_metrics = left.get("metrics")
        if not isinstance(ppo_metrics, dict):
            ppo_metrics = {}
        ppo_trace = ppo_metrics.get("timeseries")
        benchmark_trace = benchmark_strategy.get("timeseries")
        ppo_timeseries.append(
            "PASS" if isinstance(ppo_trace, list) and ppo_trace else "INCONCLUSIVE"
        )
        benchmark_timeseries.append(
            "PASS" if isinstance(benchmark_trace, list) and benchmark_trace
            else "INCONCLUSIVE"
        )
        ppo_cost = _number(ppo_metrics.get("total_transaction_costs"))
        benchmark_cost = _number(benchmark_strategy.get("total_transaction_costs"))
        cost_records.append(
            "INCONCLUSIVE" if ppo_cost is None or benchmark_cost is None
            else "PASS" if ppo_cost >= 0 and benchmark_cost >= 0
            else "FAIL"
        )

    checks["execution_data_identity"] = _aggregate(fingerprints)
    checks["entry_execution"] = _aggregate(execution)
    checks["ppo_daily_exposure_trace"] = _aggregate(ppo_timeseries)
    checks["benchmark_daily_exposure_trace"] = _aggregate(benchmark_timeseries)
    checks["reported_cost_values"] = _aggregate(cost_records)
    statuses = set(checks.values())
    status = "FAIL" if "FAIL" in statuses else (
        "INCONCLUSIVE" if "INCONCLUSIVE" in statuses else "PASS"
    )
    return {
        "schema_version": 1,
        "status": status,
        "checks": checks,
        "fold_count": len(paired),
        "selected_seed": selected_seed,
        "interpretation": (
            "PASS means all implemented checks have supporting report evidence. "
            "INCONCLUSIVE means required provenance, seed selection, or daily traces "
            "are absent or ambiguous; it must not be interpreted as parity. "
            "FAIL means a concrete mismatch was detected. This audit does not prove "
            "equivalent market impact or fills."
        ),
    }


def _aggregate(values: list[str]) -> str:
    if not values:
        return "INCONCLUSIVE"
    if "FAIL" in values:
        return "FAIL"
    if "INCONCLUSIVE" in values:
        return "INCONCLUSIVE"
    return "PASS"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ppo-report", type=Path, required=True)
    parser.add_argument("--benchmark-report", type=Path, required=True)
    parser.add_argument(
        "--ppo-seed", type=int, default=None,
        help="Required when --ppo-report uses the multi-seed runs[] schema.",
    )
    parser.add_argument(
        "--output", type=Path,
        default=Path("reports/benchmark_methodology_audit.json"),
    )
    args = parser.parse_args(argv)
    try:
        result = audit_reports(
            _load(args.ppo_report), _load(args.benchmark_report), ppo_seed=args.ppo_seed
        )
    except (ValueError, TypeError, KeyError) as exc:
        parser.error(str(exc))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")
    print(f"Methodology audit: {result['status']}")
    for name, status in result["checks"].items():
        print(f"{name}: {status}")
    if result.get("reason"):
        print(f"Reason: {result['reason']}")
    if result.get("selected_seed") is not None:
        print(f"Selected PPO seed: {result['selected_seed']}")
    print(f"Report: {args.output}")
    return 0 if result["status"] == "PASS" else 2 if result["status"] == "FAIL" else 3


if __name__ == "__main__":
    raise SystemExit(main())
