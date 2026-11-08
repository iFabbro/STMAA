# STMAA Residual Work Register

**Register snapshot:** 2026-11-08  
**Repository:** `iFabbro/STMAA`  
**Main commit at snapshot:** `1b7651eb5b1a7101a74ea138d21586355a4dd654`  
**Purpose:** track follow-up work beyond the bounded end-to-end code audit. This register does not reopen that audit or imply production/live-trading readiness.

## Status conventions

- **DONE:** implementation or verification is complete with the evidence stated.
- **PARTIAL:** implementation exists, but one or more acceptance conditions remain unverified.
- **OPEN:** work has not yet met its acceptance criteria.
- **DEFERRED:** intentionally outside the current single-host, Paper-only scope.

## Ordered residual work

| ID | Priority | Area | Status | Current evidence / remaining acceptance |
|---|---|---|---|---|
| RW-01 | High | One PPO decision per completed daily market bar | **DONE** | PR #269 (`dc8e8b583364a4ecd523cf5096367a7f81f81f9a`) adds a durable daily-decision guard, validates market timestamps, prevents duplicate decisions on the same bar, and reconciles interrupted decisions read-only after restart. |
| RW-02 | High — next | Continuous 48-hour Alpaca Paper shadow soak | **PARTIAL** | PRs #261–268 provide the fail-closed shadow runner, restart/orphan handling, acceptance report, production read-only PPO provider, exposure telemetry and explicit read-only Paper preflight. **No 48-hour run has been executed or accepted.** The operational run must be completed and reviewed before this item can be closed. |
| RW-03 | High | Reproducible dependencies and supported macOS architectures | **DONE** | PR #270 (`b064090c03bdfadf9d6769a2d5b10e227e669a1e`) adds declarative dependencies, `uv.lock`, locked CI installation, and Linux/macOS Intel/Apple Silicon checks. User-reported local verification on macOS 12.7: Python 3.12.13, 1,090 tests passed, Ruff passed. |
| RW-04 | Medium | Model validation and acceptance criteria | **OPEN** | Training records holdout metrics and metadata, but no agreed quantitative acceptance protocol has been established for out-of-sample performance, benchmark comparison, turnover/cost sensitivity, drawdown, stability across periods/seeds, or model promotion/rejection. Define this as a research-quality gate; do not imply profitability. |
| RW-05 | Medium | Clean-install and first-run workflow | **PARTIAL** | README and locked `uv sync` workflow are documented and the user has verified installation/tests on macOS 12.7. Still required: a reproducible clean-environment walkthrough from clone to read-only Paper preflight, with explicit expected outputs and failure handling for a new repository downloader. |
| RW-06 | Medium | Operational monitoring and incident response | **OPEN** | SQLite/TUI monitoring and soak stop evidence exist. External alert delivery, named operator ownership, escalation, credential rotation/least privilege, backup/restore, kill-switch procedure, and incident runbook still require operational definition and verification. |
| RW-07 | Low | Documentation/ROADMAP consistency | **PARTIAL** | `ROADMAP.md` is being replaced with a status-oriented summary aligned to the README and current repository. Close after the documentation PR merges and its links/status are rechecked. |
| RW-08 | Medium | Dashboard persisted-order semantic validation | **PARTIAL** | PR #315 adds validation for IDs, sides, statuses, numeric fields, fill consistency, timestamps, duplicate JSON keys, account identity and OPG batch structures. Close only after CI passes and the change is merged. |
| RW-09 | Medium | Manual Alpaca workflow dependency/runtime alignment | **PARTIAL** | PR #314 replaces Python 3.13 and the absent `requirements.txt` with Python 3.12 and locked `uv` installation. Close only after CI passes and the workflow definitions are rechecked. |

## Dashboard and workflow consolidation (2026-11-08)

- PR #314: align the four manual Alpaca workflows with the supported Python 3.12 runtime and locked `uv` dependency installation. The workflow triggers, concurrency groups, Paper credentials boundary and read-only versus mutation scripts are unchanged.
- PR #315: strengthen local dashboard validation for persisted execution records and future-dated snapshots. The dashboard remains a local evidence view and does not query Alpaca.
- PR #316: update this roadmap and register to remove obsolete June 2026 completion claims and make the current experimental status and residual gates explicit. These PRs remain pending until their CI checks pass and they are merged.

## Completed platform follow-up

PR #271 (`8a7ba4fc1586ad30a3c4460b2caa1b8ba4ae566d`) adds native macOS process identity and orphan-session recovery while retaining Linux `/proc` handling, PID-reuse protection, and fail-closed behavior when process identity cannot be verified. User-reported local verification on macOS 12.7 after pulling the merge: **1,090 passed**, Ruff clean. This resolves the macOS-specific test/recovery gap discovered during the dependency portability work.

## RW-02 — 48-hour Paper shadow soak: acceptance gate

The soak is an operational validation, not another code-audit percentage and not a live-trading authorization. It must use the production read-only provider and the exact source commit recorded by the run.

Required evidence:

1. Explicit startup preflight confirms Alpaca Paper endpoint, active/unblocked Paper account, and valid account identity.
2. A continuous 48-hour run completes without interruption. An interrupted, orphaned, stopped, or failed run is not resumable evidence; start a new run after investigation.
3. Heartbeats and production shadow observations meet the configured 60-second cadence and the report's minimum coverage requirement.
4. Required health signals remain complete and valid: broker snapshot/identity, corporate-action reconciliation, market data, VIX, persisted state, determinable aggregate and per-symbol exposure, and no unresolved execution state.
5. No stop condition, provider error, or broker mutation occurs. The provider is read-only; order submission remains disabled.
6. The report is generated from the persisted SQLite evidence and returns `PASS`.
7. The `CI` and `Secrets scan` workflows are verified green on GitHub for the exact same 40-character source commit SHA recorded by the run. The `CI` workflow must show all three platform test jobs and the Ruff job successful. The operator-supplied CI JSON is only a snapshot and must be checked against GitHub.
8. The operator reviews the run report and logs before closing this item.

Operational command and report procedure are maintained in [PAPER_SOAK_MONITOR.md](PAPER_SOAK_MONITOR.md). A short smoke test can validate wiring but cannot satisfy the 48-hour acceptance gate. Do not start the 48-hour run until the model artifact/metadata, local Paper configuration, account identity, available market-data access, and stop/monitoring arrangements have been checked.

## Scope boundaries and working rules

- STMAA remains experimental/alpha and Paper-oriented. Green tests, a successful soak, or this register do not certify live trading or strategy profitability.
- No live broker orders, cancellations, replacements, or liquidations are part of these work items. The soak provider must remain read-only.
- Multi-host/distributed execution remains deferred; the current lock is host-local. Use one active executor per Paper account.
- Each code or documentation change must be isolated in a dedicated branch and pull request. Do not commit directly to `main`; require the repository's CI, test, lint, and secrets checks to pass before merge.
- Close an item only when its stated evidence is available. Distinguish implementation complete from operational validation complete.
