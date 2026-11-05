# RW-08.4 — Operational OPG preflight contract

## Scope

RW-08.4 adds a read-only operational gate between the immutable RW-08.2 sizing plan and RW-08.3 batch persistence/submission. It verifies the current Paper account, available buying power, each asset's eligibility for the specific order side, and the aggregate estimated purchase budget.

The preflight is not an order-submission operation. It must not submit, cancel, replace, close, or otherwise mutate broker orders or positions. All tests use mocked broker APIs. The existing DAY rebalance path and its risk/liquidation semantics are out of scope.

The preflight must run inside the same executor lock and immediately before the OPG batch is persisted. A failed or incomplete preflight must stop the batch before the first durable OPG intent and before any broker order mutation.

## Required account snapshot

Obtain a fresh account response from Alpaca for every OPG batch. Cached or persisted account values are not acceptable.

Require all of the following:

- `account_data_fresh is True` and source is the current broker account endpoint.
- Current account ID is present, verified by the current executor instance, and matches the persisted `state.account_id`.
- Persisted and configured account mode are Paper; the executor's TradingClient is Paper.
- Account status normalizes to `active`.
- `trading_blocked is False`; missing, null, or true is a block.
- `equity` and `buying_power` are finite numeric values; equity is positive and buying power is non-negative.
- The account snapshot timestamp is present and belongs to the current read.

Do not fall back to configured capital, the previous account snapshot, `portfolio_value`, or cached buying power if the broker read fails. A stale or incomplete snapshot blocks the entire OPG batch, including sell-only batches.

## Position freshness and sizing-plan consistency

Read the broker's current positions before approving the plan. For every symbol in `plan.assets`, the broker quantity must be present (zero when absent) and exactly equal to the plan's `current_quantity`. Reject fractional, negative, non-finite, malformed, or duplicate position data.

Any non-zero broker position outside the sizing plan's symbol universe blocks the batch. This catches holdings opened or changed after sizing and avoids silently omitting account exposure. The preflight must not modify or re-size the plan to accommodate drift.

## Asset eligibility is side-specific

Fetch current asset metadata for every symbol with a non-zero `delta_quantity`. Normalize enum-like values through their `value` attribute; missing or unknown fields fail closed.

Require:

- The returned asset symbol matches the requested normalized symbol.
- Asset class is `us_equity`.
- Asset status is `active`.
- The asset's `tradable` field is an actual boolean.
- Assets carrying the `ipo` attribute are blocked for this MOO market-order path; Alpaca documents that pre-first-trade IPO symbols accept limit orders only.

Side-specific handling:

- BUY: `tradable is True` is mandatory.
- SELL: `tradable is True` is accepted. An active asset with `tradable is False` may be accepted only as a close-only sale, and only when the fresh broker position is long and the requested integer sell quantity does not exceed that position. This reflects Alpaca's distinction between assets that cannot be opened but can still be closed.
- Missing `tradable`, inactive status, wrong asset class, symbol mismatch, unknown metadata, or IPO attribute blocks either side.
- `fractionable` and `shortable` do not authorize fractional or short OPG orders. RW-08.3 continues to enforce positive whole-share quantities, and the sizing contract is long-only.

## Aggregate purchase budget

Calculate the estimated purchase requirement using only BUY deltas:

```text
estimated_buy_notional = sum(buy_delta_quantity_i * completed_signal_bar_close_i)
```

Use decimal arithmetic. Do not use the full target notional, integer-sized target notional, sells, or rounding residuals as substitutes for the incremental BUY requirement. SELL proceeds must not be counted as available buying power before execution.

Require:

- Every BUY quantity is a positive integer.
- Every BUY reference Close is finite and positive.
- Aggregate estimated BUY notional is finite and non-negative.
- If there are BUY orders, broker buying power must be at least the aggregate estimate.
- A sell-only or no-order batch does not require positive buying power, but still requires a fresh, valid Paper account and asset/position checks.

Alpaca's reported buying power already accounts for reservations from relevant open orders; do not subtract estimated open-order reserves a second time. The broker remains the final authority when accepting each market order.

**Important limitation:** the estimate uses the completed signal-bar Close, while an MOO order executes in the next opening auction. An overnight gap can make the actual purchase cost higher than this estimate. This preflight is a fail-closed affordability screen at the available reference price, not a price cap or guarantee of auction affordability. RW-08.3 must still stop on any broker rejection or ambiguous submission; no residual retry or catch-up is allowed. Do not invent a fixed price buffer in code: a future buffer policy must be separately specified, configured, tested, and versioned.

## Result and integration

The public executor method is `preflight_opg_plan(plan)`. On success it returns a JSON-serializable report containing:

- `approved: true`;
- verified account ID, account snapshot timestamp, equity and buying power;
- normalized order-side asset checks;
- BUY and SELL order counts;
- estimated aggregate BUY notional and buying-power headroom.

On any failed, missing, contradictory, stale, or unparseable input it raises a clear `RuntimeError` (or a documented validation error) and does not persist an OPG batch or call a broker mutation endpoint.

`execute_opg_plan` must call this preflight after acquiring its execution lock and before `_persist_opg_batch` or the first `submit_order`. The report should be attached to the persisted batch as immutable preflight evidence. The evidence must identify its source and timestamp; it must not be represented as a broker fill or a guarantee of future buying power.

## PR acceptance sequence

- PR A: this contract and mock-only executable tests, initially staged as strict expected failures.
- PR B: implement account/position/asset/budget validation and integrate it before OPG batch persistence/submission.
- PR C (only if required after review): address any separately identified integration, observability, or documentation gaps without widening the DAY path.
- Final gate: full pytest, Ruff, Gitleaks, review of the complete diff, and explicit operator authorization before merge.

RW-08.4 does not authorize Live trading and does not change the Paper-only boundary established by RW-08.3.


## References

- [Alpaca account endpoint](https://docs.alpaca.markets/us/docs/working-with-account) — account status, trading restriction and buying-power fields.
- [Alpaca asset endpoint](https://docs.alpaca.markets/us/docs/working-with-assets) — asset status, tradability and per-symbol lookup.
- [Alpaca asset model](https://docs.alpaca.markets/us/reference/get-v2-assets-1) — asset class, status, tradable and IPO attributes.
- [Alpaca order buying-power checks](https://docs.alpaca.markets/us/v1.1/docs/orders-at-alpaca) — available buying power and the effect of existing opening orders.
- [Alpaca asset FAQ](https://docs.alpaca.markets/us/docs/broker-api-faq) — distinction between active-but-nontradable close-only assets and tradable assets.
