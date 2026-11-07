# Terminal Dashboard — Phase 2 Local Data Specification

**Status:** Proposed implementation contract  
**Baseline:** `main` at `0d0ea8e184d97f99e83da45be6a658a9da009bb8` (PR #307)  
**Scope:** Read existing local STMAA persistence for dashboard display. No broker connectivity.

## 1. Objective

Replace the static placeholder content in the Textual dashboard with verified information already persisted locally by STMAA. The dashboard is an observer: it must not initiate trading work, reconcile broker state, alter persisted trading state, or represent a local record as current broker truth.

This phase is local-only. It does not add or enable Alpaca connectivity.

## 2. Verified baseline and persistence sources

The current repository has these relevant components:

- `hub.py` is the Textual entry point. Its current `load_state()` reads `logs/hub_state.json` through `HUB_STATE_PATH`.
- `src/utils/monitoring_state.py` defines `HUB_STATE_PATH = <repository>/logs/hub_state.json`, plus atomic snapshot writing and equity-curve loading helpers.
- `src/utils/sqlite_logger.py` defines `DB_PATH = <repository>/logs/stmaa.db` and the SQLite tables `runs`, `events`, and `snapshots`. Snapshot rows include timestamp, run ID, source, portfolio value, cash, equity, drawdown, exposure, regime, positions JSON and payload.
- `src/dashboard/app.py` currently contains static disconnected-state copy and has no data provider.

### 2.1 Allowed sources in the first local-data implementation

1. **Monitoring JSON snapshot** — `logs/hub_state.json`. Read only. Accept only a JSON object and only explicitly documented fields. Missing, malformed, non-object or invalid values must remain unavailable/invalid; do not coerce strings, booleans, NaN or infinity into financial numbers.
2. **SQLite monitoring history** — `logs/stmaa.db`, limited to `runs`, `events`, and `snapshots`. Read only. Use bounded, parameterized SELECT queries and preserve source timestamps, run IDs and source labels.
3. **No additional local source is implicitly trusted.** A path existing on disk does not establish that its contents are valid or current.

### 2.2 Explicitly excluded from this phase

- `logs/execution/broker_state.json`: do not parse or display its internal execution state in the first adapter. Its schema and semantics require a separate review; local persisted order intent/reconciliation metadata is not broker-confirmed order truth.
- Model binaries, PPO loading, training artifacts, environment variables, `.env`, API credentials and authentication material.
- Any live or paper broker endpoint, market-data endpoint, external network, or subprocess that can start trading work.

The first implementation may display SQLite snapshots and events only where their stored source and payload support a precise label. It must not invent missing fields or infer a broker account state.

## 3. Architecture

Keep three layers separate:

1. **Local providers** — small read-only adapters for the monitoring JSON snapshot and SQLite history.
2. **Normalized view models** — typed records carrying value, source, observed timestamp, status and safe diagnostic detail.
3. **Textual presentation** — renders already-loaded view models. Rendering, navigation and resize handlers must not perform file or database I/O.

Recommended status vocabulary:

- `OK`: valid record with a usable source timestamp.
- `STALE`: valid record older than the configured threshold for that data class.
- `MISSING`: expected source or field is absent.
- `INVALID`: source exists but cannot be parsed or validated.
- `UNAVAILABLE`: source cannot currently be read.
- `EMPTY`: valid collection with zero records; never use this for a missing collection.

A value without a trustworthy timestamp must not be labelled current. Preserve timestamps as stored; do not manufacture an observation time from dashboard startup.

## 4. Read-only guarantees

### 4.1 File and database access

- JSON provider opens the existing snapshot for reading only. It must never call `write_hub_state()`.
- SQLite provider must open the existing database in SQLite URI `mode=ro`, set `PRAGMA query_only = ON`, and use SELECT statements only.
- Do not call `init_db()`, `db_conn()`, `start_run()`, `log_event()`, `write_snapshot()`, `end_run()` or any helper that can create directories, migrate schema, recover runs or write rows from a data-provider or refresh path.
- If the database does not exist, report `UNAVAILABLE`; do not create an empty database.
- Use query timeouts, bounded row limits and indexed timestamp/run filters. Do not scan unbounded event history.
- No schema migrations, cleanup, orphan recovery, repair, reconciliation or file locking that competes with trading processes.

### 4.2 Existing dashboard lifecycle writes

At this baseline, `hub.py` calls `start_run()`, `log_event()` and `end_run()` for its own dashboard session. These are local audit writes, not broker operations, but they mean the process is not literally zero-write.

For the first data-provider PR, do not expand these writes or use them to update trading snapshots. Preserve this existing lifecycle behavior only if tests and the implementation can keep it isolated from the read-only providers. The implementation PR must explicitly document this narrow dashboard-session audit exception. Any proposal to remove it or make the entire process zero-write must be a separate, reviewed decision because it changes the existing hub lifecycle contract.

### 4.3 Forbidden dependencies and actions

The dashboard and its providers must not import or instantiate:

- `alpaca` clients, `TradingClient`, `StockHistoricalDataClient`, `AlpacaFeed`;
- `BrokerExecutor`, modules under `src.execution`, broker reconciliation or execution-lock helpers;
- `trade.py`, strategy execution loops, PPO agents or model loaders.

No HTTP clients, broker SDK calls, order submission/cancel/replace/close/liquidation methods, trading subprocesses, execution locks, or broker state mutations. Refresh is limited to re-reading the approved local sources.

## 5. View contracts

### Overview

Display the latest valid local monitoring snapshot, with its source and timestamp. Financial fields (equity, cash, portfolio value, drawdown, exposure and regime) are shown only when present and validated. Do not substitute zero or the configured initial capital. Show source age and status. If the JSON snapshot and SQLite latest snapshot disagree, show the two sources distinctly or report a conflict; do not silently select whichever value looks newer without a documented rule.

### Portfolio

Display positions only when a valid persisted positions collection exists in an approved local snapshot. A valid empty list may be shown as “no positions in this local snapshot”; missing or malformed positions must be shown as unknown. Include source and timestamp. Do not claim these are current broker positions.

### Orders & Executions

This local-only phase may display explicitly labelled **local execution records / submission metadata** only when such records are available in the approved SQLite event/snapshot payloads and their meaning is established by tests or schema documentation. Do not label them “broker open orders”, “confirmed fills” or “executions” unless the source explicitly proves that classification. Keep local intent, broker observation and execution evidence as separate categories. If no approved local records exist, display “not available in local history”, not an empty broker order list.

### System

Display SQLite availability, latest local run metadata, bounded recent events, and monitoring snapshot availability/freshness. Preserve run status as stored. Do not infer that a process is currently alive from a historical `running` row alone. Do not report market feed or broker connectivity in this local-only phase.

## 6. Refresh and failure behavior

- Refresh re-reads providers once, then updates the view model; it does not perform work inside render methods.
- Provider failures are isolated. A database read failure must not hide a valid JSON snapshot, and vice versa.
- Retain no stale value as if it were current. If last-known-good caching is later added, retain its original timestamp and mark it stale.
- Errors shown to the user must be concise and sanitized; never display credentials, environment values, raw SQL parameters or sensitive payloads.
- Repeated refreshes must not create unbounded tasks, connections or event history.

## 7. Implementation PR sequence

Each implementation step is a separate PR and must pass CI, lint and secrets scan before merge.

### PR A — Local read-only provider contracts and tests

- Add typed status/envelope and local provider modules.
- Add isolated temporary-file and temporary-SQLite tests for missing, malformed, invalid, valid-empty, stale and valid data.
- Verify SQLite is opened read-only and that refresh/provider code cannot create or mutate the source database.
- Do not change dashboard presentation yet, except what is required to test provider contracts.

### PR B — Overview and System local data integration

- Wire the approved local JSON and SQLite sources into Overview and System.
- Add source, timestamp, freshness and failure labels.
- Keep all unverified fields visibly unavailable.
- Keep existing hub lifecycle audit writes isolated and documented as in §4.2.

### PR C — Portfolio local snapshot integration

- Display validated positions and portfolio fields from approved local snapshots only.
- Test malformed, partial, stale and valid-empty position data.
- Never imply broker freshness or current holdings.

### PR D — Orders & Executions local-history integration (conditional)

- Proceed only after the actual event/payload contracts are mapped from code and representative persisted records.
- Keep local intents and broker-confirmed records separate.
- If the repository does not persist sufficient evidence, leave the relevant view unavailable rather than inventing an adapter.

### PR E — Visual QA, README and screenshot

- Validate on macOS 12.7 at 80+ columns and below 80 columns.
- Verify persistent READ-ONLY label, unknown/not-connected states, all four navigation keys, refresh, help and quit.
- Update README only with behaviors demonstrated by tests and local acceptance.
- Capture a screenshot only from a truthful state; label fixture-based screenshots as illustrative.

## 8. Required tests and acceptance criteria

- Unit tests cover every status and malformed-data path.
- SQLite provider uses read-only URI mode and query-only behavior; missing DB is not created.
- Tests prove provider refresh leaves source file bytes and SQLite table contents unchanged.
- Tests prove dashboard/provider imports do not load broker, execution, trading or PPO modules.
- Tests prove no broker/network clients are instantiated and no broker mutation method is reachable.
- UI tests verify each view renders the correct source/status and never substitutes missing values with zero or an empty list.
- Queries are parameterized and bounded.
- Existing trading tests remain green; CI and Secrets Scan are green on the exact PR head before merge.
- Manual macOS acceptance remains required for terminal resize/layout and visual legibility.

## 9. Non-goals

This phase does not add Alpaca Paper or live connectivity, broker account/position/order requests, market quotes, real-time polling, trading controls, PPO inference, strategy execution, risk recalculation, broker reconciliation, model loading, database schema changes, or claims of production/live-trading readiness.
