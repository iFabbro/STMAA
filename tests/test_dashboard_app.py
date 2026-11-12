from pathlib import Path

from textual.widgets import Static

from src.dashboard import app as dashboard_app
from src.dashboard.app import STMAADashboard
from src.dashboard.local_providers import SourceResult, SourceStatus


def _result(status=SourceStatus.OK, value=None, observed_at="2026-11-07T10:00:00+00:00", detail=None):
    return SourceResult(status, "fixture", value, observed_at, detail)


def test_dashboard_overview_renders_both_local_snapshot_sources(monkeypatch):
    monkeypatch.setattr(
        dashboard_app.providers,
        "read_json_snapshot",
        lambda **kwargs: _result(value={
            "equity": 1200.5, "cash": 300.0, "exposure": 0.25, "regime": "risk_on",
            "timestamp": "2026-11-07T10:00:00+00:00",
        }),
    )
    monkeypatch.setattr(
        dashboard_app.providers,
        "read_latest_snapshot",
        lambda **kwargs: _result(value={
            "portfolio_value": 1200.5, "equity": 1200.5, "cash": 300.0,
            "drawdown": 0.02, "exposure": 0.25, "source": "paper-cycle",
            "run_id": "run-123",
        }),
    )
    monkeypatch.setattr(dashboard_app.providers, "read_latest_run", lambda: _result(value={}))
    monkeypatch.setattr(dashboard_app.providers, "read_recent_events", lambda **kwargs: _result(value=[]))

    app = STMAADashboard()

    async def run():
        async with app.run_test(size=(120, 36)) as pilot:
            content = str(app.query_one("#content", Static).render())
            assert "OVERVIEW" in content
            assert "Monitoring JSON" in content
            assert "SQLite snapshot" in content
            assert "1,200.50" in content
            assert "risk_on" in content
            await pilot.press("4")
            assert app.current_view == "system"

    import asyncio
    asyncio.run(run())


def test_dashboard_system_renders_run_metadata_and_bounded_local_events(monkeypatch):
    monkeypatch.setattr(dashboard_app.providers, "read_json_snapshot", lambda **kwargs: _result(value={}))
    monkeypatch.setattr(dashboard_app.providers, "read_latest_snapshot", lambda **kwargs: _result(value={}))
    monkeypatch.setattr(
        dashboard_app.providers,
        "read_latest_run",
        lambda: _result(value={
            "status": "running", "mode": "paper", "started_at": "2026-11-07T10:00:00+00:00",
            "ended_at": None, "branch": "main", "commit_sha": "abc123",
        }),
    )
    monkeypatch.setattr(
        dashboard_app.providers,
        "read_recent_events",
        lambda **kwargs: _result(value=[{
            "ts": "2026-11-07T10:01:00+00:00", "level": "WARNING",
            "event_type": "risk_block", "source": "risk", "symbol": "AAPL",
            "message": "New exposure blocked",
        }]),
    )

    app = STMAADashboard()

    async def run():
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.press("4")
            content = str(app.query_one("#content", Static).render())
            assert "LATEST LOCAL RUN" in content
            assert "Stored status" in content
            assert "running" in content
            assert "does not prove that a process is currently alive" in content
            assert "risk_block" in content
            assert "AAPL" in content
            assert "New exposure blocked" in content

    import asyncio
    asyncio.run(run())


def test_refresh_reloads_providers_without_touching_audit_lifecycle(monkeypatch):
    calls = {"json": 0, "snapshot": 0, "run": 0, "events": 0}
    monkeypatch.setattr(dashboard_app.providers, "read_json_snapshot", lambda **kwargs: (calls.__setitem__("json", calls["json"] + 1) or _result(value={})))
    monkeypatch.setattr(dashboard_app.providers, "read_latest_snapshot", lambda **kwargs: (calls.__setitem__("snapshot", calls["snapshot"] + 1) or _result(value={})))
    monkeypatch.setattr(dashboard_app.providers, "read_latest_run", lambda: (calls.__setitem__("run", calls["run"] + 1) or _result(value={})))
    monkeypatch.setattr(dashboard_app.providers, "read_recent_events", lambda **kwargs: (calls.__setitem__("events", calls["events"] + 1) or _result(value=[])))

    app = STMAADashboard()

    async def run():
        async with app.run_test(size=(120, 36)) as pilot:
            initial = dict(calls)
            await pilot.press("r")
            assert all(calls[key] == initial[key] + 1 for key in calls)

    import asyncio
    asyncio.run(run())


def test_missing_sources_are_not_rendered_as_zero_or_empty_portfolio(monkeypatch):
    missing = _result(SourceStatus.MISSING, value=None, observed_at=None, detail="source is missing")
    monkeypatch.setattr(dashboard_app.providers, "read_json_snapshot", lambda **kwargs: missing)
    monkeypatch.setattr(dashboard_app.providers, "read_latest_snapshot", lambda **kwargs: missing)
    monkeypatch.setattr(dashboard_app.providers, "read_latest_run", lambda: _result(SourceStatus.EMPTY, value=[]))
    monkeypatch.setattr(dashboard_app.providers, "read_recent_events", lambda **kwargs: _result(SourceStatus.EMPTY, value=[]))

    app = STMAADashboard()

    async def run():
        async with app.run_test(size=(120, 36)):
            content = str(app.query_one("#content", Static).render())
            assert "MISSING" in content
            assert "0.00" not in content
            app.action_show_portfolio()
            portfolio = str(app.query_one("#content", Static).render())
            assert "POSITIONS: UNKNOWN" in portfolio
            assert "approved snapshot unavailable" in portfolio
            assert "NO POSITIONS IN THIS PERSISTED SNAPSHOT" not in portfolio

    import asyncio
    asyncio.run(run())


def test_dashboard_app_has_no_broker_or_execution_dependencies():
    source = Path("src/dashboard/app.py").read_text(encoding="utf-8").lower()
    forbidden = (
        "alpaca", "brokerexecutor", "submit_order", "cancel_order",
        "replace_order", "close_position", "liquidate", "src.execution", "trade.py",
    )
    assert all(token not in source for token in forbidden)
    assert "start_run(" not in source
    assert "log_event(" not in source
    assert "end_run(" not in source


def _patch_empty_sources(monkeypatch):
    monkeypatch.setattr(dashboard_app.providers, "read_json_snapshot", lambda **kwargs: _result(value={}))
    monkeypatch.setattr(dashboard_app.providers, "read_latest_snapshot", lambda **kwargs: _result(value={}))
    monkeypatch.setattr(dashboard_app.providers, "read_latest_run", lambda: _result(value={}))
    monkeypatch.setattr(dashboard_app.providers, "read_recent_events", lambda **kwargs: _result(value=[]))


def test_dashboard_keyboard_navigation_switches_all_four_views(monkeypatch):
    _patch_empty_sources(monkeypatch)
    app = STMAADashboard()

    async def run():
        async with app.run_test(size=(120, 36)) as pilot:
            assert app.current_view == "overview"
            await pilot.press("2")
            assert app.current_view == "portfolio"
            assert "PORTFOLIO" in str(app.query_one("#content", Static).render())
            await pilot.press("3")
            assert app.current_view == "orders"
            assert "ORDERS & EXECUTIONS" in str(app.query_one("#content", Static).render())
            await pilot.press("4")
            assert app.current_view == "system"
            assert "SYSTEM" in str(app.query_one("#content", Static).render())
            await pilot.press("1")
            assert app.current_view == "overview"

    import asyncio
    asyncio.run(run())


def test_dashboard_displays_narrow_terminal_warning(monkeypatch):
    _patch_empty_sources(monkeypatch)
    app = STMAADashboard()

    async def run():
        async with app.run_test(size=(79, 24)):
            assert app.query_one("#size-warning", Static).display is True

    import asyncio
    asyncio.run(run())


def test_dashboard_refresh_help_and_quit_are_local_ui_actions(monkeypatch):
    _patch_empty_sources(monkeypatch)
    app = STMAADashboard()

    async def run():
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press("r")
            assert app.current_view == "overview"
            await pilot.press("question_mark")
            assert app.current_view == "overview"
            await pilot.press("q")
            assert app.is_running is False

    import asyncio
    asyncio.run(run())


def test_snapshot_values_without_timestamp_remain_visible_as_missing(monkeypatch):
    missing_timestamp = _result(
        SourceStatus.MISSING,
        value={"equity": 875.0, "cash": 125.0},
        observed_at=None,
        detail="snapshot timestamp is missing",
    )
    monkeypatch.setattr(dashboard_app.providers, "read_json_snapshot", lambda **kwargs: missing_timestamp)
    monkeypatch.setattr(dashboard_app.providers, "read_latest_snapshot", lambda **kwargs: missing_timestamp)
    monkeypatch.setattr(dashboard_app.providers, "read_latest_run", lambda: _result(value={}))
    monkeypatch.setattr(dashboard_app.providers, "read_recent_events", lambda **kwargs: _result(value=[]))

    app = STMAADashboard()

    async def run():
        async with app.run_test(size=(120, 36)):
            content = str(app.query_one("#content", Static).render())
            assert "MISSING" in content
            assert "875.00" in content
            assert "age unknown" not in content

    import asyncio
    asyncio.run(run())


def test_dashboard_portfolio_renders_persisted_positions_with_provenance(monkeypatch):
    snapshot = _result(value={
        "portfolio_value": 1250.0,
        "equity": 1240.0,
        "cash": 500.0,
        "positions_status": "OK",
        "positions_source": "alpaca_positions",
        "positions_snapshot_at": "2026-11-08T09:55:00+00:00",
        "positions": [{
            "symbol": "AAPL", "qty": 2.0, "market_value": 400.0,
            "avg_entry_price": 190.0, "unrealized_pl": 20.0,
        }],
    })
    monkeypatch.setattr(dashboard_app.providers, "read_json_snapshot", lambda **kwargs: snapshot)
    monkeypatch.setattr(dashboard_app.providers, "read_latest_snapshot", lambda **kwargs: _result(value={}))
    monkeypatch.setattr(dashboard_app.providers, "read_latest_run", lambda: _result(value={}))
    monkeypatch.setattr(dashboard_app.providers, "read_recent_events", lambda **kwargs: _result(value=[]))

    app = STMAADashboard()

    async def run():
        async with app.run_test(size=(140, 36)) as pilot:
            await pilot.press("2")
            content = str(app.query_one("#content", Static).render())
            assert "PERSISTED POSITION SNAPSHOT" in content
            assert "not a live broker query" in content
            assert "alpaca_positions" in content
            assert "AAPL" in content
            assert "LONG" in content
            assert "400.00" in content
            assert "20.00" in content

    import asyncio
    asyncio.run(run())


def test_dashboard_portfolio_distinguishes_empty_from_missing_positions(monkeypatch):
    missing = _result(value={"equity": 1000.0, "positions_status": "UNAVAILABLE", "positions": None})
    monkeypatch.setattr(dashboard_app.providers, "read_json_snapshot", lambda **kwargs: missing)
    monkeypatch.setattr(dashboard_app.providers, "read_latest_snapshot", lambda **kwargs: _result(value={}))
    monkeypatch.setattr(dashboard_app.providers, "read_latest_run", lambda: _result(value={}))
    monkeypatch.setattr(dashboard_app.providers, "read_recent_events", lambda **kwargs: _result(value=[]))
    app = STMAADashboard()

    async def run():
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.press("2")
            content = str(app.query_one("#content", Static).render())
            assert "POSITIONS: UNKNOWN" in content
            assert "NO POSITIONS IN THIS PERSISTED SNAPSHOT" not in content

    import asyncio
    asyncio.run(run())


def test_dashboard_portfolio_renders_independent_position_freshness_states():
    cases = (
        ("STALE", "STALE"),
        ("MISSING", "MISSING"),
        ("INVALID", "INVALID"),
        ("UNKNOWN", "UNKNOWN"),
        ("UNAVAILABLE", "UNKNOWN"),
    )
    for stored_status, expected_status in cases:
        result = _result(value={
            "equity": 1000.0,
            "positions_status": stored_status,
            "positions": None,
        })
        content = "\n".join(dashboard_app._portfolio_lines(result))
        assert f"POSITIONS: {expected_status}" in content
        assert "NO POSITIONS IN THIS PERSISTED SNAPSHOT" not in content


def test_dashboard_composes_terminal_regions_and_marks_active_navigation(monkeypatch):
    _patch_empty_sources(monkeypatch)
    app = STMAADashboard()

    async def run():
        async with app.run_test(size=(120, 36)) as pilot:
            for widget_id in (
                "#root", "#masthead", "#status-strip", "#navigation",
                "#size-warning", "#view-container", "#view-heading",
                "#view-subheading", "#content", "#positions-table",
            ):
                assert app.query_one(widget_id)
            assert app.query_one("#nav-overview", Static).has_class("active")
            await pilot.press("3")
            assert app.query_one("#nav-orders", Static).has_class("active")
            assert not app.query_one("#nav-overview", Static).has_class("active")
            assert "NO BROKER ACTIONS" in str(app.query_one("#status-strip", Static).render())

    import asyncio
    asyncio.run(run())


def test_dashboard_responsive_warning_boundary_and_navigation(monkeypatch):
    _patch_empty_sources(monkeypatch)
    app = STMAADashboard()

    async def run():
        async with app.run_test(size=(79, 24)) as pilot:
            assert app.query_one("#size-warning", Static).display is True
            await pilot.resize_terminal(80, 24)
            assert app.query_one("#size-warning", Static).display is False
            await pilot.resize_terminal(100, 30)
            assert app.query_one("#size-warning", Static).display is False
            await pilot.resize_terminal(120, 36)
            assert app.query_one("#size-warning", Static).display is False
            await pilot.resize_terminal(160, 45)
            assert app.query_one("#size-warning", Static).display is False
            await pilot.press("4")
            assert app.query_one("#nav-system", Static).has_class("active")

    import asyncio
    asyncio.run(run())


def test_portfolio_table_uses_only_verified_persisted_positions(monkeypatch):
    snapshot = _result(status=SourceStatus.STALE, value={
        "positions_status": "OK",
        "positions_source": "persisted-fixture",
        "positions": [{
            "symbol": "AAPL", "qty": 2, "avg_entry_price": 190.0,
            "market_value": 400.0, "unrealized_pl": 20.0,
        }],
    })
    monkeypatch.setattr(dashboard_app.providers, "read_json_snapshot", lambda **kwargs: snapshot)
    monkeypatch.setattr(dashboard_app.providers, "read_latest_snapshot", lambda **kwargs: _result(value={}))
    monkeypatch.setattr(dashboard_app.providers, "read_latest_run", lambda: _result(value={}))
    monkeypatch.setattr(dashboard_app.providers, "read_recent_events", lambda **kwargs: _result(value=[]))
    app = STMAADashboard()

    async def run():
        async with app.run_test(size=(140, 36)) as pilot:
            await pilot.press("2")
            table = app.query_one("#positions-table")
            assert table.display is True
            assert table.row_count == 1
            assert "AAPL" in str(table.get_row_at(0))
            await pilot.press("1")
            assert table.display is False

    import asyncio
    asyncio.run(run())


def test_positions_table_stays_hidden_for_missing_or_empty_positions(monkeypatch):
    missing = _result(value={"positions_status": "UNAVAILABLE", "positions": None})
    monkeypatch.setattr(dashboard_app.providers, "read_json_snapshot", lambda **kwargs: missing)
    monkeypatch.setattr(dashboard_app.providers, "read_latest_snapshot", lambda **kwargs: _result(value={}))
    monkeypatch.setattr(dashboard_app.providers, "read_latest_run", lambda: _result(value={}))
    monkeypatch.setattr(dashboard_app.providers, "read_recent_events", lambda **kwargs: _result(value=[]))
    app = STMAADashboard()

    async def run():
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.press("2")
            table = app.query_one("#positions-table")
            assert table.display is False
            assert table.row_count == 0

    import asyncio
    asyncio.run(run())
