# STMAA A08 — Audit Readiness & Evidence Dossier

**Version:** Working register 0.6 (A01–A07 cross-audit evidence and provenance consolidated)
**Date:** 27 November 2026
**Repository:** `iFabbro/STMAA`
**Repository reference checked:** `main` at `73187e8a7c5a7ef2140d7d94fb452b47f706d0be` (post-merge PR #402)
**Purpose:** evidence traceability for A01–A07; not a new audit, certification, or readiness claim.

## Evidence conventions

- **Verified linkage:** PR/commit and report or source evidence directly inspected in GitHub or the supplied Roadmap file.
- **Reported:** information present in supplied project handoff/Roadmap, not independently re-run here.
- **Gap:** original report or exact retest evidence not recovered in this pass.
- A merged PR is not, by itself, proof that an audit finding is closed.
- This dossier preserves the audit conclusions and limits; it does not reopen or repeat concluded audits.

## A01 — Software Engineering & Architecture

**Disposition reported:** PASS WITH RESIDUAL RISKS, score 7.5/10; closed on 24 November 2026 according to the recovered prior audit context. This register does not reopen A01.
**Audit baseline reported:** `001a66cddf134ea7c5344ff292a1c08d0458c198`; local checkout `main`, clean, macOS 12.7.6 Intel, Python 3.12.13.
**Finding inventory recovered from prior audit context:** A01-F01 through A01-F04 and A01-OBS-03/A01-OBS-05. All were classified OPTIONAL in the reported audit outcome; no finding-specific remediation or retest was recorded as authorized/performed in that outcome.

| Finding | Reported description | Severity | Evidence/recovery status |
|---|---|---|---|
| A01-F01 | Branch protection/rulesets absent | Medium | Finding description recovered from prior audit context. Current GitHub branch metadata reports `main.protected=false`; repository rulesets endpoint returned an empty list on 27 Nov 2026. This confirms the current state, not a historical snapshot of branch settings on 24 Nov. No remediation PR identified. |
| A01-F02 | Import-time side effects | Low | Finding description recovered. PRs #179 and #362 contain related changes, but no primary evidence links either PR to this finding or proves complete remediation. |
| A01-F03 | Alternative import namespaces/package-fallback patterns | Low | Finding description recovered. PR #228 fixes one fallback import defect; it does not prove repository-wide namespace standardization or closure of this finding. |
| A01-F04 | Non-uniform network isolation in tests | Low | Finding description recovered. PR #297 adds an autouse suite-wide socket guard; no finding-specific linkage or retest record recovered. |
| A01-OBS-03 | Concentration of responsibilities in `BrokerExecutor` | Informational | Observation recovered. No refactor was proposed or mapped to a PR; do not treat as a remediation requirement. |
| A01-OBS-05 | Coverage reporting without a minimum `fail_under` threshold | Informational | Observation recovered. PR #356 adds line/branch coverage reporting but explicitly does not add a minimum threshold; it is instrumentation, not closure of the observation. |

**Related historical PRs — verified existence, merge and ancestry; not proven A01 remediation links:**

| PR / merge commit | Verified scope | Relationship limit |
|---|---|---|
| [#179](https://github.com/iFabbro/STMAA/pull/179) — `58e1098c758f3fd9184a6701ff610862209b182e` | Moves SQLite run creation/startup logging from module import to executable `main()`; adds lifecycle/import-side-effect regressions. Merged 30 Oct 2026. | Related to F02, but no explicit A01 finding linkage. |
| [#228](https://github.com/iFabbro/STMAA/pull/228) — `35cb8b04d8619570367dce6246d9b27f9d61501d` | Fixes missing `market_data_fingerprint` in the `data.alpaca_feed` fallback import and adds a subprocess regression. Merged 31 Oct 2026. | Related to import fallback behavior, but does not establish F03 remediation. |
| [#297](https://github.com/iFabbro/STMAA/pull/297) — `2eefa2caba38d933c833b76d87298ccf8a48d010` | Adds an autouse pytest network guard and tests for blocked socket entry points. Merged 6 Nov 2026. | Related to F04, but no direct finding/retest linkage. |
| [#356](https://github.com/iFabbro/STMAA/pull/356) — `f6e62b3aa199d419218cd80b1c78a678a1bbe363` | Adds cross-platform line/branch coverage reports. Merged 13 Nov 2026. | Explicitly introduces no minimum coverage threshold; does not close OBS-05. |
| [#362](https://github.com/iFabbro/STMAA/pull/362) — `c5045592f495c0f183c269b253bbda3b24fa94a5` | Defers dotenv loading until trade runtime startup; adds a regression proving importing `trade` does not load `.env`. Merged 15 Nov 2026. | Related to F02, but no explicit A01 finding linkage. |

The five PR merge commits above were compared with the reported A01 baseline; each is an ancestor of `001a66c...`. Their presence in the baseline is verified, but their inclusion does not prove they were authored as A01 remediation or retested against A01 acceptance criteria.

**A01 CI evidence verified on the exact reported baseline:**
- CI run [45048291683](https://github.com/iFabbro/STMAA/actions/runs/45048291683): completed success; Ubuntu, macOS 15 Intel, macOS 15 ARM and Ruff jobs all success. The three platform test jobs each report **1,591 passed** (Intel: 1 warning); no failed tests.
- Secrets scan [45048291733](https://github.com/iFabbro/STMAA/actions/runs/45048291733): completed success.
- Security Static Analysis [45048291750](https://github.com/iFabbro/STMAA/actions/runs/45048291750): Bandit and Semgrep jobs success.
- Security Baseline push [45048291800](https://github.com/iFabbro/STMAA/actions/runs/45048291800): Gitleaks and Python dependency audit jobs success.

**Primary coverage artifacts recovered from CI run 45048291683 (baseline SHA above):**

| Runner / artifact | Lines covered | Branches covered | Artifact |
|---|---:|---:|---|
| Ubuntu / `coverage-ubuntu-latest` | 7,081 / 20,612 (34.35%) | 2,483 / 3,698 (67.14%) | [14763733900](https://github.com/iFabbro/STMAA/actions/runs/45048291683) |
| macOS Intel / `coverage-macos-15-intel` | 7,082 / 20,612 (34.36%) | 2,482 / 3,698 (67.12%) | [14764139921](https://github.com/iFabbro/STMAA/actions/runs/45048291683) |
| macOS 15 / `coverage-macos-15` | 7,082 / 20,612 (34.36%) | 2,482 / 3,698 (67.12%) | [14764512823](https://github.com/iFabbro/STMAA/actions/runs/45048291683) |

The retained `coverage.xml` files were downloaded and their root counters inspected. At the A01 baseline, `pyproject.toml` `[tool.coverage.report]` contains `show_missing = true` and `skip_covered = true`, but no `fail_under` threshold. These artifacts substantiate coverage reporting and the absence of an enforced minimum; they do not prove an A01-specific retest or finding closure.

| Traceability element | Evidence / status |
|---|---|
| Original report | **Gap:** exact A01 report path/content not recovered from the current GitHub `docs/audit/` directory, repository code search, supplied project files, Project Library search, or prior conversation records. |
| Findings | Finding IDs, descriptions, severities and OPTIONAL classification recovered from prior audit context; original finding evidence and report linkage remain unavailable. |
| Remediation PR/commit | Related historical PRs listed above; no finding-specific remediation chain is proven. |
| Retest | Baseline CI/security workflow results and coverage artifacts verified. The 1,591-test platform runs are general CI evidence, not finding-specific A01 retests. No A01-specific retest artifact recovered. |
| Branch protection | Current `main` reports `protected=false`; rulesets list is empty. This is current-state evidence only. |
| Final status | Closed per prior audit outcome; no audit reopened. |
| Residual evidence gap | Original report, finding-level source evidence from the audit itself, explicit PR mapping and A01-specific retest records. CI coverage artifacts improve baseline evidence but do not replace the missing report. |

## A02 — Security & Supply Chain

**Disposition reported:** CLOSED — PARTIAL, score 6.5/10.
**Audit baseline reported:** `001a66cddf134ea7c5344ff292a1c08d0458c198`; macOS Intel, Python 3.12.13.
**Open residual:** `A02-DEP-01` — PyTorch 2.2.2 on Intel macOS; not remediated. The original report and its advisory-to-finding mapping were not recovered. Independent official PyTorch advisories applicable to 2.2.2 are recorded below as corroborating external evidence, not attributed to the original audit.

| Traceability element | Evidence / status |
|---|---|
| Original report | **Gap:** exact A02 report path/content not recovered from the current GitHub `docs/audit/` directory, repository search or supplied project files. |
| Finding | `A02-DEP-01` — Torch 2.2.2 on macOS Intel. **OPEN / not remediated**, as recorded in the supplied Roadmap. Exact advisory IDs and complete original rationale remain unavailable. |
| Remediation-related PR | [#354](https://github.com/iFabbro/STMAA/pull/354), merged 13 Nov 2026 at `d9d7872ff05bcdb19fc10fb34379a55d0353459e`. Keeps torch 2.2.2 only on Intel macOS x86_64, uses torch 2.13.0 elsewhere, constrains Soup Sieve to 4.x (reported runtime 4.1), refreshes platform-specific `uv.lock`, and documents the Intel residual security exposure. This is compatibility mitigation/platform separation, not remediation of the Intel PyTorch advisory exposure. |
| PR #354 validation | PR reports on macOS Intel: `uv lock --check`, `uv sync --locked --group dev`, `uv pip check` passed; `uv run --locked pytest src/tests tests -q`: 1,487 passed. Reported in PR metadata; not rerun in this pass. |
| Security control infrastructure | PR [#353](https://github.com/iFabbro/STMAA/pull/353), merge `9da81114eed48b5f4860a7a46129cc0f5bc63a13`, adds the Security Baseline workflow (locked dependency audit/pip-audit and full-history Gitleaks). PR [#355](https://github.com/iFabbro/STMAA/pull/355), merge `005640aed76d716b5e7d1c26c742117bdfe13db1`, adds Bandit and Semgrep. These establish scanning controls; neither is a remediation of A02-DEP-01. |
| Baseline CI/security runs | CI [45048291683](https://github.com/iFabbro/STMAA/actions/runs/45048291683) success (Ubuntu/macOS Intel/macOS ARM/Ruff); Secrets scan [45048291733](https://github.com/iFabbro/STMAA/actions/runs/45048291733) success; Static Analysis [45048291750](https://github.com/iFabbro/STMAA/actions/runs/45048291750) success; Security Baseline push [45048291800](https://github.com/iFabbro/STMAA/actions/runs/45048291800) success (Gitleaks and dependency audit). All refer to baseline `001a66c...`. |
| Scheduled Security Baseline | [Run 45198477329](https://github.com/iFabbro/STMAA/actions/runs/45198477329), same baseline `001a66c...`, completed failure. Job [137775212971](https://github.com/iFabbro/STMAA/actions/runs/45198477329) failed at Gitleaks step. Its retained log and SARIF artifact [14827964127](https://github.com/iFabbro/STMAA/actions/runs/45198477329) independently confirm a Gitleaks alert under rule `generic-api-key`, file `src/execution/broker_executor.py`, line 267 (columns 26–65), historical commit `7f52b0da517f68afd468ca2ed5ed8454f5b2ea21`, fingerprint `7f52b0da517f68afd468ca2ed5ed8454f5b2ea21:src/execution/broker_executor.py:generic-api-key:267`; scan log reports 1,989 commits scanned. The SARIF snippet is redacted. Direct inspection of that exact source line shows only keyword arguments passing `self.api_key` and `self.api_secret`, not a literal credential. Therefore the Gitleaks detection event is verified, but a true secret disclosure is **not established**; this may be a false positive and remains unclassified without the unredacted secret-validation context. It is separate from A02-DEP-01. |
| Push Security Baseline contrast | [Run 45048291800](https://github.com/iFabbro/STMAA/actions/runs/45048291800) succeeded; its Gitleaks job scanned one commit and reported no leaks. This push result does not negate the scheduled full-history finding above. |
| Dependency audit scope limitation | Job [137287601574](https://github.com/iFabbro/STMAA/actions/runs/45048291800) ran `pip-audit==2.10.1` on `ubuntu-latest` and reported “No known vulnerabilities found”. The workflow defines the dependency-audit job only on Ubuntu. At this baseline, the dependency marker selects `torch==2.13.0` outside macOS Intel and retains `torch==2.2.2` specifically for macOS x86_64. Therefore this successful Linux pip-audit run does **not** establish that the Intel-specific PyTorch 2.2.2 dependency was audited or cleared. |
| Independent upstream advisory cross-check (not attribution to the missing A02 report) | The official PyTorch advisory [GHSA-53q9-r3pm-6pq6 / CVE-2025-32434](https://github.com/pytorch/pytorch/security/advisories/GHSA-53q9-r3pm-6pq6) lists affected versions `<=2.5.1` and patched version `2.6.0`; [GHSA-63cw-57p8-fm3p / CVE-2026-24747](https://github.com/pytorch/pytorch/security/advisories/GHSA-63cw-57p8-fm3p) lists affected versions `<=2.9.1` and patched versions `>=2.10.0`. PyTorch 2.2.2 falls inside both published affected-version ranges. This independently supports the existence of security exposure in the pinned version, but the original A02 report is unavailable, so it does **not** prove which advisory/advisories were cited by the original auditor or whether these were the complete rationale for A02-DEP-01. |
| Final status | PARTIAL / A02-DEP-01 remains open. PR #354 does not remediate PyTorch 2.2.2 on Intel. |
| Residual evidence gap | Original A02 report and exact mapping of `A02-DEP-01` to upstream advisories; platform-matched Intel dependency-audit evidence; no A02-specific retest/acceptance record. The two official PyTorch advisories above independently affect 2.2.2 but cannot substitute for the missing audit record. Gitleaks output is recovered (log + SARIF). |

No A02 tests, dependency scans, broker operations or remediation were rerun/performed in this evidence-recovery pass. Existing CI/workflow evidence is reused as recorded above.

## A07 — Operational Resilience & Reliability

**Disposition reported in latest supplied Roadmap:** CLOSED — PASS WITH RESIDUAL RISKS, score 8/10. Do not rerun the soak absent new material evidence or explicit request.

**48-hour run evidence reported:** run `8b6f062e-472f-4e3b-88d9-b692763c61a5`, commit `001a66cddf134ea7c5344ff292a1c08d0458c198`, Paper read-only production provider. Start `2026-11-22T17:29:49.895084Z`; terminal `2026-11-24T17:29:41.003017Z`; elapsed monotonic `172800.005` seconds. Terminal status `completed` / `duration_elapsed`. 2,641 heartbeats and observations; SQLite integrity check `ok`; two recovered market-data retry warnings; no reported terminal failure or stop trigger. Automatic acceptance report remained `INCOMPLETE` solely on coverage predicate; Roadmap reconciles this against sleep-after-work loop semantics and records it as a residual criterion limitation, not evidence that the soak failed.

| Finding | Severity | Retest / evidence | Disposition |
|---|---|---|---|
| A07-01 — automated coverage threshold does not align with sleep-after-work loop semantics | Low | Run duration and 2,641 heartbeat/observation counts reported; automated coverage predicate remains false and report INCOMPLETE. No runner/threshold change or rerun. | Closed as audit residual risk, per Roadmap |
| A07-02 — maximum heartbeat gap 165.285 seconds, temporally associated with recovered market-data retries | Low | Retry-recovered warning events and run evidence reported; no terminal failure, persistent unavailable observation, stop-safety event or session failure recorded. | Closed as audit residual risk, per Roadmap |

| Traceability element | Evidence / status |
|---|---|
| Original report | [A07 Operational Resilience report](https://github.com/iFabbro/STMAA/blob/main/docs/audit/A07_OPERATIONAL_RESILIENCE_REPORT.md), blob SHA `545207c5773df0ab380566c957dd036f57527b3e`; report date/source commit noted in prior GitHub retrieval as 20 Nov / `8e7ae5e293608952190d39e02e295257dd18ac24`. The report reflects an earlier soak outcome and is materially superseded for the final disposition by the later supplied Roadmap/run evidence. Preserve it as historical, do not overwrite. |
| Later run evidence | Supplied Roadmap, section on post-soak evidence; details above. **Reported, not independently re-run.** |
| Remediation PRs | No remediation PR is asserted as closing A07-01/A07-02 in this register; the final closure is based on interpretation/reconciliation of the completed run and report criterion, not a code fix. |
| Retest | No retest soak requested or performed. Existing run evidence is reused. |
| Final status | Closed with residual risks, as reported by latest supplied Roadmap. |
| Conflict/history | The earlier A07 report on `main` states an unsuccessful/shorter run and open transport/telemetry findings. Subsequent supplied Roadmap documents a different completed 48-hour run on commit `001a66...`. Treat these as chronologically distinct evidence packages and preserve the original report. The later Roadmap/run package determines current disposition; do not silently rewrite the earlier report. |
| Residual evidence gap | The final post-soak evidence package/run artifacts were not fetched directly from repository storage in this pass; they are cited from the supplied Roadmap. |

## A03 — AI Governance & Model Risk (evidence consolidated)

**Verified report:** [A03 final audit report](https://github.com/iFabbro/STMAA/blob/main/docs/audit/A03_AI_GOVERNANCE_MODEL_RISK_REPORT.md), directly retrieved and inspected in full during A08 consolidation.
**Audit baseline:** `09b5eb55985b93c1c8b3a2ac1fac5ed202a16177`.
**Disposition:** CLOSED — PASS WITH RESIDUAL RISKS, 8/10 (report conclusion).
**Scope:** source-review, governance controls, documentation and offline/mock regression evidence only. No holdout activation or prospective outcome evidence.

| Finding / control | Remediation linkage | Retest evidence / status |
|---|---|---|
| A03-F01 — persisted accounting values trusted by semantic replay (Medium) | PR #395, merge/baseline `09b5eb55985b93c1c8b3a2ac1fac5ed202a16177` | PASS: replay reconstructs portfolio and SPY benchmark states; regression cases reject altered accounting values/metrics and semantically inconsistent ledger records. |
| A03-F02 — activation approval attribution insufficient (Medium) | PR #390 (`2169c6001d10702a26a07c1496e1a46883f930b0`) and PR #394 (`c0cae0514f5ac19edc56804fd6eddab3d8737673`) | PASS: structured operator identity, exact scope and review evidence bound to freeze-register/source conditions. Residual manual verification/no cryptographic identity proof. |
| A03-F03 — truthy non-boolean authorization accepted (Low) | PR #391 (`0a6440746082613da6181792499b31c2ea5cf940`) | PASS: strict bool check and negative regression cases. |
| A03-F04 — stale holdout documentation (Informational) | Documentation reconciled in A03 closeout | Remediated per report; holdout status remains PRE-FREEZE / NOT ACTIVE / COLLECTION NOT AUTHORIZED. |

**Post-merge verification (report evidence):** CI run `45815137366` (Ruff and Ubuntu/macOS Intel/macOS ARM tests), Security Baseline `45815137309`, Static Analysis `45815137426`, Secrets scan `45815137455`; all success on exact audited commit `09b5eb55985b93c1c8b3a2ac1fac5ed202a16177`. This was reported by the audit document; no workflows/tests were re-run during A08 consolidation.

**Explicit residuals:** manual external approval verification; holdout not activated (freeze-host validation remains a future authorization gate); no prospective 252-session outcome evidence; AST isolation tests cannot prove absence of all indirect execution paths; upstream data truth/lineage limitations remain referenced under A05.

| Traceability element | Evidence / status |
|---|---|
| Original report | **Verified:** report path above; full report inspected. |
| Findings | Four findings A03-F01 through A03-F04, IDs/severity/disposition mapped above. |
| Remediation PR/commits | #390, #391, #394, #395 mapped above. |
| Retest | **Reported:** specific regression retests and four post-merge workflows success on audited SHA. Not rerun during A08. |
| Final status | CLOSED — PASS WITH RESIDUAL RISKS, 8/10, per report. |
| Remaining gate | Holdout freeze/activation remains unauthorized and is not an A08 evidence gap or closure condition for A03. |

## A04 — Quantitative & Statistical Validation

**Disposition reported in the latest supplied Roadmap:** CLOSED — PASS WITH RESIDUAL RISKS, score 7/10. A08 does not repeat the benchmark or authorize PPO tuning/retraining.

**Roadmap-reported benchmark evidence (not independently re-read from the JSON payloads in this pass):**
- Source files named by the Roadmap: `reports/ppo_benchmark_multiseed_20261110_42_7_21.json` and `reports/ppo_benchmark_comparison_matched_20261110.json`. These files are not present in the tracked GitHub tree at the A08 baseline; the tracked repository contains methodology documentation (`docs/PPO_BENCHMARK.md`, `docs/PPO_BENCHMARK_METHODOLOGY_AUDIT.md`), not these run payloads.
- Roadmap reports a 2,207-row × 112-column snapshot whose SHA-256 matched the `processed.sha256` in manifest `4e8f9eac*.json`; both benchmark reports had 11 folds. Multi-seed PPO evaluation used seeds 42, 7 and 21, 11 folds each (33 PPO executions).
- Reported mean-per-fold results: PPO seed 42 return 5.644%, volatility 14.295%, Sharpe 0.976, Sortino 1.524, max drawdown -8.639%, costs USD 2,354.74; seed 7 return 2.998%, volatility 13.683%, Sharpe 0.675, Sortino 1.147, max drawdown -10.198%, costs USD 2,380.71; seed 21 return 2.019%, volatility 14.454%, Sharpe 0.419, Sortino 0.624, max drawdown -9.600%, costs USD 2,315.40. Equal-weight static: 5.664% return, Sharpe 0.945, costs USD 75.00; equal-weight at PPO exposure: 5.377% return, Sharpe 1.091, costs USD 645.92; SPY 50%: 3.501% return, Sharpe 1.123; SPY 100%: 6.998% return, Sharpe 1.156.
- The matched exposure comparator applies to seed 42 only; do not generalize it across all three seeds. Fold averages are descriptive independent all-cash simulations, not a compounded continuous track record.
- Interpretation in the Roadmap: seed sensitivity; robust economic advantage not established; trading costs high versus static equal-weight; no formal significance tests or confidence intervals.

| Finding | Severity | Roadmap-reported disposition |
|---|---|---|
| A04-F01 — seed sensitivity | Medium | Residual risk |
| A04-F02 — robust economic advantage over benchmarks not demonstrated | Medium | Residual risk |
| A04-F03 — trading costs high versus static equal-weight | Medium | Residual risk |
| A04-F04 — descriptive statistical evidence only; no formal significance test/CI | Low | Residual risk |

**Evidence limitation:** the final A04 report and named JSON payloads were not found in the tracked GitHub tree or the available Project Library search. The metrics and finding inventory above are therefore **Roadmap-reported**, not independently verified from the original report/payloads in this A08 pass. The methodology docs do not replace run artifacts. PR #375 remains unmerged and is not treated as merged remediation or proof of closure.

## A05 — Data Integrity & Data Governance

**Disposition reported in the latest supplied Roadmap:** CLOSED — PASS WITH RESIDUAL RISKS, score 7.5/10.

- **A05-F01 — Medium:** dataset provenance was insufficiently reconstructable and date-only local files could collide.
- **Remediation:** PR [#384](https://github.com/iFabbro/STMAA/pull/384), merge `db01b78cfcc485b5488f08d91b091bcc27b08ad8`. It adds content-addressed raw/processed snapshots and a JSON manifest binding request/contract, SHA-256, schema, dimensions, acquisition timestamp and Git revision; provenance references are propagated into training metadata, walk-forward and benchmark outputs.
- **Retest evidence:** PR head `e800489480911edf915c1d4cabe559e697cbbf42`; all nine PR checks independently queried on 27 Nov 2026 completed success (Ubuntu, macOS Intel, macOS ARM, Ruff, dependency audit, Gitleaks, Bandit, Semgrep and the additional Gitleaks check). Merge commit verified above. No separate post-merge workflow result is claimed.
- **Residual risks:** snapshots/manifests remain local and Git-ignored; SHA-256 proves integrity of archived bytes, not independent truth/correctness of Alpaca's upstream response; historical acquisitions before the change are not retroactively covered.
- **Evidence limitation:** the A05 final audit report/data-control matrix was not found in the tracked GitHub tree or available Project Library search. The finding, disposition and residuals above are Roadmap-reported; PR #384 and its CI state are independently verified.

## A06 — Risk Controls & Execution Safety

**Disposition:** CLOSED — PASS WITH RESIDUAL RISKS, score 8/10, for source review and offline/mock retest scope only.

**Primary report supplied in this Project conversation:** `STMAA_A06_FINAL_AUDIT_REPORT_2026-11-25.md` (inspected for this consolidation); it is not present as a tracked report under `docs/audit/` in the current repository tree.

- Baseline/merge: PR #388, head `fc3b0ecdae02938b82aa6c3fbf5ad55275afe738`, merge commit `82a24d4a9a56cb77f154674409ea975edbd9e91e`.
- **A06-F01 — Medium:** stop signal was not an execution interlock. Remediated by PRs #386/#388; direct mocked boundary regressions cover DAY submit, OPG submit, liquidation cancel and emergency close.
- **A06-F02 — Medium:** pre-submit guard rejection misclassified as ambiguous submission. Remediated by PR #387; DAY clears only the exact proven-unsubmitted intent; OPG records not-submitted state without a needless broker lookup.
- **A06-F03 — Medium:** emergency close guard rejection left a false unresolved intent. Remediated by PR #388; exact unsubmitted close intent is cleaned up; persistence failure restores unresolved state and remains fail-closed.
- **Retest:** `src/tests/test_a06_stop_interlock_mutation_boundaries.py` adds five mocked regressions. PR-head CI run `45654569534` passed Ubuntu/macOS Intel/macOS ARM and Ruff; Gitleaks, dependency audit, Bandit and Semgrep workflows also passed. The merge commit has no separately reported workflow run in the inspected evidence.
- **Control review:** aggregate USD 1,000 exposure cap and pending BUY reserve; DAY/OPG sizing/preflight; Paper endpoint/client identity; stop interlock; state persistence/reconciliation; drawdown provenance; host-local execution lock.
- **Residual risks:** guard-to-SDK race; host-local (not distributed) lock; manual OPG recovery for skipped/partial batches; ambiguous broker outcomes may require operator reconciliation; no real Alpaca mutation/fault-injection test; no local macOS 12.7.6 iMac retest. No broker mutation or Live Trading was performed.

## A07 — Operational Resilience & Reliability

**Disposition reported in the latest supplied Roadmap:** CLOSED — PASS WITH RESIDUAL RISKS, score 8/10. No soak rerun is authorized or required absent new material evidence or explicit request.

**Chronology must remain explicit:**
- The tracked report `docs/audit/A07_OPERATIONAL_RESILIENCE_REPORT.md` (report date 20 Nov 2026; source commit `8e7ae5e293608952190d39e02e295257dd18ac24`; run `6c538292-6ddf-497c-be8f-1a929b3f1911`) records a failed/short run of 105,672.817 seconds, approximately 29h 21m, ending after DNS resolution failures and stop-controller activation. Preserve this report unchanged as the historical first run.
- The later supplied Roadmap records a distinct run `8b6f062e-472f-4e3b-88d9-b692763c61a5`, baseline `001a66cddf134ea7c5344ff292a1c08d0458c198`, Paper read-only provider, start `2026-11-22T17:29:49.895084Z`, terminal `2026-11-24T17:29:41.003017Z`, elapsed monotonic 172,800.005 seconds, terminal `completed/duration_elapsed`.
- Later-run counts: 2,641 heartbeats and 2,641 shadow observations; two recovered market-data retry warnings; SQLite integrity check `ok`; no reported ERROR, terminal failure, stop trigger, persistent market-data-unavailable observation or unrecovered outage. Maximum heartbeat gap 165.285 seconds, temporally associated with recovered retries.
- The automatic acceptance report remained `INCOMPLETE` (exit 2) solely because `heartbeat_and_observation_coverage_sufficient` was false. The Roadmap interprets this as a criterion/loop-semantics limitation and records A07-01/A07-02 Low as residual risks; it does not rewrite the automatic report to PASS.
- A Paper account snapshot at `2026-11-24T18:07:54.795489Z` reportedly showed ACTIVE, equity/cash USD 100,000, zero positions and zero orders. This is a point-in-time post-run snapshot, not evidence for every instant of the soak.

**Evidence limitation:** the later run's raw event/database/report artifacts were not found in the tracked repository tree in this pass. Its detailed metrics and final disposition are **Roadmap-reported**, not independently reconstructed from the raw soak database/logs. Do not conflate the later completed run with the earlier failed run in the tracked A07 report.


## Consolidated open evidence gaps / residuals

1. **A01:** original report and finding-level audit evidence remain missing. The exact-baseline CI jobs (1,591 passed on each platform) and coverage XML artifacts are now recovered; measured line coverage is 34.35–34.36%, branch coverage 67.12–67.14%, with no configured `fail_under`. These are baseline quality artifacts, not finding-specific retests. Related PRs #179, #228, #297, #356 and #362 remain contextual only; no finding-specific remediation/retest mapping is proven.
2. **A02:** original report and its exact advisory mapping remain missing. Independent official PyTorch advisories GHSA-53q9-r3pm-6pq6 (CVE-2025-32434) and GHSA-63cw-57p8-fm3p (CVE-2026-24747) both list affected ranges that include 2.2.2; this corroborates exposure but is not attributed to the missing report. `A02-DEP-01` is still open; PR #354 is a compatibility mitigation, not closure. Scheduled Gitleaks alert is now independently verified from its log and SARIF at `broker_executor.py:267`, commit `7f52b0d...`; the source line passes variable attributes rather than a visible literal credential, so a true leak is not established and the alert may be a false positive. The successful dependency audit ran on Ubuntu and resolved the non-Intel PyTorch marker, so it does not clear the Intel-specific dependency.
3. **A07:** later 48-hour run package is referenced by the supplied Roadmap, but run artifacts were not independently fetched from repository storage in this pass. Historical report remains preserved and explicitly superseded for disposition by later evidence.
4. **A04:** exposure-matched equal-weight multi-seed comparator remains inconclusive; draft PR #375 is not merged and is not confirmed evidence.
5. **A03:** holdout remains PRE-FREEZE / NOT ACTIVE / COLLECTION NOT AUTHORIZED; this is a deliberate authorization gate, not a missing audit-evidence gap.
6. **A06:** residual risks remain as listed in its final report/handoff; no live broker fault injection or local macOS 12.7.6 retest claimed.

## Overall A08 status

**IN PROGRESS — register history: PRs #398–#402; this revision consolidates A04–A07 evidence/provenance on main after PR #402.** Current `main` baseline checked for this revision is `73187e8a7c5a7ef2140d7d94fb452b47f706d0be`. The register now distinguishes directly inspected GitHub/Project evidence from Roadmap-reported outcomes and missing raw artifacts. Original A01/A02 reports, A04 benchmark JSON payloads, A05 final report/data-control matrix and the raw later A07 soak package remain unavailable in the tracked repository/available Project Library; those gaps are not reconstructed by assumption. Publication does not close A08. This document is not a certification, compliance attestation, release authorization or live-trading readiness statement. No audit, benchmark, soak or test was rerun; no source, model, configuration, `.env`, runtime, holdout or broker state was changed.

## Next step

Perform the final A08 consistency/readiness pass against this register and the current Roadmap/README: verify that every audit disposition, finding, remediation, retest and residual risk is represented without overstating source strength; preserve the evidence gaps listed above; then prepare the internal A08 closeout decision and readiness status. Do not rerun A01–A07, the A04 benchmark or the A07 soak, and do not close A02-DEP-01. No source, configuration, runtime, model, holdout or broker operation is part of this step.
