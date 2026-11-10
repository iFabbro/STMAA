# PPO benchmark methodology audit

The audit compares saved PPO walk-forward reports with the corresponding benchmark
comparison report. It is deliberately fail-closed: absent provenance or exposure
traces are reported as `INCONCLUSIVE`, never silently accepted as equivalent.

## Run

```bash
uv run --locked python benchmark_methodology_audit.py \
  --ppo-report reports/ppo_walk_forward_YYYYMMDD.json \
  --benchmark-report reports/ppo_benchmark_comparison_YYYYMMDD.json \
  --output reports/benchmark_methodology_audit_YYYYMMDD.json
```

Exit codes: 0 = PASS, 2 = FAIL, 3 = INCONCLUSIVE.

## Checks

- Fold boundaries and observation counts.
- Initial capital, transaction cost, slippage, per-name cap and gross cap.
- Exact execution-data fingerprint, when both source reports contain it.
- Next-bar Open entry declaration, when both reports contain it.
- Presence of daily PPO and benchmark exposure traces.
- Presence and non-negativity of reported transaction-cost totals.

The helper in `src/backtest/benchmark_methodology.py` fingerprints the ordered
execution-price panel (Open and Close for each ticker, plus the full DatetimeIndex).
It intentionally excludes indicators and unrelated columns. This is the appropriate
identity primitive for comparing fills and mark-to-market inputs.

## Interpretation and known limitations

The existing walk-forward and benchmark report producers do not yet persist this
execution-panel fingerprint in every fold. Older reports therefore return
`INCONCLUSIVE` for data identity. They cannot be retroactively certified from
matching dates and row counts alone.

The benchmark CLI supports `--include-timeseries` for benchmark exposure traces.
The PPO walk-forward producer must also be run with `include_timeseries=True` to
retain the policy's daily weights, gross exposure, turnover and costs. The audit
checks trace presence, not equivalence of realized exposure; a matched-exposure
passive comparator is a separate experiment and is not synthesized by this tool.

Cost totals are checked for availability and validity; their absolute values are
not expected to match because PPO trades dynamically while buy-and-hold trades
only on entry. A valid comparison requires the same cost rates and consistent
turnover accounting, not identical total costs.

A PASS is limited to the implemented evidence checks. It does not prove identical
provider revisions, market impact, queue position, partial fills, or live execution.
No broker interaction or order submission is performed.
