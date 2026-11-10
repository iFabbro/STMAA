"""Tests for the fail-closed PPO benchmark methodology report auditor."""
from benchmark_methodology_audit import audit_reports


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
            "metrics": {"total_transaction_costs": 1.5, "timeseries": [{"gross_exposure_close": 0.3}]},
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


def test_methodology_audit_passes_when_all_required_report_evidence_matches():
    result = audit_reports(*_reports())
    assert result["status"] == "PASS"
    assert all(value == "PASS" for value in result["checks"].values())


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
