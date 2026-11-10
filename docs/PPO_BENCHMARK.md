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
downloads the PPO tickers plus SPY, keeping SPY outside the PPO training
universe, and validates every saved test window, observation count, and (when
present) exact execution-price fingerprint. The fingerprint covers ordered
Open/Close values and timestamps for each fold. A mismatch aborts the comparison;
matching dates alone are not accepted as data identity. Legacy PPO reports without
a fingerprint remain INCONCLUSIVE for provenance.

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


## Multi-seed report compatibility

The comparison CLI also accepts the JSON report produced by the multi-seed
walk-forward runner. It verifies that every seed uses the same fold boundaries,
evaluates each passive benchmark once per market fold, and compares every
seed/fold PPO result against that shared benchmark result. Benchmark results
are not duplicated as if they were independent observations. Per-seed results
remain separate, and PPO returns are never concatenated across folds.


## Provenance and exposure-matched comparator

Generate the PPO report with daily traces enabled:

```bash
uv run --locked python walk_forward.py ... --include-timeseries
```

Then generate the benchmark report with the same option:

```bash
uv run --locked python benchmark_walk_forward.py ... --include-timeseries
```

The PPO walk-forward producer stores a SHA-256 fingerprint for each fold's ordered Open/Close execution panel and declares next_bar_open execution. The benchmark producer recomputes that fingerprint from its downloaded fold data and refuses to compare a fold when a saved PPO fingerprint differs. Reports created before this integration do not contain the fingerprint and cannot be retroactively certified.

When PPO daily traces are available, the report also calculates ppo_exposure_matched_equal_weight. At each next-bar Open it takes only the PPO target gross exposure for that execution and distributes the same gross exposure equally across the PPO ticker universe. It rebalances daily, applies the same transaction-cost and slippage rates to turnover, and marks holdings at the same day's Close. It does not copy PPO's asset selections or weights. This comparator helps separate asset selection from gross-exposure timing, but it remains an active daily-rebalanced reference and is not a buy-and-hold portfolio.

The exposure-matched comparator is currently available for single-seed reports with complete PPO traces. For multi-seed reports, a single shared exposure schedule would misrepresent the different seed policies, so the comparator is reported as unavailable/inconclusive rather than borrowing the first seed's exposure path. The existing static equal-weight and SPY comparisons remain available.

For the fail-closed evidence audit, use both reports with their daily traces:

```bash
uv run --locked python benchmark_methodology_audit.py \
  --ppo-report reports/ppo_walk_forward_YYYYMMDD.json \
  --benchmark-report reports/ppo_benchmark_comparison_YYYYMMDD.json \
  --output reports/benchmark_methodology_audit_YYYYMMDD.json
```
