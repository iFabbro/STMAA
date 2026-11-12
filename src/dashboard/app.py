"""Read-only Textual dashboard backed by approved local monitoring sources.

The UI reads persisted monitoring snapshots and SQLite history only. It does
not connect to a broker, trading engine, execution state, or PPO artifacts.
Dashboard-session audit writes remain exclusively in hub.py.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Any, Callable, TypeVar

from rich.markup import escape
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import DataTable, Footer, Static

from . import local_providers as providers

SNAPSHOT_MAX_AGE_SECONDS = 900
EVENT_LIMIT = 12
T = TypeVar("T")


def _safe_read(reader: Callable[[], T], source: str) -> T | providers.SourceResult[Any]:
    """Isolate one provider failure without hiding other local sources."""
    try:
        return reader()
    except Exception:
        return providers.SourceResult(
            providers.SourceStatus.UNAVAILABLE,
            source,
            detail="provider read failed",
        )


def _text(value: Any, *, limit: int = 160) -> str:
    if value is None:
        return "NOT PERSISTED"
    if isinstance(value, bool):
        rendered = "true" if value else "false"
    elif isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            return "INVALID"
        rendered = f"{value:,.2f}" if isinstance(value, float) else f"{value:,}"
    elif isinstance(value, str):
        rendered = value.strip() or "NOT PERSISTED"
    else:
        rendered = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    if len(rendered) > limit:
        rendered = rendered[: limit - 1] + "…"
    return escape(rendered)


def _age(observed_at: str | None) -> str:
    if not observed_at:
        return "age unknown"
    candidate = observed_at[:-1] + "+00:00" if observed_at.endswith("Z") else observed_at
    try:
        parsed = datetime.fromisoformat(candidate)
    except (TypeError, ValueError):
        return "timestamp invalid"
    if parsed.tzinfo is None:
        return "timestamp has no timezone"
    raw_seconds = (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds()
    if raw_seconds < -60:
        return "timestamp in future"
    seconds = max(0, int(raw_seconds))
    if seconds < 60:
        return f"{seconds}s old"
    if seconds < 3600:
        return f"{seconds // 60}m old"
    if seconds < 86400:
        return f"{seconds // 3600}h old"
    return f"{seconds // 86400}d old"


def _source_header(label: str, result: providers.SourceResult[Any]) -> str:
    observed = f" | {escape(result.observed_at)} | {_age(result.observed_at)}" if result.observed_at else ""
    detail = f" — {escape(result.detail)}" if result.detail else ""
    return f"[b]{escape(label)}[/b]  STATUS: {escape(result.status.value)}{observed}{detail}"


def _snapshot_lines(label: str, result: providers.SourceResult[dict[str, Any]]) -> list[str]:
    lines = [_source_header(label, result)]
    value = result.value if isinstance(result.value, dict) else {}
    if result.status in {providers.SourceStatus.INVALID, providers.SourceStatus.UNAVAILABLE, providers.SourceStatus.EMPTY} or not value:
        return lines
    fields = (
        ("Portfolio value", "portfolio_value"),
        ("Equity", "equity"),
        ("Cash", "cash"),
        ("Drawdown", "drawdown"),
        ("Exposure", "exposure"),
        ("Regime", "regime"),
    )
    for label_text, key in fields:
        lines.append(f"  {label_text:<17} {_text(value.get(key))}")
    if value.get("source") is not None:
        lines.append(f"  Stored source     {_text(value.get('source'))}")
    if value.get("run_id") is not None:
        lines.append(f"  Run ID            {_text(value.get('run_id'), limit=48)}")
    return lines


def _portfolio_lines(result: providers.SourceResult[dict[str, Any]]) -> list[str]:
    """Render only the explicitly persisted position snapshot and its provenance."""
    lines = [
        "[b]PORTFOLIO — PERSISTED POSITION SNAPSHOT[/b]",
        "Local snapshot only. This is not a live broker query or a guarantee of current holdings.",
        "",
        _source_header("Monitoring JSON · hub_state.json", result),
    ]
    value = result.value if isinstance(result.value, dict) else {}
    if result.status is providers.SourceStatus.INVALID:
        lines.append("POSITIONS: INVALID — monitoring snapshot is invalid")
        return lines
    if result.status in {
        providers.SourceStatus.UNAVAILABLE,
        providers.SourceStatus.EMPTY,
    } or not value:
        lines.append("POSITIONS: UNKNOWN — approved snapshot unavailable")
        return lines

    for label, key in (
        ("Portfolio value", "portfolio_value"),
        ("Equity", "equity"),
        ("Cash", "cash"),
    ):
        lines.append(f"  {label:<17} {_text(value.get(key))}")

    positions_status = value.get("positions_status", "UNKNOWN")
    if positions_status == "UNAVAILABLE":
        positions_status = "UNKNOWN"
    if positions_status not in {"OK", "STALE", "MISSING", "INVALID", "UNKNOWN"}:
        positions_status = "UNKNOWN"
    positions = value.get("positions")
    if positions_status != "OK":
        lines.extend([
            "",
            f"POSITIONS: {positions_status} — no verified fresh position collection persisted",
        ])
        if value.get("positions_source"):
            lines.append(f"  Source            {_text(value.get('positions_source'))}")
        if value.get("positions_snapshot_at"):
            lines.append(f"  Observed          {_text(value.get('positions_snapshot_at'), limit=40)}")
        return lines

    observed_at = value.get("positions_snapshot_at")
    source = value.get("positions_source") or "not specified"
    lines.extend([
        "",
        f"POSITIONS: {result.status.value} — persisted broker observation",
        f"  Source            {_text(source)}",
        f"  Observed          {_text(observed_at, limit=40)}",
        f"  Age               {_age(observed_at)}",
        "",
    ])
    if not isinstance(positions, list):
        lines.append("POSITION COLLECTION: INVALID")
        return lines
    if not positions:
        lines.append("NO POSITIONS IN THIS PERSISTED SNAPSHOT")
        return lines

    lines.append("SYMBOL   SIDE   QTY          AVG ENTRY     MARKET VALUE   UNREALIZED P/L")
    for position in positions:
        symbol = _text(position.get("symbol"), limit=8)
        qty = position.get("qty")
        side = "LONG" if isinstance(qty, (int, float)) and qty > 0 else "SHORT"
        lines.append(
            f"{symbol:<8} {side:<6} {_text(qty, limit=12):>12} "
            f"{_text(position.get('avg_entry_price'), limit=12):>12} "
            f"{_text(position.get('market_value'), limit=14):>14} "
            f"{_text(position.get('unrealized_pl'), limit=14):>14}"
        )
    return lines


def _record_section(title: str, records: Any, fields: tuple[str, ...], *, limit: int = 12) -> list[str]:
    lines = [f"[b]{escape(title)}[/b]"]
    if not isinstance(records, dict):
        lines.append("  NOT AVAILABLE — persisted schema does not contain this collection")
        return lines
    if not records:
        lines.append("  No records in this persisted snapshot")
        return lines
    entries = sorted(records.items(), key=lambda item: str(item[0]))
    for record_id, record in entries[:limit]:
        lines.append(f"  ID: {_text(record_id, limit=64)}")
        if isinstance(record, dict):
            for field in fields:
                if field in record:
                    lines.append(f"    {field}: {_text(record[field], limit=100)}")
        else:
            lines.append(f"    Value: {_text(record, limit=100)}")
    if len(entries) > limit:
        lines.append(f"  … {len(entries) - limit} additional records not rendered")
    return lines


def _orders_lines(result: providers.SourceResult[dict[str, Any]]) -> list[str]:
    lines = [
        "[b]ORDERS & EXECUTIONS — LOCAL PERSISTED EVIDENCE[/b]",
        "No broker query is performed. Persisted state may be stale and is not a live order/fill ledger.",
        "",
        _source_header("Broker state JSON", result),
    ]
    value = result.value if isinstance(result.value, dict) else {}
    if result.status in {providers.SourceStatus.INVALID, providers.SourceStatus.UNAVAILABLE} or not value:
        lines.append("EXECUTION STATE: UNKNOWN — persisted broker state unavailable or invalid")
        return lines
    freshness_status = value.get("account_snapshot_status", result.status.value)
    if freshness_status == providers.SourceStatus.STALE.value:
        lines.append("FRESHNESS: STALE — records below are historical evidence, not current broker state")
    elif freshness_status == providers.SourceStatus.MISSING.value:
        lines.append("FRESHNESS: UNKNOWN — account snapshot timestamp is missing")

    lines.extend([
        f"Account mode: {_text(value.get('account_mode'))}  |  "
        f"Schema: {_text(value.get('state_schema_version'))}  |  "
        f"Revision: {_text(value.get('state_revision'))}",
        f"Account snapshot: {_text(value.get('account_snapshot_at'), limit=40)} | {_age(value.get('account_snapshot_at'))}",
        f"Persisted freshness flag: {_text(value.get('account_data_fresh'))}",
        "Collections below are independent; no category is merged or inferred from another.",
        "",
    ])
    lines.extend(_record_section(
        "UNRESOLVED SUBMISSION INTENTS",
        value.get("order_submission_unresolved"),
        ("ticker", "client_order_id", "side", "quantity", "price", "order_type", "created_at", "reason"),
    ))
    lines.extend(["", *_record_section(
        "BROKER-CONFIRMED ORDER RECORDS (PERSISTED)",
        value.get("order_submission_confirmed"),
        ("ticker", "status", "side", "qty", "quantity", "filled_qty", "client_order_id", "order_id", "position_reconciled", "updated_at"),
    )])
    lines.extend(["", *_record_section(
        "OPG BATCHES",
        value.get("opg_batches"),
        ("status", "signal_bar_at", "target_session", "created_at", "updated_at", "account_mode"),
    )])
    lines.extend(["", *_record_section(
        "EMERGENCY LIQUIDATION — PENDING",
        value.get("emergency_liquidation_pending"),
        (),
    )])
    lines.extend(["", *_record_section(
        "EMERGENCY LIQUIDATION — UNRESOLVED INTENTS",
        value.get("emergency_liquidation_unresolved"),
        ("requested_qty", "expected_side", "created_at", "reason"),
    )])
    return lines


class STMAADashboard(App):
    """Keyboard-driven local monitoring dashboard; no broker integration."""

    TITLE = "STMAA Institutional Terminal"
    SUB_TITLE = "STRUCTURAL TRADING MULTI-AGENT ARCHITECTURE"

    # Centralized visual tokens. Keep textual status labels in every state.
    CSS = """
    Screen {
        background: #0B1118;
        color: #E6EDF3;
    }
    #root {
        height: 1fr;
        background: #0B1118;
    }
    #masthead {
        height: 3;
        padding: 0 2;
        background: #152331;
        color: #E6EDF3;
        text-style: bold;
        content-align: left middle;
    }
    #descriptor {
        color: #A9BAC8;
        text-style: none;
    }
    #status-strip {
        height: 2;
        padding: 0 2;
        background: #0E1721;
        color: #A9BAC8;
        content-align: left middle;
    }
    #navigation {
        height: 3;
        padding: 0 1;
        background: #111C27;
        color: #A9BAC8;
        content-align: left middle;
    }
    .nav-item {
        width: 1fr;
        height: 3;
        padding: 0 1;
        content-align: center middle;
        border-bottom: tall #294052;
    }
    .nav-item.active {
        color: #55C5C1;
        text-style: bold;
        background: #152331;
        border-bottom: tall #55C5C1;
    }
    #size-warning {
        height: 2;
        padding: 0 2;
        color: #D29922;
        background: #382B16;
        content-align: left middle;
        display: none;
    }
    #view-container {
        height: 1fr;
        padding: 1 1;
        background: #0B1118;
    }
    #view-heading {
        height: auto;
        padding: 0 1;
        color: #55C5C1;
        text-style: bold;
    }
    #view-subheading {
        height: auto;
        padding: 0 1 1 1;
        color: #A9BAC8;
    }
    #content {
        height: auto;
        min-height: 8;
        margin: 0 0 1 0;
        padding: 1 2;
        border: round #294052;
        background: #0E1721;
        color: #E6EDF3;
    }
    #positions-table {
        height: auto;
        max-height: 14;
        margin: 0 0 1 0;
        border: round #294052;
        background: #0E1721;
    }
    #positions-table > .datatable--header {
        background: #152331;
        color: #55C5C1;
        text-style: bold;
    }
    Footer {
        height: 1;
        background: #152331;
        color: #E6EDF3;
    }
    """

    BINDINGS = [
        Binding("1", "show_overview", "Overview", show=True),
        Binding("2", "show_portfolio", "Portfolio", show=True),
        Binding("3", "show_orders", "Orders", show=True),
        Binding("4", "show_system", "System", show=True),
        Binding("r", "refresh_view", "Refresh", show=True),
        Binding("question_mark", "show_help", "Help", show=True),
        Binding("q", "quit", "Quit", show=True),
    ]

    VIEW_LABELS = {
        "overview": ("OVERVIEW", "Latest persisted local monitoring state"),
        "portfolio": ("PORTFOLIO", "Persisted position observation · not a live broker query"),
        "orders": ("ORDERS & EXECUTIONS", "Local persisted execution evidence · no broker query"),
        "system": ("SYSTEM", "Source availability and historical lifecycle evidence"),
    }

    def __init__(self) -> None:
        super().__init__()
        self.current_view = "overview"
        self._last_refresh_at: str | None = None
        self._portfolio_snapshot: providers.SourceResult[dict[str, Any]] | None = None
        self._view_content = {
            "overview": "Loading approved local monitoring sources…",
            "portfolio": (
                "[b]POSITIONS NOT CONNECTED[/b]\n"
                "No verified position collection is currently available from the approved local snapshot."
            ),
            "orders": "[b]Loading persisted execution evidence…[/b]",
            "system": "Loading local system history…",
        }

    def compose(self) -> ComposeResult:
        with Vertical(id="root"):
            yield Static("STMAA  |  STRUCTURAL TRADING MULTI-AGENT ARCHITECTURE", id="masthead", markup=False)
            yield Static(
                "ENVIRONMENT: UNKNOWN  |  LOCAL READ-ONLY DASHBOARD\nNO BROKER ACTIONS  |  REFRESH: NOT YET",
                id="status-strip",
                markup=False,
            )
            with Horizontal(id="navigation"):
                yield Static("1  OVERVIEW", id="nav-overview", classes="nav-item active", markup=False)
                yield Static("2  PORTFOLIO", id="nav-portfolio", classes="nav-item", markup=False)
                yield Static("3  ORDERS", id="nav-orders", classes="nav-item", markup=False)
                yield Static("4  SYSTEM", id="nav-system", classes="nav-item", markup=False)
            yield Static(
                "Terminal width below 80 columns. Expand the terminal for the full layout.",
                id="size-warning",
                markup=False,
            )
            with VerticalScroll(id="view-container"):
                yield Static("OVERVIEW", id="view-heading", markup=False)
                yield Static("Latest persisted local monitoring state", id="view-subheading", markup=False)
                yield Static(self._view_content[self.current_view], id="content", markup=True)
                yield DataTable(id="positions-table", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#positions-table", DataTable)
        table.add_columns("SYMBOL", "SIDE", "QUANTITY", "AVERAGE ENTRY", "MARKET VALUE", "UNREALIZED P/L")
        table.display = False
        self._update_width_warning()
        self._refresh_local_data()
        self._render_view()

    def on_resize(self, event) -> None:
        self._update_width_warning(event.size.width)

    def _update_width_warning(self, width: int | None = None) -> None:
        if width is None:
            width = self.size.width
        self.query_one("#size-warning", Static).display = width < 80

    def _refresh_status_strip(self) -> None:
        refresh = self._last_refresh_at or "NOT YET"
        self.query_one("#status-strip", Static).update(
            f"ENVIRONMENT: UNKNOWN  |  LOCAL READ-ONLY DASHBOARD\nNO BROKER ACTIONS  |  REFRESH: {refresh}"
        )

    def _populate_positions_table(self) -> None:
        table = self.query_one("#positions-table", DataTable)
        table.clear()
        snapshot = self._portfolio_snapshot
        if snapshot is None or not isinstance(snapshot.value, dict):
            return
        value = snapshot.value
        positions = value.get("positions")
        if snapshot.status in {
            providers.SourceStatus.INVALID,
            providers.SourceStatus.UNAVAILABLE,
            providers.SourceStatus.EMPTY,
        } or value.get("positions_status") != "OK":
            return
        if not isinstance(positions, list):
            return
        for position in positions:
            if not isinstance(position, dict):
                continue
            qty = position.get("qty")
            side = "LONG" if isinstance(qty, (int, float)) and qty > 0 else "SHORT"
            table.add_row(
                _text(position.get("symbol"), limit=8),
                side,
                _text(qty, limit=16),
                _text(position.get("avg_entry_price"), limit=16),
                _text(position.get("market_value"), limit=16),
                _text(position.get("unrealized_pl"), limit=16),
            )

    def _refresh_local_data(self) -> None:
        json_snapshot = _safe_read(
            lambda: providers.read_json_snapshot(
                max_age_seconds=SNAPSHOT_MAX_AGE_SECONDS,
                positions_max_age_seconds=SNAPSHOT_MAX_AGE_SECONDS,
            ),
            "hub_state.json",
        )
        sqlite_snapshot = _safe_read(
            lambda: providers.read_latest_snapshot(max_age_seconds=SNAPSHOT_MAX_AGE_SECONDS),
            "stmaa.db",
        )
        latest_run = _safe_read(providers.read_latest_run, "stmaa.db")
        recent_events = _safe_read(
            lambda: providers.read_recent_events(limit=EVENT_LIMIT),
            "stmaa.db",
        )

        if isinstance(json_snapshot, providers.SourceResult):
            self._portfolio_snapshot = json_snapshot
            self._view_content["portfolio"] = "\n".join(_portfolio_lines(json_snapshot))

        broker_state = _safe_read(
            lambda: providers.read_persisted_broker_state(
                max_age_seconds=providers.BROKER_STATE_MAX_AGE_SECONDS
            ),
            "broker_state.json",
        )
        if isinstance(broker_state, providers.SourceResult):
            self._view_content["orders"] = "\n".join(_orders_lines(broker_state))

        if isinstance(json_snapshot, providers.SourceResult) and isinstance(sqlite_snapshot, providers.SourceResult):
            overview = [
                "[b]OVERVIEW — LOCAL MONITORING SNAPSHOTS[/b]",
                "Values below are persisted local observations, not live broker/account truth.",
                "",
                *_snapshot_lines("Monitoring JSON · hub_state.json", json_snapshot),
                "",
                *_snapshot_lines("SQLite snapshot · stmaa.db", sqlite_snapshot),
            ]
            self._view_content["overview"] = "\n".join(overview)

        if all(isinstance(item, providers.SourceResult) for item in (json_snapshot, sqlite_snapshot, latest_run, recent_events)):
            system = [
                "[b]SYSTEM — LOCAL HISTORY & SOURCE AVAILABILITY[/b]",
                "Historical records only. A stored run status does not prove that a process is currently alive.",
                "",
                _source_header("Monitoring JSON", json_snapshot),
                _source_header("SQLite snapshots", sqlite_snapshot),
                "",
                "[b]LATEST LOCAL RUN — STORED/HISTORICAL STATUS[/b]",
            ]
            run_value = latest_run.value if isinstance(latest_run.value, dict) else {}
            system.append(_source_header("Run history", latest_run))
            if run_value:
                for label, key in (
                    ("Stored status", "status"),
                    ("Mode", "mode"),
                    ("Started", "started_at"),
                    ("Ended", "ended_at"),
                    ("Branch", "branch"),
                    ("Commit", "commit_sha"),
                ):
                    system.append(f"  {label:<14} {_text(run_value.get(key), limit=64)}")
            system.extend(["", "[b]RECENT LOCAL EVENTS[/b]", _source_header("Event history", recent_events)])
            if isinstance(recent_events.value, list):
                for event in recent_events.value:
                    if not isinstance(event, dict):
                        continue
                    timestamp = _text(event.get("ts"), limit=32)
                    level = _text(event.get("level"), limit=12)
                    event_type = _text(event.get("event_type"), limit=32)
                    source = _text(event.get("source"), limit=24)
                    symbol = f" · {_text(event.get('symbol'), limit=16)}" if event.get("symbol") else ""
                    message = _text(event.get("message"), limit=120)
                    system.append(f"  {timestamp} {level:<8} {event_type} {source}{symbol}")
                    system.append(f"    {message}")
            self._view_content["system"] = "\n".join(system)

        self._last_refresh_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        self._populate_positions_table()
        self._refresh_status_strip()

    def _render_view(self) -> None:
        title, subtitle = self.VIEW_LABELS[self.current_view]
        self.query_one("#view-heading", Static).update(title)
        self.query_one("#view-subheading", Static).update(subtitle)
        self.query_one("#content", Static).update(self._view_content[self.current_view])
        table = self.query_one("#positions-table", DataTable)
        snapshot = self._portfolio_snapshot
        positions = snapshot.value.get("positions") if snapshot and isinstance(snapshot.value, dict) else None
        table.display = (
            self.current_view == "portfolio"
            and snapshot is not None
            and snapshot.status not in {
                providers.SourceStatus.INVALID,
                providers.SourceStatus.UNAVAILABLE,
                providers.SourceStatus.EMPTY,
            }
            and isinstance(snapshot.value, dict)
            and snapshot.value.get("positions_status") == "OK"
            and isinstance(positions, list)
            and bool(positions)
        )
        nav_ids = {
            "overview": "nav-overview",
            "portfolio": "nav-portfolio",
            "orders": "nav-orders",
            "system": "nav-system",
        }
        for view, widget_id in nav_ids.items():
            widget = self.query_one(f"#{widget_id}", Static)
            widget.set_classes("nav-item active" if view == self.current_view else "nav-item")

    def _show_view(self, view: str) -> None:
        self.current_view = view
        self._render_view()

    def action_show_overview(self) -> None:
        self._show_view("overview")

    def action_show_portfolio(self) -> None:
        self._show_view("portfolio")

    def action_show_orders(self) -> None:
        self._show_view("orders")

    def action_show_system(self) -> None:
        self._show_view("system")

    def action_refresh_view(self) -> None:
        self._refresh_local_data()
        self._render_view()
        self.notify(
            "Local monitoring sources re-read. No trading or broker actions were performed.",
            title="Read-only refresh",
        )

    def action_show_help(self) -> None:
        self.notify(
            "1 Overview  |  2 Portfolio  |  3 Orders & executions  |  "
            "4 System  |  R Refresh (local read-only)  |  ? Help  |  Q Quit",
            title="Keyboard map",
            timeout=10,
        )
