from pathlib import Path

from textual.widgets import Static

from src.dashboard.app import STMAADashboard


def test_dashboard_keyboard_navigation_switches_all_four_views():
    app = STMAADashboard()

    async def scenario(pilot):
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

    async def run():
        async with app.run_test(size=(120, 36)) as pilot:
            await scenario(pilot)

    import asyncio

    asyncio.run(run())


def test_dashboard_displays_narrow_terminal_warning():
    app = STMAADashboard()

    async def run():
        async with app.run_test(size=(79, 24)):
            warning = app.query_one("#size-warning", Static)
            assert warning.display is True

    import asyncio

    asyncio.run(run())


def test_dashboard_refresh_help_and_quit_are_local_ui_actions():
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


def test_dashboard_module_has_no_broker_or_execution_dependencies():
    source = Path("src/dashboard/app.py").read_text(encoding="utf-8").lower()
    forbidden = (
        "alpaca",
        "brokerexecutor",
        "submit_order",
        "cancel_order",
        "replace_order",
        "close_position",
        "liquidate",
        "src.execution",
        "trade.py",
    )
    assert all(token not in source for token in forbidden)
