# PPO prospective holdout freeze register

**Status: PRE-FREEZE — NOT ACTIVE — COLLECTION NOT AUTHORIZED**

This register records verified model/configuration identity and the gates that must pass before a prospective shadow/read-only holdout can start. It is not evidence that collection has started. No holdout start date or first session is assigned until all implementation and operator gates are closed.

## 1. Verified source and artifact identity

| Field | Verified value |
|---|---|
| Repository | `iFabbro/STMAA` |
| Remote `main` baseline at register preparation | `92d7d2c6dd511d96b573b1e08c7855f258ba69d1` |
| Local operator checkout reported | `6391c8c719f77ce1d85ab33218b19c3d8e8848f9` |
| Local working tree | Clean |
| Local checkout status | One merge commit behind remote `main` at time of report; pull and verify before freeze |
| Model base path | `models/ppo_trading_model.zip` |
| Content-addressed model | `models/ppo_trading_model.fc4f70c21547484e4a45f5c486fa5389f7f6334c653837696ba2d859dddc0318.zip` |
| Model size | 204,896 bytes |
| Manifest | `models/ppo_trading_model.zip.metadata.json` |
| Manifest contract version | 7 |
| State dimension | 43 |
| Model SHA-256 in manifest | `fc4f70c21547484e4a45f5c486fa5389f7f6334c653837696ba2d859dddc0318` |
| SHA-256 recomputed on operator host | Same value; exact match verified |
| Training dataset SHA-256 | `cf92b9db7cafeb5fe47fbef058e1aef2edd637829fb14e2e1dd946dd32a1cb80` |
| Training source commit recorded in manifest | `518f5eb30de0ffb784dbddb4704604bc9910149e` |
| Training working tree recorded clean | `true` |

The training source commit is model provenance; it is not the source commit to freeze for the prospective collector. The runtime source baseline must be frozen separately after the operator checkout is updated to the reviewed remote main and the collector implementation is merged.

## 2. Model and strategy configuration

| Field | Verified value |
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
| Combined simulator rate | 0.0015 × L1 turnover, already charged by the simulator |
| Drawdown threshold | 15% |
| Automatic liquidation on drawdown | false |
| Trading mode / endpoint reported | Paper / https://paper-api.alpaca.markets |
| Runtime interval default | 300 seconds |

Unset environment variables are not treated as unknown where code defaults are authoritative: `MAX_DRAWDOWN` defaults to 0.15, `LIQUIDATE_ON_DRAWDOWN` to false, and `STMAA_INTERVAL_SECONDS` to 300. The user-reported strategy values match the versioned defaults.

## 3. Market-data contract

The manifest contract and runtime contract were identical in the operator report.

- Provider: Alpaca.
- Historical/inference bars: SIP, `1Day`, adjustment `all`.
- Contract version: 4; feature version: 1.
- Features: Close, RSI, MACD, BB_middle, ATR; execution field: Open.
- Execution alignment: observe completed bar t; decision is for next bar open t+1.
- Timestamp: provider UTC timestamp normalized to naive UTC daily index.
- Missing OHLCV values: no forward-fill; interior/trailing gaps fail closed.
- Corporate-action adjustment scope: split, dividend, spin-off.
- Recent quote feed constant: `delayed_sip`. It is not the model's historical/inference bar feed and must not be substituted for SIP daily bars.

The prospective collector must persist the exact completed SIP bar panel and processed inference frame (or immutable content-addressed copies), not only their hashes. A hash alone does not permit reconstruction.

## 4. Prospective evaluation protocol — proposed fixed terms

| Item | Proposed rule |
|---|---|
| Mode | Shadow/read-only; no broker order, replace, cancel, close, or submission |
| Window | 252 completed NYSE sessions |
| Start | Unassigned; first eligible session strictly after the final freeze/activation record |
| Model/seed | The exact artifact above; seed 42; no ex-post seed selection |
| Virtual portfolio | USD 100,000, initially all cash; carry virtual holdings and cash forward across sessions |
| Decision timing | After a completed SIP daily bar is available; record target weights for the next session open |
| Virtual execution | Apply target weights at next session open, then mark at session close; preserve the simulator's overnight-gap and turnover-cost semantics |
| Costs | Transaction cost 0.001 and slippage 0.0005; apply once using the documented L1-turnover formula |
| Benchmarks | Equal-weight, SPY 50%, SPY 100%; exposure-matched equal-weight only if computed from the same holdout daily exposure path and independently validated |
| Primary criteria | Net cumulative return > 0 and maximum drawdown <= 15% |
| Diagnostic metrics | Volatility, Sharpe, Sortino, turnover, exposure, and total modeled costs |
| Intermediate access | Integrity/availability/operational checks only; no performance review, tuning, or criteria changes |
| Final evaluation | One evaluation after session 252; preserve raw evidence and generated report |
| Restart behavior | Never backfill or silently skip a missed decision; record interruption and require explicit protocol disposition |

The 15% drawdown threshold is an evaluation criterion, not a runtime liquidation mechanism. No claim of an enforced stop-loss is made.

## 5. Collector capability audit

**Current result: NOT IMPLEMENTED / NOT READY.**

Existing components provide building blocks, not a complete prospective holdout collector:

1. `PPOAgent.predict_weights()` supports deterministic inference on a DataFrame carrying a validated market-data contract and fingerprint.
2. `trade.py` currently couples its daily inference path to broker account/position snapshots and execution. It is not an acceptable shadow collector entry point.
3. `monitoring_state.py` atomically persists a dashboard monitoring snapshot, not a complete append-only per-session research ledger.
4. Existing benchmark exposure traces are evaluation diagnostics, not a prospective, durable decision-and-data archive.
5. The current data processor writes CSV files, but does not by itself implement session gating, a virtual portfolio state machine, tamper-evident append-only records, restart reconciliation, or the final one-shot evaluation protocol.

A dedicated collector must be implemented and tested before activation. It must not instantiate or call the broker trading executor. Read-only market-data retrieval is permitted.

## 6. Mandatory implementation and activation gates

- [ ] Operator checkout updated to reviewed `main`; record exact source commit and clean working tree.
- [ ] Reconfirm model artifact SHA-256 and full manifest on the freeze host.
- [ ] Record a canonical configuration snapshot and its SHA-256; include all effective defaults and market-data contract.
- [ ] Implement a standalone collector that loads the exact artifact and performs deterministic inference without broker account/position/order endpoints.
- [ ] Implement a virtual portfolio state machine initialized all-cash, with next-open rebalance, overnight gap, close marking, turnover and costs aligned with the v7 simulator.
- [ ] Persist each session's source bar data, processed feature frame, fingerprints, latest completed session timestamp, model/config/source fingerprints, inference output/target weights, virtual holdings/cash/equity, benchmark values, costs and validation status.
- [ ] Use append-only, crash-safe records with sequence numbers and hash chaining or equivalent tamper evidence; define recovery and duplicate-session behavior.
- [ ] Reject stale, partial, duplicate, unordered, missing, or contract-incompatible bars; do not infer on a partial current session.
- [ ] Define NYSE calendar handling, unscheduled closures, symbol suspensions, missing bars, provider outages, clock skew, and restart/missed-session disposition before start.
- [ ] Add tests proving no broker trading client is constructed/called and no order-related endpoint can be reached from the collector.
- [ ] Add deterministic replay test: reconstruct predictions, virtual portfolio and benchmarks from the persisted evidence.
- [ ] Add final evaluator that accepts only the frozen ledger and runs once after 252 valid completed sessions.
- [ ] Review CI and security checks in a dedicated PR; merge only after all required checks are green.
- [ ] After merge, create a signed/immutable activation record containing the exact collector commit, artifact hash, config hash, start-session rule, and operator authorization.
- [ ] Only then begin collection.

## 7. Activation record (intentionally blank)

| Field | Value |
|---|---|
| Collector commit | NOT SET |
| Freeze record commit | NOT SET |
| Artifact SHA-256 reverified on freeze host | NOT SET |
| Effective config SHA-256 | NOT SET |
| Activation timestamp (UTC) | NOT SET |
| First eligible NYSE session | NOT SET |
| Operator approval | NOT GRANTED |
| Collection status | NOT STARTED |

**No trading authority:** this protocol does not authorize Paper orders. Any Paper execution test requires separate, explicit authorization. Live trading is prohibited.
