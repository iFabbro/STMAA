from src.dashboard import app as dashboard_app
from src.dashboard.app import STMAADashboard
from src.dashboard.local_providers import SourceResult, SourceStatus
from textual.containers import Grid
from textual.widgets import Static


def _result(status=SourceStatus.OK, value=None, observed_at="2026-11-07T10:00:00+00:00"):
    return SourceResult(status, "overview-fixture", value, observed_at)


def _patch_sources(monkeypatch, *, json_result=None, sqlite_result=None, events=None):
    json_result = json_result or _result(value={})
    sqlite_result = sqlite_result or _result(value={})
    events = events or _result(value=[])

    monkeypatch.setattr(dashboard_app.providers, "read_json_snapshot", lambda **kwargs: json_result)
    monkeypatch.setattr(dashboard_app.providers, "read_latest_snapshot", lambda **kwargs: sqlite_result)
    monkeypatch.setattr(dashboard_app.providers, "read_latest_run", lambda: _result(value={}))
    monkeypatch.setattr(dashboard_app.providers, "read_recent_events", lambda **kwargs: events)
    monkeypatch.setattr(
        dashboard_app.providers,
        "read_persisted_broker_state",
        lambda **kwargs: _result(value={}),
    )


def _render(app, widget_id):
    return str(app.query_one(widget_id, Static).render())


def test_overview_composes_six_kpis_and_separate_source_panels(monkeypatch):
    _patch_sources(monkeypatch)
    app = STMAADashboard()

    async def run():
        async with app.run_test(size=(120, 36)):
            assert app.query_one("#overview-components")
            for metric in (
                "portfolio-value",
                "equity",
                "cash",
                "drawdown",
                "exposure",
                "regime",
            ):
                assert app.query_one(f"#overview-kpi-{metric}", Static)
            assert "MONITORING JSON" in _render(app, "#overview-json-panel")
            assert "SQLITE SNAPSHOT" in _render(app, "#overview-sqlite-panel")
            assert "RECENT LOCAL EVENTS" in _render(app, "#overview-events-panel")

    import asyncio
    asyncio.run(run())


def test_overview_kpis_preserve_json_and_sqlite_values_independently(monkeypatch):
    json_result = _result(value={
        "portfolio_value": 1100.0,
        "equity": 1090.0,
        "cash": 300.0,
        "drawdown": 0.01,
        "exposure": 0.20,
        "regime": "json-regime",
    })
    sqlite_result = _result(value={
        "portfolio_value": 1200.0,
        "equity": 1180.0,
        "cash": 400.0,
        "drawdown": 0.03,
        "exposure": 0.35,
        "regime": "sqlite-regime",
    })
    _patch_sources(monkeypatch, json_result=json_result, sqlite_result=sqlite_result)
    app = STMAADashboard()

    async def run():
        async with app.run_test(size=(120, 36)):
            portfolio_value = _render(app, "#overview-kpi-portfolio-value")
            assert "JSON:" in portfolio_value
            assert "1,100.00" in portfolio_value
            assert "SQLITE:" in portfolio_value
            assert "1,200.00" in portfolio_value
            regime = _render(app, "#overview-kpi-regime")
            assert "json-regime" in regime
            assert "sqlite-regime" in regime

    import asyncio
    asyncio.run(run())


def test_overview_does_not_replace_missing_sources_with_zero(monkeypatch):
    missing = _result(SourceStatus.MISSING, value=None, observed_at=None)
    _patch_sources(monkeypatch, json_result=missing, sqlite_result=missing)
    app = STMAADashboard()

    async def run():
        async with app.run_test(size=(120, 36)):
            for metric in ("portfolio-value", "equity", "cash", "drawdown", "exposure", "regime"):
                rendered = _render(app, f"#overview-kpi-{metric}")
                assert "MISSING" in rendered
                assert "0.00" not in rendered
                assert "UNKNOWN" in rendered or "None" in rendered

    import asyncio
    asyncio.run(run())


def test_overview_snapshot_panels_show_source_status_age_and_provenance(monkeypatch):
    json_result = _result(
        value={
            "portfolio_value": 1250.0,
            "equity": 1240.0,
            "cash": 500.0,
            "drawdown": 0.02,
            "exposure": 0.25,
            "regime": "risk_on",
            "source": "json-fixture",
            "run_id": "run-json-42",
        },
    )
    sqlite_result = _result(
        value={
            "portfolio_value": 1300.0,
            "equity": 1290.0,
            "cash": 600.0,
            "drawdown": 0.01,
            "exposure": 0.30,
            "regime": "risk_off",
            "source": "sqlite-fixture",
            "run_id": "run-sqlite-43",
        },
    )
    _patch_sources(monkeypatch, json_result=json_result, sqlite_result=sqlite_result)
    app = STMAADashboard()

    async def run():
        async with app.run_test(size=(120, 36)):
            json_panel = _render(app, "#overview-json-panel")
            sqlite_panel = _render(app, "#overview-sqlite-panel")
            assert "OK" in json_panel and "json-fixture" in json_panel
            assert "run-json-42" in json_panel
            assert "OBSERVED:" in json_panel
            assert "OK" in sqlite_panel and "sqlite-fixture" in sqlite_panel
            assert "run-sqlite-43" in sqlite_panel
            assert "OBSERVED:" in sqlite_panel

    import asyncio
    asyncio.run(run())


def test_overview_source_availability_and_events_distinguish_empty_from_missing(monkeypatch):
    missing_events = _result(SourceStatus.MISSING, value=None, observed_at=None)
    _patch_sources(
        monkeypatch,
        json_result=_result(SourceStatus.STALE, value={"equity": 900.0}),
        sqlite_result=_result(SourceStatus.UNAVAILABLE, value=None, observed_at=None),
        events=missing_events,
    )
    app = STMAADashboard()

    async def run():
        async with app.run_test(size=(120, 36)):
            quality = _render(app, "#overview-source-quality")
            assert "STALE" in quality
            assert "UNAVAILABLE" in quality
            assert "MISSING" in quality
            events = _render(app, "#overview-events-panel")
            assert "MISSING" in events
            assert "EVENTS: NOT AVAILABLE" in events

    import asyncio
    asyncio.run(run())


def test_overview_renders_bounded_local_event_fields(monkeypatch):
    events = _result(value=[
        {
            "ts": "2026-11-07T10:01:00+00:00",
            "level": "WARNING",
            "event_type": "risk_block",
            "source": "risk",
            "symbol": "AAPL",
            "message": "Exposure limit reached",
        },
    ])
    _patch_sources(monkeypatch, events=events)
    app = STMAADashboard()

    async def run():
        async with app.run_test(size=(120, 36)):
            rendered = _render(app, "#overview-events-panel")
            assert "risk_block" in rendered
            assert "WARNING" in rendered
            assert "AAPL" in rendered
            assert "Exposure limit reached" in rendered

    import asyncio
    asyncio.run(run())


def test_overview_kpi_grid_switches_between_two_and_three_columns(monkeypatch):
    _patch_sources(monkeypatch)
    app = STMAADashboard()

    async def run():
        async with app.run_test(size=(120, 36)) as pilot:
            grid = app.query_one("#overview-kpis", Grid)
            assert grid.styles.grid_size_columns == 3
            await pilot.resize_terminal(90, 36)
            assert grid.styles.grid_size_columns == 2
            assert grid.styles.grid_size_rows == 3
            await pilot.resize_terminal(120, 36)
            assert grid.styles.grid_size_columns == 3
            assert grid.styles.grid_size_rows == 2

    import asyncio
    asyncio.run(run())


def test_overview_refresh_reloads_only_read_only_providers(monkeypatch):
    calls = {"json": 0, "sqlite": 0, "events": 0, "run": 0, "broker_state": 0}

    def read_json(**kwargs):
        calls["json"] += 1
        return _result(value={"equity": 1000.0})

    def read_sqlite(**kwargs):
        calls["sqlite"] += 1
        return _result(value={"equity": 1000.0})

    def read_events(**kwargs):
        calls["events"] += 1
        return _result(value=[])

    def read_run():
        calls["run"] += 1
        return _result(value={})

    def read_broker_state(**kwargs):
        calls["broker_state"] += 1
        return _result(value={})

    monkeypatch.setattr(dashboard_app.providers, "read_json_snapshot", read_json)
    monkeypatch.setattr(dashboard_app.providers, "read_latest_snapshot", read_sqlite)
    monkeypatch.setattr(dashboard_app.providers, "read_recent_events", read_events)
    monkeypatch.setattr(dashboard_app.providers, "read_latest_run", read_run)
    monkeypatch.setattr(dashboard_app.providers, "read_persisted_broker_state", read_broker_state)
    app = STMAADashboard()

    async def run():
        async with app.run_test(size=(120, 36)) as pilot:
            before = dict(calls)
            await pilot.press("r")
            assert all(calls[key] == before[key] + 1 for key in calls)
            assert "NO BROKER ACTIONS" in str(app.query_one("#status-strip", Static).render())

    import asyncio
    asyncio.run(run())


def test_overview_is_visible_only_in_overview_navigation(monkeypatch):
    _patch_sources(monkeypatch)
    app = STMAADashboard()

    async def run():
        async with app.run_test(size=(120, 36)) as pilot:
            overview = app.query_one("#overview-components")
            legacy_content = app.query_one("#content", Static)
            assert overview.display is True
            assert legacy_content.display is False
            await pilot.press("2")
            assert overview.display is False
            assert legacy_content.display is True
            await pilot.press("1")
            assert overview.display is True
            assert legacy_content.display is False

    import asyncio
    asyncio.run(run())
