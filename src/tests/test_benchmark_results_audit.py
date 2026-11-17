"""Tests for read-only multi-seed benchmark report consistency auditing."""

from benchmark_results_audit import audit_multi_seed_benchmark_report


def _report():
    seeds = [7, 21]
    values = {
        1: {7: 0.02, 21: 0.05},
        2: {7: 0.03, 21: 0.01},
    }
    benchmark = {
        1: (0.04, 0.03, 0.06),
        2: (0.01, 0.02, 0.03),
    }
    seed_results, shared_folds = [], []
    for fold_id, (ew, spy50, spy100) in benchmark.items():
        comparisons, ppo_by_seed = [], []
        for seed in seeds:
            ppo_return = values[fold_id][seed]
            row = {
                "fold": fold_id,
                "ppo": {"total_return": ppo_return},
                "ppo_exposure_matched_equal_weight": None,
                "ppo_exposure_matched_equal_weight_status": "INCONCLUSIVE",
                "comparison": {
                    "equal_weight_return_difference_percentage_points": (ew - ppo_return) * 100,
                    "spy_50pct_return_difference_percentage_points": (spy50 - ppo_return) * 100,
                    "spy_100pct_return_difference_percentage_points": (spy100 - ppo_return) * 100,
                    "equal_weight_outperformed_ppo": ew > ppo_return,
                    "spy_50pct_outperformed_ppo": spy50 > ppo_return,
                    "spy_100pct_outperformed_ppo": spy100 > ppo_return,
                    "exposure_matched_return_difference_percentage_points": None,
                    "exposure_matched_outperformed_ppo": None,
                },
            }
            comparisons.append(row)
            ppo_by_seed.append({"seed": seed, "metrics": row["ppo"]})
        shared_folds.append({
            "fold": fold_id,
            "equal_weight_buy_and_hold": {"total_return": ew},
            "spy_50pct_exposure": {"total_return": spy50},
            "spy_100pct_exposure": {"total_return": spy100},
            "ppo_by_seed": ppo_by_seed,
            "seed_comparisons": comparisons,
        })

    for seed in seeds:
        rows = [
            row for fold in shared_folds for row in fold["seed_comparisons"]
            if row["ppo"]["total_return"] == values[fold["fold"]][seed]
        ]
        returns = [row["ppo"]["total_return"] for row in rows]
        wins = {
            "equal_weight_return_wins": sum(r["comparison"]["equal_weight_outperformed_ppo"] for r in rows),
            "spy_50pct_return_wins": sum(r["comparison"]["spy_50pct_outperformed_ppo"] for r in rows),
            "spy_100pct_return_wins": sum(r["comparison"]["spy_100pct_outperformed_ppo"] for r in rows),
        }
        seed_results.append({
            "seed": seed, "fold_count": 2, **wins,
            "mean_ppo_fold_return": sum(returns) / 2,
            "median_ppo_fold_return": sum(returns) / 2,
            "folds": rows,
        })
    summary = {
        "seed_count": 2, "fold_count": 2, "ppo_run_count": 4,
        "per_seed": [
            {key: value for key, value in row.items() if key != "folds"}
            for row in seed_results
        ],
        "mean_benchmark_fold_return": 0.025,
        "median_benchmark_fold_return": 0.025,
        "mean_spy_50pct_exposure_fold_return": 0.025,
        "median_spy_50pct_exposure_fold_return": 0.025,
        "mean_spy_100pct_exposure_fold_return": 0.045,
        "median_spy_100pct_exposure_fold_return": 0.045,
    }
    return {
        "seeds": seeds, "seed_results": seed_results,
        "folds": shared_folds, "summary": summary,
    }


def test_consistency_audit_accepts_reconciled_report_but_flags_legacy_matched_path():
    result = audit_multi_seed_benchmark_report(_report())
    assert result["status"] == "INCONCLUSIVE"
    assert result["checks"]["report_structure"] == "PASS"
    assert result["checks"]["seed_fold_mirrors"] == "PASS"
    assert result["checks"]["return_comparison_consistency"] == "PASS"
    assert result["checks"]["summary_counter_consistency"] == "PASS"
    assert result["checks"]["benchmark_summary_consistency"] == "PASS"
    assert result["checks"]["exposure_matched_seed_coverage"] == "INCONCLUSIVE"


def test_consistency_audit_detects_seed_win_counter_mismatch():
    report = _report()
    report["seed_results"][0]["equal_weight_return_wins"] += 1
    report["summary"]["per_seed"][0]["equal_weight_return_wins"] += 1
    result = audit_multi_seed_benchmark_report(report)
    assert result["status"] == "FAIL"
    assert result["checks"]["summary_counter_consistency"] == "FAIL"


def test_consistency_audit_detects_fold_return_difference_mismatch():
    report = _report()
    report["seed_results"][0]["folds"][0]["comparison"]["equal_weight_return_difference_percentage_points"] = 999
    result = audit_multi_seed_benchmark_report(report)
    assert result["status"] == "FAIL"
    assert result["checks"]["return_comparison_consistency"] == "FAIL"


def test_consistency_audit_detects_seed_comparison_mirror_mismatch():
    report = _report()
    report["folds"][0]["seed_comparisons"][0]["ppo"]["total_return"] = 0.99
    result = audit_multi_seed_benchmark_report(report)
    assert result["status"] == "FAIL"
    assert result["checks"]["seed_fold_mirrors"] == "FAIL"
