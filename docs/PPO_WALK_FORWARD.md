# PPO walk-forward evaluation

The walk-forward runner evaluates temporal robustness without reusing a test
window to train the policy in the same fold.

## Method

- Uses expanding training windows beginning at the first available observation.
- Each test window starts immediately after its corresponding training window.
- Test windows are chronological and non-overlapping. The next fold expands the
  training history by one test-window step.
- A fresh PPO agent is trained for every fold. Model artifacts are written to
  temporary directories and removed after that fold's evaluation.
- The final training close is passed into the test environment as
  `previous_close`; the first test observation therefore has the correct
  close-to-close return without looking ahead.
- Hyperparameters and seed are fixed for the run. Do not tune them against the
  reported test windows. Multi-seed analysis and benchmark comparisons are
  separate experiments.

Feature calculations may be performed once on the complete downloaded history:
the current feature formulas are causal (rolling windows, lagged previous
close, and EMA with `adjust=False`). At each timestamp they depend only on
that timestamp and earlier observations. The regression suite includes a
future-perturbation test to guard this property.

## Execution and interpretation

Run from the repository root:

```bash
uv run --locked python walk_forward.py \
  --start-date 2018-01-01 \
  --end-date 2026-01-01 \
  --min-train-observations 756 \
  --test-observations 126 \
  --total-timesteps 50000 \
  --seed 42
```

`--end-date` is exclusive. Defaults are 756 training observations and 126
test observations. The runner prints the report location and writes one JSON
report under `reports/`; use `--report-path` to choose another destination.

Each fold starts from independent all-cash capital. Results are reported per
fold; the runner intentionally does not concatenate them into a synthetic
continuous equity curve or claim a compounded out-of-sample track record.
This avoids implying that positions are carried across model retraining
boundaries when the current evaluator resets the portfolio. A later phase
must define and test portfolio-state carry-forward before producing a
continuous walk-forward equity curve.

The test windows are out-of-sample relative to each fold's own training set,
but repeated inspection of these results can still create research-selection
bias. Choose the evaluation design and hyperparameters before interpreting
the results. A positive result is not evidence of future profitability and
does not establish readiness for live trading.

This workflow downloads historical Alpaca market data through the existing
data processor. It does not use the broker trading client, access account
state, or submit, replace, cancel, or close orders. It is separate from
`trade.py` and Paper execution.
