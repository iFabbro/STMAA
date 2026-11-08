# Dashboard Orders & Executions — Read-Only Contract

## Purpose

Define the Orders & Executions view as a local, read-only presentation of persisted execution evidence. The dashboard must not create the appearance of a live broker terminal.

The view may display only information already persisted by the trading process in the configured broker execution state file. It must not instantiate an Alpaca client, import the broker executor, query broker endpoints, or infer broker state from local intent.

## Approved source and boundary

- Source: the existing JSON broker execution state file configured by `STMAA_BROKER_STATE_FILE`, or the repository default `logs/execution/broker_state.json`.
- Access: ordinary file read only. No lock-file creation, lock acquisition, recovery, migration, normalization write-back, or mutation.
- The dashboard must not import `src.execution.broker_executor` or any broker SDK module.
- Missing file, unreadable file, invalid JSON, unsupported schema, malformed collections, and account provenance mismatch must remain explicit states. None may be converted to an empty order list.
- The dashboard-session audit lifecycle in `hub.py` remains unchanged and is not part of this provider.

## Data classification

The persisted state contains different kinds of evidence. They must not be merged into one undifferentiated "orders" list.

1. **Unresolved submission intents** — records under `order_submission_unresolved`. These represent a submission whose broker outcome is not yet conclusively known. Display as unresolved intent, never as a confirmed broker order.
2. **Confirmed submissions** — records under `order_submission_confirmed`. Display the persisted broker order identifier and last persisted status only when the record validates. These are historical observations from the last successful reconciliation, not proof of current broker status.
3. **OPG batches** — records under `opg_batches`. Display batch lifecycle and its contained order evidence in a separate group. A batch status must not be substituted for an individual order status.
4. **Emergency liquidation state** — `emergency_liquidation_pending` and `emergency_liquidation_unresolved`. If shown, keep it in a separate safety/reconciliation section; do not mix it with strategy submissions.

The view must not claim to show all current open orders or all fills. Broker-confirmed execution evidence is shown only if the relevant persisted record actually contains it. Missing fill details remain "not persisted".

## Required provenance and status semantics

- Show source path, persisted account mode, state schema version, state revision, and available observation/update timestamps.
- If account identity is present, display it in a safely truncated form; do not expose API credentials or secrets.
- Label the content "Persisted execution state — last recorded observation". Never label it "live", "current broker state", or "real-time".
- Distinguish at minimum: `OK`, `STALE`, `MISSING`, `INVALID`, and `UNAVAILABLE`.
- Freshness must be based on validated timestamps in the persisted records/state. Do not treat file modification time as proof of broker observation freshness.
- Unknown status values, duplicate identifiers, invalid quantities/prices, malformed timestamps, and contradictory fill/status combinations must be visible as invalid/blocked evidence, not silently omitted or coerced.
- A valid empty collection is distinct from a missing collection. An absent key is not an empty list.

## Rendering requirements

- Keep unresolved intents, confirmed submissions, OPG batches, and emergency liquidation/reconciliation evidence in distinct sections.
- For each record, render only validated fields actually present: symbol, side, requested quantity/notional, order type, time-in-force, broker order ID, client order ID, persisted status, filled quantity, average fill price, and timestamps.
- Do not calculate or display realized P/L, remaining quantity, current exposure, or current open-order status unless the persisted source provides sufficient validated evidence and the label clearly describes the calculation/provenance.
- Bound the number of rendered records and text lengths. Preserve stable ordering by validated persisted timestamps; if ordering cannot be established, say so.
- Empty, missing, stale, invalid, and unavailable states must have distinct messages.
- Refresh re-reads the local JSON source only. Navigation and rendering perform no I/O.

## Validation and tests

Add provider tests for:
- missing, unreadable, malformed JSON, unsupported schema, and valid schema;
- absent versus empty collections;
- valid and invalid unresolved/confirmed records;
- duplicate IDs, invalid numeric values, non-finite values, malformed timestamps, unknown statuses, and contradictory fill data;
- account mode/identity provenance and stale observations;
- OPG batch separation and malformed batch records;
- emergency liquidation records remaining separate;
- bounded record counts and safe text rendering;
- read-only behavior and source non-mutation.

Add Textual tests for:
- each section and each source status;
- unresolved intent never rendered as a confirmed broker order;
- confirmed historical status never labelled as live/current;
- no fabricated order or fill rows;
- local refresh re-reads the provider;
- UI import/runtime dependency graph does not include Alpaca or execution modules.

## Acceptance boundary

The Orders & Executions view is complete only when its source contract, provider validation, UI rendering, failure semantics, and no-broker/no-write tests all pass. It remains a local historical/evidence view. A separate, explicitly authorized Paper operational test is required to verify broker behavior; the dashboard itself never performs that test.
