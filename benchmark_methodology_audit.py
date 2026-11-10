#!/usr/bin/env python3
"""Audit whether saved PPO and benchmark reports are methodologically comparable."""
from __future__ import annotations

import argparse
import json
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


def audit_reports(ppo: dict[str, Any], benchmark: dict[str, Any]) -> dict[str, Any]:
    """Return PASS/FAIL/INCONCLUSIVE checks; missing evidence is never treated as PASS."""
    checks: dict[str, str] = {}
    ppo_parameters = ppo.get("training_parameters")
    benchmark_parameters = benchmark.get("parameters")
    if not isinstance(ppo_parameters, dict) or not isinstance(benchmark_parameters, dict):
        checks["cost_and_cap_parameters"] = "INCONCLUSIVE"
    else:
        keys = ("initial_capital", "transaction_cost", "slippage",
                "max_position_size", "max_gross_exposure")
        missing = [key for key in keys if key not in ppo_parameters or key not in benchmark_parameters]
        if missing:
            checks["cost_and_cap_parameters"] = "INCONCLUSIVE"
        else:
            checks["cost_and_cap_parameters"] = (
                "PASS" if all(float(ppo_parameters[key]) == float(benchmark_parameters[key]) for key in keys)
                else "FAIL"
            )

    ppo_folds = ppo.get("folds")
    benchmark_folds = benchmark.get("folds")
    if not isinstance(ppo_folds, list) or not isinstance(benchmark_folds, list) or len(ppo_folds) != len(benchmark_folds):
        checks["fold_alignment"] = "FAIL"
        paired = []
    else:
        paired = list(zip(ppo_folds, benchmark_folds))
        checks["fold_alignment"] = (
            "PASS" if all(
                str(left.get("test_start")) == str(right.get("test_start"))
                and str(left.get("test_end")) == str(right.get("test_end"))
                and left.get("test_observations", left.get("observations")) == right.get("observations")
                for left, right in paired
            ) else "FAIL"
        )

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
        ppo_execution = left.get("entry_execution")
        benchmark_execution = right.get("equal_weight_buy_and_hold", {}).get("entry_execution")
        execution.append(
            "PASS" if ppo_execution == benchmark_execution == "next_bar_open"
            else "INCONCLUSIVE" if not ppo_execution or not benchmark_execution
            else "FAIL"
        )
        ppo_trace = left.get("metrics", {}).get("timeseries")
        benchmark_trace = right.get("equal_weight_buy_and_hold", {}).get("timeseries")
        ppo_timeseries.append("PASS" if isinstance(ppo_trace, list) and ppo_trace else "INCONCLUSIVE")
        benchmark_timeseries.append("PASS" if isinstance(benchmark_trace, list) and benchmark_trace else "INCONCLUSIVE")
        ppo_cost = left.get("metrics", {}).get("total_transaction_costs")
        benchmark_cost = right.get("equal_weight_buy_and_hold", {}).get("total_transaction_costs")
        cost_records.append(
            "INCONCLUSIVE" if ppo_cost is None or benchmark_cost is None
            else "PASS" if float(ppo_cost) >= 0 and float(benchmark_cost) >= 0
            else "FAIL"
        )

    def aggregate(values: list[str]) -> str:
        if not values:
            return "INCONCLUSIVE"
        if "FAIL" in values:
            return "FAIL"
        if "INCONCLUSIVE" in values:
            return "INCONCLUSIVE"
        return "PASS"

    checks["execution_data_identity"] = aggregate(fingerprints)
    checks["entry_execution"] = aggregate(execution)
    checks["ppo_daily_exposure_trace"] = aggregate(ppo_timeseries)
    checks["benchmark_daily_exposure_trace"] = aggregate(benchmark_timeseries)
    checks["reported_cost_values"] = aggregate(cost_records)
    statuses = set(checks.values())
    status = "FAIL" if "FAIL" in statuses else (
        "INCONCLUSIVE" if "INCONCLUSIVE" in statuses else "PASS"
    )
    return {
        "schema_version": 1,
        "status": status,
        "checks": checks,
        "fold_count": len(paired),
        "interpretation": (
            "PASS means all implemented checks have supporting report evidence. "
            "INCONCLUSIVE means required provenance or daily traces are absent; "
            "it must not be interpreted as parity. FAIL means a concrete mismatch "
            "was detected. This audit does not prove equivalent market impact or fills."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ppo-report", type=Path, required=True)
    parser.add_argument("--benchmark-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("reports/benchmark_methodology_audit.json"))
    args = parser.parse_args(argv)
    try:
        result = audit_reports(_load(args.ppo_report), _load(args.benchmark_report))
    except (ValueError, TypeError, KeyError) as exc:
        parser.error(str(exc))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")
    print(f"Methodology audit: {result['status']}")
    for name, status in result["checks"].items():
        print(f"{name}: {status}")
    print(f"Report: {args.output}")
    return 0 if result["status"] == "PASS" else 2 if result["status"] == "FAIL" else 3


if __name__ == "__main__":
    raise SystemExit(main())
