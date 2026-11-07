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
from textual.containers import Vertical
from textual.widgets import Footer, Static

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
    seconds = max(0, int((datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds()))
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
    return f"[b]{escape(label)}[/b]  [{escape(result.status.value)}]{observed}{detail}"


def _snapshot_lines(label: str, result: providers.SourceResult[dict[str, Any]]) -> list[str]:
    lines = [_source_header(label, result)]
    value = result.value if isinstance(result.value, dict) else {}
    if result.status in {providers.SourceStatus.MISSING, providers.SourceStatus.INVALID, providers.SourceStatus.UNAVAILABLE, providers.SourceStatus.EMPTY}:
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


class STMAADashboard(App):
    """Keyboard-driven local monitoring dashboard; no broker integration."""

    TITLE = "STMAA Institutional Terminal"
    SUB_TITLE = "STRUCTURAL TRADING MULTI-AGENT ARCHITECTURE"

    CSS = """
    Screen {
        background: #0b1118;
        color: #e6edf3;
    }
    #masthead {
        height: 3;
        padding: 0 2;
        background: #111c27;
        color: #e6edf3;
        text-style: bold;
        content-align: left middle;
    }
    #status-strip {
        height: 2;
        padding: 0 2;
        background: #152331;
        color: #b9c8d5;
        content-align: left middle;
    }
    #navigation {
        height: 2;
        padding: 0 2;
        color: #55c5c1;
        content-align: left middle;
    }
    #size-warning {
        height: 2;
        padding: 0 2;
        color: #f0c674;
        background: #382b16;
        content-align: left middle;
        display: none;
    }
    #content {
        height: 1fr;
        margin: 0 1;
        padding: 1 2;
        border: round #294052;
        background: #0e1721;
        color: #dce6ee;
        overflow-y: auto;
    }
    Footer {
        background: #152331;
        color: #dce6ee;
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

    def __init__(self) -> None:
        super().__init__()
        self.current_view = "overview"
        self._view_content = {
            "overview": "Loading approved local monitoring sources…",
            "portfolio": (
                "[b]PORTFOLIO[/b]\n\n"
                "[#e0a84b]POSITIONS NOT CONNECTED[/]\n"
                "The approved local-data phase does not yet expose a verified position view. "
                "No broker positions are queried or inferred."
            ),
            "orders": (
                "[b]ORDERS & EXECUTIONS[/b]\n\n"
                "[#e0a84b]BROKER ORDERS NOT CONNECTED[/]\n"
                "This local-only dashboard does not query broker open orders or fills. "
                "Local submission intent and broker-confirmed execution evidence are not interchangeable."
            ),
            "system": "Loading local system history…",
        }

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(
                "STMAA  |  STRUCTURAL TRADING MULTI-AGENT ARCHITECTURE",
                id="masthead",
                markup=False,
            )
            yield Static(
                "ENVIRONMENT: UNKNOWN     ACCESS: READ-ONLY DASHBOARD",
                id="status-strip",
                markup=False,
            )
            yield Static(
                "1 OVERVIEW    2 PORTFOLIO    3 ORDERS & EXECUTIONS    4 SYSTEM",
                id="navigation",
                markup=False,
            )
            yield Static(
                "Terminal width below 80 columns. Expand the terminal for the full layout.",
                id="size-warning",
                markup=False,
            )
            yield Static(self._view_content[self.current_view], id="content", markup=True)
        yield Footer()

    def on_mount(self) -> None:
        self._update_width_warning()
        self._refresh_local_data()
        self._render_view()

    def on_resize(self, event) -> None:
        self._update_width_warning(event.size.width)

    def _update_width_warning(self, width: int | None = None) -> None:
        if width is None:
            width = self.size.width
        self.query_one("#size-warning", Static).display = width < 80

    def _refresh_local_data(self) -> None:
        json_snapshot = _safe_read(
            lambda: providers.read_json_snapshot(max_age_seconds=SNAPSHOT_MAX_AGE_SECONDS),
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
                "[b]LATEST LOCAL RUN[/b]",
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
                    symbol = f" [{_text(event.get('symbol'), limit=16)}]" if event.get("symbol") else ""
                    message = _text(event.get("message"), limit=120)
                    system.append(f"  {timestamp} {level:<8} {event_type} {source}{symbol}")
                    system.append(f"    {message}")
            self._view_content["system"] = "\n".join(system)

    def _render_view(self) -> None:
        self.query_one("#content", Static).update(self._view_content[self.current_view])

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
