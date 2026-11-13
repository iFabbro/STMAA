# PPO prospective holdout freeze register

**Status: PRE-FREEZE — NOT ACTIVE — COLLECTION NOT AUTHORIZED**

This register records model/configuration identity and the gates that must pass before a prospective shadow/read-only holdout can start. It is not an activation record and is not evidence that collection has started. No holdout start date or first session is assigned until the remaining implementation, integrity, and operator gates are closed.

**Update after collector PR #334:** a dedicated read-only collector and virtual portfolio are now present on `main` (merge commit `d6bf905a2956caec0eb742f9f814f05cfca7edda`). This supersedes the original implementation-status assessment below. The collector remains disabled by default. The code audit identified unresolved activation-hardening items; therefore this register remains PRE-FREEZE and collection remains unauthorized.

## 1. Verified source and artifact identity

| Field | Verified value |
|---|---|
| Repository | `iFabbro/STMAA` |
| Remote `main` baseline at original register preparation | `92d7d2c6dd511d96b573b1e08c7855f258ba69d1` |
| Current `main` after collector PR #334 | `d6bf905a2956caec0eb742f9f814f05cfca7edda` |
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
| Prospective ledger benchmark | SPY 100% buy-and-hold, same initial notional, first next-session Open entry, daily Close marking (implemented by collector PR #334) |
| Other benchmark comparisons | Equal-weight and SPY 50% remain separate walk-forward research comparisons; do not imply they are recorded by the current prospective collector |
| Primary criteria | Net cumulative return > 0 and maximum drawdown <= 15% |
| Diagnostic metrics | Volatility, Sharpe, Sortino, turnover, exposure, and total modeled costs |
| Intermediate access | Integrity/availability/operational checks only; no performance review, tuning, or criteria changes |
| Final evaluation | One evaluation after session 252; preserve raw evidence and generated report |
| Restart behavior | Never backfill or silently skip a missed decision; record interruption and require explicit protocol disposition |

The 15% drawdown threshold is an evaluation criterion, not a runtime liquidation mechanism. No claim of an enforced stop-loss is made.

## 5. Collector capability audit

**Current result: IMPLEMENTED — ACTIVATION HARDENING INCOMPLETE.**

PR #334 (`d6bf905a2956caec0eb742f9f814f05cfca7edda`) added:

1. A standalone collector using `PPOAgent.predict_weights()` and completed Alpaca SIP daily bars.
2. Next-session-Open virtual execution, overnight-gap accounting, close marking, turnover costs, slippage, and 20%/50% allocation caps.
3. A same-capital SPY 100% buy-and-hold benchmark, entered at the first next-session Open.
4. An append-only JSONL ledger with sequence numbers, SHA-256 hash chaining, exclusive process lock, and `fsync`.
5. Decision/transition persistence, provenance checks, deterministic replay, and regression tests for accounting, tampering, duplicate/skipped sessions, restart/retry, and known broker endpoint isolation.
6. Explicit activation flags; importing the module does not start collection, and unauthorized `collect_once()` fails before creating the ledger.

The implementation does **not** yet close all activation gates. The code audit found:

- `activation_reference` is only checked for non-empty text; it is not verified against an approved freeze-register/activation record.
- The constructor permits injected provenance, so the operational entry point must prevent arbitrary provenance from bypassing artifact/manifest verification (test injection must remain test-only).
- Replay verifies the record chain and reconstructs state, but does not yet validate the full semantic state machine across every decision/transition sequence.
- Hash chaining detects alteration but does not prove semantic correctness; malformed/partial trailing writes fail closed and recovery must remain a documented, non-destructive operator procedure.

The collector does not submit, replace, cancel, or close broker orders and does not import the trading executor or Alpaca Trading Client. Static AST checks cover known imports/calls, not every conceivable indirect path. No Paper or live orders are authorized.

## 6. Mandatory implementation and activation gates

- [ ] Operator checkout updated to reviewed `main`; record exact source commit and clean working tree.
- [ ] Reconfirm model artifact SHA-256 and full manifest on the freeze host.
- [ ] Record a canonical configuration snapshot and its SHA-256; include all effective defaults and market-data contract.
- [x] Implement a standalone collector using completed SIP bars and deterministic PPO inference without broker trading endpoints (PR #334 merged; see remaining isolation caveat above).
- [x] Implement an all-cash virtual portfolio with next-open rebalance, overnight-gap accounting, close marking, turnover and costs (PR #334 merged).
- [x] Persist decision/transition records, provenance, portfolio state, SPY benchmark state, and data fingerprints (PR #334 merged).
- [x] Add append-only hash-chained ledger, sequence numbers, exclusive lock, `fsync`, and fail-closed handling for malformed/tampered records (PR #334 merged; recovery procedure still needs explicit operator documentation).
- [x] Add regression tests for accounting, provenance, hash-chain tampering, duplicate/skipped sessions, restart/retry, replay, and known broker endpoint isolation (PR #334 CI passed).
- [ ] Bind activation to a verifiable approved freeze/activation record; reject arbitrary non-empty activation references.
- [ ] Remove arbitrary provenance injection from the operational activation path while retaining isolated test fixtures.
- [ ] Strengthen replay to validate the complete semantic decision → next-session transition state machine, not only hash integrity and reconstructed terminal state.
- [ ] Add explicit tests for partial trailing writes, concurrent reader/writer behavior, and a documented non-destructive recovery procedure; do not auto-repair or discard ledger evidence.
- [ ] Re-audit the import/call graph and runtime client construction to strengthen the no-trading isolation evidence.
- [ ] Define the final evaluator and one-shot report acceptance rules for 252 valid completed sessions; ensure intermediate access cannot enable tuning or selection.
- [ ] Review the hardening changes in a dedicated PR; merge only after all required CI/security checks are green.
- [ ] After hardening and freeze approval, create an immutable activation record containing exact collector commit, artifact/manifest/config hashes, start-session rule, and explicit operator authorization.
- [ ] Only then begin collection.

## 7. Objective hierarchy and evaluation decision

The prospective holdout must follow the project's current priority order:

1. Verifiable profitability: positive net return after modeled costs on genuinely out-of-sample observations.
2. Risk control: drawdown, volatility, exposure, and capital protection.
3. Statistical robustness: multi-seed evidence, walk-forward stability, and sensitivity checks.
4. Operational validation: read-only Alpaca Paper data collection; no broker orders.
5. Optional competitive diagnostics: benchmark comparisons may inform research, but beating SPY is not a project acceptance criterion. STMAA succeeds if it operates autonomously within its risk constraints and demonstrates positive net profitability on genuinely out-of-sample evidence.

The 252-session holdout is prospective evidence, not a tuning set. The primary outcome is autonomous operational correctness, risk-limit adherence, and positive net return after modeled costs; benchmark outperformance is not required. Do not inspect interim performance, change model/configuration/costs/criteria, select a seed, or restart the clock based on results. Any protocol deviation must be recorded and adjudicated before continuing.

## 8. Activation record (intentionally blank)

| Field | Value |
|---|---|
| Collector commit | `d6bf905a2956caec0eb742f9f814f05cfca7edda` (implementation merged; activation hardening pending) |
| Freeze record commit | NOT SET — this PR is still a draft and is not an approved freeze record |
| Artifact SHA-256 reverified on freeze host | NOT SET |
| Effective config SHA-256 | NOT SET |
| Activation timestamp (UTC) | NOT SET |
| First eligible NYSE session | NOT SET |
| Operator approval | NOT GRANTED |
| Collection status | NOT STARTED |

**No trading authority:** this protocol does not authorize Paper orders. Any Paper execution test requires separate, explicit authorization. Live trading is prohibited.
