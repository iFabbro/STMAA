# STMAA A08 — Audit Readiness & Evidence Dossier

**Version:** Working register 0.4 (A01/A02 evidence recovery; A03 consolidated)
**Date:** 27 November 2026
**Repository:** `iFabbro/STMAA`
**Repository reference checked:** `main` at `0b9aba255e5b40265bab0f10dc0df9782e4c8741` (post-merge PR #400)
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
- CI run [45048291683](https://github.com/iFabbro/STMAA/actions/runs/45048291683): completed success; Ubuntu, macOS 15 Intel, macOS 15 ARM and Ruff jobs all success.
- Secrets scan [45048291733](https://github.com/iFabbro/STMAA/actions/runs/45048291733): completed success.
- Security Static Analysis [45048291750](https://github.com/iFabbro/STMAA/actions/runs/45048291750): Bandit and Semgrep jobs success.
- Security Baseline push [45048291800](https://github.com/iFabbro/STMAA/actions/runs/45048291800): Gitleaks and Python dependency audit jobs success.

| Traceability element | Evidence / status |
|---|---|
| Original report | **Gap:** exact A01 report path/content not recovered from the current GitHub `docs/audit/` directory, repository search or supplied project files. |
| Findings | Finding IDs, descriptions, severities and OPTIONAL classification recovered from prior audit context; original finding evidence and report linkage remain unavailable. |
| Remediation PR/commit | Related historical PRs listed above; no finding-specific remediation chain is proven. |
| Retest | Baseline CI/security workflow results verified. No A01-specific retest was run in this evidence-recovery pass; no finding-specific retest artifact recovered. |
| Branch protection | Current `main` reports `protected=false`; rulesets list is empty. This is current-state evidence only. |
| Final status | Closed per prior audit outcome; no audit reopened. |
| Residual evidence gap | Original report, finding-level source evidence, explicit PR mapping and A01-specific retest records. |

## A02 — Security & Supply Chain

**Disposition reported:** CLOSED — PARTIAL, score 6.5/10.
**Audit baseline reported:** `001a66cddf134ea7c5344ff292a1c08d0458c198`; macOS Intel, Python 3.12.13.
**Open residual:** `A02-DEP-01` — PyTorch 2.2.2 on Intel macOS; not remediated. The original report/advisory identifiers were not recovered, so this register does not invent CVE/advisory IDs or severity beyond the recorded finding.

| Traceability element | Evidence / status |
|---|---|
| Original report | **Gap:** exact A02 report path/content not recovered from the current GitHub `docs/audit/` directory, repository search or supplied project files. |
| Finding | `A02-DEP-01` — Torch 2.2.2 on macOS Intel. **OPEN / not remediated**, as recorded in the supplied Roadmap. Exact advisory IDs and complete original rationale remain unavailable. |
| Remediation-related PR | [#354](https://github.com/iFabbro/STMAA/pull/354), merged 13 Nov 2026 at `d9d7872ff05bcdb19fc10fb34379a55d0353459e`. Keeps torch 2.2.2 only on Intel macOS x86_64, uses torch 2.13.0 elsewhere, constrains Soup Sieve to 4.x (reported runtime 4.1), refreshes platform-specific `uv.lock`, and documents the Intel residual security exposure. This is compatibility mitigation/platform separation, not remediation of the Intel PyTorch advisory exposure. |
| PR #354 validation | PR reports on macOS Intel: `uv lock --check`, `uv sync --locked --group dev`, `uv pip check` passed; `uv run --locked pytest src/tests tests -q`: 1,487 passed. Reported in PR metadata; not rerun in this pass. |
| Security control infrastructure | PR [#353](https://github.com/iFabbro/STMAA/pull/353), merge `9da81114eed48b5f4860a7a46129cc0f5bc63a13`, adds the Security Baseline workflow (locked dependency audit/pip-audit and full-history Gitleaks). PR [#355](https://github.com/iFabbro/STMAA/pull/355), merge `005640aed76d716b5e7d1c26c742117bdfe13db1`, adds Bandit and Semgrep. These establish scanning controls; neither is a remediation of A02-DEP-01. |
| Baseline CI/security runs | CI [45048291683](https://github.com/iFabbro/STMAA/actions/runs/45048291683) success (Ubuntu/macOS Intel/macOS ARM/Ruff); Secrets scan [45048291733](https://github.com/iFabbro/STMAA/actions/runs/45048291733) success; Static Analysis [45048291750](https://github.com/iFabbro/STMAA/actions/runs/45048291750) success; Security Baseline push [45048291800](https://github.com/iFabbro/STMAA/actions/runs/45048291800) success (Gitleaks and dependency audit). All refer to baseline `001a66c...`. |
| Scheduled Security Baseline | [Run 45198477329](https://github.com/iFabbro/STMAA/actions/runs/45198477329), same baseline `001a66c...`, completed failure: Python dependency audit job **success**, Gitleaks secret scan job **failure**. The supplied Roadmap reports this as a historical-commit finding in `src/execution/broker_executor.py` at commit `7f52b0da517f68afd468ca2ed5ed8454f5b2ea21`; the detailed Gitleaks log was not retrievable in this pass, so that location remains Roadmap-reported rather than independently confirmed here. This failure is separate from A02-DEP-01 and must not be conflated with the successful push-triggered run. |
| Final status | PARTIAL / A02-DEP-01 remains open. PR #354 does not remediate PyTorch 2.2.2 on Intel. |
| Residual evidence gap | Original A02 report, exact advisory IDs/rationale, full Gitleaks finding output and any A02-specific retest/acceptance record. |

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

## A04–A06 cross-register references

These audits are summarized here to maintain the A08 cross-register, not to reopen them:

- **A04:** benchmark audit closed per Roadmap. The multi-seed exposure-matched equal-weight comparator remains INCONCLUSIVE where no result payload exists; the separate seed-42 single-seed matched comparison must not be generalized to all seeds. Draft PR #375 remains unmerged and is not confirmed evidence.
- **A05:** finding A05-F01 mitigated by PR #384, merge `db01b78cfcc485b5488f08d91b091bcc27b08ad8`; retest/CI reported green. Residual limitations remain.
- **A06:** PRs #386, #387, #388; merge baseline `82a24d4a9a56cb77f154674409ea975edbd9e91e`; closed with residual risks per Roadmap. No live broker fault-injection claimed.

## Consolidated open evidence gaps / residuals

1. **A01:** original report and primary finding evidence remain missing. Finding descriptions/IDs were recovered from prior audit context. Related PRs #179, #228, #297, #356 and #362 are verified historical artifacts, but no finding-specific remediation/retest mapping is proven.
2. **A02:** original report and exact PyTorch advisory identifiers/rationale remain missing. `A02-DEP-01` is still open; PR #354 is a compatibility mitigation, not closure. Scheduled Gitleaks run failure is verified at job level, but detailed log output remains unavailable.
3. **A07:** later 48-hour run package is referenced by the supplied Roadmap, but run artifacts were not independently fetched from repository storage in this pass. Historical report remains preserved and explicitly superseded for disposition by later evidence.
4. **A04:** exposure-matched equal-weight multi-seed comparator remains inconclusive; draft PR #375 is not merged and is not confirmed evidence.
5. **A03:** holdout remains PRE-FREEZE / NOT ACTIVE / COLLECTION NOT AUTHORIZED; this is a deliberate authorization gate, not a missing audit-evidence gap.
6. **A06:** residual risks remain as listed in its final report/handoff; no live broker fault injection or local macOS 12.7.6 retest claimed.

## Overall A08 status

**IN PROGRESS — initial register merged through PR #398; Roadmap/README alignment merged through PR #399; A03 evidence consolidation merged through PR #400.** Current `main` baseline checked for this revision is `0b9aba255e5b40265bab0f10dc0df9782e4c8741`. A01/A02 artifact recovery has improved the traceability register but has not recovered either original report or proven finding-specific remediation/retest chains. Publication does not close A08. This document is not a certification, compliance attestation, release authorization or live-trading readiness statement. No audit was rerun and no source, model, configuration, `.env`, runtime, holdout or broker state was changed.

## Next step

Review this A08 evidence update and its PR checks. Do not infer direct A01 remediation links from related historical PRs. Keep A02-DEP-01 open until primary advisory evidence and a separately authorized remediation/retest exist. Continue looking only for primary A01/A02 audit records or finding-level artifacts; if none are recoverable, preserve the gaps explicitly rather than reconstructing them by assumption. A08 remains IN PROGRESS.
