"""Tests for comparing saved PPO folds with the passive benchmark."""

import pandas as pd
import pytest

import benchmark_walk_forward as benchmark_cli
from benchmark_walk_forward import compare_walk_forward_report


def _history():
    index = pd.date_range("2024-01-02", periods=4, freq="B")
    return pd.DataFrame(
        {
            "Open_A": [10.0, 10.0, 11.0, 12.0],
            "Close_A": [10.0, 11.0, 12.0, 13.0],
            "Open_B": [20.0, 20.0, 20.0, 20.0],
            "Close_B": [20.0, 20.0, 20.0, 20.0],
            "Open_SPY": [100.0, 100.0, 101.0, 102.0],
            "Close_SPY": [100.0, 101.0, 102.0, 103.0],
        },
        index=index,
    )


def _report():
    return {
        "data": {"tickers": ["A", "B"]},
        "training_parameters": {
            "initial_capital": 1000.0,
            "transaction_cost": 0.001,
            "slippage": 0.0005,
            "max_position_size": 0.2,
            "max_gross_exposure": 0.5,
        },
        "folds": [
            {
                "fold": 1,
                "test_start": "2024-01-03",
                "test_end": "2024-01-05",
                "test_observations": 3,
                "metrics": {
                    "total_return": 0.01,
                    "sharpe_ratio": 0.5,
                },
            }
        ],
    }


def test_comparison_uses_saved_fold_dates_and_reports_return_difference():
    result = compare_walk_forward_report(_history(), _report())
    assert result["summary"]["fold_count"] == 1
    assert result["folds"][0]["observations"] == 3
    assert result["folds"][0]["equal_weight_buy_and_hold"]["steps"] == 2
    assert result["folds"][0]["comparison"]["return_difference_percentage_points"] == pytest.approx(
        (
            result["folds"][0]["equal_weight_buy_and_hold"]["total_return"] - 0.01
        ) * 100.0
    )


def test_comparison_rejects_missing_saved_test_dates():
    history = _history().iloc[:3]
    with pytest.raises(ValueError, match="does not exactly cover"):
        compare_walk_forward_report(history, _report())


def test_comparison_adds_spy_at_50_and_100_percent_without_changing_ppo_universe():
    result = compare_walk_forward_report(_history(), _report())
    fold = result["folds"][0]
    assert result["data"]["tickers"] == ["A", "B"]
    assert result["data"]["benchmark_ticker"] == "SPY"
    assert fold["spy_50pct_exposure"]["gross_target_exposure"] == pytest.approx(0.5)
    assert fold["spy_100pct_exposure"]["gross_target_exposure"] == pytest.approx(1.0)
    assert fold["spy_50pct_exposure"]["entry_execution"] == "next_bar_open"
    assert fold["spy_100pct_exposure"]["entry_execution"] == "next_bar_open"


def test_comparison_rejects_spy_inside_ppo_training_universe():
    report = _report()
    report["data"]["tickers"].append("SPY")
    with pytest.raises(ValueError, match="outside the PPO training ticker universe"):
        compare_walk_forward_report(_history(), report)


def test_comparison_rejects_missing_spy_prices():
    history = _history().drop(columns=["Open_SPY", "Close_SPY"])
    with pytest.raises(ValueError, match="missing SPY"):
        compare_walk_forward_report(history, _report())


def test_multi_seed_comparison_reuses_one_benchmark_per_fold_for_all_seeds():
    from benchmark_walk_forward import compare_multi_seed_walk_forward_report
    base = _report()
    fold = {
        "fold": 1, "training_start": "2023-01-02", "training_end": "2024-01-02",
        "test_start": "2024-01-03", "test_end": "2024-01-05",
        "training_observations": 250, "test_observations": 3,
    }
    base["runs"] = [
        {"seed": 11, "fold_count": 1, "folds": [{**fold, "metrics": {"total_return": 0.01}}]},
        {"seed": 22, "fold_count": 1, "folds": [{**fold, "metrics": {"total_return": 0.02}}]},
    ]
    base.pop("folds")
    result = compare_multi_seed_walk_forward_report(_history(), base)
    assert result["seeds"] == [11, 22]
    assert result["summary"]["ppo_run_count"] == 2
    assert "mean_ppo_fold_return" not in result["summary"]
    assert "benchmark_return_wins" not in result["summary"]
    assert len(result["folds"]) == 1
    assert len(result["folds"][0]["ppo_by_seed"]) == 2
    assert result["folds"][0]["equal_weight_buy_and_hold"]["observations"] == 3
    assert result["seed_results"][1]["folds"][0]["ppo"]["total_return"] == pytest.approx(0.02)


def test_multi_seed_comparison_rejects_changed_fold_layout():
    from benchmark_walk_forward import compare_multi_seed_walk_forward_report
    base = _report()
    fold = {
        "fold": 1, "training_start": "2023-01-02", "training_end": "2024-01-02",
        "test_start": "2024-01-03", "test_end": "2024-01-05",
        "training_observations": 250, "test_observations": 3,
        "metrics": {"total_return": 0.01},
    }
    changed = {**fold, "test_end": "2024-01-08"}
    base["runs"] = [
        {"seed": 11, "fold_count": 1, "folds": [fold]},
        {"seed": 22, "fold_count": 1, "folds": [changed]},
    ]
    base.pop("folds")
    with pytest.raises(ValueError, match="inconsistent fold boundaries"):
        compare_multi_seed_walk_forward_report(_history(), base)



def test_cli_loads_project_dotenv_before_downloading_history(monkeypatch, tmp_path):
    events = []
    monkeypatch.setattr(
        benchmark_cli,
        "load_dotenv",
        lambda **kwargs: events.append(("dotenv", kwargs)),
    )
    monkeypatch.setattr(benchmark_cli, "_load_ppo_report", lambda path: _report())
    monkeypatch.setattr(benchmark_cli, "DataProcessor", lambda path: object())

    def fake_prepare_historical_data(processor, tickers, start_date, end_date):
        events.append(("history", tickers))
        return _history()

    monkeypatch.setattr(
        benchmark_cli, "prepare_historical_data", fake_prepare_historical_data
    )
    monkeypatch.setattr(
        benchmark_cli,
        "compare_walk_forward_report",
        lambda history, report, include_timeseries=False: {
            "summary": {"fold_count": 1, "benchmark_return_wins": 0},
            "folds": [],
        },
    )

    benchmark_cli.main(
        [
            "--ppo-report",
            str(tmp_path / "ppo.json"),
            "--start-date",
            "2024-01-01",
            "--end-date",
            "2024-02-01",
            "--report-path",
            str(tmp_path / "comparison.json"),
        ]
    )

    assert [event[0] for event in events] == ["dotenv", "history"]
    assert events[0][1] == {
        "dotenv_path": benchmark_cli.BASE_DIR / ".env",
        "override": False,
    }
