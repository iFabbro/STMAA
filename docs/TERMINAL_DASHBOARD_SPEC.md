# STMAA Terminal Dashboard — Technical Specification

**Status:** Proposed architecture  
**Scope:** Terminal user interface and read-only observability  
**Target runtime:** Python 3.12, macOS 12.7+ (Intel and Apple Silicon), Linux CI  
**Existing entry point:** `uv run --locked python hub.py`

## 1. Purpose and boundaries

The terminal dashboard is the operator-facing monitoring surface for STMAA. It must make the state of the running system understandable without becoming part of the trading decision or execution path.

The dashboard is an observer, not an executor:
- It must never submit, replace, cancel, close, or liquidate broker orders or positions.
- It must not call mutating broker APIs.
- It must not start, stop, restart, or reconfigure the trading process.
- It must not alter PPO artifacts, risk settings, broker state, or execution locks.
- Quitting or refreshing the UI must not affect the trading process.
- The dashboard may write its own UI/session audit records only through the existing local SQLite logger; those records must remain clearly distinct from broker activity.

The existing `hub.py` entry point is retained. No web server, browser UI, new daemon, or second database is introduced.

## 2. Current implementation findings

The current `hub.py` is a periodic ANSI renderer with a fixed-width presentation and a 30-second configured refresh. It reads `logs/hub_state.json`, writes hub run/events/snapshots through `src/utils/sqlite_logger.py`, and fetches latest quotes through `AlpacaFeed`.

Important constraints discovered in the current code:
- `load_state()` maps missing, unreadable, malformed, and non-object JSON to an empty dictionary. The future UI must distinguish these conditions instead of silently presenting them as an ordinary empty state.
- `get_live_quotes()` constructs an `AlpacaFeed` on each render. The data provider should be decoupled from rendering and reused or polled on a controlled cadence.
- A quote failure currently falls back to zero-valued quote dictionaries. The new UI must render unavailable/stale states, never zero as if it were a real quote.
- The persisted `orders` collection is described by the current UI as orders in the last cycle. It is not a verified broker open-order book and must not be labelled as such.
- Current snapshots do not provide a complete, explicit dashboard contract for live positions and open broker orders.
- `src/utils/monitoring_state.py` atomically writes a JSON snapshot. It is an existing source of monitoring data, not a complete source of truth for every broker field.
- SQLite already stores `runs`, `events`, and `snapshots`; read-only query functions can expose relevant history without introducing a new store.
- There is no current `tests/test_hub.py`; the new dashboard needs its own isolated tests.

## 3. Technology decision

**Proposed UI stack: Textual, with Rich rendering primitives.**

Textual is preferred over hand-written ANSI output because the target is a keyboard-driven terminal application with multiple views, resizable layout, focus management, tables, status indicators, and controlled refresh. Rich remains useful for consistent formatting and static components.

Adoption conditions:
1. Pin an exact Textual version in `pyproject.toml`; never use an unbounded dependency.
2. Update `uv.lock` with the repository's pinned uv version and commit the lock change in the same PR as the dependency.
3. Verify installation and startup on the actual macOS 12.7 machine, both supported Mac architectures where available, and the existing Linux/macOS CI matrix.
4. Keep a graceful, explicit error if the terminal is too small or lacks required terminal capabilities.
5. Do not make a dependency change in this specification-only PR.

The first implementation PR must validate the selected Textual release and its transitive dependency set before merging. If compatibility or lock generation fails, stop and reassess rather than bypassing `--locked`.

## 4. Visual system — “STMAA Institutional Terminal”

The visual language should communicate a research and execution-monitoring system, not a retail trading app.

### Palette and typography
- Base: deep charcoal / blue-black.
- Primary text: high-contrast off-white.
- Secondary text: cool muted gray.
- Accent: restrained cyan/teal for navigation, focus, and neutral highlights.
- Positive: green; warning: amber; critical/error: red.
- Avoid gradients, decorative emoji, excessive borders, blinking, and rainbow coloring.
- Color must never be the only carrier of meaning: pair status colors with explicit labels such as `HEALTHY`, `STALE`, `BLOCKED`, `UNAVAILABLE`.
- Use aligned numeric columns, consistent decimal precision, fixed-width tabular values, and explicit units/currency.

### Brand treatment
- Header label: `STMAA`.
- Descriptor: `STRUCTURAL TRADING MULTI-AGENT ARCHITECTURE`.
- Persistent environment indicator: `PAPER`, `RESEARCH`, or `UNKNOWN`, based only on verified configuration/state.
- Persistent safety indicator: `READ-ONLY DASHBOARD`.
- Do not show a live/production badge or imply certification or production readiness.

### Responsive behavior
- Design for 80 columns as the minimum usable width; 120–160 columns should reveal additional detail rather than merely stretch whitespace.
- At narrow widths, stack KPI cards and hide non-essential secondary columns.
- Do not truncate critical status, ticker, order state, timestamp, or error text without an accessible detail view.
- Support keyboard-only operation. Mouse support is optional.
- Provide a clear footer with available keys and the current view.

## 5. Information architecture

The dashboard is organized into four views. Navigation is local to the dashboard and cannot invoke broker actions.

### 5.1 Overview
Purpose: one-screen operational picture.

Sections:
- Process/monitoring state: dashboard connected, last successful snapshot, snapshot age, source timestamp.
- Broker/data status: broker identity/environment when verified, account snapshot age, market-data feed and last successful quote timestamp.
- Account KPIs: equity, cash, portfolio value, daily P&L, total return, gross exposure and configured operational exposure cap.
- Risk: max drawdown and available persisted risk ratios, with observation window/source where known.
- Equity history: persisted observations only; no synthetic interpolation or invented history.
- Recent events: latest relevant SQLite events with timestamp, source, severity and message.

Do not derive a daily P&L from incompatible or unverified observations. If the source contract does not support a metric, show `N/A — NOT PERSISTED`.

### 5.2 Portfolio
Purpose: show broker-confirmed holdings.

For each position, when supplied by a successful read-only broker snapshot:
- symbol, side, quantity, average entry price, latest price, market value, unrealized P&L and portfolio weight;
- source timestamp and snapshot age;
- explicit indication if a field is missing or the snapshot is stale.

Cash and account equity must be shown separately. A missing position snapshot is not an empty portfolio. Never infer a zero-position account from a failed request.

### 5.3 Orders & executions
Purpose: separate working orders from historical executions.

Separate sections:
- Open/working orders from a broker-confirmed open-orders snapshot.
- Recent fills/executions from verified broker activity or reconciled local records.
- Local submission intents and reconciliation status, clearly labelled as local persisted evidence.

Show order ID/client-order ID where safe, symbol, side, type, time-in-force, quantity, filled quantity, status, limit price, average fill price, and timestamps when available.

The existing `orders` list from the last cycle must remain labelled as cycle-local data until a dedicated contract proves otherwise. Never relabel it as broker open orders or executions.

### 5.4 System
Purpose: explain the health and provenance of the system.

Show:
- process/run identity and lifecycle state when available;
- PPO artifact discovery/load/validation state and model contract metadata when available;
- market-data adapter, feed, last successful update, and latest error;
- broker read-only connectivity and snapshot timestamps;
- SQLite run/session status and recent warning/error events;
- configuration values relevant to monitoring, with secrets always redacted.

The dashboard must not expose API keys, secrets, environment variable values containing credentials, or raw authentication headers.

## 6. Data architecture

Separate the dashboard into three layers:

1. **Providers** — read existing local monitoring snapshots and SQLite records; acquire broker account/positions/open-orders/activity only through explicitly read-only adapter methods.
2. **Normalized view model** — convert provider results into typed dashboard records with source, observed-at timestamp, status, and error metadata.
3. **Textual presentation** — render view models only. Rendering must not perform network calls, mutate files, or call trading code.

Recommended conceptual records:
- `DataStatus`: `OK`, `STALE`, `MISSING`, `UNAVAILABLE`, `INVALID`.
- `DataEnvelope[T]`: optional value, source, observed_at, fetched_at, status, and safe error summary.
- `AccountView`, `PositionView`, `OpenOrderView`, `ExecutionView`, `SystemHealthView`, `DashboardSnapshot`.

These are design contracts, not a requirement to create a generic framework prematurely. Implement only the fields backed by verified sources.

### Data freshness
- Preserve the source timestamp and the local fetch timestamp separately.
- Define configurable stale thresholds per data class; do not apply one threshold to account snapshots, quotes, and process heartbeat indiscriminately.
- Show snapshot age in human-readable form.
- Stale data remains visible but is visibly marked stale; it must not be silently promoted to healthy.
- Missing, invalid, unavailable, and empty-but-valid collections are distinct states.
- Never replace a failed or missing value with zero, an empty portfolio, or a fabricated timestamp.

### Polling and resource use
- UI refresh and provider polling are separate settings.
- Reuse provider/client instances where safe.
- Do not make an Alpaca request on every paint/layout refresh.
- Bound network timeouts and retain the last known good value with its original timestamp and stale status.
- A provider failure must not crash the entire UI; show the error and continue rendering other independent data sources.
- Do not create concurrent duplicate polling tasks for the same provider.

### SQLite
Use read-only SQLite connections/transactions for dashboard history queries. Query the existing `runs`, `events`, and `snapshots` tables with bounded result counts and indexed timestamp/run filters. Do not run schema migrations or write dashboard metrics into the trading process tables. Existing hub audit writes, if retained, must remain explicitly identified as dashboard session records.

## 7. Safety and failure semantics

The dashboard is strictly read-only with respect to Alpaca and the trading engine.

Required invariants:
- No import or call path from UI actions to order submission, replacement, cancellation, liquidation, or broker mutation methods.
- No controls named or behaving as Start Trading, Stop Trading, Close Position, Cancel Order, or similar.
- Refresh is a read operation only.
- Quit exits the UI process only; it does not signal or terminate another process.
- The UI must not acquire or release the broker execution lock.
- UI failure must not stop or modify the trading process.
- An unknown state is not healthy. Use fail-closed status presentation.
- Never represent a cached snapshot as current broker truth.
- All data must carry provenance and timestamps where available.

## 8. Navigation and interaction

Initial key map:
- `1` — Overview
- `2` — Portfolio
- `3` — Orders & executions
- `4` — System
- `R` — request a data refresh (read-only)
- `?` — help / key map
- `Q` — quit dashboard

No keyboard shortcut may submit or mutate a broker order.

## 9. Implementation sequence and PR boundaries

Each stage is a dedicated PR and must pass the existing CI checks before merge.

### PR 1 — Technical specification (this PR)
- Record architecture, UX, data semantics, safety boundaries, and implementation plan.
- No runtime dependency changes or application behavior changes.

### PR 2 — Textual shell and visual foundation
- Add pinned Textual dependency and regenerate `uv.lock`.
- Preserve `hub.py` as entry point.
- Add the app shell, header, status strip, responsive layout, navigation, help and quit.
- Use isolated, explicitly labelled fixtures only in UI tests; never show fixtures in normal runtime.
- Add unit tests for navigation, minimum-width behavior, and no broker mutation paths.
- Keep live data integration out of this PR.

### PR 3 — Snapshot adapter and Overview
- Introduce normalized read-only snapshot providers and status envelopes.
- Connect only data already available and verified.
- Replace silent fallback behavior with explicit missing/invalid/stale states.
- Keep provider polling outside render methods.

### PR 4 — Portfolio view
- Add broker-confirmed positions and account fields via read-only APIs.
- Add tests for missing, stale, partial, malformed and empty-valid snapshots.

### PR 5 — Orders & executions view
- Add broker-confirmed open orders and executions separately from local intents/cycle records.
- Add reconciliation/provenance labels and failure tests.

### PR 6 — System view and SQLite history
- Add bounded read-only event/run/snapshot queries.
- Add process, PPO, feed and broker health indicators backed by actual state.

### PR 7 — Visual QA, documentation and screenshot
- Validate at narrow and wide terminal sizes on macOS 12.7 and CI platforms.
- Replace the stale repository screenshot only with a reproducible, truthful capture.
- If the screenshot uses fixtures, label it clearly as illustrative and ensure it cannot be mistaken for live account data.
- Update README usage and troubleshooting.

## 10. Acceptance criteria

The redesign is acceptable only when:
1. `uv run --locked python hub.py` remains the documented launch command.
2. Startup and exit work on the user's macOS 12.7 environment.
3. UI navigation, refresh, help, and quit work using keyboard only.
4. No broker mutation method is reachable from the dashboard.
5. Rendering is deterministic for a given view model and performs no network/database work.
6. Data states distinguish healthy, stale, missing, invalid, unavailable, and valid-empty.
7. No fabricated account, quote, position, order, execution, risk, or equity-curve values appear in normal runtime.
8. Broker open orders, local intents, and executions are never conflated.
9. UI refresh does not create a new Alpaca client/request on every render.
10. Existing trading tests and cross-platform CI remain green.
11. The UI remains usable at 80 columns and degrades gracefully below that width.
12. Secrets and credentials are never rendered or logged.

## 11. Out of scope

- Live trading readiness, production certification, or claims of strategy profitability.
- Any broker order-management controls.
- Changes to PPO policy, training, risk sizing, execution schedule, or order semantics.
- Multi-host coordination or a web dashboard.
- Replacing SQLite or the existing monitoring snapshot store.
- Adding synthetic/demo account data to the live runtime.
