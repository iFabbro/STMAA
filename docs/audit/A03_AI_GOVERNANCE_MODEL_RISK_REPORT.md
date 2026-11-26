# STMAA — A03 AI Governance & Model Risk

**Final audit report — 26 November 2026**  
**Repository:** `iFabbro/STMAA`  
**Audited baseline:** `main` = `09b5eb55985b93c1c8b3a2ac1fac5ed202a16177`  
**Disposition:** `PASS WITH RESIDUAL RISKS`  
**Score:** **8/10**

## 1. Executive conclusion

The approved A03 remediation scope has been implemented and the integrated post-merge CI/security matrix passed on the exact audited `main` commit. The semantic replay, activation attribution, and strict authorization-type findings have passed their required automated retests. The holdout documentation is reconciled in this change.

A03 is closed for the source-review, governance-control, and offline/mock regression scope. This is an internal project audit, not an independent certification, a performance validation, or evidence that STMAA is production-ready, profitable, or suitable for live trading.

The prospective PPO holdout remains **PRE-FREEZE — NOT ACTIVE — COLLECTION NOT AUTHORIZED**. No holdout collection, model training/inference, benchmark execution, or broker operation was performed as part of this audit.

## 2. Scope and exclusions

Included:
- PPO shadow collector activation and provenance controls.
- Operator authorization attribution and binding to reviewed GitHub evidence.
- Strict type validation of collection authorization.
- Hash-chain and semantic replay integrity for portfolio and SPY benchmark accounting.
- Relevant documentation and regression coverage.

Excluded:
- Holdout freeze approval, activation, or collection.
- PPO training, inference, tuning, optimization, or retraining.
- A04 comparative benchmark execution.
- Paper broker orders or any broker mutation; all live trading.
- Revalidation of the local iMac runtime or the freeze-host model/configuration inventory.

## 3. Baseline and remediation PRs

| PR | Finding / purpose | Merge commit | Disposition |
|---|---|---|---|
| [#390](https://github.com/iFabbro/STMAA/pull/390) | Bind activation to exact clean Git source and committed freeze-register evidence | `2169c6001d10702a26a07c1496e1a46883f930b0` | Merged |
| [#391](https://github.com/iFabbro/STMAA/pull/391) | Reject non-boolean collection authorization | `0a6440746082613da6181792499b31c2ea5cf940` | Merged |
| [#394](https://github.com/iFabbro/STMAA/pull/394) | Require attributable operator identity, scope, and GitHub approval evidence | `c0cae0514f5ac19edc56804fd6eddab3d8737673` | Merged |
| [#395](https://github.com/iFabbro/STMAA/pull/395) | Independently recompute semantic portfolio and benchmark replay | `09b5eb55985b93c1c8b3a2ac1fac5ed202a16177` | Merged |

The current `main` branch was independently checked and points to the audited baseline above. PR #395 is closed and merged.

## 4. Findings and retest

### A03-F01 — Medium — semantic replay trusted persisted accounting values

**Disposition: REMEDIATED — RETEST PASS (PR #395).**

Replay now reconstructs each portfolio transition from the prior replayed state, pending target weights, and immutable model-input snapshots. It independently recomputes SPY benchmark shares, equity, and returns from benchmark snapshots. Persisted state and metrics are compared against the recomputation, including open/close equity, step return, turnover, traded notional, transaction costs, cash, shares, close weights, and benchmark state/metrics.

Regression tests alter portfolio return, cash, shares, transaction costs, turnover, persisted portfolio shares, and benchmark return, then rebuild the ledger hash chain. Replay rejects the semantically inconsistent records. The final evaluator uses read-only snapshot verification and performs semantic replay before deriving metrics.

### A03-F02 — Medium — activation approval attribution insufficient

**Disposition: REMEDIATED — RETEST PASS (PRs #390 and #394).**

Activation now requires structured GitHub operator identity (login and numeric account ID), exact scope `prospective_read_only_ppo_shadow_holdout_collection`, and traceable GitHub PR-review evidence (repository, PR, review ID, reviewer, APPROVED state, reviewed SHA, merge SHA, canonical review URL). The approval merge SHA must equal the freeze-register commit; the freeze commit must be an ancestor of the executing collector commit; the executing commit must exactly match HEAD on a clean worktree; and the committed freeze-register bytes must match the current register and approval markers.

Residual limitation: the operator must independently verify the GitHub review. The collector does not query GitHub online and these fields are not a cryptographic signature or cryptographic proof of the human operator's identity.

### A03-F03 — Low — truthy non-boolean authorization accepted

**Disposition: REMEDIATED — RETEST PASS (PR #391).**

The collector requires `type(collection_authorized) is bool`. Strings, numeric impostors, containers, `None`, and NumPy booleans are rejected. Negative regression cases are included.

### A03-F04 — Informational — holdout documentation stale after hardening PRs

**Disposition: REMEDIATED in this documentation PR.**

The holdout register's narrative, implementation status, checklist, and activation-record field are reconciled with PRs #390, #391, #394, and #395. The register explicitly preserves the state **PRE-FREEZE — NOT ACTIVE — COLLECTION NOT AUTHORIZED** and does not assign an activation commit or start date.

## 5. Post-merge CI and security evidence

All four workflows below ran on the exact merge commit `09b5eb55985b93c1c8b3a2ac1fac5ed202a16177` and completed successfully:

| Workflow | Run ID | Jobs | Result |
|---|---:|---|---|
| CI | `45815137366` | Ruff; Ubuntu full test suite; macOS 15 Intel full test suite; macOS 15 ARM full test suite | PASS |
| Security Baseline | `45815137309` | Gitleaks; Python dependency audit | PASS |
| Security Static Analysis | `45815137426` | Semgrep; Bandit | PASS |
| Secrets scan | `45815137455` | Gitleaks | PASS |

All listed jobs completed with conclusion `success`. These are post-merge runs on the actual `main` merge commit, not merely PR-head checks.

## 6. Residual risks and limitations

1. **Manual approval verification:** GitHub review evidence is structured and bound to source/register revisions, but the operator still performs external verification; there is no cryptographic signature or online GitHub validation in the collector.
2. **Holdout not activated:** freeze-host verification of the exact model artifact, manifest, effective configuration, source checkout, and activation record remains outstanding. This is intentionally not authorized by A03 closure.
3. **No prospective outcome evidence:** no 252-session holdout has run; this audit makes no strategy-performance or profitability claim.
4. **Bounded static isolation evidence:** AST tests reject known broker execution imports/calls, but cannot prove the absence of every conceivable indirect execution path.
5. **Upstream data truth and lineage:** snapshot hashes establish byte integrity, not truthfulness of provider data; historical raw-to-training lineage limitations recorded under A05 remain.

## 7. Audit decision and next gate

**Proposed A03 disposition: CLOSED — PASS WITH RESIDUAL RISKS — 8/10**, effective after this documentation closeout PR is merged, for the approved source-review, governance-control, documentation, and offline/mock retest scope.

After this documentation closeout PR is merged, the next audit in the approved sequence is **A04 — Quantitative & Statistical Validation**, beginning with the comparative benchmark. No PPO tuning, optimization, training, or retraining is permitted before the A04 benchmark and its gates are complete.

A03 closure does not approve or activate the prospective holdout, authorize Paper orders, authorize Live Trading, or change any operational configuration or risk cap.
