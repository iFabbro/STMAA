# RW-08.2 — Integer OPG sizing contract

## Status and scope

This document describes the pure sizing component introduced by RW-08.2. It does not describe a complete OPG execution workflow. The component has no broker client, network access, order submission, cancellation, persistence or scheduling side effects.

The model artifact contract remains PPO v7. The separate execution-sizing contract is `opg_moo_integer_floor_v1`.

## Inputs

For one completed daily signal bar (t):

- (E_t): positive, finite account equity captured for the signal.
- (w_i): final non-negative PPO v7 target weight after applicable risk controls.
- (C_{i,t}): positive Close price from the completed signal bar.
- (Q_i^{current}): current account position quantity.

The target-weight and Close maps must contain the same non-empty symbol set. The account-position map must contain every target symbol and may contain additional account symbols. All account positions are checked: any fractional or short position blocks the whole sizing batch. A non-zero whole-share position outside the PPO universe also blocks the batch rather than being silently ignored or implicitly liquidated. This includes positions outside the PPO universe.

## Sizing

For each target symbol:

```text
target_notional_i = E_t * w_i
target_quantity_i = floor(target_notional_i / C_i,t)
integer_notional_i = target_quantity_i * C_i,t
rounding_residual_i = target_notional_i - integer_notional_i
integer_weight_i = integer_notional_i / E_t
weight_deviation_i = integer_weight_i - w_i
delta_quantity_i = target_quantity_i - Q_i,current
```

A positive delta is a buy; a negative delta is a sell; zero means no order. The component does not submit any order.

Decimal arithmetic is used for the floor and residual calculations. The residual for each symbol must satisfy:

```text
0 <= rounding_residual_i < C_i,t
```

Portfolio residual is the sum of per-symbol rounding residuals. PPO cash allocation (`1 - sum(target_weights)`) is reported separately. Neither residual nor cash is redistributed to other symbols.

## Interpretation and limitations

The integer-sized weights and deviations in this contract are calculated using signal-bar Close prices. They quantify the sizing difference against the PPO v7 target at that reference price; they are not actual next-session Open weights, broker fills, or realized portfolio weights.

A next-session price gap, auction execution price, unfilled/partially filled quantity, fees and slippage can create additional divergence. RW-08.3 must persist the sizing snapshot, construct MOO/OPG orders, and reconcile actual broker order/fill/position state before any actual-execution deviation can be reported.

This component deliberately does not change PPO v7 training or inference semantics. The training environment continues to model fractional target-weight holdings at the next bar Open. Integer OPG sizing is therefore a separately versioned execution approximation, not exact training/runtime parity.

## Fail-closed rules

- Reject missing, extra or mismatched target/Close symbols.
- Reject non-finite or non-positive equity and Close prices.
- Reject weights outside `[0, 1]` and gross target weights above 1.
- Reject malformed account-position data.
- Reject any fractional or short account position, including symbols outside the model universe.
- Reject non-zero whole-share holdings outside the model universe until an explicit disposition policy exists.
- Do not round existing positions, liquidate them, ignore them, redistribute residuals, or invent replacement prices.
- No DAY fallback and no broker interaction.
