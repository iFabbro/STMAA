# PPO Shadow Collector

## Scope

This is an independent research collector. It reads completed Alpaca SIP daily bars, calls the validated PPO inference interface, records target weights and feature snapshots, and simulates a virtual portfolio. It does not submit, replace, cancel, or close broker orders and does not import trade.py, a broker executor, or an Alpaca trading client. The component remains experimental and does not establish profitability or production readiness.

## Timing contract

A decision uses the latest completed daily bar (t). Its target remains pending until the immediately following NYSE session. At that session, the previous target is applied at the Open. Existing shares are marked at the new Open before rebalance, so they absorb the overnight gap; new holdings then earn the Open-to-Close return. A new decision is generated for the next session.

The collector rejects a skipped session rather than simulating a stale target at a later Open. If a transition was persisted but inference failed, retrying the same session can finish that decision without executing the prior target twice. Advancing to another session without completing the missing decision fails closed.

## Portfolio and benchmark

The virtual portfolio is long-only and enforces the current 20% per-position and 50% gross-exposure caps. Turnover costs and slippage use the configured rates. The SPY buy-and-hold benchmark starts with the same notional at the first next-session Open and is marked at each subsequent Close. Strategy and benchmark transitions are persisted together.

## Provenance and persistence

At startup, the operational constructor always recomputes the loaded PPO artifact SHA-256 and validates its content-addressed filename, manifest digest, model contract, market-data contract, and active strategy settings. Arbitrary provenance injection is not part of the constructor API. The ledger header freezes the verified provenance and effective collector configuration; later runs with different model/configuration are rejected.

The JSONL ledger uses monotonically increasing sequence numbers, previous-record hashes, SHA-256 record hashes, shared reader / exclusive writer process locks, complete-write loops, and fsync. Symlink paths are rejected. A partial, malformed, reordered, or modified record fails closed; the collector never truncates or repairs a damaged ledger. Preserve the original file and create a forensic copy before any manual recovery. Protect the ledger and lock file with restrictive permissions and backups.

Replay validates both the hash chain and the semantic state machine: one decision may be pending at a time; each transition must consume that decision on the immediately following NYSE session; a new decision must occur on the transition session; portfolio and benchmark state/metrics must agree; target weights must match the frozen ticker set and allocation caps. A hash chain proves record integrity, not truthfulness of external market data or the correctness of an operator-approved activation.

## Activation

No collection starts on import, and collection is disabled by default. The operational constructor accepts no injected provenance. To activate, the operator must provide both collection_authorized=True and an activation_record_path pointing to a regular, non-symlink JSON file with schema stmaa_ppo_shadow_activation_v1. The record must state freeze_register_status=approved and include the freeze-register commit, collector commit, operator authorization, timezone-aware approval timestamp, first eligible NYSE session, model SHA-256, manifest SHA-256, and SHA-256 of the effective strategy configuration. The collector validates these fields and binds the record digest into the ledger header. The first collected session must equal the approved first_session.

The activation record is an operator authorization artifact, not a cryptographic signature or an online GitHub status check. The operator must independently verify that the cited freeze-register commit is approved/merged and that the collector commit is the reviewed runtime source before creating the record. The factory constructs only AlpacaFeed, whose provider client is StockHistoricalDataClient for market data. No Paper orders or live trading are authorized.

## Validation

Run:

    uv run --locked pytest src/tests/test_ppo_shadow_collector.py -q
    uv run --locked ruff check src/agents/ppo_shadow_*.py src/tests/test_ppo_shadow_collector.py

Tests cover next-open accounting, SPY benchmark replay, artifact/config provenance binding, activation-record validation, rejection of injected provenance, hash-chain tampering, partial trailing writes, concurrent reader/writer access, semantic decision/transition ordering, duplicate and skipped sessions, restart/retry behavior, and AST-based rejection of broker execution imports and order/position endpoint calls.

Passing tests is not broker certification or evidence of profitability.
