# STMAA End-to-End Risk Audit — Evidence and Residual Risks

**Audit snapshot:** 2026-11-01  
**Repository:** `iFabbro/STMAA`  
**Latest verified commit:** `f3b4b0b73491b6c80adf44bd7b4361069ec5ca1a` (merge PR #256)  
**Scope:** broker order lifecycle, emergency liquidation, restart/idempotency, persisted-state integrity, concurrency, portfolio exposure/risk gates, post-liquidation metrics, and market-data validity.

## Executive status

The reviewed code and automated regression suite contain fail-closed controls for the scenarios listed below. The four required post-merge workflows on the PR #256 merge commit `f3b4b0b73491b6c80adf44bd7b4361069ec5ca1a` completed successfully:

- [CI](https://github.com/iFabbro/STMAA/actions/runs/41304087051)
- [Tests / pytest](https://github.com/iFabbro/STMAA/actions/runs/41304087042)
- [Lint / ruff](https://github.com/iFabbro/STMAA/actions/runs/41304087072)
- [Secrets scan / gitleaks](https://github.com/iFabbro/STMAA/actions/runs/41304087060)

PR #256 updated this audit record only; it made no production-code changes and performed no broker operations.

This is evidence of the configured tests passing on the stated commit. It is **not** a live-trading safety certification, proof of strategy profitability, or proof that every possible broker or infrastructure failure has been covered.

No live orders were sent during this audit. Targeted broker tests use mocked broker responses. The read-only Paper reconciliation workflow is separate from these mocked tests; a 48-hour Paper soak is not evidenced by this document.

## Recent merged changes

- **PR #253 — Reject ambiguous timestamps in liquidation reconciliation.** Naive or malformed timestamps are not silently treated as UTC or reliable matching evidence. Merge commit: `b60b3317c42e3497ce7becc2b9766e483657bc89`.
- **PR #254 — Revalidate market quote immediately before order submission.** The executor refreshes and validates the quote after broker preflight, rejects stale/invalid/changed prices, and recalculates quantity before submission. Merge commit: `339c40f625af0ec52d104a3ec60151e5f834dc3d`.
- **PR #255 — Fail closed on inconsistent terminal liquidation fill data.** A terminal `filled` status is not accepted as proof of a complete close unless broker `qty` and `filled_qty` are consistent. Contradictory/incomplete evidence retains the pending marker and prevents a duplicate close. Regression: `src/tests/test_liquidation_terminal_fill_integrity.py`. Merge commit: `75bb342b639000e7a2ca768c332afb5fd832cf4d`.

Earlier risk-matrix extension **PR #95** is merged; it adds combinations for unpriced pending orders, missing target quotes, and stale account data with unknown pending exposure. PR #94's combined fail-closed scenarios are also merged.

## Coverage reviewed in the current test suite

### 1. Order lifecycle and emergency liquidation

Coverage includes ambiguous submissions and closes, durable client/order identifiers, restart reconciliation, replacement chains, cancellation verification, partial fills and residual quantities, terminal order states, broker identity mismatch, stale/lagging position snapshots, and no-duplicate-close behavior.

Recent regression files include:

- `src/tests/test_liquidation_terminal_fill_integrity.py`
- `src/tests/test_liquidation_e2e_recovery_semantics.py`
- `src/tests/test_circuit_breaker_liquidation_e2e_audit.py`
- `src/tests/test_terminal_order_position_reconciliation_e2e.py`
- `src/tests/test_multi_symbol_reconciliation_restart_idempotency.py`

### 2. Persisted state, crash recovery, and concurrency

The implementation uses a revision check to reject stale writers, a temporary file plus atomic replacement, file and parent-directory fsync, and a path-scoped OS file lock. The lock is host-local and requires cooperating processes to share the same state path on a filesystem supporting advisory locks.

Regression coverage includes interrupted JSON writes, disk-full/fsync errors, permission errors, parent-directory fsync failure after replacement, concurrent writers, stale executor instances, lock timeout/acquisition failure, process termination while holding the lock, and lock recovery after process exit.

Relevant files:

- `src/tests/test_broker_state_persistence_failures.py`
- `src/tests/test_broker_executor_execution_lock.py`
- `src/tests/test_restart_reconciliation_persistence_atomicity.py`
- `src/tests/test_multi_symbol_reconciliation_persistence_atomicity.py`

### 3. Risk and exposure fail-closed gates

The suite covers unknown or non-finite pending-order exposure, partial-fill exposure reservation, incomplete position/open-order snapshots, stale or missing target quotes, quote-provider exceptions, stale/invalid account equity, and combinations of these failures. The tested invariant is that no new order is submitted when the inputs needed to calculate exposure or risk are unreliable.

Relevant files:

- `src/tests/test_risk_matrix_missing_data_combinations.py`
- `src/tests/test_pending_order_aggregate_exposure_fail_closed.py`
- `src/tests/test_fail_closed_risk_preflight_matrix.py`
- `src/tests/test_risk_gate_combination_regressions.py`
- `src/tests/test_combined_risk_failure_scenarios.py`

### 4. Post-liquidation equity, metrics, and monitoring consistency

The suite checks post-liquidation account refresh, stale post-liquidation equity, metrics export failures, persistence rollback, dashboard snapshot durability, and prevention of advancing the equity curve on invalid or unpersisted data.

Relevant files:

- `src/tests/test_circuit_breaker_liquidation_e2e_audit.py`
- `src/tests/test_monitoring_state.py`
- `src/tests/test_metrics.py`
- `src/tests/test_metrics_export.py`

## Corrected failure modes evidenced by merged changes

- Ambiguous broker submissions are reconciled rather than blindly retried.
- Pending-order exposure that cannot be estimated blocks new exposure.
- Incomplete or stale account, position, order, and market-data snapshots fail closed in the covered execution paths.
- Emergency liquidation preserves unresolved intent across ambiguous outcomes and restart.
- Terminal liquidation evidence is checked for quantity consistency before a flat position can be accepted.
- Persisted-state writes reject stale revisions and fail closed on durability errors.
- A fresh quote is checked immediately before order submission; sizing is recalculated from that verified quote.

## Audit closure against the agreed code-audit scope

**Status: complete — 100% of the bounded code-audit scope agreed for this review.** This percentage refers only to the defined repository review and regression-evidence work; it does not mean 100% assurance, zero software risk, or production/live-trading certification.

The agreed exit criteria have been met on the reviewed `main` history through PR #256:

- No outstanding P0/P1 finding was identified in the reviewed scope.
- The defined safety cases for order lifecycle/liquidation, restart and persistence, concurrency, risk/exposure gates, market-data freshness, and post-liquidation metrics have corresponding regression coverage.
- The four required workflows (CI, pytest, ruff, gitleaks) are green on the latest merged commit recorded above.
- Broker reconciliation and local persisted-state behavior are covered for the defined mocked and read-only cases; no live orders were sent during this code audit.
- Remaining limitations and operational prerequisites are explicitly listed below.

The code audit is closed at this boundary. Reopen it only for a concrete vulnerability, regression, failed control, or newly identified unmet safety requirement. Paper soak, real-broker fault injection, independent risk review, and live authorization are separate operational/certification workstreams, not unfinished items in this bounded code audit.

## Residual risks and work outside this code audit

1. **No live-trading authorization.** STMAA remains an alpha-stage, Paper-oriented research system. No live deployment path is certified.
2. **No 48-hour Paper soak evidenced here.** Sustained operation across market sessions, reconnects, restarts, and broker-side eventual consistency remains a separate operational validation.
3. **No full real-broker fault-injection campaign.** The targeted regressions primarily use mocks. They do not prove behavior under every real Alpaca timeout, rate limit, outage, cancellation race, partial fill, or delayed update.
4. **Host-local lock boundary.** `fcntl.flock` is not a distributed lock. Multi-host execution and network filesystems are outside the supported safety boundary; operate one active executor per broker account on a shared local state path.
5. **Independent risk review remains necessary.** Sizing, buying-power semantics, exposure aggregation, drawdown provenance, corporate actions, halts, and instrument-specific constraints require independent review before any live consideration.
6. **Operational controls remain separate.** Alert delivery, kill-switch ownership, broker-side notional/order limits, credential least privilege/rotation, backup/restore, and incident runbooks require operational verification.
7. **CI evidence is bounded.** Green workflows prove only that the configured checks passed on the identified commit; they do not prove profitability or absence of untested defects.

## Release gate

Do not enable live orders based on this audit or on green CI alone. A separate, explicitly authorized Paper operational phase must first define and complete the soak duration, monitoring, reconciliation cadence, fault scenarios, acceptance thresholds, and incident/stop procedures. Live consideration requires a separate independent risk review and explicit authorization after those results are reviewed.

The 48-hour Paper test and any live-trading decision are outside the scope of this code-audit record.
