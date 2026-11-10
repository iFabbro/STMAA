#!/usr/bin/env python3
"""Run expanding-window walk-forward evaluation for STMAA PPO."""
import argparse
import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent / "src"))

from config import BASE_DIR, DATA_DIR
from data.data_processor import DataProcessor
from train import load_training_config, prepare_historical_data
from backtest.walk_forward import (
    run_multi_seed_walk_forward_validation,
    run_walk_forward_validation,
)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Train independent PPO policies on expanding historical windows and "
            "evaluate each policy on the immediately following unseen period."
        )
    )
    parser.add_argument("--start-date", required=True, help="Inclusive history start, YYYY-MM-DD")
    parser.add_argument("--end-date", required=True, help="Exclusive history end, YYYY-MM-DD")
    parser.add_argument("--tickers", nargs="+", default=None)
    parser.add_argument("--min-train-observations", type=int, default=756)
    parser.add_argument("--test-observations", type=int, default=126)
    parser.add_argument("--step-observations", type=int, default=None)
    parser.add_argument("--total-timesteps", type=int, default=50000)
    parser.add_argument("--seed", type=int, default=42, help="Single-seed mode (default: 42)")
    parser.add_argument("--seeds", nargs="+", type=int, default=None, help="Run identical folds independently for each seed; overrides --seed")
    parser.add_argument("--learning-rate", type=float, default=3e-4)
    parser.add_argument("--n-steps", type=int, default=2048)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--n-epochs", type=int, default=10)
    parser.add_argument("--gamma", type=float, default=0.99)
    parser.add_argument("--initial-capital", type=float, default=None)
    parser.add_argument("--max-position-size", type=float, default=None)
    parser.add_argument("--max-gross-exposure", type=float, default=None)
    parser.add_argument("--transaction-cost", type=float, default=None)
    parser.add_argument("--slippage", type=float, default=None)
    parser.add_argument("--report-path", type=Path, default=None)
    parser.add_argument("--include-timeseries", action="store_true", help="Include daily exposure and turnover traces for diagnostics")
    args = parser.parse_args(argv)

    if args.total_timesteps <= 0:
        parser.error("--total-timesteps must be positive")
    if args.n_steps <= 0 or args.batch_size <= 0 or args.n_epochs <= 0:
        parser.error("--n-steps, --batch-size and --n-epochs must be positive")
    if args.min_train_observations < 22 or args.test_observations < 2:
        parser.error("training observations must be >= 22 and test observations >= 2")
    if args.step_observations is not None and args.step_observations < args.test_observations:
        parser.error("--step-observations must be >= --test-observations")
    overrides = {}
    for argument, environment_name in (
        ("tickers", "STMAA_TICKERS"),
        ("initial_capital", "STMAA_INITIAL_CAPITAL"),
        ("max_position_size", "MAX_POSITION_SIZE"),
        ("max_gross_exposure", "MAX_PORTFOLIO_EXPOSURE"),
        ("transaction_cost", "TRANSACTION_COST"),
        ("slippage", "SLIPPAGE"),
    ):
        value = getattr(args, argument)
        if value is not None:
            overrides[environment_name] = value

    strategy_config = load_training_config(overrides=overrides)
    tickers = list(strategy_config.tickers)
    history = prepare_historical_data(
        DataProcessor(DATA_DIR), tickers, args.start_date, args.end_date
    )

    evaluation_parameters = {
        "min_train_size": args.min_train_observations,
        "test_size": args.test_observations,
        "step_size": args.step_observations,
        "total_timesteps": args.total_timesteps,
        "initial_capital": strategy_config.initial_capital,
        "transaction_cost": strategy_config.transaction_cost,
        "slippage": strategy_config.slippage,
        "max_position_size": strategy_config.max_position_size,
        "max_gross_exposure": strategy_config.max_gross_exposure,
        "learning_rate": args.learning_rate,
        "n_steps": args.n_steps,
        "batch_size": args.batch_size,
        "n_epochs": args.n_epochs,
        "gamma": args.gamma,
        "include_timeseries": args.include_timeseries,
    }
    if args.seeds is not None:
        if len(set(args.seeds)) != len(args.seeds):
            parser.error("--seeds must contain unique integers")
        report = run_multi_seed_walk_forward_validation(
            history, tickers, seeds=args.seeds, **evaluation_parameters
        )
    else:
        report = run_walk_forward_validation(
            history, tickers, seed=args.seed, **evaluation_parameters
        )
    report["data"] = {
        "tickers": tickers,
        "start_date": str(history.index.min()),
        "end_date": str(history.index.max()),
        "observations": len(history),
    }
    report["training_parameters"] = {
        "learning_rate": args.learning_rate,
        "n_steps": args.n_steps,
        "batch_size": args.batch_size,
        "n_epochs": args.n_epochs,
        "gamma": args.gamma,
        "initial_capital": strategy_config.initial_capital,
        "transaction_cost": strategy_config.transaction_cost,
        "slippage": strategy_config.slippage,
        "max_position_size": strategy_config.max_position_size,
        "max_gross_exposure": strategy_config.max_gross_exposure,
    }

    default_report_name = (
        f"ppo_walk_forward_multiseed_{args.start_date}_{args.end_date}.json"
        if args.seeds is not None
        else f"ppo_walk_forward_{args.start_date}_{args.end_date}.json"
    )
    output_path = args.report_path or (BASE_DIR / "reports" / default_report_name)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    print("PPO walk-forward evaluation completed.")
    print(f"Folds: {report['fold_count']}")
    print(f"Report: {output_path}")
    print("Fold results are independent diagnostics, not a continuous portfolio track record.")


if __name__ == "__main__":
    main()
