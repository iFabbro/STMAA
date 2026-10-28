# STMAA End-to-End Risk Audit — Evidence and Residual Risks

**Audit snapshot:** 2026-10-28  
**Scope:** order submission/restart recovery, broker-state persistence, portfolio exposure limits, pending orders, account/position integrity, and missing or stale market data.

## Executive status

The repository's automated checks validate a growing set of fail-closed behaviours, but this is **not a live-trading safety certification**. The project remains an alpha-stage/paper-trading system. No live orders were sent as part of this audit. The E2E smoke scripts that can submit Paper orders were inspected but not executed; broker interactions in the targeted regression tests are mocked.

## Evidence checked

### Merged changes and CI evidence

- **PR #76** — rejects a materially future fill timestamp even when a linked commission exists. The post-merge `pytest`, `test`, `ruff`, and `gitleaks` checks were verified green.
- **PR #77** — anchors the default broker-state path to the repository root and adds restart/path regression tests.
- **PR #78** — anchors relative `STMAA_BROKER_STATE_FILE` overrides to the repository root. The post-merge `pytest`, `test`, `ruff`, and `gitleaks` checks were verified green.
- **PR #79** — adds restart reconciliation coverage for ambiguous order submissions and verifies that an incomplete account snapshot blocks submission. The PR was merged; this audit record does not substitute for a fresh check of its post-merge run history.
- **PR #94** — adds combined fail-closed scenarios. All four checks on merge commit `6e1cc3a888d910c5c3e422e922a185c1dd29d457` were verified complete and successful: `pytest`, `test`, `ruff`, `gitleaks`.

### Risk-matrix coverage present in the merged test suite

- Pending order with unestimable exposure: block before submission.
- Partially filled pending order: reserve exposure conservatively.
- Priced pending order plus stale quote for a new target: block before submission.
- Persisted ambiguous submission after restart plus broker lookup timeout: preserve unresolved intent and block before account/market-data reads and new submission.
- Incomplete broker position snapshot: block before submission.
- Stale, future, or untimestamped quotes: reject for new-position preflight.
- Stale account snapshot or invalid equity: block target execution.
- Non-liquidating circuit breaker: no target-order path and no liquidation.
- Liquidation recovery: pending/ambiguous close intents are retained until broker evidence and fresh position snapshots support resolution.

### Additional matrix extension in review

**PR #95** adds three combination tests:
1. Unpriced pending order with otherwise fresh target quotes.
2. Priced pending order with a missing target quote.
3. Stale account snapshot combined with an unpriced pending order, asserting no market-data read and no order submission.

Do not mark PR #95 as accepted until all required CI checks finish successfully and the merge/post-merge checks are verified.

## Corrected anomalies

- Commission reconciliation no longer bypasses fill-timestamp validation when a linked commission is present.
- Broker-state persistence no longer relies on the process working directory for its default path; relative configured paths are also rooted consistently.
- Ambiguous submissions are persisted and reconciled by durable client order ID rather than blindly retried.
- Unknown pending-order exposure, invalid/incomplete broker snapshots, and unusable market prices fail closed rather than allowing new exposure.

## Residual risks / not yet demonstrated

1. **No live-trading authorization:** repository documentation describes the system as alpha-stage and paper trading. No live deployment path has been certified.
2. **No real broker execution test in this audit:** Paper/live order submission, cancellation, partial fill, broker outage, rate limiting, and eventual consistency were tested only with mocks or source inspection where noted.
3. **No prolonged Paper soak test:** there is no audit evidence here of sustained operation across sessions, market opens/closes, reconnects, and broker-side state reconciliation.
4. **Persistence under infrastructure failures:** verify atomic writes, disk-full/permission errors, process termination during writes, backup/restore, and concurrent process access. Path stability alone does not prove durable transactional state.
5. **Independent risk review:** independently validate sizing math, exposure aggregation, buying-power semantics, drawdown high-water mark provenance, corporate actions, trading halts, and instrument-specific constraints.
6. **Operational controls:** verify monitoring/alert delivery, kill-switch ownership, broker-side order/notional limits, credential rotation and least privilege, restart/runbook procedures, and incident recovery.
7. **Evidence limits:** green CI proves only that the checked code passed the configured automated tests; it does not prove coverage of every broker response, market regime, race condition, or deployment configuration.

## Release gate recommended

Do not enable live orders until all required CI checks are green on the final merged commit; the remaining matrix is reviewed; Paper soak and fault-injection tests have documented results; persistence and concurrency failure modes are tested; broker-side limits and kill switch are verified; and a separate reviewer signs off on the risk model and deployment configuration.
