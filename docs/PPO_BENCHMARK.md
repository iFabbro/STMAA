# PPO benchmark comparison

This benchmark compares an existing PPO walk-forward report with an equal-weight
buy-and-hold portfolio and SPY on exactly the same out-of-sample test dates.

## Method

- Uses the seven-ticker PPO universe and fold boundaries stored in the PPO report. SPY is downloaded separately and is never added to the PPO training universe.
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
  are used for all strategies.
- SPY is evaluated at fixed 50% gross exposure (comparable to the PPO gross cap)
  and at 100% gross exposure (unscaled market reference). The PPO per-position
  cap is not applied to SPY because it is a single ETF. Both variants enter at
  the next test bar Open and incur the same transaction-cost and slippage rates,
  with no rebalancing.

The benchmarks are passive references, not optimized strategies. The report
compares equal-weight, SPY at 50% exposure, and SPY at 100% exposure. The
100% SPY reference is not exposure-matched and is a separate market context,
not a like-for-like risk comparison.
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

The raw data range must match the original PPO walk-forward run. The runner
downloads the original seven PPO tickers plus SPY, keeping SPY outside the PPO
training universe, and validates every saved test window and observation count. It reads the existing PPO report and does not
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
