# RW-08.3 — Persistent MOO/OPG execution contract

## Scope

RW-08.3 connects the completed RW-08.1 NYSE session resolver and RW-08.2 integer sizing plan to a durable, idempotent OPG batch workflow. This contract is deliberately separate from the existing DAY rebalance path. It does not authorize live trading and does not include RW-08.4 account/buying-power/asset preflight.

All automated tests use an injected broker mock. No test may submit, cancel, replace, or close an order against Alpaca Paper or Live.

## Order contract

For each plan asset with `delta_quantity != 0`, build exactly one `MarketOrderRequest` with `TimeInForce.OPG`, positive integer `qty = abs(delta_quantity)`, `OrderSide.BUY` for positive deltas and `OrderSide.SELL` for negative deltas. Do not create an order for zero deltas. Reject booleans, fractional values, zero, negative quantities, malformed symbols, and client IDs outside Alpaca's supported length. There is no DAY, fractional, limit-order, or automatic fallback.

## Stable identity

The batch key is deterministic over the broker account identity, completed signal-bar timestamp (normalized to UTC), target NYSE session, PPO model-contract version, and execution-sizing-contract version. The same logical signal must resolve to the same batch key across process restarts; a different account, signal bar, or target session must not collide.

Each order client ID is deterministic over the batch key and normalized symbol, unique within the batch, stable across restarts, and no longer than 48 characters. Random UUIDs are not valid OPG identities.

## Durable batch record

Persist `opg_batches` in broker state. Each batch records: batch ID; account ID and account mode; signal-bar timestamp; target NYSE session; PPO model version; sizing-contract version; complete immutable `plan.to_dict()` snapshot; expected orders and deterministic client IDs; per-order submission state; requested and filled quantities; average fill price when available; reconciliation state; created/updated timestamps; and blocking reason/evidence when unresolved.

A batch must be durably recorded, including every order's pre-submission intent, before the first call to `submit_order`. A persistence error blocks all submissions. A duplicate batch ID returns the existing record and must not submit again. A batch with unresolved submission/reconciliation state blocks any subsequent OPG batch.

## State schema migration

The existing broker state uses schema version 1 and already persists DAY unresolved/confirmed submissions, liquidation safety maps, corporate-action reconciliation, and a monotonic revision under the single-host file lock. RW-08.3 must add OPG state through an explicit, deterministic schema migration. Migration from v1 preserves every existing key and nested record byte-for-value at the JSON data level, adds an empty `opg_batches` object, and advances to the new schema version. Unsupported versions, malformed maps, corrupt JSON, or failed migration must fail closed without overwriting the original state file. The existing atomic write, fsync, flock, and stale-revision protections remain mandatory.

## Submission and idempotency

Submit OPG orders sequentially in deterministic symbol order. After each acknowledgement, persist broker order ID, client order ID, full identity, status, and fill snapshot before continuing. Stop the batch immediately on any submission exception, missing/invalid acknowledgement, identity mismatch, persistence failure, or ambiguous outcome.

For an ambiguous outcome, query by the deterministic client order ID. A verified matching broker order may be recorded; missing visibility, lookup failure, incomplete status/fill data, or identity mismatch remains unresolved. Absence from one broker read is not proof that the order was never accepted. Never resubmit an ambiguous order and never continue with later symbols.

## Reconciliation

Validate broker order ID, client order ID, symbol, side, integer quantity, market type, and `time_in_force=opg`. Track accepted/new/pending, partially filled, filled, canceled/cancelled, expired, rejected, and unknown/unresolvable states distinctly. Validate filled quantity and average fill price when present. A terminal order is not position-reconciled until a fresh broker position read confirms the expected post-fill quantity using the persisted pre-order position baseline. Partial fills remain partial; do not submit the residual. No retroactive catch-up is permitted after the OPG window is missed.

## Paper-only boundary

The RW-08.3 executor must fail closed unless the configured account mode is Paper and the broker account identity matches the persisted batch. This is a software safety boundary, not a claim of live-trading readiness. Unit/integration tests must use mocks and must assert that no Live path or network order side effect is reached.

## PR acceptance sequence

- PR A: executable contract tests and this specification; tests are staged as strict expected failures until the corresponding PR B/C implementation lands.
- PR B: schema migration, deterministic identity, batch persistence, request construction, and sequential fail-closed submission.
- PR C: restart recovery, ambiguous/partial/terminal order reconciliation, and position verification.
- Final gate: full pytest, Ruff, Gitleaks, review of the combined diff, and explicit operator authorization before merge.

RW-08.4 preflight (account trading status, buying power, eligible assets, and aggregate purchase budget) remains out of scope.