#!/usr/bin/env python3
"""Read-only consistency audit for saved multi-seed PPO benchmark reports."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _close(left: Any, right: Any) -> bool:
    a, b = _number(left), _number(right)
    return a is not None and b is not None and math.isclose(
        a, b, rel_tol=1e-9, abs_tol=1e-10
    )


def audit_multi_seed_benchmark_report(report: dict[str, Any]) -> dict[str, Any]:
    """Reconcile seed/fold mirrors, counters, return comparisons and matched exposure."""
    checks = {
        "report_structure": "INCONCLUSIVE",
        "seed_fold_mirrors": "INCONCLUSIVE",
        "return_comparison_consistency": "INCONCLUSIVE",
        "summary_counter_consistency": "INCONCLUSIVE",
        "benchmark_summary_consistency": "INCONCLUSIVE",
        "exposure_matched_seed_coverage": "INCONCLUSIVE",
    }
    issues: list[str] = []
    seeds, results = report.get("seeds"), report.get("seed_results")
    folds, summary = report.get("folds"), report.get("summary")
    if (
        not isinstance(seeds, list) or not seeds
        or any(not isinstance(s, int) or isinstance(s, bool) for s in seeds)
        or len(set(seeds)) != len(seeds)
        or not isinstance(results, list) or not isinstance(folds, list)
        or not folds or not isinstance(summary, dict)
    ):
        issues.append("Missing or malformed seeds, seed_results, folds, or summary.")
        return {
            "schema_version": 1, "status": "INCONCLUSIVE", "checks": checks,
            "seed_count": len(seeds) if isinstance(seeds, list) else 0,
            "fold_count": len(folds) if isinstance(folds, list) else 0,
            "ppo_run_count": 0, "issues": issues,
        }

    seed_count, fold_count = len(seeds), len(folds)
    expected_runs = seed_count * fold_count
    seed_map = {
        row.get("seed"): row for row in results if isinstance(row, dict)
    }
    structure_ok = (
        len(seed_map) == len(results) and set(seed_map) == set(seeds)
        and summary.get("seed_count") == seed_count
        and summary.get("fold_count") == fold_count
        and summary.get("ppo_run_count") == expected_runs
    )
    if not structure_ok:
        issues.append("Declared seeds, seed_results, and summary counts do not reconcile.")
    checks["report_structure"] = "PASS" if structure_ok else "FAIL"

    fold_ids = [row.get("fold") for row in folds if isinstance(row, dict)]
    if len(fold_ids) != fold_count or any(value is None for value in fold_ids) or len(set(fold_ids)) != fold_count:
        checks["report_structure"] = "FAIL"
        issues.append("Shared benchmark fold identifiers are missing, malformed, or duplicated.")

    summary_rows = summary.get("per_seed")
    summary_map = {
        row.get("seed"): row for row in summary_rows if isinstance(row, dict)
    } if isinstance(summary_rows, list) else {}
    counters_ok = (
        isinstance(summary_rows, list) and len(summary_map) == len(summary_rows)
        and set(summary_map) == set(seeds)
    )
    if not counters_ok:
        issues.append("summary.per_seed does not contain exactly one row for each seed.")

    mirrors_ok = True
    comparisons_ok = True
    exposure_ok = True
    benchmark_returns = {
        key: [] for key in (
            "equal_weight_buy_and_hold", "spy_50pct_exposure", "spy_100pct_exposure"
        )
    }

    for shared in folds:
        if not isinstance(shared, dict):
            mirrors_ok = comparisons_ok = exposure_ok = False
            continue
        fold_id = shared.get("fold")
        ppo_rows, comparison_rows = shared.get("ppo_by_seed"), shared.get("seed_comparisons")
        ppo_map = {r.get("seed"): r for r in ppo_rows if isinstance(r, dict)} if isinstance(ppo_rows, list) else {}
        comparison_map = {r.get("seed"): r for r in comparison_rows if isinstance(r, dict)} if isinstance(comparison_rows, list) else {}
        if (
            not isinstance(ppo_rows, list) or not isinstance(comparison_rows, list)
            or len(ppo_map) != len(seeds) or set(ppo_map) != set(seeds)
            or len(comparison_map) != len(seeds) or set(comparison_map) != set(seeds)
        ):
            mirrors_ok = False
            issues.append(f"Fold {fold_id!r} lacks exactly one PPO and comparison mirror per seed.")

        for name in benchmark_returns:
            item = shared.get(name)
            value = _number(item.get("total_return")) if isinstance(item, dict) else None
            if value is None:
                comparisons_ok = False
                issues.append(f"Fold {fold_id!r} lacks finite {name}.total_return.")
            else:
                benchmark_returns[name].append(value)

        matched_rows = shared.get("ppo_exposure_matched_equal_weight_by_seed")
        matched_map = {
            row.get("seed"): row for row in matched_rows if isinstance(row, dict)
        } if isinstance(matched_rows, list) else {}
        if (
            not isinstance(matched_rows, list) or len(matched_map) != len(seeds)
            or set(matched_map) != set(seeds)
        ):
            exposure_ok = False

        for seed in seeds:
            seed_result = seed_map.get(seed)
            seed_folds = seed_result.get("folds") if isinstance(seed_result, dict) else None
            matching = [
                row for row in seed_folds
                if isinstance(row, dict) and row.get("fold") == fold_id
            ] if isinstance(seed_folds, list) else []
            if len(matching) != 1:
                mirrors_ok = comparisons_ok = False
                issues.append(f"Seed {seed} fold {fold_id!r} is missing or duplicated in seed_results.")
                continue
            row = matching[0]
            ppo_mirror = ppo_map.get(seed)
            if not isinstance(ppo_mirror, dict) or ppo_mirror.get("metrics") != row.get("ppo"):
                mirrors_ok = False
                issues.append(f"Seed {seed} fold {fold_id!r} PPO metrics mirror differs.")
            if comparison_map.get(seed) != row:
                mirrors_ok = False
                issues.append(f"Seed {seed} fold {fold_id!r} seed_comparisons mirror differs.")

            ppo_metrics = row.get("ppo")
            ppo_return = _number(ppo_metrics.get("total_return")) if isinstance(ppo_metrics, dict) else None
            comparison = row.get("comparison")
            if ppo_return is None or not isinstance(comparison, dict):
                comparisons_ok = False
                issues.append(f"Seed {seed} fold {fold_id!r} lacks PPO return or comparison data.")
                continue
            for prefix, name in (
                ("equal_weight", "equal_weight_buy_and_hold"),
                ("spy_50pct", "spy_50pct_exposure"),
                ("spy_100pct", "spy_100pct_exposure"),
            ):
                item = shared.get(name)
                value = _number(item.get("total_return")) if isinstance(item, dict) else None
                if value is None:
                    continue
                if (
                    not _close(comparison.get(f"{prefix}_return_difference_percentage_points"), (value - ppo_return) * 100)
                    or comparison.get(f"{prefix}_outperformed_ppo") is not (value > ppo_return)
                ):
                    comparisons_ok = False
                    issues.append(f"Seed {seed} fold {fold_id!r} {prefix} comparison is inconsistent.")

            matched = matched_map.get(seed)
            if matched is None:
                exposure_ok = False
                continue
            status = matched.get("status")
            metrics = matched.get("metrics")
            if (
                status != row.get("ppo_exposure_matched_equal_weight_status")
                or metrics != row.get("ppo_exposure_matched_equal_weight")
            ):
                exposure_ok = False
                issues.append(f"Seed {seed} fold {fold_id!r} exposure-matched mirror differs.")
                continue
            diff = comparison.get("exposure_matched_return_difference_percentage_points")
            win = comparison.get("exposure_matched_outperformed_ppo")
            if status == "AVAILABLE":
                value = _number(metrics.get("total_return")) if isinstance(metrics, dict) else None
                if value is None or not _close(diff, (value - ppo_return) * 100) or win is not (value > ppo_return):
                    exposure_ok = False
                    issues.append(f"Seed {seed} fold {fold_id!r} available exposure-matched metrics do not reconcile.")
            elif status == "INCONCLUSIVE":
                exposure_ok = False
                if metrics is not None or diff is not None or win is not None:
                    issues.append(f"Seed {seed} fold {fold_id!r} has values despite INCONCLUSIVE matched status.")
            else:
                exposure_ok = False
                issues.append(f"Seed {seed} fold {fold_id!r} has unknown matched status.")

    checks["seed_fold_mirrors"] = "PASS" if mirrors_ok else "FAIL"
    checks["return_comparison_consistency"] = "PASS" if comparisons_ok else "FAIL"

    if counters_ok:
        for seed in seeds:
            result, summary_row = seed_map[seed], summary_map[seed]
            rows = result.get("folds")
            if not isinstance(rows, list) or len(rows) != fold_count:
                counters_ok = False
                issues.append(f"Seed {seed} fold count does not match shared fold count.")
                continue
            expected = {
                "fold_count": fold_count,
                "equal_weight_return_wins": sum(r.get("comparison", {}).get("equal_weight_outperformed_ppo") is True for r in rows if isinstance(r, dict)),
                "spy_50pct_return_wins": sum(r.get("comparison", {}).get("spy_50pct_outperformed_ppo") is True for r in rows if isinstance(r, dict)),
                "spy_100pct_return_wins": sum(r.get("comparison", {}).get("spy_100pct_outperformed_ppo") is True for r in rows if isinstance(r, dict)),
            }
            for key, value in expected.items():
                if result.get(key) != value or summary_row.get(key) != value:
                    counters_ok = False
                    issues.append(f"Seed {seed} {key} differs from the fold-level comparisons.")
            if "exposure_matched_available_fold_count" in result:
                matched_rows = [
                    row for row in rows
                    if isinstance(row, dict)
                    and row.get("ppo_exposure_matched_equal_weight_status") == "AVAILABLE"
                ]
                matched_returns = [
                    float(row["ppo_exposure_matched_equal_weight"]["total_return"])
                    for row in matched_rows
                ]
                matched_expected = {
                    "exposure_matched_available_fold_count": len(matched_rows),
                    "exposure_matched_equal_weight_return_wins": sum(
                        row.get("comparison", {}).get("exposure_matched_outperformed_ppo") is True
                        for row in matched_rows
                    ),
                    "mean_exposure_matched_equal_weight_fold_return": (
                        sum(matched_returns) / len(matched_returns) if matched_returns else None
                    ),
                    "median_exposure_matched_equal_weight_fold_return": (
                        float(__import__("statistics").median(matched_returns))
                        if matched_returns else None
                    ),
                }
                for key, value in matched_expected.items():
                    if key not in result or key not in summary_row:
                        counters_ok = False
                        issues.append(f"Seed {seed} is missing matched-exposure summary field {key}.")
                    elif value is None:
                        if result.get(key) is not None or summary_row.get(key) is not None:
                            counters_ok = False
                            issues.append(f"Seed {seed} {key} should be null with no available matched folds.")
                    elif result.get(key) != value if isinstance(value, int) else not _close(result.get(key), value):
                        counters_ok = False
                        issues.append(f"Seed {seed} {key} differs from its matched fold results.")
                    elif result.get(key) != summary_row.get(key):
                        counters_ok = False
                        issues.append(f"Seed {seed} summary.per_seed {key} differs from seed_results.")
            for key in ("mean_ppo_fold_return", "median_ppo_fold_return"):
                if not _close(result.get(key), summary_row.get(key)):
                    counters_ok = False
                    issues.append(f"Seed {seed} summary.per_seed {key} differs from seed_results.")
    checks["summary_counter_consistency"] = "PASS" if counters_ok else "FAIL"

    summary_ok = True
    for name, values in benchmark_returns.items():
        if not values:
            summary_ok = False
            continue
        mean_key = {
            "equal_weight_buy_and_hold": "mean_benchmark_fold_return",
            "spy_50pct_exposure": "mean_spy_50pct_exposure_fold_return",
            "spy_100pct_exposure": "mean_spy_100pct_exposure_fold_return",
        }[name]
        median_key = {
            "equal_weight_buy_and_hold": "median_benchmark_fold_return",
            "spy_50pct_exposure": "median_spy_50pct_exposure_fold_return",
            "spy_100pct_exposure": "median_spy_100pct_exposure_fold_return",
        }[name]
        ordered = sorted(values)
        middle = len(ordered) // 2
        median = ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2
        if not _close(summary.get(mean_key), sum(values) / len(values)) or not _close(summary.get(median_key), median):
            summary_ok = False
            issues.append(f"summary mean/median for {name} differs from fold-level benchmark returns.")
    checks["benchmark_summary_consistency"] = "PASS" if summary_ok else "FAIL"
    checks["exposure_matched_seed_coverage"] = "PASS" if exposure_ok else "INCONCLUSIVE"
    if not exposure_ok and not any("exposure-matched" in issue for issue in issues):
        issues.append("Per-seed exposure-matched results are absent; legacy shared results are not sufficient.")

    statuses = set(checks.values())
    status = "FAIL" if "FAIL" in statuses else "INCONCLUSIVE" if "INCONCLUSIVE" in statuses else "PASS"
    return {
        "schema_version": 1, "status": status, "checks": checks,
        "seed_count": seed_count, "fold_count": fold_count,
        "ppo_run_count": expected_runs,
        "exposure_matched": {
            "available_seed_fold_count": sum(
                1 for fold in folds if isinstance(fold, dict)
                for row in fold.get("ppo_exposure_matched_equal_weight_by_seed", [])
                if isinstance(row, dict) and row.get("status") == "AVAILABLE"
            ),
            "expected_seed_fold_count": expected_runs,
        },
        "issues": issues,
        "interpretation": (
            "PASS means seed/fold mirrors, counters, return comparisons, benchmark "
            "summaries and per-seed exposure-matched evidence reconcile. INCONCLUSIVE "
            "means per-seed exposure-matched evidence is unavailable. FAIL means a "
            "concrete internal inconsistency was detected."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("reports/benchmark_results_consistency_audit.json"))
    args = parser.parse_args(argv)
    try:
        report = json.loads(args.benchmark_report.read_text(encoding="utf-8"))
        if not isinstance(report, dict):
            raise ValueError("Benchmark report must contain a JSON object")
        result = audit_multi_seed_benchmark_report(report)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        parser.error(str(exc))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")
    print(f"Benchmark results consistency audit: {result['status']}")
    for key, value in result["checks"].items():
        print(f"{key}: {value}")
    for issue in result["issues"]:
        print(f"Issue: {issue}")
    print(f"Report: {args.output}")
    return 0 if result["status"] == "PASS" else 2 if result["status"] == "FAIL" else 3


if __name__ == "__main__":
    raise SystemExit(main())
