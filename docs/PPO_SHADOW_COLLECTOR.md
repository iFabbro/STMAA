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


## Immutable inference evidence and final holdout evaluation

Each collection session stores the complete model-feed DataFrame supplied to `PPOAgent.predict_weights()` and the complete benchmark DataFrame in a sibling directory named `<ledger>.snapshots/`. Snapshot files are canonical JSON, content-addressed by SHA-256, created without overwrite, written with complete-write loops and `fsync`, and referenced from both the decision and transition ledger events. The model snapshot also preserves the ordered ticker list, the exact current-weight vector supplied to inference, and the inference-call contract. The ledger records each snapshot's role, relative path, byte size, and digest. Missing, unsafe, or digest-mismatched snapshots fail closed during final evaluation. Keep the ledger and its snapshot directory together; back up both as one evidence set. A hash proves byte integrity, not the truth of the upstream provider response.

### Fixed 252-session counting rule

The holdout contains **252 return-bearing NYSE sessions**, not 252 PPO decision dates:

1. Decision 1 is generated after the first eligible completed SIP session. It is a warm-up signal and produces no return.
2. Decision 1 is applied at the next NYSE session Open; the portfolio is marked at that session's Close. This is return-bearing session 1.
3. At each return-bearing session close, the collector records the next decision. The next NYSE session Open consumes that decision. Returns are measured from the prior close to the current close, with existing holdings absorbing the overnight gap before the new target is applied.
4. The final evidence set therefore contains exactly **253 decisions and 252 portfolio/benchmark transitions**, with no pending decision. The first decision date is the warm-up date; the first return date is the next NYSE session.

A missed session, malformed event, missing snapshot, partial ledger, or count other than 253 decisions / 252 transitions prevents final evaluation. No backfill, skipped-session substitution, early stop, or restart of the clock is permitted.

### One-shot evaluator

After the 252nd transition is durably recorded, run the evaluator once from the freeze host:

```python
from src.agents.ppo_shadow_evaluator import evaluate_shadow_holdout

report = evaluate_shadow_holdout("logs/ppo_shadow_holdout.jsonl")
print(report["acceptance"])
```

The default output is `logs/ppo_shadow_holdout.final_report.json`. The evaluator replays the complete ledger, checks the exact event counts and terminal state, verifies every decision and transition snapshot, recomputes the strategy and SPY metrics from the 252 persisted transitions, and atomically publishes one final report. It refuses incomplete or overlong ledgers and refuses to overwrite an existing report. If evaluation is interrupted or a report already exists, preserve the evidence and use a separate documented forensic review; do not delete the report to rerun evaluation.

The report includes net cumulative return, maximum drawdown, annualized volatility, Sharpe and Sortino diagnostics for the strategy and SPY. Daily strategy returns include the overnight gap on pre-existing holdings plus the current-session Open-to-Close movement; the first return-bearing session begins from the all-cash initial state. The fixed primary acceptance checks are positive net strategy return and maximum drawdown no worse than -15%. SPY outperformance is not required. The evaluator does not change the model, select seeds, or tune parameters.

The collector's return value exposes session/status only, not interim portfolio or benchmark performance. The append-only ledger still contains the auditable transition data required for replay; software cannot prevent an operator with filesystem access from manually inspecting that raw evidence. The no-interim-performance-review / no-tuning rule therefore remains an explicit operator control, not a cryptographic access-control guarantee. Do not use intermediate ledger contents to select, tune, stop, or restart the holdout.

Passing the evaluator is evidence about this fixed prospective sample only. It does not authorize collection, Paper orders, or live trading.
