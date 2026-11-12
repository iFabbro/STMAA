# STMAA Dashboard Visual Redesign — PR 1 Technical Specification

**Status:** Proposed implementation contract  
**Baseline:** `main` at `61bc9be49d25d47727cb4dfabdbb2a103c78fa9a`  
**Target runtime:** Python 3.12, Textual 8.2.8, macOS 12.7+ (Intel and Apple Silicon), Linux CI  
**Entry point:** `uv run --locked python hub.py`  
**Scope:** Presentation-only redesign of the existing four-view local dashboard.

## 1. Objective

Improve visual hierarchy, scanability, navigation clarity, and narrow-terminal behavior without changing the dashboard's data sources or operational authority.

The dashboard remains a local observer. This PR must not add broker connectivity, trading controls, process controls, new persistence, new dependencies, or changes to trading/risk/model logic.

This document supplements, and does not replace, the existing contracts in:
- `docs/TERMINAL_DASHBOARD_SPEC.md`
- `docs/TERMINAL_DASHBOARD_LOCAL_DATA_SPEC.md`
- `docs/DASHBOARD_ORDERS_CONTRACT.md`

If a visual requirement conflicts with a read-only, provenance, or fail-closed requirement in those documents, the existing safety/data contract takes precedence.

## 2. Verified implementation baseline

At the baseline commit:
- `src/dashboard/app.py` uses Textual and renders each view into a single `Static#content` widget.
- The screen has a masthead, a static status strip, a text navigation strip, a narrow-width warning, the content widget, and Textual's footer.
- Four keyboard-navigable views already exist: Overview, Portfolio, Orders & Executions, System.
- Refresh is bound to `r`; quit is bound to `q`; `?` opens help.
- Local providers are isolated in `src/dashboard/local_providers.py`.
- Dashboard provider reads are separate from the dashboard lifecycle audit writes in `hub.py`.
- Existing app tests are in `tests/test_dashboard_app.py`; provider tests are in `tests/test_dashboard_local_providers.py` and `tests/test_dashboard_orders_provider.py`.

The implementation PR must preserve these behaviors and contracts. It must not assume that a historical run marked `running` means a process is alive, that a missing positions collection means zero positions, or that local order metadata is a live broker order book.

## 3. Scope boundaries

### Included
- Replace the monolithic text presentation with composed Textual containers and focused widgets.
- Establish reusable visual tokens and consistent status rendering.
- Add a structured masthead/status area and visible active-view navigation.
- Improve the information hierarchy of the four existing views.
- Use native Textual tables for tabular records where the current view model already supplies structured rows.
- Improve responsive behavior at terminal widths of 80, 100, 120, and 160 columns.
- Preserve keyboard-only navigation and current actions.
- Add UI regression tests using Textual's `run_test`/Pilot.

### Explicitly excluded
- No changes to broker APIs, Alpaca clients, trading/execution modules, PPO, model artifacts, feed logic, risk limits, order sizing, preflight, soak runner, or SQLite schema.
- No new market-data or broker requests.
- No new dashboard write path. Existing dashboard-session audit writes remain confined to the existing `hub.py` lifecycle.
- No new dependencies and no Textual version upgrade in this PR.
- No web dashboard, browser frontend, charting framework, animation system, or background daemon.
- No new metric calculations, derived financial values, synthetic history, or guessed status.
- No independent positions freshness logic in this visual PR. Treat that as a separate data-contract correction with its own tests/PR; the UI must display only the status/provenance it is actually given.

## 4. Visual design system

Use a restrained dark institutional-terminal style. Colors are tokens, not ad hoc inline values.

| Token | Hex | Use |
|---|---|---|
| `screen` | `#0B1118` | Main background |
| `surface` | `#0E1721` | Primary content surface |
| `surface-raised` | `#152331` | Header, status strip, selected navigation |
| `surface-muted` | `#111C27` | Secondary panels |
| `border` | `#294052` | Subtle separators and panel edges |
| `text-primary` | `#E6EDF3` | Main text |
| `text-secondary` | `#A9BAC8` | Labels and secondary metadata |
| `accent` | `#55C5C1` | Focus, active navigation, neutral emphasis |
| `positive` | `#3FB950` | Explicitly verified healthy/positive state |
| `warning` | `#D29922` | Stale data, attention required |
| `critical` | `#F85149` | Error, invalid, blocked condition |
| `unknown` | `#8B949E` | Missing/unavailable/unknown state |

Rules:
- Status must always have a textual label; color alone is insufficient.
- `OK`, `STALE`, `MISSING`, `EMPTY`, `INVALID`, and `UNAVAILABLE` must remain distinct.
- Do not color missing/unknown data green.
- Do not use decorative gradients, blinking, emoji, or excessive borders.
- Use tabular/monospaced alignment for numbers, identifiers, timestamps, and symbols. Keep explanatory copy readable.
- Financial values must retain explicit currency/units where the source contract supports them. Do not introduce new rounding or calculations in the UI layer.

## 5. Component and layout contract

Use existing Textual primitives; do not add third-party widgets.

Recommended component tree:

```text
STMAADashboard
└── Vertical#root
    ├── Horizontal#masthead
    │   ├── Static#brand
    │   └── Static#descriptor
    ├── Horizontal#status-strip
    │   ├── Static#environment-status
    │   ├── Static#dashboard-authority
    │   └── Static#data-refresh-status
    ├── Horizontal#navigation
    │   ├── Static#nav-overview
    │   ├── Static#nav-portfolio
    │   ├── Static#nav-orders
    │   └── Static#nav-system
    ├── Static#size-warning
    ├── VerticalScroll#view-container
    │   └── view-specific composed widgets
    └── Footer
```

The tree is a target structure, not a requirement to create one Python class per widget. Keep the implementation small and avoid a generic component framework.

### Persistent header
- Brand: `STMAA`.
- Descriptor: `STRUCTURAL TRADING MULTI-AGENT ARCHITECTURE`.
- Environment: display `UNKNOWN` unless an already-approved provider supplies a verified value. Do not infer Paper/Live from a title, local configuration guess, or previous run.
- Authority: `LOCAL READ-ONLY DASHBOARD` and `NO BROKER ACTIONS`. This describes dashboard capability, not the state of an external broker.
- Refresh: show last dashboard refresh time only if the UI has an actual refresh timestamp; distinguish it from source observation timestamps.
- Do not present the application as production-ready, certified, or live-trading-ready.

### Navigation
- Keep keys `1`–`4`, `r`, `?`, and `q`.
- Active view must be visually distinct and also include a textual active marker for accessibility/low-color terminals.
- Navigation must never trigger provider I/O except when the existing refresh contract explicitly does so.
- Footer must continue to expose available keys and quit behavior.

### View container
- Use a scrollable container for content longer than the viewport.
- Do not silently drop critical status/error details. Long records may be clipped only when a visible indication and a usable detail path exist.
- Keep source, observation timestamp, age, and status adjacent to the values they qualify.

## 6. View behavior and layout

### 6.1 Overview

Purpose: answer “what is the latest persisted local monitoring state?” without implying live account truth.

Layout:
1. Title and one-line provenance disclaimer.
2. KPI row(s) for fields already present in the approved monitoring JSON/SQLite snapshots: portfolio value, equity, cash, drawdown, exposure, regime.
3. Separate source panels for monitoring JSON and SQLite snapshot; never silently merge conflicting values.
4. Recent local events, if already returned by the existing provider contract.

Behavior:
- Each KPI shows its value plus source status and observation age.
- Missing values render as `NOT PERSISTED`/unknown, not zero.
- A valid empty collection is different from missing/unavailable data.
- Do not add daily P&L, return, or risk calculations unless the persisted contract explicitly provides them.

### 6.2 Portfolio

Purpose: present only the persisted position observation currently supplied by the approved local snapshot.

Layout:
1. Title and explicit disclaimer: persisted local observation, not a live broker query.
2. Portfolio value/equity/cash summary, each with source status.
3. Positions table with columns already supported by the provider: Symbol, Side, Quantity, Average Entry, Market Value, Unrealized P/L.
4. Snapshot source, observation timestamp, and age.

Behavior:
- Use a Textual `DataTable` for positions when the collection is valid.
- Missing/invalid/unavailable positions must not render as an empty table that implies zero holdings.
- Empty-but-valid positions may render “No positions in this persisted snapshot”.
- Preserve the provider's current status. Do not calculate portfolio weight, current price, or position freshness in this PR unless those values are explicitly supplied and validated.
- At narrow widths, hide only non-critical columns and retain a clear indication that additional fields are not shown.

### 6.3 Orders & Executions

Purpose: prioritize unresolved local execution evidence and make its provenance unmistakable.

Layout order:
1. Unresolved submission intents.
2. Broker-confirmed order records persisted locally.
3. OPG batches.
4. Emergency liquidation pending and unresolved records.

Use compact summary counts only when they can be counted directly from the returned collections. Use tables for repeated records; expandable/detail text may be used for long IDs and reasons.

Behavior:
- Keep the page-level disclaimer that no broker query is performed and persisted state may be stale.
- Do not call local records “live open orders”, “current broker orders”, or “fills” unless the existing source contract explicitly proves that classification.
- Keep each collection separate; never merge or infer state between categories.
- Sort by semantic urgency only where the underlying record fields support a deterministic rule. Otherwise preserve a deterministic, documented ordering; do not invent timestamps.
- Long IDs/reasons must remain inspectable.

### 6.4 System

Purpose: explain local source availability and historical lifecycle evidence.

Layout:
1. Source health panel: monitoring JSON, SQLite snapshots, run history, event history.
2. Latest local run metadata: stored status, mode, started/ended timestamps, branch, commit.
3. Recent events table/list: timestamp, level, event type, source, symbol, message.

Behavior:
- Explicitly label run status as stored/historical.
- Never infer current process liveness from a stored run row.
- Keep event list bounded to the existing provider limit.
- Render source errors safely; do not expose credentials, raw environment values, or unbounded payloads.

## 7. Responsive behavior

Test and support at minimum:
- 80×24: minimum usable width; warning may appear for constrained layout, but navigation and critical statuses remain visible.
- 100×30: compact layout.
- 120×36: standard layout.
- 160×45: expanded layout; use extra width for additional columns/spacing, not oversized empty regions.

Rules:
- At widths below 80 columns, show the existing narrow-terminal warning.
- At 80 columns and above, no critical text may be clipped horizontally.
- Stack KPI cards and panels at compact widths; use multiple columns only when each panel remains readable.
- Numeric table columns must remain aligned.
- Height constraints must allow the content container to scroll without hiding the footer.

## 8. Keyboard and interaction contract

- `1`: Overview
- `2`: Portfolio
- `3`: Orders & Executions
- `4`: System
- `r`: refresh approved local providers once
- `?`: help
- `q`: quit dashboard only

No clickable control may imply an operation it does not perform. No action may submit/cancel/replace/close/liquidate an order, start/stop a process, alter configuration, or modify a model.

## 9. Regression test plan

Extend `tests/test_dashboard_app.py`; retain existing provider and orders-provider tests unchanged unless a separate defect is discovered and split into its own PR.

### Structure and visual contract
- Assert the composed app contains the masthead, status strip, navigation, size warning, view container, and footer.
- Assert active navigation state changes for keys `1`–`4`.
- Assert all four views render their own title and provenance disclaimer.
- Assert palette tokens are centralized and status labels remain textual (not color-only).

### Responsive behavior
- Run Textual Pilot at 80×24, 100×30, 120×36, and 160×45.
- Assert the narrow warning is visible below 80 columns and hidden at/above 80.
- Assert navigation, active-view marker, and critical status labels remain present at supported widths.
- Assert long content can scroll and the footer remains available.

### Data semantics
- Parameterize source statuses `OK`, `STALE`, `MISSING`, `EMPTY`, `INVALID`, `UNAVAILABLE`.
- Assert no missing/invalid/unavailable field is rendered as numeric zero.
- Assert missing positions are not rendered as an empty portfolio; valid empty positions are explicitly labelled empty.
- Assert source timestamps and age remain associated with the correct source.
- Assert persisted historical run status is never labelled as current process liveness.
- Assert unresolved, confirmed, OPG, and liquidation collections remain separate and are not relabelled as live broker state.

### Interaction and isolation
- Assert keys `1`–`4`, `r`, `?`, and `q` retain their behavior.
- Assert one refresh causes exactly one call to each approved provider, as applicable.
- Assert navigation and resize do not call providers.
- Assert no broker/execution/trading imports or mutating method names are introduced into dashboard presentation modules.
- Assert dashboard provider modules remain read-only and lifecycle writes remain confined to `hub.py`.
- Assert refresh, navigation, resize, and quit do not create/modify trading state, broker state, model artifacts, or configuration.

### Required commands
At minimum:
```bash
uv run --locked ruff check src/dashboard tests/test_dashboard_app.py
uv run --locked pytest tests/test_dashboard_app.py tests/test_dashboard_local_providers.py tests/test_dashboard_orders_provider.py -q
uv run --locked pytest -q
```

The full repository CI matrix and Gitleaks must pass before merge. Do not weaken, skip, or bypass existing checks to accommodate UI changes.

## 10. Acceptance criteria

The implementation PR is acceptable only when:
- All four views have a structured, consistent visual hierarchy.
- The active view and dashboard read-only boundary are obvious.
- Data provenance, status, and timestamps remain visible and unambiguous.
- Missing, empty, stale, invalid, and unavailable states are distinguishable.
- The dashboard remains keyboard-operable at supported terminal sizes.
- Existing local provider semantics and audit lifecycle behavior are unchanged.
- No broker calls, trading actions, new persistence, or trading logic changes are introduced.
- All targeted tests, full test suite, Ruff, Gitleaks, and required CI jobs pass.
- Local validation on the target Intel Mac confirms startup, navigation, refresh, narrow-width warning, scrolling, and quit.

## 11. Implementation sequencing

This document/specification PR is documentation-only. It does not implement the redesign.

The subsequent implementation PR should be limited to:
- `src/dashboard/app.py` and narrowly scoped dashboard presentation helpers/styles;
- `tests/test_dashboard_app.py`;
- optional README screenshot refresh only if a verified screenshot is captured from the actual application.

Do not combine the independent positions-freshness correction, Doctor/CLI redesign, or PPO/backtest reporting with this first visual implementation PR.
