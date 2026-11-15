"""Tests for the fail-closed PPO benchmark methodology report auditor."""
import copy
import json

from benchmark_methodology_audit import audit_reports, main


def _reports():
    ppo = {
        "training_parameters": {
            "initial_capital": 1000.0, "transaction_cost": 0.001,
            "slippage": 0.0005, "max_position_size": 0.2,
            "max_gross_exposure": 0.5,
        },
        "folds": [{
            "fold": 1, "test_start": "2024-01-03", "test_end": "2024-01-05",
            "test_observations": 3, "entry_execution": "next_bar_open",
            "execution_data_fingerprint": "a" * 64,
            "metrics": {
                "total_transaction_costs": 1.5,
                "timeseries": [{"gross_exposure_close": 0.3}],
            },
        }],
    }
    benchmark = {
        "parameters": {
            "initial_capital": 1000.0, "transaction_cost": 0.001,
            "slippage": 0.0005, "max_position_size": 0.2,
            "max_gross_exposure": 0.5,
        },
        "folds": [{
            "fold": 1, "test_start": "2024-01-03", "test_end": "2024-01-05",
            "observations": 3, "execution_data_fingerprint": "a" * 64,
            "equal_weight_buy_and_hold": {
                "entry_execution": "next_bar_open",
                "total_transaction_costs": 0.6,
                "timeseries": [{"gross_exposure_close": 0.4}],
            },
        }],
    }
    return ppo, benchmark


def _multi_seed_report():
    ppo, _ = _reports()
    folds = ppo.pop("folds")
    ppo["runs"] = [
        {"seed": 42, "fold_count": len(folds), "folds": copy.deepcopy(folds)},
        {"seed": 7, "fold_count": len(folds), "folds": copy.deepcopy(folds)},
    ]
    return ppo


def test_methodology_audit_passes_when_all_required_report_evidence_matches():
    result = audit_reports(*_reports())
    assert result["status"] == "PASS"
    assert result["checks"]["seed_selection"] == "PASS"
    assert all(value == "PASS" for value in result["checks"].values())
    assert result["selected_seed"] is None


def test_multi_seed_audit_passes_only_for_explicitly_selected_seed():
    _, benchmark = _reports()
    result = audit_reports(_multi_seed_report(), benchmark, ppo_seed=42)
    assert result["status"] == "PASS"
    assert result["selected_seed"] == 42
    assert result["fold_count"] == 1
    assert all(value == "PASS" for value in result["checks"].values())


def test_multi_seed_audit_is_inconclusive_when_seed_is_not_selected():
    _, benchmark = _reports()
    result = audit_reports(_multi_seed_report(), benchmark)
    assert result["status"] == "INCONCLUSIVE"
    assert result["checks"]["seed_selection"] == "INCONCLUSIVE"
    assert "explicit --ppo-seed" in result["reason"]


def test_multi_seed_audit_is_inconclusive_for_unknown_seed():
    _, benchmark = _reports()
    result = audit_reports(_multi_seed_report(), benchmark, ppo_seed=999)
    assert result["status"] == "INCONCLUSIVE"
    assert result["selected_seed"] is None
    assert "not present" in result["reason"]


def test_multi_seed_audit_is_inconclusive_for_duplicate_seed():
    ppo = _multi_seed_report()
    ppo["runs"].append(copy.deepcopy(ppo["runs"][0]))
    _, benchmark = _reports()
    result = audit_reports(ppo, benchmark, ppo_seed=42)
    assert result["status"] == "INCONCLUSIVE"
    assert "more than once" in result["reason"]


def test_seed_argument_is_not_silently_accepted_for_legacy_single_seed_report():
    ppo, benchmark = _reports()
    result = audit_reports(ppo, benchmark, ppo_seed=42)
    assert result["status"] == "INCONCLUSIVE"
    assert result["checks"]["seed_selection"] == "INCONCLUSIVE"
    assert "legacy single-seed" in result["reason"]


def test_methodology_audit_marks_missing_fingerprint_and_traces_inconclusive():
    ppo, benchmark = _reports()
    del ppo["folds"][0]["execution_data_fingerprint"]
    del ppo["folds"][0]["metrics"]["timeseries"]
    result = audit_reports(ppo, benchmark)
    assert result["status"] == "INCONCLUSIVE"
    assert result["checks"]["execution_data_identity"] == "INCONCLUSIVE"
    assert result["checks"]["ppo_daily_exposure_trace"] == "INCONCLUSIVE"


def test_methodology_audit_fails_on_cost_parameter_or_data_mismatch():
    ppo, benchmark = _reports()
    benchmark["parameters"]["slippage"] = 0.002
    benchmark["folds"][0]["execution_data_fingerprint"] = "b" * 64
    result = audit_reports(ppo, benchmark)
    assert result["status"] == "FAIL"
    assert result["checks"]["cost_and_cap_parameters"] == "FAIL"
    assert result["checks"]["execution_data_identity"] == "FAIL"


def test_methodology_audit_fails_on_fold_date_mismatch():
    ppo, benchmark = _reports()
    benchmark["folds"][0]["test_end"] = "2024-01-08"
    result = audit_reports(ppo, benchmark)
    assert result["checks"]["fold_alignment"] == "FAIL"
    assert result["status"] == "FAIL"


def test_methodology_audit_marks_missing_parameters_inconclusive():
    ppo, benchmark = _reports()
    del ppo["training_parameters"]["slippage"]
    result = audit_reports(ppo, benchmark)
    assert result["status"] == "INCONCLUSIVE"
    assert result["checks"]["cost_and_cap_parameters"] == "INCONCLUSIVE"


def test_methodology_audit_marks_fold_count_mismatch_as_fail():
    ppo, benchmark = _reports()
    benchmark["folds"].append(copy.deepcopy(benchmark["folds"][0]))
    result = audit_reports(ppo, benchmark)
    assert result["status"] == "FAIL"
    assert result["checks"]["fold_alignment"] == "FAIL"


def test_cli_accepts_explicit_ppo_seed_for_multi_seed_report(tmp_path):
    ppo, benchmark = _multi_seed_report(), _reports()[1]
    ppo_path = tmp_path / "ppo.json"
    benchmark_path = tmp_path / "benchmark.json"
    output_path = tmp_path / "audit.json"
    ppo_path.write_text(json.dumps(ppo), encoding="utf-8")
    benchmark_path.write_text(json.dumps(benchmark), encoding="utf-8")

    exit_code = main([
        "--ppo-report", str(ppo_path),
        "--benchmark-report", str(benchmark_path),
        "--ppo-seed", "42",
        "--output", str(output_path),
    ])

    assert exit_code == 0
    result = json.loads(output_path.read_text(encoding="utf-8"))
    assert result["status"] == "PASS"
    assert result["selected_seed"] == 42


def test_cli_without_seed_does_not_pass_multi_seed_report(tmp_path):
    ppo, benchmark = _multi_seed_report(), _reports()[1]
    ppo_path = tmp_path / "ppo.json"
    benchmark_path = tmp_path / "benchmark.json"
    output_path = tmp_path / "audit.json"
    ppo_path.write_text(json.dumps(ppo), encoding="utf-8")
    benchmark_path.write_text(json.dumps(benchmark), encoding="utf-8")

    exit_code = main([
        "--ppo-report", str(ppo_path),
        "--benchmark-report", str(benchmark_path),
        "--output", str(output_path),
    ])

    assert exit_code == 3
    result = json.loads(output_path.read_text(encoding="utf-8"))
    assert result["status"] == "INCONCLUSIVE"
    assert result["checks"]["seed_selection"] == "INCONCLUSIVE"



def _seed_results_report():
    ppo, _ = _reports()
    legacy_fold = ppo.pop("folds")[0]
    metrics = {
        "observations": 3,
        "total_transaction_costs": 1.5,
        "timeseries": [{"gross_exposure_close": 0.3}],
    }
    provenance = {
        **legacy_fold,
        "observations": 3,
        "ppo_by_seed": [{"seed": 42, "metrics": copy.deepcopy(metrics)}],
    }
    return {
        "parameters": copy.deepcopy(ppo["training_parameters"]),
        "folds": [provenance],
        "seed_results": [{
            "seed": 42,
            "fold_count": 1,
            "folds": [{"fold": 1, "ppo": copy.deepcopy(metrics), "comparison": {}}],
        }],
    }


def test_seed_results_audit_uses_explicit_seed_and_top_level_provenance():
    ppo = _seed_results_report()
    _, benchmark = _reports()
    result = audit_reports(ppo, benchmark, ppo_seed=42)
    assert result["status"] == "PASS"
    assert result["selected_seed"] == 42
    assert result["fold_count"] == 1
    assert all(value == "PASS" for value in result["checks"].values())


def test_seed_results_audit_requires_explicit_seed():
    ppo = _seed_results_report()
    _, benchmark = _reports()
    result = audit_reports(ppo, benchmark)
    assert result["status"] == "INCONCLUSIVE"
    assert "explicit --ppo-seed" in result["reason"]


def test_seed_results_audit_rejects_unknown_or_duplicate_seed():
    ppo = _seed_results_report()
    _, benchmark = _reports()
    unknown = audit_reports(ppo, benchmark, ppo_seed=7)
    assert unknown["status"] == "INCONCLUSIVE"
    assert "not present" in unknown["reason"]

    ppo["seed_results"].append(copy.deepcopy(ppo["seed_results"][0]))
    duplicate = audit_reports(ppo, benchmark, ppo_seed=42)
    assert duplicate["status"] == "INCONCLUSIVE"
    assert "more than once" in duplicate["reason"]


def test_seed_results_audit_rejects_mismatched_per_seed_mirror():
    ppo = _seed_results_report()
    ppo["folds"][0]["ppo_by_seed"][0]["metrics"]["total_return"] = 0.99
    _, benchmark = _reports()
    result = audit_reports(ppo, benchmark, ppo_seed=42)
    assert result["status"] == "INCONCLUSIVE"
    assert "does not match" in result["reason"]


def test_seed_results_audit_rejects_missing_or_duplicate_fold_provenance():
    ppo = _seed_results_report()
    _, benchmark = _reports()
    ppo["folds"][0]["fold"] = 2
    missing = audit_reports(ppo, benchmark, ppo_seed=42)
    assert missing["status"] == "INCONCLUSIVE"
    assert "lacks matching" in missing["reason"]

    ppo = _seed_results_report()
    ppo["folds"].append(copy.deepcopy(ppo["folds"][0]))
    duplicate = audit_reports(ppo, benchmark, ppo_seed=42)
    assert duplicate["status"] == "INCONCLUSIVE"
    assert "duplicated" in duplicate["reason"]


def test_seed_results_cli_accepts_explicit_seed(tmp_path):
    ppo = _seed_results_report()
    _, benchmark = _reports()
    ppo_path = tmp_path / "ppo.json"
    benchmark_path = tmp_path / "benchmark.json"
    output_path = tmp_path / "audit.json"
    ppo_path.write_text(json.dumps(ppo), encoding="utf-8")
    benchmark_path.write_text(json.dumps(benchmark), encoding="utf-8")

    exit_code = main([
        "--ppo-report", str(ppo_path),
        "--benchmark-report", str(benchmark_path),
        "--ppo-seed", "42",
        "--output", str(output_path),
    ])
    assert exit_code == 0
    result = json.loads(output_path.read_text(encoding="utf-8"))
    assert result["status"] == "PASS"
    assert result["selected_seed"] == 42
