# PPO benchmark comparison

This benchmark compares an existing PPO walk-forward report with an equal-weight
buy-and-hold portfolio on exactly the same out-of-sample test dates.

## Method

- Uses the seven-ticker universe and fold boundaries stored in the PPO report.
- Each fold starts with independent all-cash capital at its first test observation.
- Both strategies first execute at the Open of the second test observation. This
  matches the PPO evaluator, which observes the first completed test bar before
  it can submit a target for the next bar.
- The benchmark assigns equal target scores and applies the same maximum
  per-position weight and maximum gross exposure as the PPO run. Any amount
  clipped by the per-position cap remains in cash.
- Entry turnover incurs the same transaction-cost plus slippage rate used by PPO.
  The benchmark then holds the purchased shares and does not rebalance.
- The same daily performance-metric implementation and 252-period annualization
  are used for both strategies.

The benchmark is a passive baseline, not a second optimized strategy. The
comparison reports fold-level returns, risk metrics, and return differences.
Summary averages are descriptive statistics over independent folds and must
not be compounded or described as a continuous portfolio track record.

## Run without retraining PPO

Run from the repository root, using the saved report produced by
`walk_forward.py`:

```bash
uv run --locked python benchmark_walk_forward.py \
  --ppo-report reports/ppo_walk_forward_20261109.json \
  --start-date 2018-01-01 \
  --end-date 2026-01-01 \
  --report-path reports/ppo_benchmark_comparison_20261109.json
```

The raw data range must match the one used for the original PPO walk-forward
run. The runner validates that every saved test window is present with the
expected observation count. It reads the existing PPO report and does not
train or load a PPO model.

The data download uses the existing historical Alpaca data processor. This
workflow does not use the broker trading client, access account state, or
submit, replace, cancel, or close orders.

## Interpretation

A PPO return win is not sufficient by itself. Compare drawdown, volatility,
Sharpe/Sortino, turnover, and transaction costs fold by fold. A passive
benchmark that performs better in a given period indicates that PPO's active
decisions did not add value in that period under the modeled costs and
constraints. Neither result establishes future profitability or readiness
for live trading.
