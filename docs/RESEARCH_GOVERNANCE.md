# Research Governance Specification

**Status:** design specification; not implemented  
**Scope:** research experiments, benchmark runs, walk-forward evaluations, robustness studies, and their evidence/decisions.  
**Non-goal:** this document does not authorize broker activity, live trading, PPO retraining, benchmark regeneration, or changes to existing research results.

## 1. Purpose and governing principles

The Research Governance Registry (RGR) will provide a verifiable chain from a declared research protocol to each execution, its evidence, independent audit results, and any human decision made from that evidence.

The system MUST follow these principles:

1. **Reproducibility:** identify the protocol, source revision, data, configuration, parameters, random seeds, and execution environment.
2. **Immutability:** never rewrite a completed run, evidence record, or decision. Corrections are new records linked to the originals.
3. **Evidence-first:** record references and cryptographic digests for evidence; do not silently regenerate or normalize source reports.
4. **Fail-closed:** missing, malformed, inconsistent, or unverifiable required information MUST block a positive governance result.
5. **Separation of concerns:** experiment execution, evidence collection, audit, and human decision are distinct stages and records.
6. **No implied approval:** passing tests or auditors does not automatically accept a strategy, establish profitability, or authorize trading.
7. **Human accountability:** only an explicit, attributable decision may accept or reject research evidence.
8. **Read-only integration:** existing reports and artifacts are inputs. The registry MUST NOT mutate them.

## 2. Scope and non-goals

The first implementation covers research provenance and governance metadata for PPO benchmark and walk-forward work, then may be generalized to other research families.

It does not:
- train or optimize models;
- generate or regenerate benchmarks, historical data, or reports;
- submit, amend, cancel, or close broker orders;
- connect to Alpaca or any broker;
- change execution safeguards or trading configuration;
- claim statistical validity merely because a record is complete;
- retroactively assert that legacy runs followed a protocol that did not exist when they ran.

STMAA remains experimental/alpha and Paper-only under its existing operational constraints. RGR is a research-governance component, not a production-readiness or live-trading control.

## 3. Identity model

The implementation MUST distinguish these identifiers:

- **experiment_id:** stable identity of a research question/experiment family and its declared protocol.
- **run_id:** identity of one logical run under an experiment, including a declared seed/fold or run configuration.
- **execution_id:** globally unique identity of one actual process execution/attempt. Retries receive a new execution_id, never reuse the previous one.

Identifiers MUST be generated before execution where possible, validated for uniqueness, and never inferred from timestamps alone. A rerun is a new execution, even when all inputs are identical.

## 4. Research protocol

A protocol is a versioned, machine-readable declaration of the research method. It MUST include:

- protocol_id, semantic version, status, and description;
- research objective and hypothesis;
- instrument universe and selection rule, including point-in-time limitations;
- data provider, feed, timeframe, adjustment policy, timezone/session rules, date range, and missing-data policy;
- feature/observation contract and model/strategy contract;
- train/validation/test or walk-forward split rules, fold boundaries, embargo/purge rules where applicable;
- execution timing and fill assumptions;
- fees, spread, slippage, market-impact assumptions, and other cost parameters;
- position, gross exposure, leverage, and risk constraints;
- benchmark definitions and exposure-matching methodology;
- seeds and multi-seed aggregation rules;
- metrics, statistical tests, acceptance thresholds, and multiple-testing considerations;
- required auditors and required evidence;
- allowed deviations and their approval process.

Protocol content MUST be canonicalized deterministically (UTF-8 JSON, sorted keys, stable separators, no NaN/Infinity) before SHA-256 fingerprinting. The fingerprint identifies the exact protocol content, not merely its version label. A changed protocol requires a new version and fingerprint; released versions are immutable.

A protocol marked draft MUST NOT be used to produce an ACCEPTED decision. The initial PPO benchmark protocol is to be authored and reviewed separately; this specification does not silently promote current benchmark parameters into an approved protocol.

## 5. Run record and provenance

Every governed run MUST record, at minimum:

- experiment_id, run_id, execution_id;
- protocol_id, protocol_version, protocol_fingerprint;
- repository, Git commit SHA, branch/ref, and worktree cleanliness;
- start/end timestamps in UTC and execution outcome;
- dataset identifiers, date coverage, source/feed, row/session counts where available, and SHA-256 fingerprints of immutable input snapshots;
- configuration and parameter snapshot digests;
- seed, fold identity, and parent/related run references;
- Python/runtime and dependency-lock identity;
- output artifact references and SHA-256 digests;
- auditor names/versions, execution status, and report references when available.

A dirty worktree MUST be recorded explicitly. A governed run MUST NOT be considered reproducible/eligible for acceptance when source provenance is missing or ambiguous. Timestamps are descriptive metadata, not unique identity.

## 6. Evidence references

Evidence records reference existing files/artifacts; they do not copy or rewrite report contents. Each reference MUST identify:

- evidence type and stable logical name;
- repository-relative path or other approved immutable locator;
- SHA-256 digest of the exact bytes;
- byte size and media/format where known;
- producer run/execution identity, if known;
- creation timestamp and provenance status.

Evidence classes include protocol, source revision, dataset snapshot, model artifact and manifest, benchmark report, walk-forward report, audit report, environment/lock manifest, and decision attachment.

Verification MUST recompute the digest from the referenced bytes. Missing files, path traversal, symlinks where disallowed, non-regular files, digest mismatch, or unsupported locators MUST produce an explicit failure/unknown state and MUST NOT be treated as valid evidence. The registry MUST preserve the distinction between “not supplied”, “not verifiable”, and “verified”.

## 7. Audit result semantics

Auditors remain independent components. RGR records their identity, version, inputs, output reference/digest, timestamp, and status; it does not replace their domain-specific logic.

The normalized statuses are:

- **PASS:** the required check completed and passed.
- **FAIL:** the check completed and found a violation.
- **INCONCLUSIVE:** evidence or coverage was insufficient to decide.
- **NOT_RUN:** the required check was not executed.
- **ERROR:** the auditor could not complete reliably.

These states MUST NOT be collapsed. In particular, INCONCLUSIVE, NOT_RUN, and ERROR are not PASS.

A required FAIL, INCONCLUSIVE, NOT_RUN, ERROR, missing audit, or audit-evidence digest mismatch blocks acceptance. Optional checks may be recorded as optional but MUST NOT be represented as passed when absent. The governance layer MUST preserve the detailed auditor report as evidence.

## 8. Decision journal

Run records and decisions MUST be separate. A run describes what was executed and what evidence exists; a decision describes a human judgment over a specific, immutable set of evidence.

Supported decision states:

- **PENDING**
- **ACCEPTED**
- **REJECTED**
- **INCONCLUSIVE**
- **WITHDRAWN**

A decision MUST reference the exact run/execution, protocol fingerprint, evidence digests, and auditor outcomes considered. It MUST include decision author/actor, timestamp, rationale, and optional limitations/conditions. ACCEPTED requires all mandatory evidence verified, all mandatory auditors PASS, a released protocol, and an explicit human action. The system MUST reject attempts to accept when any required condition is unmet.

Decisions are append-only. A later reassessment creates a new decision linked to the prior decision; it never edits or deletes history. WITHDRAWN records withdrawal of a prior decision and does not erase it. No automatic decision based solely on a metric threshold is permitted.

## 9. Persistence and integrity

The first implementation will use append-only JSONL journals; SQLite is out of scope for the initial release.

Journal requirements:

- canonical JSON serialization and SHA-256 hash chaining;
- monotonically increasing sequence numbers and explicit previous-hash linkage;
- inter-process locking for readers/writers as required by the access pattern;
- complete-chain verification before append;
- durable writes using fsync, including directory durability when a new journal is created;
- reject symlinks and non-regular journal/lock files;
- reject malformed records, sequence gaps, hash mismatches, and incomplete trailing records;
- no automatic repair, truncation, reordering, or silent recovery;
- preserve original files on integrity failure and provide a clear fail-closed error.

The existing PPO shadow ledger is specialized to shadow-agent events. Its implementation and file format MUST NOT be reused as the general research registry merely because it already has hash chaining. RGR may adopt equivalent proven integrity techniques while maintaining separate schemas, semantics, and tests.

## 10. Legacy evidence and migration

Legacy reports may be registered only through an explicit import operation. The importer MUST:

- leave original report and model files byte-for-byte unchanged;
- compute and record their current digests;
- mark unavailable historical metadata as UNKNOWN or NOT_PROVIDED, never invent it;
- identify the import as LEGACY_IMPORT and record importer/version/time;
- avoid assigning a new protocol fingerprint as though the historical run had used it;
- prevent legacy evidence with missing provenance from satisfying requirements that demand contemporaneous provenance.

Supported operating modes are:

- **NEW_RUN:** create governance records for a newly executed run.
- **VERIFY_ONLY:** verify existing records/evidence without mutation.
- **LEGACY_IMPORT:** explicitly register historical artifacts with limitations.

No bulk historical migration is implied by this specification.

## 11. CLI contract (proposed)

The future CLI should expose narrowly scoped operations:

- `verify`: verify journal integrity and record schemas;
- `list` / `show`: inspect experiments, runs, evidence, audits, and decisions;
- `verify-evidence`: recompute evidence digests and report missing/mismatched inputs;
- `import-legacy`: explicit, read-only import of existing artifacts;
- `decide`: append a human decision after validating all mandatory gates.

CLI commands MUST default to read-only behavior. Mutating commands must be explicit, scoped, and auditable. No command may call a broker or alter trading state.

## 12. Integration boundaries

RGR MUST integrate with existing benchmark, walk-forward, PPO provenance, and shadow-audit components by recording references and results, not by duplicating their calculations or rewriting their outputs.

Existing auditors remain independently runnable. RGR may orchestrate them in a later phase, but each audit invocation must be identifiable and its exact inputs/outputs hashed.

The dashboard is not an initial integration target. If later integrated, it must remain read-only with respect to broker state and research evidence; any audit writes must occur through an explicitly invoked governance operation, not rendering/refresh.

## 13. Security and operational constraints

- No live broker credentials or broker API calls are required or permitted by RGR.
- No order placement, cancellation, replacement, or liquidation functionality belongs in this subsystem.
- Paths and serialized input must be validated; no arbitrary code execution or dynamic imports from evidence.
- Secrets MUST NOT be copied into run records, logs, protocol files, or evidence metadata.
- Integrity, provenance, and schema errors fail closed.
- RGR must not weaken existing Paper-only configuration, exposure caps, execution guards, or dashboard boundaries.

## 14. Implementation sequence

Implementation must be split into dedicated pull requests, each with tests and review:

1. **Specification (this PR):** settle terminology, invariants, states, and boundaries.
2. **Schemas and protocol catalog:** JSON schemas, canonicalization, fingerprinting, and first reviewed protocol version.
3. **Registry foundation:** identifiers, run records, provenance capture, and read-only inspection.
4. **Append-only journal:** locking, hash chain, fsync, verification, and corruption handling.
5. **Evidence verification:** immutable references, digest verification, and fail-closed path handling.
6. **Audit integration:** record existing auditor invocations/results without duplicating audit logic.
7. **Decision journal and CLI:** human decisions, immutable history, and explicit command semantics.
8. **Optional historical import:** only after the new-run path is stable; no report rewrites.
9. **Optional CI integration:** verify governed artifacts in CI without auto-committing to main.

Each implementation PR must be independently reviewable, target a dedicated branch, pass required CI before merge, and avoid direct commits to main. No phase is implicitly authorized by merging this specification.

## 15. Acceptance tests required before implementation is considered complete

The implementation must test at least:

- deterministic protocol canonicalization and fingerprinting;
- rejection of protocol mutation under an existing released version;
- uniqueness and separation of experiment_id, run_id, and execution_id;
- recording of dirty/unknown Git provenance without falsely marking it clean;
- dataset/config/artifact digest verification and mismatch detection;
- missing, symlink, path traversal, non-regular, and unreadable evidence handling;
- concurrent append/read behavior and journal sequence integrity;
- tampered records, reordered records, sequence gaps, hash mismatch, and incomplete trailing line;
- full-chain verification before append and no automatic repair;
- distinction among PASS, FAIL, INCONCLUSIVE, NOT_RUN, and ERROR;
- missing mandatory auditor and failed/inconclusive auditor blocking acceptance;
- immutable decisions and append-only reassessment/withdrawal;
- legacy import preserving source bytes and marking missing provenance;
- no broker imports/calls, no trading operations, and no mutation of source research reports.

## 16. Governance conclusion

This specification establishes the rules that implementation must satisfy. It does not establish that any current PPO model, benchmark, or strategy is statistically robust or suitable for deployment. Research acceptance is an evidence-based governance decision, not a performance guarantee.
