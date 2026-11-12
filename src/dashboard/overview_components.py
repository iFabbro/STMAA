"""Composed, provenance-preserving Overview widgets for the local dashboard.

This module formats only values already returned by the approved local
monitoring providers. It performs no I/O and derives no financial metrics.
"""
from __future__ import annotations

from typing import Any, Callable

from rich.markup import escape
from textual.app import ComposeResult
from textual.containers import Grid, Vertical
from textual.widgets import Static

from . import local_providers as providers


class OverviewView(Vertical):
    """Six persisted KPIs, separate source panels, and bounded local events."""

    DEFAULT_CSS = """
    OverviewView {
        width: 100%;
        height: auto;
    }
    #overview-disclaimer {
        height: auto;
        padding: 0 1 1 1;
        color: #A9BAC8;
    }
    #overview-source-quality {
        height: auto;
        margin: 0 0 1 0;
        padding: 1 2;
        border: round #294052;
        background: #111C27;
        color: #E6EDF3;
    }
    #overview-kpis {
        grid-size: 3 2;
        grid-columns: 1fr 1fr 1fr;
        grid-gutter: 1 1;
        height: auto;
        margin: 0 0 1 0;
    }
    .overview-kpi {
        height: auto;
        min-height: 6;
        padding: 1 1;
        border: round #294052;
        background: #0E1721;
        color: #E6EDF3;
    }
    .overview-panel {
        height: auto;
        margin: 0 0 1 0;
        padding: 1 2;
        border: round #294052;
        background: #0E1721;
        color: #E6EDF3;
    }
    """

    KPI_SPECS = (
        ("portfolio-value", "PORTFOLIO VALUE", "portfolio_value"),
        ("equity", "EQUITY", "equity"),
        ("cash", "CASH", "cash"),
        ("drawdown", "DRAWDOWN", "drawdown"),
        ("exposure", "EXPOSURE", "exposure"),
        ("regime", "REGIME", "regime"),
    )

    def compose(self) -> ComposeResult:
        yield Static(
            "Persisted local observations only. Values are not live broker/account truth.",
            id="overview-disclaimer",
            markup=False,
        )
        yield Static(
            "SOURCE AVAILABILITY: JSON=UNKNOWN | SQLITE=UNKNOWN | EVENTS=UNKNOWN",
            id="overview-source-quality",
            markup=False,
        )
        with Grid(id="overview-kpis"):
            for metric_id, label, _field in self.KPI_SPECS:
                yield Static(
                    f"[b]{label}[/b]\nNOT PERSISTED\nSTATUS: UNKNOWN",
                    id=f"overview-kpi-{metric_id}",
                    classes="overview-kpi",
                )
        yield Static(
            "MONITORING JSON · hub_state.json\nSTATUS: UNKNOWN",
            id="overview-json-panel",
            classes="overview-panel",
        )
        yield Static(
            "SQLITE SNAPSHOT · stmaa.db\nSTATUS: UNKNOWN",
            id="overview-sqlite-panel",
            classes="overview-panel",
        )
        yield Static(
            "RECENT LOCAL EVENTS\nSTATUS: UNKNOWN",
            id="overview-events-panel",
            classes="overview-panel",
        )

    def on_mount(self) -> None:
        """Apply the initial KPI grid width after the widget is mounted."""
        self._update_kpi_grid(self.size.width)

    def on_resize(self, event) -> None:
        """Keep KPI cards readable without relying on unsupported CSS media rules."""
        self._update_kpi_grid(event.size.width)

    def _update_kpi_grid(self, width: int) -> None:
        grid = self.query_one("#overview-kpis", Grid)
        columns = 2 if width < 100 else 3
        grid.styles.grid_size_columns = columns
        grid.styles.grid_size_rows = 3 if columns == 2 else 2
        grid.styles.grid_columns = "1fr 1fr" if columns == 2 else "1fr 1fr 1fr"

    @staticmethod
    def _status(result: Any) -> str:
        if isinstance(result, providers.SourceResult):
            return result.status.value
        return "UNKNOWN"

    @staticmethod
    def _snapshot_panel(
        label: str,
        result: Any,
        fields: tuple[tuple[str, str], ...],
        *,
        format_value: Callable[..., str],
        format_age: Callable[[str | None], str],
    ) -> str:
        if not isinstance(result, providers.SourceResult):
            return f"[b]{escape(label)}[/b]\nSTATUS: UNKNOWN"
        lines = [
            f"[b]{escape(label)}[/b]",
            f"STATUS: {escape(result.status.value)}",
        ]
        if result.observed_at:
            lines.append(
                f"OBSERVED: {escape(result.observed_at)} · {escape(format_age(result.observed_at))}"
            )
        elif result.status not in {providers.SourceStatus.INVALID, providers.SourceStatus.UNAVAILABLE}:
            lines.append("OBSERVED: NOT PERSISTED · age unknown")
        if result.detail:
            lines.append(f"DETAIL: {escape(result.detail)}")
        if result.status in {
            providers.SourceStatus.INVALID,
            providers.SourceStatus.UNAVAILABLE,
            providers.SourceStatus.EMPTY,
        }:
            lines.append("VALUES: NOT AVAILABLE")
            return "\n".join(lines)
        value = result.value if isinstance(result.value, dict) else {}
        if not value:
            lines.append("VALUES: NOT PERSISTED")
            return "\n".join(lines)
        for label_text, key in fields:
            lines.append(f"{label_text}: {format_value(value.get(key))}")
        if value.get("source") is not None:
            lines.append(f"Stored source: {format_value(value.get('source'))}")
        if value.get("run_id") is not None:
            lines.append(f"Run ID: {format_value(value.get('run_id'), limit=48)}")
        return "\n".join(lines)

    @staticmethod
    def _event_panel(
        result: Any,
        *,
        format_value: Callable[..., str],
        format_age: Callable[[str | None], str],
    ) -> str:
        if not isinstance(result, providers.SourceResult):
            return "[b]RECENT LOCAL EVENTS[/b]\nSTATUS: UNKNOWN"
        lines = [
            "[b]RECENT LOCAL EVENTS[/b]",
            f"STATUS: {escape(result.status.value)}",
        ]
        if result.observed_at:
            lines.append(
                f"LATEST EVENT: {escape(result.observed_at)} · {escape(format_age(result.observed_at))}"
            )
        if result.detail:
            lines.append(f"DETAIL: {escape(result.detail)}")
        if result.status in {
            providers.SourceStatus.INVALID,
            providers.SourceStatus.UNAVAILABLE,
            providers.SourceStatus.MISSING,
        }:
            lines.append("EVENTS: NOT AVAILABLE")
            return "\n".join(lines)
        if result.status is providers.SourceStatus.EMPTY:
            if result.value == []:
                lines.append("NO EVENTS IN THIS PERSISTED SNAPSHOT")
            else:
                lines.append("EVENT COLLECTION: UNKNOWN")
            return "\n".join(lines)
        if not isinstance(result.value, list):
            lines.append("EVENT COLLECTION: INVALID")
            return "\n".join(lines)
        if not result.value:
            lines.append("EVENT COLLECTION: UNKNOWN — empty payload with OK status")
            return "\n".join(lines)
        for event in result.value:
            if not isinstance(event, dict):
                continue
            timestamp = format_value(event.get("ts"), limit=32)
            level = format_value(event.get("level"), limit=12)
            event_type = format_value(event.get("event_type"), limit=32)
            source = format_value(event.get("source"), limit=24)
            symbol = f" · {format_value(event.get('symbol'), limit=16)}" if event.get("symbol") else ""
            message = format_value(event.get("message"), limit=120)
            lines.append(f"{timestamp} {level} {event_type} · {source}{symbol}")
            lines.append(f"  {message}")
        return "\n".join(lines)

    def update_data(
        self,
        json_snapshot: Any,
        sqlite_snapshot: Any,
        recent_events: Any,
        *,
        format_value: Callable[..., str],
        format_age: Callable[[str | None], str],
    ) -> None:
        """Update widgets from one provider refresh without performing I/O."""
        sources = {"json": json_snapshot, "sqlite": sqlite_snapshot}
        for metric_id, label, field in self.KPI_SPECS:
            source_lines = []
            for source_key in ("json", "sqlite"):
                result = sources[source_key]
                value: Any = None
                if (
                    isinstance(result, providers.SourceResult)
                    and result.status not in {
                        providers.SourceStatus.INVALID,
                        providers.SourceStatus.UNAVAILABLE,
                        providers.SourceStatus.EMPTY,
                    }
                    and isinstance(result.value, dict)
                ):
                    value = result.value.get(field)
                status = self._status(result)
                observed = (
                    format_age(result.observed_at)
                    if isinstance(result, providers.SourceResult) and result.observed_at
                    else "age unknown"
                )
                source_lines.append(
                    f"{source_key.upper()}: {format_value(value)} · {status} · {observed}"
                )
            self.query_one(f"#overview-kpi-{metric_id}", Static).update(
                f"[b]{label}[/b]\n" + "\n".join(source_lines)
            )

        quality = (
            "SOURCE AVAILABILITY — "
            f"JSON: {self._status(json_snapshot)} · "
            f"SQLITE: {self._status(sqlite_snapshot)} · "
            f"EVENTS: {self._status(recent_events)}"
        )
        self.query_one("#overview-source-quality", Static).update(quality)

        self.query_one("#overview-json-panel", Static).update(
            self._snapshot_panel(
                "MONITORING JSON · hub_state.json",
                json_snapshot,
                (
                    ("Portfolio value", "portfolio_value"),
                    ("Equity", "equity"),
                    ("Cash", "cash"),
                    ("Drawdown", "drawdown"),
                    ("Exposure", "exposure"),
                    ("Regime", "regime"),
                ),
                format_value=format_value,
                format_age=format_age,
            )
        )
        self.query_one("#overview-sqlite-panel", Static).update(
            self._snapshot_panel(
                "SQLITE SNAPSHOT · stmaa.db",
                sqlite_snapshot,
                (
                    ("Portfolio value", "portfolio_value"),
                    ("Equity", "equity"),
                    ("Cash", "cash"),
                    ("Drawdown", "drawdown"),
                    ("Exposure", "exposure"),
                    ("Regime", "regime"),
                ),
                format_value=format_value,
                format_age=format_age,
            )
        )
        self.query_one("#overview-events-panel", Static).update(
            self._event_panel(
                recent_events,
                format_value=format_value,
                format_age=format_age,
            )
        )
