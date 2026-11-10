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
from dotenv import load_dotenv
from train import prepare_historical_data
from backtest.benchmark import (
    evaluate_equal_weight_buy_and_hold,
    evaluate_exposure_matched_equal_weight,
    evaluate_single_asset_buy_and_hold,
)

from backtest.benchmark_methodology import execution_data_fingerprint


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
    if not isinstance(report, dict) or not (
        isinstance(report.get("folds"), list) or isinstance(report.get("runs"), list)
    ):
        raise ValueError("PPO report must contain folds or multi-seed runs")
    data = report.get("data")
    parameters = report.get("training_parameters")
    if not isinstance(data, dict) or not isinstance(data.get("tickers"), list):
        raise ValueError("PPO report is missing data.tickers")
    if not isinstance(parameters, dict):
        raise ValueError("PPO report is missing training_parameters")
    missing = [key for key in _REQUIRED_TRAINING_PARAMETERS if key not in parameters]
    if missing:
        raise ValueError(f"PPO report is missing benchmark parameters: {missing}")
    if "folds" in report and not report["folds"]:
        raise ValueError("PPO report contains no folds")
    if "runs" in report and not report["runs"]:
        raise ValueError("PPO multi-seed report contains no runs")
    return report


def compare_walk_forward_report(
    history: pd.DataFrame,
    ppo_report: dict,
    *,
    include_timeseries: bool = False,
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

        data_fingerprint = execution_data_fingerprint(testing, tickers)
        expected_fingerprint = fold.get("execution_data_fingerprint")
        if expected_fingerprint is not None and expected_fingerprint != data_fingerprint:
            raise ValueError(
                f"Execution-data fingerprint mismatch for PPO fold {fold_number}; "
                "benchmark data is not identical to the PPO evaluation data"
            )

        benchmark = evaluate_equal_weight_buy_and_hold(
            testing,
            tickers,
            initial_capital=initial_capital,
            transaction_cost=float(parameters["transaction_cost"]),
            slippage=float(parameters["slippage"]),
            max_position_size=float(parameters["max_position_size"]),
            max_gross_exposure=float(parameters["max_gross_exposure"]),
            include_timeseries=include_timeseries,
        )
        spy_50 = evaluate_single_asset_buy_and_hold(
            testing, "SPY", exposure=0.5, initial_capital=initial_capital,
            transaction_cost=float(parameters["transaction_cost"]),
            slippage=float(parameters["slippage"]),
            include_timeseries=include_timeseries,
        )
        ppo_trace = ppo_metrics.get("timeseries")
        if isinstance(ppo_trace, list) and len(ppo_trace) == len(testing) - 1:
            matched_exposure = evaluate_exposure_matched_equal_weight(
                testing,
                tickers,
                ppo_trace,
                initial_capital=initial_capital,
                transaction_cost=float(parameters["transaction_cost"]),
                slippage=float(parameters["slippage"]),
                max_position_size=float(parameters["max_position_size"]),
                include_timeseries=include_timeseries,
            )
            matched_exposure_status = "AVAILABLE"
        else:
            matched_exposure = None
            matched_exposure_status = "INCONCLUSIVE"
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
                "entry_execution": "next_bar_open",
                "execution_data_fingerprint": data_fingerprint,
                "execution_data_identity": "PASS" if expected_fingerprint == data_fingerprint else "INCONCLUSIVE",
                "ppo": ppo_metrics,
                "equal_weight_buy_and_hold": benchmark,
                "ppo_exposure_matched_equal_weight": matched_exposure,
                "ppo_exposure_matched_equal_weight_status": matched_exposure_status,
                "spy_50pct_exposure": spy_50,
                "spy_100pct_exposure": spy_100,
                "comparison": {
                    "return_difference_percentage_points": (
                        benchmark_return - ppo_return
                    ) * 100.0,
                    "benchmark_outperformed_ppo_return": benchmark_return > ppo_return,
                    "exposure_matched_return_difference_percentage_points": (
                        (float(matched_exposure["total_return"]) - ppo_return) * 100.0
                        if matched_exposure is not None else None
                    ),
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
        "benchmarks": ["equal_weight_buy_and_hold", "ppo_exposure_matched_equal_weight", "SPY_50pct_exposure", "SPY_100pct_exposure"],
        "execution_data_identity": "Per-fold SHA-256 over ordered Open/Close prices and timestamps; a mismatch against the PPO report aborts comparison.",
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
    parser.add_argument("--include-timeseries", action="store_true", help="Include daily benchmark exposure traces")
    args = parser.parse_args(argv)

    try:
        load_dotenv(dotenv_path=BASE_DIR / ".env", override=False)
        ppo_report = _load_ppo_report(args.ppo_report)
        tickers = list(ppo_report["data"]["tickers"])
        if "SPY" in tickers:
            raise ValueError("SPY must remain outside the PPO training ticker universe")
        history = prepare_historical_data(
            DataProcessor(DATA_DIR), [*tickers, "SPY"], args.start_date, args.end_date
        )
        comparison = (
            compare_multi_seed_walk_forward_report(history, ppo_report, include_timeseries=args.include_timeseries)
            if "runs" in ppo_report
            else compare_walk_forward_report(history, ppo_report, include_timeseries=args.include_timeseries)
        )
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
    if "benchmark_return_wins" in comparison["summary"]:
        print(
            "Equal-weight benchmark return wins: "
            f"{comparison['summary']['benchmark_return_wins']}"
        )
    else:
        print(f"Multi-seed runs: {comparison['summary']['ppo_run_count']}")
        print("Per-seed benchmark wins are available in the JSON report.")
    print(f"Report: {output_path}")
    print("Fold results are independent diagnostics, not a continuous portfolio record.")


def compare_multi_seed_walk_forward_report(history: pd.DataFrame, ppo_report: dict, *, include_timeseries: bool = False) -> dict:
    """Compare every PPO seed with one shared benchmark simulation per fixed fold."""
    runs = ppo_report.get("runs")
    if not isinstance(runs, list) or not runs:
        raise ValueError("Multi-seed PPO report must contain non-empty runs")
    seeds = [run.get("seed") for run in runs if isinstance(run, dict)]
    if len(seeds) != len(runs) or any(
        not isinstance(seed, int) or isinstance(seed, bool) for seed in seeds
    ) or len(set(seeds)) != len(seeds):
        raise ValueError("Multi-seed PPO report must contain unique integer seeds")
    reference_folds = runs[0].get("folds")
    if not isinstance(reference_folds, list) or not reference_folds:
        raise ValueError("Multi-seed PPO run is missing folds")
    layout_keys = (
        "fold", "training_start", "training_end", "test_start", "test_end",
        "training_observations", "test_observations",
    )
    reference_layout = [
        {key: fold.get(key) for key in layout_keys} for fold in reference_folds
    ]
    for run in runs:
        folds = run.get("folds")
        if not isinstance(folds, list) or len(folds) != len(reference_folds):
            raise ValueError("Multi-seed PPO runs have inconsistent fold counts")
        layout = [{key: fold.get(key) for key in layout_keys} for fold in folds]
        if layout != reference_layout:
            raise ValueError("Multi-seed PPO runs have inconsistent fold boundaries")
        if any(
            not isinstance(fold.get("metrics"), dict)
            or "total_return" not in fold["metrics"]
            for fold in folds
        ):
            raise ValueError("Multi-seed PPO fold is missing total_return metrics")

    # A single benchmark schedule cannot represent several distinct PPO
    # exposure schedules. Keep multi-seed exposure matching explicitly
    # inconclusive until a per-seed comparator is computed.
    reference_folds_for_benchmark = []
    for fold in reference_folds:
        benchmark_fold = dict(fold)
        benchmark_metrics = dict(fold["metrics"])
        benchmark_metrics.pop("timeseries", None)
        benchmark_fold["metrics"] = benchmark_metrics
        reference_folds_for_benchmark.append(benchmark_fold)
    single = {**ppo_report, "folds": reference_folds_for_benchmark}
    single.pop("runs", None)
    comparison = compare_walk_forward_report(history, single, include_timeseries=include_timeseries)
    per_seed = []
    for run in runs:
        seed_rows = []
        for index, fold in enumerate(run["folds"]):
            base = comparison["folds"][index]
            metrics = fold["metrics"]
            ppo_return = float(metrics["total_return"])
            ew = float(base["equal_weight_buy_and_hold"]["total_return"])
            spy50 = float(base["spy_50pct_exposure"]["total_return"])
            spy100 = float(base["spy_100pct_exposure"]["total_return"])
            seed_rows.append({
                "fold": fold["fold"],
                "ppo": metrics,
                "comparison": {
                    "equal_weight_return_difference_percentage_points": (ew - ppo_return) * 100.0,
                    "spy_50pct_return_difference_percentage_points": (spy50 - ppo_return) * 100.0,
                    "spy_100pct_return_difference_percentage_points": (spy100 - ppo_return) * 100.0,
                    "equal_weight_outperformed_ppo": ew > ppo_return,
                    "spy_50pct_outperformed_ppo": spy50 > ppo_return,
                    "spy_100pct_outperformed_ppo": spy100 > ppo_return,
                },
            })
        returns = [float(row["ppo"]["total_return"]) for row in seed_rows]
        per_seed.append({
            "seed": run["seed"], "fold_count": len(seed_rows),
            "equal_weight_return_wins": sum(row["comparison"]["equal_weight_outperformed_ppo"] for row in seed_rows),
            "spy_50pct_return_wins": sum(row["comparison"]["spy_50pct_outperformed_ppo"] for row in seed_rows),
            "spy_100pct_return_wins": sum(row["comparison"]["spy_100pct_outperformed_ppo"] for row in seed_rows),
            "mean_ppo_fold_return": sum(returns) / len(returns),
            "median_ppo_fold_return": float(pd.Series(returns).median()),
            "folds": seed_rows,
        })
    for index, base in enumerate(comparison["folds"]):
        base.pop("ppo", None)
        base["ppo_by_seed"] = [
            {"seed": run["seed"], "metrics": run["folds"][index]["metrics"]}
            for run in runs
        ]
        base["seed_comparisons"] = [seed["folds"][index] for seed in per_seed]
    comparison["method"] = "independent_fold_multi_seed_ppo_vs_equal_weight_and_spy"
    comparison["seeds"] = seeds
    # Remove the single-seed summary inherited from the reference run: it would
    # otherwise mislabel the first seed as the aggregate multi-seed result.
    for key in (
        "benchmark_return_wins", "ppo_return_wins",
        "mean_ppo_fold_return", "median_ppo_fold_return",
    ):
        comparison["summary"].pop(key, None)
    comparison["summary"]["seed_count"] = len(seeds)
    comparison["summary"]["ppo_run_count"] = len(seeds) * len(reference_folds)
    comparison["summary"]["per_seed"] = [
        {key: value for key, value in seed.items() if key != "folds"} for seed in per_seed
    ]
    comparison["seed_results"] = per_seed
    comparison["aggregation"] = (
        "Each benchmark is evaluated once per market fold and shared across seeds. "
        "PPO seed/fold runs remain independent; returns are not concatenated or compounded."
    )
    return comparison



if __name__ == "__main__":
    main()
