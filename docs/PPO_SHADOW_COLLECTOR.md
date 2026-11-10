# PPO Shadow Collector

## Scope

This is an independent research collector. It reads completed Alpaca SIP daily bars, calls the validated PPO inference interface, records target weights and feature snapshots, and simulates a virtual portfolio. It does not submit, replace, cancel, or close broker orders and does not import trade.py, a broker executor, or an Alpaca trading client. The component remains experimental and does not establish profitability or production readiness.

## Timing contract

A decision uses the latest completed daily bar (t). Its target remains pending until the immediately following NYSE session. At that session, the previous target is applied at the Open. Existing shares are marked at the new Open before rebalance, so they absorb the overnight gap; new holdings then earn the Open-to-Close return. A new decision is generated for the next session.

The collector rejects a skipped session rather than simulating a stale target at a later Open. If a transition was persisted but inference failed, retrying the same session can finish that decision without executing the prior target twice. Advancing to another session without completing the missing decision fails closed.

## Portfolio and benchmark

The virtual portfolio is long-only and enforces the current 20% per-position and 50% gross-exposure caps. Turnover costs and slippage use the configured rates. The SPY buy-and-hold benchmark starts with the same notional at the first next-session Open and is marked at each subsequent Close. Strategy and benchmark transitions are persisted together.

## Provenance and persistence

At startup, the collector verifies the loaded PPO artifact SHA-256 against its manifest and records the manifest digest, model digest, artifact name, training metadata, and strategy settings. The ledger header freezes that provenance and the collector configuration; later runs with different model/configuration are rejected.

The JSONL ledger uses monotonically increasing sequence numbers, previous-record hashes, SHA-256 record hashes, an exclusive process lock, and fsync. Each decision stores the full inference-frame fingerprint, versioned data contract, latest feature row, target weights, current virtual weights, and SPY snapshot. Each transition stores portfolio state, costs, turnover, returns, benchmark state, and both data fingerprints. Replay validates the chain and reconstructs the final virtual portfolio and benchmark state.

Partial, malformed, reordered, or modified records fail closed. The filesystem remains a trust boundary; protect the ledger with permissions and backups.

## Activation

No collection starts on import. Construct a validated PPOAgent with the frozen model and exact strategy parameters, then explicitly call build_default_collector with a durable ledger path outside version control. The factory constructs only AlpacaFeed, whose provider client is StockHistoricalDataClient for market data. Collection must remain inactive until the prospective holdout freeze register is reviewed and explicitly activated.

## Validation

Run:

    uv run --locked pytest src/tests/test_ppo_shadow_collector.py -q
    uv run --locked ruff check src/agents/ppo_shadow_*.py src/tests/test_ppo_shadow_collector.py

Tests cover next-open accounting, SPY benchmark replay, artifact/config provenance binding, hash-chain tampering, duplicate and skipped sessions, restart/retry behavior, and AST-based rejection of broker execution imports and order/position endpoint calls.

Passing tests is not broker certification or evidence of profitability.
