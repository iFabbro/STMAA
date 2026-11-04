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

## Reconciliation and restart recovery

Recovery is a read-only operation and runs only after a fresh broker account snapshot verifies that the current account ID matches the persisted batch account ID and that the executor is in Paper mode. For each persisted order, recover an order with its known broker order ID, or resolve an ambiguous `submission_pending` intent once by its deterministic client order ID. Validate broker order ID, client order ID, symbol, side, integer requested quantity, market type, and `time_in_force=opg`; reject identity mismatches and unknown statuses without mutating broker state.

Track active/new/accepted/pending, partially filled, filled, canceled/cancelled, expired, rejected, and unknown/unresolvable states distinctly. Validate integer filled quantity in the range zero through requested quantity, status/fill consistency, and average fill price when supplied. Active and partially filled orders remain blocking and are refreshed on a later read-only reconciliation. They are never topped up or resubmitted.

A terminal order is not position-reconciled until a fresh broker positions read confirms `pre_order_position_qty + filled_qty` for buys or `pre_order_position_qty - filled_qty` for sells. This applies to terminal partial fills (for example, canceled after a partial fill): only the actual broker-filled quantity is used. Missing baselines, invalid snapshots, or quantity divergence persist an unresolved batch and block further execution.

If a process restarts while an order is `submission_pending`, recovery queries by client ID and never resubmits. Orders still marked `not_submitted` because sequential execution had not reached them are marked `skipped_after_restart`; they are not submitted as catch-up orders. An unresolved or partially reconciled batch blocks both new OPG batches and the existing DAY execution path. No retroactive catch-up is permitted after the OPG window is missed.

## Paper-only boundary

The RW-08.3 executor must fail closed unless the configured account mode is Paper and the broker account identity matches the persisted batch. This is a software safety boundary, not a claim of live-trading readiness. Unit/integration tests must use mocks and must assert that no Live path or network order side effect is reached.

## PR acceptance sequence

- PR A: executable contract tests and this specification; all contract tests are active and use mocked broker interactions.
- PR B: schema-v1-to-v2 migration, deterministic identity, batch persistence, OPG request construction, and sequential fail-closed submission. Ambiguous submissions are queried once by stable client ID and remain blocked if not verifiable.
- PR C: read-only startup recovery after fresh account identity verification; broker order identity/status/fill reconciliation; active/partial/terminal handling; fresh position verification; persisted fail-closed evidence; no residual retry or catch-up; block DAY while an OPG batch remains unresolved.
- Final gate: full pytest, Ruff, Gitleaks, review of the combined diff, and explicit operator authorization before merge.

RW-08.4 preflight (account trading status, buying power, eligible assets, and aggregate purchase budget) remains out of scope.
