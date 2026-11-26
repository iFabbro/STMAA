# PPO prospective holdout freeze register

**Status: PRE-FREEZE — NOT ACTIVE — COLLECTION NOT AUTHORIZED**

This register records model/configuration identity and the gates that must pass before a prospective shadow/read-only holdout can start. It is not an activation record and is not evidence that collection has started. No holdout start date or first session is assigned until the remaining implementation, integrity, and operator gates are closed.

**Update 2026-11-25:** this document has been refreshed against repository `main` at `001a66cddf134ea7c5344ff292a1c08d0458c198`. Collector PR #334 is merged. The local operator inventory was repeated on 2026-11-25 and is recorded in §9. The collector remains disabled by default; the activation-hardening gates below remain open. This update does not approve a freeze or authorize collection.

## 1. Verified source and artifact identity

| Field | Verified value |
|---|---|
| Repository | `iFabbro/STMAA` |
| Current `main` baseline at document refresh (2026-11-25) | `001a66cddf134ea7c5344ff292a1c08d0458c198` |
| Local operator checkout reported (2026-11-25) | `main`, same commit as above |
| Local working tree | Clean (`## main...origin/main`) |
| Content-addressed model | `models/ppo_trading_model.fc4f70c21547484e4a45f5c486fa5389f7f6334c653837696ba2d859dddc0318.zip` |
| Model size | 204,896 bytes |
| Manifest | `models/ppo_trading_model.zip.metadata.json` |
| Manifest contract version | 7 |
| State dimension | 43 |
| Model SHA-256 in manifest / recomputed local artifact | `fc4f70c21547484e4a45f5c486fa5389f7f6334c653837696ba2d859dddc0318` — exact match in local inventory |
| Training dataset fingerprint recorded in prior register | `cf92b9db7cafeb5fe47fbef058e1aef2edd637829fb14e2e1dd946dd32a1cb80` |
| Training source commit recorded in manifest (prior register) | `518f5eb30de0ffb784dbddb4704604bc9910149e` |
| Training working tree recorded clean (prior register) | `true` |

The training source commit is model provenance; it is not the source commit to freeze for the prospective collector. The runtime source baseline must be frozen separately in an approved activation record.

The 2026-11-25 inventory confirms the model file hash matches the value above. It does **not** independently reconstruct the training matrix or prove the raw-to-processed-to-training lineage. The local CSV file SHA-256 values are hashes of individual serialized files; they are not directly comparable to the canonical prepared-dataset fingerprint above.

## 2. Model and strategy configuration

The following values are carried forward from the previously recorded manifest/configuration evidence and have **not** been re-certified as a freeze snapshot on 2026-11-25.

| Field | Previously recorded value |
|---|---|
| Seed | 42 |
| Learning rate | 0.0003 |
| PPO n_steps | 2048 |
| Batch size | 64 |
| Epochs | 10 |
| Gamma | 0.99 |
| Requested training timesteps | 50,000 |
| Tickers, ordered | AAPL, GOOGL, MSFT, AMZN, TSLA, NVDA, META |
| Initial virtual capital | USD 100,000 |
| Maximum single-name weight | 20% |
| Maximum gross exposure | 50% |
| Transaction-cost assumption | 0.001 |
| Slippage assumption | 0.0005 |
| Combined simulator rate | 0.0015 × L1 turnover, charged by the simulator |
| Drawdown threshold | 15% |
| Automatic liquidation on drawdown | false |
| Trading mode / endpoint previously reported | Paper / https://paper-api.alpaca.markets |
| Runtime interval default | 300 seconds |

Unset environment variables are not treated as unknown where code defaults are authoritative: `MAX_DRAWDOWN` defaults to 0.15, `LIQUIDATE_ON_DRAWDOWN` to false, and `STMAA_INTERVAL_SECONDS` to 300. These are previously documented values, not a newly captured effective-configuration snapshot. A canonical configuration snapshot and SHA-256 remain mandatory before any freeze.

## 3. Market-data contract

The previously recorded manifest and runtime contract were identical:

- Provider: Alpaca.
- Historical/inference bars: SIP, `1Day`, adjustment `all`.
- Contract version: 4; feature version: 1.
- Features: Close, RSI, MACD, BB_middle, ATR; execution field: Open.
- Execution alignment: observe completed bar t; decision is for next bar open t+1.
- Timestamp: provider UTC timestamp normalized to naive UTC daily index.
- Missing OHLCV values: no forward-fill; interior/trailing gaps fail closed.
- Corporate-action adjustment scope: split, dividend, spin-off.
- Recent quote feed constant previously reported: `delayed_sip`. It is not the model's historical/inference bar feed and must not be substituted for SIP daily bars.

The prospective collector must persist the exact completed SIP bar panel and processed inference frame (or immutable content-addressed copies), not only their hashes. A hash alone does not permit reconstruction. Reconfirm the effective feed and full contract on the freeze host before activation.

## 4. Prospective evaluation protocol — proposed fixed terms

| Item | Proposed rule |
|---|---|
| Mode | Shadow/read-only; no broker order, replace, cancel, close, or submission |
| Window | 252 completed NYSE sessions |
| Start | Unassigned; first eligible session strictly after the final freeze/activation record |
| Model/seed | Exact artifact identified above; seed 42; no ex-post seed selection |
| Virtual portfolio | USD 100,000, initially all cash; carry virtual holdings and cash forward |
| Decision timing | After a completed SIP daily bar is available; record target weights for the next session open |
| Virtual execution | Apply target weights at next session open, then mark at session close; preserve overnight-gap and turnover-cost semantics |
| Costs | Transaction cost 0.001 and slippage 0.0005; apply once using the documented L1-turnover formula |
| Prospective ledger benchmark | SPY 100% buy-and-hold, same initial notional, first next-session Open entry, daily Close marking (collector PR #334) |
| Other benchmark comparisons | Equal-weight and SPY 50% remain separate walk-forward research comparisons; do not imply they are recorded by the prospective collector |
| Primary criteria | Net cumulative return > 0 and maximum drawdown <= 15% |
| Diagnostic metrics | Volatility, Sharpe, Sortino, turnover, exposure, and total modeled costs |
| Intermediate access | Integrity/availability/operational checks only; no performance review, tuning, or criteria changes |
| Final evaluation | One evaluation after session 252; preserve raw evidence and generated report |
| Restart behavior | Never backfill or silently skip a missed decision; record interruption and require explicit protocol disposition |

The 15% drawdown threshold is an evaluation criterion, not a runtime liquidation mechanism. No claim of an enforced stop-loss is made. These terms remain proposed and are not an activation authorization.

## 5. Collector capability audit

**Current result: IMPLEMENTED — ACTIVATION HARDENING INCOMPLETE.**

Collector PR #334 was merged into `main` at `d6bf905a2956caec0eb742f9f814f05cfca7edda`. It added:

1. A standalone collector using `PPOAgent.predict_weights()` and completed Alpaca SIP daily bars.
2. Next-session-Open virtual execution, overnight-gap accounting, close marking, turnover costs, slippage, and 20%/50% allocation caps.
3. A same-capital SPY 100% buy-and-hold benchmark, entered at the first next-session Open.
4. An append-only JSONL ledger with sequence numbers, SHA-256 hash chaining, exclusive process lock, and `fsync`.
5. Decision/transition persistence, provenance checks, deterministic replay, and regression tests for accounting, tampering, duplicate/skipped sessions, restart/retry, and known broker endpoint isolation.
6. Explicit activation flags; importing the module does not start collection, and unauthorized `collect_once()` fails before creating the ledger.

The implementation does **not** yet close all activation gates. The prior code audit identified:

- `activation_reference` is only checked for non-empty text; it is not verified against an approved freeze-register/activation record.
- The constructor permits injected provenance, so the operational entry point must prevent arbitrary provenance from bypassing artifact/manifest verification (test injection must remain test-only).
- Replay verifies the record chain and reconstructs state, but does not yet validate the full semantic state machine across every decision/transition sequence.
- Hash chaining detects alteration but does not prove semantic correctness; malformed/partial trailing writes fail closed and recovery must remain a documented, non-destructive operator procedure.

The collector does not submit, replace, cancel, or close broker orders and does not import the trading executor or Alpaca Trading Client. Static AST checks cover known imports/calls, not every conceivable indirect path. No Paper or live orders are authorized.

## 6. Mandatory implementation and activation gates

- [ ] Operator checkout and source baseline are reverified at freeze time; record exact collector commit and clean working tree.
- [ ] Reconfirm model artifact SHA-256 and full manifest on the freeze host.
- [ ] Record a canonical effective-configuration snapshot and its SHA-256, including defaults and market-data contract.
- [x] Implement a standalone collector using completed SIP bars and deterministic PPO inference without broker trading endpoints (PR #334 merged; remaining isolation caveat above).
- [x] Implement an all-cash virtual portfolio with next-open rebalance, overnight-gap accounting, close marking, turnover and costs (PR #334 merged).
- [x] Persist decision/transition records, provenance, portfolio state, SPY benchmark state, and data fingerprints (PR #334 merged).
- [x] Add append-only hash-chained ledger, sequence numbers, exclusive lock, `fsync`, and fail-closed handling for malformed/tampered records (PR #334 merged; recovery procedure still needs explicit operator documentation).
- [x] Add regression tests for accounting, provenance, hash-chain tampering, duplicate/skipped sessions, restart/retry, replay, and known broker endpoint isolation (PR #334 CI passed historically).
- [ ] Bind activation to a verifiable approved freeze/activation record; reject arbitrary non-empty activation references.
- [ ] Remove arbitrary provenance injection from the operational activation path while retaining isolated test fixtures.
- [x] Strengthen replay to recompute each portfolio and benchmark transition from immutable market-data snapshots and prior replayed state; validate returns, turnover, costs, cash, shares, weights, and persisted state even when the ledger hash chain is recomputed.
- [ ] Add explicit tests for partial trailing writes, concurrent reader/writer behavior, and a documented non-destructive recovery procedure; do not auto-repair or discard ledger evidence.
- [ ] Re-audit the import/call graph and runtime client construction to strengthen the no-trading isolation evidence.
- [ ] Define the final evaluator and one-shot report acceptance rules for 252 valid completed sessions; ensure intermediate access cannot enable tuning or selection.
- [ ] Review hardening changes in a dedicated PR; merge only after all required CI/security checks are green.
- [ ] After hardening and freeze approval, create an immutable activation record containing exact collector commit, artifact/manifest/config hashes, start-session rule, and explicit operator authorization.
- [ ] Only then begin collection.

## 7. Objective hierarchy and evaluation decision

The prospective holdout must follow the project's recorded priority order:

1. Verifiable profitability: positive net return after modeled costs on genuinely out-of-sample observations.
2. Risk control: drawdown, volatility, exposure, and capital protection.
3. Statistical robustness: multi-seed evidence, walk-forward stability, and sensitivity checks.
4. Operational validation: read-only Alpaca Paper data collection; no broker orders.
5. Optional competitive diagnostics: benchmark comparisons may inform research, but beating SPY is not a project acceptance criterion.

The 252-session holdout is prospective evidence, not a tuning set. The primary outcome is autonomous operational correctness, risk-limit adherence, and positive net return after modeled costs; benchmark outperformance is not required. Do not inspect interim performance, change model/configuration/costs/criteria, select a seed, or restart the clock based on results. Any protocol deviation must be recorded and adjudicated before continuing.

The project sequence and gate remain unchanged: no holdout activation, PPO tuning, or retraining is authorized by this register. The comparative benchmark work remains ahead of further PPO optimization/retraining as already decided in the project plan.

## 8. Activation record (intentionally blank)

| Field | Value |
|---|---|
| Collector commit | `d6bf905a2956caec0eb742f9f814f05cfca7edda` (implementation merged; activation hardening pending) |
| Freeze record commit | NOT SET — this register is not an approved freeze record |
| Artifact SHA-256 reverified on freeze host | NOT SET |
| Effective config SHA-256 | NOT SET |
| Activation timestamp (UTC) | NOT SET |
| First eligible NYSE session | NOT SET |
| Operator approval | NOT GRANTED |
| Collection status | NOT STARTED |

**No trading authority:** this protocol does not authorize Paper orders. Any Paper execution test requires separate, explicit authorization. Live trading is prohibited.

## 9. Operator-host inventory — 2026-11-25

Read-only inventory performed in `~/Projects/STMAA` on 2026-11-25 at 09:19:32 CET. This is an inventory snapshot, not a freeze or activation.

### Git baseline

- Branch: `main`.
- HEAD: `001a66cddf134ea7c5344ff292a1c08d0458c198`.
- Status: clean, `main...origin/main`.

### Data inventory

- `data/raw/`: 9 CSV files.
- `data/processed/`: 9 CSV files.
- Individual file sizes, modification times, and SHA-256 values were recorded in the operator's terminal output.
- These file-level hashes do not establish equivalence to the canonical prepared-dataset fingerprint `cf92b9db7cafeb5fe47fbef058e1aef2edd637829fb14e2e1dd946dd32a1cb80`. No independent canonical reconstruction or complete raw → processed → training lineage mapping was performed in this inventory.

### Model inventory

- Content-addressed model file present: `models/ppo_trading_model.fc4f70c21547484e4a45f5c486fa5389f7f6334c653837696ba2d859dddc0318.zip`.
- Local SHA-256: exact match to the model hash recorded in the manifest.
- Manifest and additional validation model artifacts were present. This inventory did not revalidate the complete manifest contract or reconstruct the training dataset.

### Reports, logs, and holdout activation evidence

- `reports/` contained benchmark, walk-forward, and methodology-audit report files.
- `logs/` contained `logs/stmaa.db`, execution state/lock/log files, and soak logs/databases.
- The inventory search found no holdout freeze/activation artifact and no shadow JSONL ledger in the inspected `logs/`, `reports/`, `data/`, and `models/` locations.
- The inspected `research/` path contained `research/protocols/ppo-benchmark/1.0.0.json`.
- No evidence in this inventory indicates that prospective holdout collection has started. This is bounded to the paths and search terms inspected; it is not a claim about every possible filesystem location.

### Inventory conclusion

As of this inventory, the operator checkout was clean and aligned with the recorded `main` SHA; the model artifact hash matched the manifest value; raw and processed datasets and research reports were present. The inventory did not verify canonical dataset reconstruction, complete data lineage, a freeze record, an activation record, or a shadow ledger. Therefore the holdout remains **PRE-FREEZE — NOT ACTIVE — COLLECTION NOT AUTHORIZED**.

No model, dataset, configuration, collector, runtime, or broker state was modified during this inventory. No holdout collection was started or authorized.
