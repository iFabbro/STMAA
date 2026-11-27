# STMAA A08 — Audit Readiness & Evidence Dossier

**Version:** Working register 0.3 (A03 evidence consolidated; dossier still in progress)
**Date:** 27 November 2026
**Repository:** `iFabbro/STMAA`
**Repository reference checked:** register baseline prior to A03 consolidation; update will be recorded through dedicated PR
**Purpose:** evidence traceability for A01–A07; not a new audit, certification, or readiness claim.

## Evidence conventions

- **Verified linkage:** PR/commit and report or source evidence directly inspected in GitHub or the supplied Roadmap file.
- **Reported:** information present in supplied project handoff/Roadmap, not independently re-run here.
- **Gap:** original report or exact retest evidence not recovered in this pass.
- A merged PR is not, by itself, proof that an audit finding is closed.
- This dossier preserves the audit conclusions and limits; it does not reopen or repeat concluded audits.

## A01 — Software Engineering & Architecture

**Disposition reported:** PASS WITH RESIDUAL RISKS; closed per 24 Nov 2026 audit context.
**Audit baseline reported:** `001a66cddf134ea7c5344ff292a1c08d0458c198`; local checkout `main`, clean, macOS 12.7.6 Intel, Python 3.12.13.
**CI evidence reported:** CI run `45048291683` (Ubuntu, macOS Intel, macOS ARM, Ruff) success; Secrets scan `45048291733`, Static Analysis `45048291750`, Security Baseline `45048291800` success. Branch protection was not verified.

| Traceability element | Evidence / status |
|---|---|
| Original report | **Gap:** exact A01 report path/content not recovered in GitHub tree or search during this pass. Roadmap states report dated 24 Nov and audit closed. |
| Findings | **Gap:** exact finding IDs, severity, individual control evidence and report linkage not recovered. Static observations in the roadmap are explicitly preliminary and are not promoted to findings here. |
| Remediation PR/commit | **Gap:** no finding-specific remediation chain established. |
| Retest | **Reported:** baseline CI and security workflows listed above successful. No tests were re-run. |
| Final status | Closed per project Roadmap, but individual finding traceability is incomplete. |
| Residual evidence gap | Original report, finding register and finding-to-PR/retest mapping. |

## A02 — Security & Supply Chain

**Disposition reported:** CLOSED — PARTIAL, score 6.5/10.
**Baseline reported:** `001a66cddf134ea7c5344ff292a1c08d0458c198`; macOS Intel, Python 3.12.13.
**Open residual:** `A02-DEP-01` — PyTorch 2.2.2 on Intel macOS; not remediated.

| Traceability element | Evidence / status |
|---|---|
| Original report | **Gap:** exact A02 report path/content not recovered from GitHub tree/search in this pass. Roadmap records the outcome and residual. |
| Finding | `A02-DEP-01` — Torch 2.2.2 on macOS Intel. **OPEN / not remediated** as reported. |
| Remediation-related PR | [#354](https://github.com/iFabbro/STMAA/pull/354), merged `d9d7872ff05bcdb19fc10fb34379a55d0353459e`. It retains torch 2.2.2 only on Intel macOS and uses torch 2.13.0 elsewhere; explicitly documents the Intel residual exposure. This is mitigation/platform split, not closure of the advisory exposure. |
| Retest evidence | PR #354 reports on Intel macOS: `uv lock --check`, locked sync and `uv pip check` passed; `pytest src/tests tests -q`: 1,487 passed. Reported only; not rerun here. |
| Other security context | Roadmap mentions a Gitleaks failure in a historical commit `7f52b0da517f68afd468ca2ed5ed8454f5b2ea21` under `src/execution/broker_executor.py`; it is not misattributed to A01's green CI. |
| Final status | PARTIAL / finding remains open. PR #354 does not remediate PyTorch 2.2.2 on Intel. |
| Residual evidence gap | Original A02 report, exact advisory/finding rationale and evidence details beyond the Roadmap/PR #354. |

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

1. A01 original report and finding-level PR/commit/retest map not recovered.
2. A02 original report not recovered; `A02-DEP-01` PyTorch 2.2.2 on Intel macOS remains open.
3. A07 later run package referenced by supplied Roadmap, but run artifacts were not independently fetched from repository storage in this pass. Historical report remains preserved and explicitly superseded for disposition by later evidence.
4. A04 exposure-matched equal-weight comparator remains inconclusive; draft PR #375 not merged.
5. A03 holdout remains PRE-FREEZE / NOT ACTIVE / COLLECTION NOT AUTHORIZED; this is a deliberate authorization gate, not a missing audit-evidence gap.
6. A06 residual risks remain as listed in its final report/handoff; no live broker fault injection or Mac 12.7.6 retest claimed.

## Overall A08 status

**IN PROGRESS — register published and A03 traceability consolidated.** The initial A08 register was merged through PR #398; A03 consolidation is proposed in this follow-up PR. Publication does not close A08: evidence gaps and residual risks below remain open for traceability. This document is not a certification, compliance attestation, release authorization or live-trading readiness statement. No audit was rerun and no source, model, configuration, `.env`, runtime, holdout or broker state was changed.

## Next step

A03 report and traceability are consolidated in this revision. Next, recover A01/A02 reports and the final A07 run package from relevant repository artifacts or supplied primary records, without searching unrelated systems, inferring finding chains, or overwriting the historical A07 report. Keep A04's multi-seed comparator INCONCLUSIVE unless the original result payload is recovered. These gaps are not being left open by choice: they remain open only until primary evidence is located or the limitation is explicitly accepted; A08 remains IN PROGRESS pending review and approved deliverables.
