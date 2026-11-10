# PPO Shadow Collector

## Scope

This is an independent research collector. It reads completed Alpaca SIP daily bars, calls the PPO inference interface, records target weights, and simulates a virtual portfolio. It does not submit, replace, cancel, or close broker orders and does not import trade.py, a broker executor, or an Alpaca trading client. The component remains experimental and does not establish profitability or production readiness.

## Timing contract

A decision uses the latest completed daily bar (t). Its target remains pending until a later completed session is observed. At that later session, the previous target is applied at that session's Open. Existing shares are marked at the new Open before rebalance, so they absorb the overnight gap; new holdings then earn the Open-to-Close return. A new decision is generated for the next session. The first collection creates a decision only and cannot retroactively execute at the same session's Open.

## Persistence

The JSONL ledger uses monotonically increasing sequence numbers, previous-record hashes, SHA-256 record hashes, an exclusive process lock, and fsync. Replay validates every record. Partial, malformed, reordered, or modified records fail closed. The filesystem remains a trust boundary; protect the ledger with permissions and backups.

## Activation

No collection starts on import. Construct a validated PPOAgent with the frozen model and exact strategy parameters, then explicitly call build_default_collector with a durable ledger path outside version control. The factory constructs only AlpacaFeed, whose client is StockHistoricalDataClient for market data. Collection must remain inactive until the prospective holdout freeze register is reviewed and explicitly activated.

## Validation

Run:
uv run --locked pytest src/tests/test_ppo_shadow_collector.py -q
uv run --locked ruff check src/agents/ppo_shadow_*.py src/tests/test_ppo_shadow_collector.py

Passing tests is not broker certification or evidence of profitability.
