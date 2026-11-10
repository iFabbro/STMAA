#!/usr/bin/env python3
"""Compare saved PPO walk-forward folds with an execution-aligned benchmark."""

import argparse
import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent / "src"))

import pandas as pd

from config import BASE_DIR, DATA_DIR
from data.data_processor import DataProcessor
from train import prepare_historical_data
from backtest.benchmark import (
    evaluate_equal_weight_buy_and_hold,
    evaluate_single_asset_buy_and_hold,
)


_REQUIRED_TRAINING_PARAMETERS = (
    "initial_capital",
    "transaction_cost",
    "slippage",
    "max_position_size",
    "max_gross_exposure",
)


def _load_ppo_report(path: Path) -> dict:
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Unable to read PPO report: {path}") from exc
    if not isinstance(report, dict) or not isinstance(report.get("folds"), list):
        raise ValueError("PPO report must contain a folds list")
    data = report.get("data")
    parameters = report.get("training_parameters")
    if not isinstance(data, dict) or not isinstance(data.get("tickers"), list):
        raise ValueError("PPO report is missing data.tickers")
    if not isinstance(parameters, dict):
        raise ValueError("PPO report is missing training_parameters")
    missing = [key for key in _REQUIRED_TRAINING_PARAMETERS if key not in parameters]
    if missing:
        raise ValueError(f"PPO report is missing benchmark parameters: {missing}")
    if not report["folds"]:
        raise ValueError("PPO report contains no folds")
    return report


def compare_walk_forward_report(
    history: pd.DataFrame,
    ppo_report: dict,
) -> dict:
    """Evaluate the equal-weight baseline on the exact saved PPO test dates."""
    tickers = ppo_report["data"]["tickers"]
    if "SPY" in tickers:
        raise ValueError("SPY must remain outside the PPO training ticker universe")
    if not {"Open_SPY", "Close_SPY"}.issubset(history.columns):
        raise ValueError("Market data is missing SPY Open/Close prices")
    parameters = ppo_report["training_parameters"]
    initial_capital = float(parameters["initial_capital"])
    previous_test_end = None
    results = []

    for fold in ppo_report["folds"]:
        try:
            fold_number = int(fold["fold"])
            test_start = pd.Timestamp(fold["test_start"])
            test_end = pd.Timestamp(fold["test_end"])
            ppo_metrics = fold["metrics"]
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("PPO report contains an invalid fold definition") from exc
        if not isinstance(ppo_metrics, dict) or "total_return" not in ppo_metrics:
            raise ValueError(f"PPO fold {fold_number} is missing total_return")
        if previous_test_end is not None and test_start <= previous_test_end:
            raise ValueError("PPO test folds must be chronological and non-overlapping")
        previous_test_end = test_end

        testing = history.loc[test_start:test_end].copy()
        if (
            testing.empty
            or testing.index[0] != test_start
            or testing.index[-1] != test_end
        ):
            raise ValueError(
                f"Market data does not exactly cover PPO fold {fold_number}: "
                f"{test_start} through {test_end}"
            )
        expected_observations = fold.get("test_observations")
        if expected_observations is not None and len(testing) != int(expected_observations):
            raise ValueError(
                f"PPO fold {fold_number} expected {expected_observations} observations, "
                f"but market data contains {len(testing)}"
            )

        benchmark = evaluate_equal_weight_buy_and_hold(
            testing,
            tickers,
            initial_capital=initial_capital,
            transaction_cost=float(parameters["transaction_cost"]),
            slippage=float(parameters["slippage"]),
            max_position_size=float(parameters["max_position_size"]),
            max_gross_exposure=float(parameters["max_gross_exposure"]),
        )
        spy_50 = evaluate_single_asset_buy_and_hold(
            testing, "SPY", exposure=0.5, initial_capital=initial_capital,
            transaction_cost=float(parameters["transaction_cost"]),
            slippage=float(parameters["slippage"]),
        )
        spy_100 = evaluate_single_asset_buy_and_hold(
            testing, "SPY", exposure=1.0, initial_capital=initial_capital,
            transaction_cost=float(parameters["transaction_cost"]),
            slippage=float(parameters["slippage"]),
        )
        ppo_return = float(ppo_metrics["total_return"])
        benchmark_return = float(benchmark["total_return"])
        spy_50_return = float(spy_50["total_return"])
        spy_100_return = float(spy_100["total_return"])
        ppo_sharpe = ppo_metrics.get("sharpe_ratio")
        benchmark_sharpe = benchmark.get("sharpe_ratio")
        results.append(
            {
                "fold": fold_number,
                "test_start": str(test_start),
                "test_end": str(test_end),
                "observations": len(testing),
                "ppo": ppo_metrics,
                "equal_weight_buy_and_hold": benchmark,
                "spy_50pct_exposure": spy_50,
                "spy_100pct_exposure": spy_100,
                "comparison": {
                    "return_difference_percentage_points": (
                        benchmark_return - ppo_return
                    ) * 100.0,
                    "benchmark_outperformed_ppo_return": benchmark_return > ppo_return,
                    "spy_50pct_return_difference_percentage_points": (spy_50_return - ppo_return) * 100.0,
                    "spy_100pct_return_difference_percentage_points": (spy_100_return - ppo_return) * 100.0,
                    "spy_50pct_outperformed_ppo_return": spy_50_return > ppo_return,
                    "spy_100pct_outperformed_ppo_return": spy_100_return > ppo_return,
                    "sharpe_difference": (
                        float(benchmark_sharpe) - float(ppo_sharpe)
                        if benchmark_sharpe is not None and ppo_sharpe is not None
                        else None
                    ),
                },
            }
        )

    ppo_returns = [float(item["ppo"]["total_return"]) for item in results]
    benchmark_returns = [
        float(item["equal_weight_buy_and_hold"]["total_return"]) for item in results
    ]
    wins = sum(
        item["comparison"]["benchmark_outperformed_ppo_return"] for item in results
    )
    spy_50_returns = [float(item["spy_50pct_exposure"]["total_return"]) for item in results]
    spy_100_returns = [float(item["spy_100pct_exposure"]["total_return"]) for item in results]
    spy_50_wins = sum(item["comparison"]["spy_50pct_outperformed_ppo_return"] for item in results)
    spy_100_wins = sum(item["comparison"]["spy_100pct_outperformed_ppo_return"] for item in results)
    return {
        "method": "independent_fold_equal_weight_and_spy_buy_and_hold",
        "benchmarks": ["equal_weight_buy_and_hold", "SPY_50pct_exposure", "SPY_100pct_exposure"],
        "execution_alignment": (
            "Both strategies start all-cash at the first test observation and "
            "first enter at the next bar Open. The benchmark pays the same "
            "turnover-based transaction_cost plus slippage, uses the same "
            "per-position and gross-exposure caps for equal-weight. SPY is evaluated "
            "separately at fixed 50% and 100% exposure without the PPO per-position "
            "cap. All benchmarks use next-bar Open entry, identical cost and slippage "
            "rates, and no rebalancing."
        ),
        "aggregation": (
            "Fold results are independent all-cash simulations. Summary averages "
            "are descriptive only; returns are not compounded across folds."
        ),
        "data": {
            "tickers": tickers,
            "benchmark_ticker": "SPY",
            "observations": len(history),
            "start_date": str(history.index.min()),
            "end_date": str(history.index.max()),
        },
        "parameters": {
            key: parameters[key] for key in _REQUIRED_TRAINING_PARAMETERS
        },
        "summary": {
            "fold_count": len(results),
            "benchmark_return_wins": wins,
            "ppo_return_wins": len(results) - wins,
            "mean_ppo_fold_return": sum(ppo_returns) / len(ppo_returns),
            "mean_benchmark_fold_return": sum(benchmark_returns) / len(benchmark_returns),
            "median_ppo_fold_return": float(pd.Series(ppo_returns).median()),
            "median_benchmark_fold_return": float(pd.Series(benchmark_returns).median()),
            "spy_50pct_exposure_return_wins": spy_50_wins,
            "spy_100pct_exposure_return_wins": spy_100_wins,
            "mean_spy_50pct_exposure_fold_return": sum(spy_50_returns) / len(spy_50_returns),
            "median_spy_50pct_exposure_fold_return": float(pd.Series(spy_50_returns).median()),
            "mean_spy_100pct_exposure_fold_return": sum(spy_100_returns) / len(spy_100_returns),
            "median_spy_100pct_exposure_fold_return": float(pd.Series(spy_100_returns).median()),
        },
        "folds": results,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Compare an existing PPO walk-forward report with equal-weight buy-and-hold"
    )
    parser.add_argument("--ppo-report", type=Path, required=True)
    parser.add_argument("--start-date", required=True, help="Inclusive raw history start, YYYY-MM-DD")
    parser.add_argument("--end-date", required=True, help="Exclusive raw history end, YYYY-MM-DD")
    parser.add_argument("--report-path", type=Path, default=None)
    args = parser.parse_args(argv)

    try:
        ppo_report = _load_ppo_report(args.ppo_report)
        tickers = list(ppo_report["data"]["tickers"])
        if "SPY" in tickers:
            raise ValueError("SPY must remain outside the PPO training ticker universe")
        history = prepare_historical_data(
            DataProcessor(DATA_DIR), [*tickers, "SPY"], args.start_date, args.end_date
        )
        comparison = compare_walk_forward_report(history, ppo_report)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))

    output_path = args.report_path or (
        BASE_DIR / "reports" / "ppo_benchmark_comparison.json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(comparison, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    print("PPO versus equal-weight buy-and-hold comparison completed.")
    print(f"Folds: {comparison['summary']['fold_count']}")
    print(
        "Benchmark return wins: "
        f"{comparison['summary']['benchmark_return_wins']}"
    )
    print(f"Report: {output_path}")
    print("Fold results are independent diagnostics, not a continuous portfolio record.")


if __name__ == "__main__":
    main()
