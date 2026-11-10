"""Tests for comparing saved PPO folds with the passive benchmark."""

import pandas as pd
import pytest

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
