# STMAA — A07 Operational Resilience & Reliability Audit Report

**Audit ID:** A07  
**Report date:** 2026-11-20  
**Repository:** `iFabbro/STMAA`  
**Source commit:** `8e7ae5e293608952190d39e02e295257dd18ac24`  
**Soak run:** `6c538292-6ddf-497c-be8f-1a929b3f1911`  
**Audit status:** **FAIL — not closed**  
**Provisional score:** **5/10**  
**Scope:** 48-hour Alpaca Paper read-only production-provider soak, transport retry handling, health telemetry, automatic stop behavior, and persisted run evidence.

> This is an internal, evidence-based audit report. It is not an independent certification, compliance attestation, or authorization for live trading. STMAA remains experimental/alpha and Paper-only.

## 1. Executive summary

The 48-hour Paper soak did not complete. The recorded run ended in `failed` after **105,672.817 seconds (approximately 29 hours, 21 minutes)**, short of the requested 172,800 seconds.

The immediate recorded failure was a DNS name-resolution error for `data.alpaca.markets`, surfaced through the Alpaca market-data request as a connection failure. The feed exhausted three attempts; the stop controller then latched `broker_api_unreachable_threshold` and the runner finalized the session as failed.

The evidence supports that the fail-closed stop path activated and that order submission was disabled. It does **not** establish the underlying cause of the DNS failure (local resolver, network, ISP, upstream DNS, or another layer), nor does it establish a persistence subsystem failure. The provider's transport-error branch assigns several health booleans to `False` without independently measuring those subsystems in that branch.

The soak therefore fails its duration and continuity acceptance criteria. A07 remains open pending remediation decisions, targeted retests, and a new successful 48-hour soak.

## 2. Scope, exclusions, and method

### Included
- Run lifecycle, elapsed duration, heartbeat and shadow-observation evidence.
- Alpaca `get_stock_bars` retry behavior for transient transport exceptions.
- Production provider health snapshot on exhausted market-data retries.
- Stop-controller validation, threshold ordering, and sticky stop behavior.
- Runner event ordering and terminal failure behavior.
- Point-in-time SQLite integrity evidence previously supplied for this run.

### Excluded
- Root-cause attribution for the external DNS/network failure.
- Independent verification of the user's Mac, router, ISP, resolver, or Alpaca infrastructure.
- A fresh execution of the test suite or CI in this audit session.
- Broker mutations, live trading, new Paper orders, restart/recovery experiments, configuration changes, and a new soak.
- Any change to strategy, PPO artifact, holdout, trading caps, or runtime configuration.

### Method
Read-only examination of operator-supplied run output and source excerpts from the run's source commit. Source review covered:
- `src/data/alpaca_feed.py`
- `src/utils/paper_soak_production_provider.py`
- `src/utils/paper_soak_stop_controller.py`
- `src/utils/paper_soak_shadow_runner.py`
- `src/utils/paper_soak_report.py`
- `tests/test_alpaca_feed_retry.py`
- `tests/test_paper_soak_stop_controller.py`

Source inspection is not equivalent to executing tests. No test suite or CI workflow was executed as part of this report preparation.

## 3. Run facts and evidence register

| Evidence ID | Evidence | Observed result | Limitation |
|---|---|---|---|
| E-A07-01 | Run metadata and terminal events | Run `6c538292-6ddf-497c-be8f-1a929b3f1911`; status `failed`; elapsed 105,672.817 s; target 172,800 s | Operator-supplied local run evidence |
| E-A07-02 | Run heartbeat/observation records | 346 heartbeats; 346 shadow observations (345 INFO, 1 ERROR) | Does not prove uninterrupted host/network health between observations |
| E-A07-03 | Final soak log | DNS resolution failure for `data.alpaca.markets`; three transport attempts exhausted at 2026-11-19 21:57:03 UTC | Does not identify the DNS failure's infrastructure origin |
| E-A07-04 | SQLite event sequence | `market_data_retry_exhausted`, `stop_triggered`, final ERROR shadow observation, heartbeat 346, `session_failed` | Event records prove application decisions, not external root cause |
| E-A07-05 | SQLite integrity check | `PRAGMA integrity_check` returned `ok` in a point-in-time check | Does not prove every persistence operation or the provider health flag was correct throughout the run |
| E-A07-06 | Source review: `alpaca_feed.py:323-351` | Retries `requests.ConnectionError` and `requests.Timeout`; 3 attempts; backoff 0.5 s then 1.0 s | No DNS-specific regression test result was executed in this audit |
| E-A07-07 | Source review: `paper_soak_production_provider.py:94-125` | Exhausted transport exception produces an unhealthy snapshot and skips broker snapshot/strategy inference | Several false health fields are assigned without checking those subsystems in this exception branch |
| E-A07-08 | Source review: `paper_soak_stop_controller.py:16-17, 88-132, 154-175` | API failure threshold is 3; stop is latched before durable event write; API threshold has first-failure priority after snapshot validation | Source inspection only; no new execution test performed |
| E-A07-09 | Source review: `paper_soak_shadow_runner.py:83-140` | Runner records retry exhaustion, evaluates stop, records observation/heartbeat, and finalizes failed run | Source inspection only |
| E-A07-10 | Source review: `paper_soak_report.py:230-286` | Report treats any false safety boolean, stop, error event, or failed run as failure | A report output for this run was not independently regenerated in this audit session |

### Timeline

- **Start:** 2026-11-18 16:35:55.855626 UTC.
- **Last recorded healthy observation:** 2026-11-19 21:51:58 UTC.
- **Transport failure:** 2026-11-19 approximately 21:57:01–21:57:03 UTC.
- **Stop reason:** `broker_api_unreachable_threshold`.
- **End:** 2026-11-19 21:57:03.281724 UTC.
- **Elapsed:** 105,672.817 seconds, approximately 29h21m.
- **Requested duration:** 172,800 seconds (48h).

The exact onset of the DNS fault is unknown; the interval between the last healthy observation and the final failure is approximately five minutes.

## 4. Findings

### A07-F01 — Limited transport retry window for a long-running soak

**Severity:** Medium  
**Status:** OPEN  
**Evidence:** E-A07-03, E-A07-06, E-A07-08  
**Observed condition:** `_request_stock_bars_with_retry` makes at most three attempts and sleeps 0.5 s and 1.0 s between attempts (1.5 s total configured delay, excluding request time). A DNS resolution failure was surfaced as a connection error and exhausted this retry budget. The provider then returned an unhealthy observation and the controller stopped the run.

**Impact:** A brief transient resolver/network interruption can terminate a multi-hour soak. The current behavior preserves safety by refusing to infer with unavailable data, but it does not provide continuity through a recoverable transport interruption.

**Root cause:** The immediate application-level trigger is proven: bounded retries were exhausted after a DNS resolution failure. The infrastructure-level root cause is **not established**.

**Recommendation (not yet implemented or approved):**
- Evaluate a bounded recovery/wait policy appropriate for a 48-hour observation process, with a maximum outage budget and explicit degraded/unavailable telemetry.
- Do not run strategy inference, calculate new targets, or treat stale data as valid during an outage.
- Preserve fail-closed behavior if the outage budget is exceeded or data validity/freshness cannot be re-established.
- Add deterministic tests for DNS-like `ConnectionError`, timeout, recovery after multiple failures, outage-budget exhaustion, and no inference while data is unavailable.

**Retest / acceptance criteria:**
1. Deterministic tests prove transient transport recovery without inference on unavailable/stale data.
2. Exhaustion of the defined outage budget stops the run and records the primary cause.
3. Retry timing and maximum outage duration are explicit and bounded.
4. A new 48-hour Paper read-only soak completes with all required coverage and no stop/error events.

### A07-F02 — Unavailable telemetry is represented as confirmed unhealthy persistence/state

**Severity:** Medium  
**Status:** OPEN  
**Evidence:** E-A07-07, E-A07-08, E-A07-10  
**Observed condition:** In the `TransientMarketDataError` branch, the provider returns before reading a broker snapshot or evaluating persisted execution state. It nevertheless sets `persistence_healthy=False`, `broker_state_consistent=False`, `exposure_determinable=False`, and `broker_snapshot_complete=False`.

**Impact:** The resulting observation conflates “not measured because the upstream market-data request failed” with “measured and confirmed unhealthy.” This reduces diagnostic precision and can create misleading evidence in reports. The separate SQLite `integrity_check=ok` does not prove the executor's persistence state was healthy, but neither does this branch prove that it was unhealthy.

**Root cause:** The transport-error branch uses false booleans as conservative placeholders for downstream checks that it deliberately does not perform. This is directly visible in source.

**Recommendation (not yet implemented or approved):**
- Represent health dimensions as independently measured, unavailable/unknown, or confirmed unhealthy; do not use `False` to mean “not checked.”
- Preserve fail-closed behavior for unknown signals.
- Preserve the primary transport failure reason in the stop event and report while retaining explicit unavailable statuses for downstream signals.
- Ensure the report distinguishes a failed control from a control that could not be evaluated.

**Retest / acceptance criteria:**
1. A transport failure produces explicit unavailable/unknown states for checks not performed.
2. The controller still stops safely and records the transport failure as the primary cause.
3. A confirmed persistence failure remains distinguishable and triggers its own finding/reason.
4. Report tests verify the distinction between unavailable, unhealthy, and healthy telemetry.

### A07-F03 — API failure counter semantics are insufficiently explicit

**Severity:** Low  
**Status:** OPEN  
**Evidence:** E-A07-06, E-A07-07, E-A07-08  
**Observed condition:** The provider assigns `exc.attempts` from one `get_stock_bars` call to `api_consecutive_failures`. The value therefore represents the number of failed attempts within that request, not a separately accumulated count across multiple observations. The controller threshold is also 3.

**Impact:** The field name can be read as a counter accumulated across observations, while the actual value is request-scoped. This can confuse operational interpretation and future monitoring changes. The current stop decision is nevertheless consistent with the observed three failed attempts.

**Recommendation (not yet implemented or approved):**
- Rename or document the signal to state its request scope (for example, request transport attempts), or implement a genuinely observation-scoped consecutive-failure counter if that is the intended control.
- Keep retry attempts, consecutive failed requests, and consecutive failed observations as distinct metrics.

**Retest / acceptance criteria:**
1. Tests assert the intended counter scope across success, failure, recovery, and subsequent observations.
2. Event payloads and report labels match the implemented semantics.
3. Stop thresholds are tested at boundary values and after recovery.

## 5. Controls that behaved as intended

- The production provider did not proceed to broker snapshot collection or PPO inference after market-data transport retries were exhausted.
- The stop controller checks the API failure threshold before its other false health signals when the snapshot passes type validation; the recorded primary stop reason was `broker_api_unreachable_threshold`.
- The stop is latched in memory before attempting to persist the stop event.
- The runner records the stop and finalizes the run as `failed`.
- The observed event payload reports order submission as disabled. No broker order mutation or order submission is evidenced by the supplied run records.

These are positive observations for this failure scenario only. They do not establish full operational resilience or prove all broker/network failure modes are safe.

## 6. Tests and checks

### Reviewed, not re-executed
- `tests/test_alpaca_feed_retry.py`: source contains tests for transient `ConnectionError` recovery, bounded exhaustion, and non-retry of structural errors.
- `tests/test_paper_soak_stop_controller.py`: source contains tests for healthy snapshots, each boolean safety violation, exact API threshold, heartbeat threshold, and missing/invalid signals.

### Not executed in this audit session
- Targeted pytest tests.
- Full pytest suite.
- Ruff.
- Gitleaks.
- Ubuntu / macOS Intel / macOS ARM CI matrix.
- DNS fault injection on the target iMac.
- New 48-hour soak.

CI state for the soak source commit was not independently verified as part of this report. Do not infer green CI from the existence of test files or historical CI results.

## 7. Remediation plan and change governance

No source-code, configuration, model, runtime, or database change is made or authorized by this report.

If remediation is approved:
1. Keep documentation and each logically separate code remediation in dedicated PRs.
2. Do not edit `main` directly.
3. Use mock/deterministic tests first; do not submit broker orders.
4. Require Ruff, Gitleaks, and the applicable CI matrix to pass before merge.
5. Verify the actual GitHub PR, commit, workflow/job conclusions, and merge state.
6. Retest each finding against its acceptance criteria. A proposed or merged change does not close a finding by itself.

## 8. Overall assessment

| Area | Status |
|---|---|
| Requested 48-hour duration | FAIL |
| Transport retry continuity | PARTIAL / finding open |
| Fail-closed stop behavior in observed scenario | PASS (scenario-specific) |
| Persistence health attribution | PARTIAL / finding open |
| API failure telemetry semantics | PARTIAL / finding open |
| Evidence completeness | PARTIAL |
| A07 closure | **NOT AUTHORIZED** |

**Overall A07 status: FAIL — not closed.**  
**Provisional score: 5/10.**

### Residual risk

- Intermittent DNS/network failures can terminate long-running observation sessions.
- The origin of the observed DNS failure remains unknown.
- The provider's transport-error snapshot can overstate confirmed downstream subsystem failures.
- The current API failure counter is request-scoped despite its broader-sounding name.
- No successful 48-hour production-provider soak has been demonstrated by this run.

A07 can be reconsidered only after approved remediation, targeted regression tests, CI verification, post-remediation evidence review, and a new complete 48-hour Paper read-only soak.
