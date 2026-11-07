"""Read-only Textual shell for the STMAA terminal dashboard.

This first UI stage intentionally has no data providers. It must remain
independent of broker, trading, PPO, and execution modules.
"""
from __future__ import annotations

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import Footer, Static


_VIEW_CONTENT = {
    "overview": (
        "[b]OVERVIEW[/b]\n\n"
        "[#e0a84b]LIVE DATA NOT CONNECTED[/]\n"
        "The dashboard shell is active. Snapshot, account, risk, equity-history "
        "and event providers are introduced in a later implementation stage.\n\n"
        "Equity: N/A — NOT CONNECTED\n"
        "Cash: N/A — NOT CONNECTED\n"
        "Gross exposure: N/A — NOT CONNECTED\n"
        "Snapshot age: N/A — NOT CONNECTED"
    ),
    "portfolio": (
        "[b]PORTFOLIO[/b]\n\n"
        "[#e0a84b]BROKER SNAPSHOT NOT CONNECTED[/]\n"
        "No position or account values are queried or inferred in this shell stage.\n\n"
        "A missing snapshot is not an empty portfolio."
    ),
    "orders": (
        "[b]ORDERS & EXECUTIONS[/b]\n\n"
        "[#e0a84b]ORDER PROVIDERS NOT CONNECTED[/]\n"
        "Broker open orders, broker-confirmed executions and local submission "
        "intents will be displayed as separate data classes in later stages.\n\n"
        "No order-management controls are available."
    ),
    "system": (
        "[b]SYSTEM[/b]\n\n"
        "[#e0a84b]HEALTH PROVIDERS NOT CONNECTED[/]\n"
        "Process, PPO artifact, market-data, broker-read-only and SQLite health "
        "indicators are not yet connected.\n\n"
        "Environment: UNKNOWN\n"
        "Dashboard access: READ-ONLY"
    ),
}


class STMAADashboard(App):
    """Keyboard-driven terminal shell; no broker or trading integration."""

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
            yield Static(_VIEW_CONTENT[self.current_view], id="content", markup=True)
        yield Footer()

    def on_mount(self) -> None:
        self._update_width_warning()
        self._render_view()

    def on_resize(self, event) -> None:
        self._update_width_warning(event.size.width)

    def _update_width_warning(self, width: int | None = None) -> None:
        if width is None:
            width = self.size.width
        warning = self.query_one("#size-warning", Static)
        warning.display = width < 80

    def _render_view(self) -> None:
        self.query_one("#content", Static).update(_VIEW_CONTENT[self.current_view])

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
        # This stage has no providers: refreshing only redraws the current view.
        self._render_view()
        self.notify(
            "Shell redrawn. Live data providers are not part of this implementation stage.",
            title="Read-only refresh",
        )

    def action_show_help(self) -> None:
        self.notify(
            "1 Overview  |  2 Portfolio  |  3 Orders & executions  |  "
            "4 System  |  R Refresh (read-only)  |  ? Help  |  Q Quit",
            title="Keyboard map",
            timeout=10,
        )
