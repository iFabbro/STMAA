# PPO and benchmark daily exposure telemetry

The walk-forward evaluator supports an opt-in `include_timeseries=True` mode.
It is intended for diagnostic reruns; the default remains `False` so routine
validation results and saved model metadata do not grow with daily traces.

## PPO trace

`PPOAgent.evaluate(..., include_timeseries=True)` returns
`timeseries_contract: ppo_daily_exposure_v1` and one row per executed test bar.
The row records timestamp, close-marked equity, policy target weights,
pre-rebalance weights at the open, executed weights after rebalance,
close-marked holdings weights, gross exposure at open and close, turnover,
and transaction cost.

Target/executed weights are distinct from close weights: price movement after
execution causes weights to drift during the bar.

## Buy-and-hold benchmark trace

`evaluate_equal_weight_buy_and_hold(..., include_timeseries=True)` returns
`timeseries_contract: benchmark_daily_exposure_v1`. It records each
close-marked ticker weight, gross exposure, equity, turnover, and transaction
cost. Shares and cash remain unchanged after the initial next-open entry, so
close weights are allowed to drift. The SPY wrapper uses the same trace schema.

## Interpretation and safeguards

- Exposure threshold checks use `gross_exposure_close` and each ticker's
  `close_weights`, not only initial target weights.
- PPO `executed_weights` should satisfy per-position and gross target caps
  immediately after rebalance. Close-marked weights may drift beyond those
  targets due to relative price movement; this is realized drift, not an
  execution-cap breach.
- Benchmark close weights are not rebalanced and can drift above initial
  target weights.
- Turnover and transaction costs come from the same accounting as the net
  equity curve. Do not subtract them a second time.
- Each fold remains an independent all-cash experiment. Do not concatenate
  fold traces into a synthetic continuous portfolio.
- Traces are opt-in and do not change training, policy actions, fills, costs,
  or the model artifact contract.