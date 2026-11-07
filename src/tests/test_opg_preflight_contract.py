"""Executable RW-08.4 contract tests for read-only OPG operational preflight."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest


def _plan(*, target_weights=None, close_prices=None, current_positions=None):
    from src.execution.opg_sizing import build_opg_sizing_plan

    target_weights = target_weights or {"AAPL": 0.30}
    close_prices = close_prices or {"AAPL": 100}
    current_positions = current_positions or {"AAPL": 0}
    return build_opg_sizing_plan(
        equity=1000,
        target_weights=target_weights,
        close_prices=close_prices,
        current_positions=current_positions,
    )


def _asset(symbol="AAPL", *, status="active", tradable=True, asset_class="us_equity", attributes=None):
    return SimpleNamespace(
        symbol=symbol,
        status=status,
        tradable=tradable,
        asset_class=asset_class,
        attributes=[] if attributes is None else attributes,
        fractionable=True,
        shortable=True,
    )


def _executor(plan, *, buying_power=1000, equity=1000, positions=None, assets=None, account_overrides=None):
    from src.execution.broker_executor import BrokerExecutor

    executor = BrokerExecutor.__new__(BrokerExecutor)
    executor.state = {
        "account_mode": "paper",
        "account_id": "paper-account-1",
        "opg_batches": {},
    }
    executor.paper_trading = True
    executor._current_account_identity_verified = True
    account = {
        "account_data_fresh": True,
        "account_data_source": "alpaca_account",
        "account_snapshot_at": "2026-11-04T14:00:00+00:00",
        "account_id": "paper-account-1",
        "account_identity_verified": True,
        "status": "active",
        "trading_blocked": False,
        "equity": float(equity),
        "buying_power": buying_power,
    }
    account.update(account_overrides or {})
    executor.get_account_status = Mock(return_value=account)
    broker_positions = positions or {
        symbol: {"qty": float(asset.current_quantity), "market_value": 0.0}
        for symbol, asset in plan.assets.items()
        if asset.current_quantity
    }
    executor.get_positions = Mock(return_value=broker_positions)
    executor._get_open_orders = Mock(return_value=[])
    assets = assets or {symbol: _asset(symbol) for symbol, item in plan.assets.items() if item.delta_quantity}
    executor.trading_client = SimpleNamespace(
        get_asset=Mock(side_effect=lambda symbol: assets[symbol]),
        submit_order=Mock(side_effect=AssertionError("preflight must not submit")),
        cancel_order_by_id=Mock(side_effect=AssertionError("preflight must not cancel")),
        replace_order_by_id=Mock(side_effect=AssertionError("preflight must not replace")),
        close_position=Mock(side_effect=AssertionError("preflight must not close")),
    )
    return executor


def test_preflight_returns_verified_account_and_aggregate_buy_budget():
    plan = _plan()
    executor = _executor(plan, buying_power=500)

    report = executor.preflight_opg_plan(plan)

    assert report["approved"] is True
    assert report["account_id"] == "paper-account-1"
    assert report["estimated_buy_notional"] == 300
    assert report["buying_power"] == 500
    assert report["buying_power_headroom"] == 200
    executor.trading_client.get_asset.assert_called_once_with("AAPL")


@pytest.mark.parametrize(
    "overrides",
    [
        {"account_data_fresh": False},
        {"status": "inactive"},
        {"status": None},
        {"trading_blocked": True},
        {"trading_blocked": None},
        {"account_id": None},
        {"account_identity_verified": False},
        {"account_data_source": "persisted_cache"},
        {"account_snapshot_at": None},
        {"buying_power": None},
        {"buying_power": True},
        {"buying_power": float("nan")},
        {"buying_power": -1},
        {"equity": float("inf")},
    ],
)
def test_preflight_rejects_unverified_or_blocked_account(overrides):
    plan = _plan()
    executor = _executor(plan, account_overrides=overrides)

    with pytest.raises((RuntimeError, ValueError)):
        executor.preflight_opg_plan(plan)

    executor.trading_client.submit_order.assert_not_called()


def test_preflight_rejects_account_id_different_from_persisted_state():
    plan = _plan()
    executor = _executor(plan, account_overrides={"account_id": "different-paper-account"})

    with pytest.raises(RuntimeError):
        executor.preflight_opg_plan(plan)


def test_preflight_blocks_when_aggregate_buy_estimate_exceeds_buying_power():
    plan = _plan(
        target_weights={"AAPL": 0.30, "MSFT": 0.30},
        close_prices={"AAPL": 100, "MSFT": 100},
        current_positions={"AAPL": 0, "MSFT": 0},
    )
    executor = _executor(plan, buying_power=500)

    with pytest.raises(RuntimeError, match="buying power|budget"):
        executor.preflight_opg_plan(plan)

    executor.trading_client.submit_order.assert_not_called()


def test_preflight_counts_only_incremental_buys_and_excludes_sell_proceeds():
    plan = _plan(
        target_weights={"AAPL": 0.30, "MSFT": 0.00},
        close_prices={"AAPL": 100, "MSFT": 50},
        current_positions={"AAPL": 0, "MSFT": 4},
    )
    executor = _executor(
        plan,
        buying_power=300,
        positions={"MSFT": {"qty": 4.0, "market_value": 200.0}},
    )

    report = executor.preflight_opg_plan(plan)

    assert report["estimated_buy_notional"] == 300
    assert report["buy_orders"] == 1
    assert report["sell_orders"] == 1
    assert report["buying_power_headroom"] == 0


@pytest.mark.parametrize(
    "asset",
    [
        _asset(status="inactive"),
        _asset(tradable=False),
        _asset(asset_class="crypto"),
        _asset(attributes=["ipo"]),
        _asset(status=None),
        _asset(tradable=None),
        _asset(symbol="MSFT"),
    ],
)
def test_preflight_rejects_ineligible_buy_asset(asset):
    plan = _plan()
    executor = _executor(plan, assets={"AAPL": asset})

    with pytest.raises((RuntimeError, ValueError)):
        executor.preflight_opg_plan(plan)


def test_preflight_allows_active_nontradable_asset_only_for_verified_long_close():
    plan = _plan(
        target_weights={"AAPL": 0.0},
        close_prices={"AAPL": 100},
        current_positions={"AAPL": 2},
    )
    executor = _executor(
        plan,
        buying_power=0,
        positions={"AAPL": {"qty": 2.0, "market_value": 200.0}},
        assets={"AAPL": _asset(tradable=False)},
    )

    report = executor.preflight_opg_plan(plan)

    assert report["approved"] is True
    assert report["buy_orders"] == 0
    assert report["sell_orders"] == 1
    assert report["estimated_buy_notional"] == 0


def test_preflight_rejects_position_drift_since_sizing():
    plan = _plan()
    executor = _executor(
        plan,
        positions={"AAPL": {"qty": 1.0, "market_value": 100.0}},
    )

    with pytest.raises(RuntimeError, match="position|drift"):
        executor.preflight_opg_plan(plan)


def test_preflight_rejects_new_unmodeled_nonzero_position():
    plan = _plan()
    executor = _executor(
        plan,
        positions={"AAPL": {"qty": 0.0, "market_value": 0.0}, "NVDA": {"qty": 1.0, "market_value": 100.0}},
    )

    with pytest.raises(RuntimeError, match="unmodeled|outside"):
        executor.preflight_opg_plan(plan)


def test_sell_only_batch_does_not_require_positive_buying_power():
    plan = _plan(
        target_weights={"AAPL": 0.0},
        close_prices={"AAPL": 100},
        current_positions={"AAPL": 2},
    )
    executor = _executor(
        plan,
        buying_power=0,
        positions={"AAPL": {"qty": 2.0, "market_value": 200.0}},
    )

    report = executor.preflight_opg_plan(plan)

    assert report["approved"] is True
    assert report["estimated_buy_notional"] == 0
    assert report["buying_power_headroom"] == 0


def test_execute_opg_plan_runs_preflight_before_persist_or_submit():
    from src.execution.broker_executor import BrokerExecutor

    plan = _plan()
    executor = BrokerExecutor.__new__(BrokerExecutor)
    executor.state = {
        "account_mode": "paper",
        "account_id": "paper-account-1",
        "opg_batches": {},
    }
    executor.paper_trading = True
    executor._current_account_identity_verified = True
    executor.preflight_opg_plan = Mock(side_effect=RuntimeError("preflight blocked"))
    executor._persist_opg_batch = Mock()
    executor.trading_client = SimpleNamespace(
        submit_order=Mock(side_effect=AssertionError("must not submit"))
    )

    with pytest.raises(RuntimeError, match="preflight blocked"):
        executor.execute_opg_plan(
            plan, signal_bar_at="2026-11-03T21:00:00Z", target_session="2026-11-04"
        )

    executor._persist_opg_batch.assert_not_called()
    executor.trading_client.submit_order.assert_not_called()


def test_preflight_blocks_projected_target_gross_above_one_thousand_usd():
    from src.execution.opg_sizing import build_opg_sizing_plan

    plan = build_opg_sizing_plan(
        equity=2000,
        target_weights={"AAPL": 0.60, "MSFT": 0.40},
        close_prices={"AAPL": 100, "MSFT": 100},
        current_positions={"AAPL": 0, "MSFT": 0},
    )
    executor = _executor(plan, buying_power=2000)

    with pytest.raises(RuntimeError, match="exceeds hard cap"):
        executor.preflight_opg_plan(plan)

    executor.trading_client.submit_order.assert_not_called()


def test_preflight_adds_full_partial_fill_buy_reserve_to_projected_positions():
    plan = _plan(
        target_weights={"AAPL": 0.50},
        close_prices={"AAPL": 100},
        current_positions={"AAPL": 0},
    )
    executor = _executor(plan, buying_power=2000)
    executor._get_open_orders = Mock(return_value=[
        SimpleNamespace(
            id="partial-msft", symbol="MSFT", side="buy",
            status="partially_filled", qty="6", filled_qty="3",
            limit_price="100", notional=None,
        )
    ])

    with pytest.raises(RuntimeError, match="exceeds hard cap"):
        executor.preflight_opg_plan(plan)

    executor.trading_client.submit_order.assert_not_called()


def test_preflight_does_not_count_pending_sell_as_new_gross_exposure():
    plan = _plan(
        target_weights={"AAPL": 0.80},
        close_prices={"AAPL": 100},
        current_positions={"AAPL": 0},
    )
    executor = _executor(plan, buying_power=2000)
    executor._get_open_orders = Mock(return_value=[
        SimpleNamespace(
            id="pending-sell", symbol="MSFT", side="sell",
            status="accepted", qty="20", filled_qty="0",
            limit_price="100", notional=None,
        )
    ])

    report = executor.preflight_opg_plan(plan)

    assert report["projected_gross_notional"] == 800
    assert report["pending_buy_notional"] == 0
    executor.trading_client.submit_order.assert_not_called()


def test_preflight_allows_sell_only_reduction_from_above_hard_cap():
    plan = _plan(
        target_weights={"AAPL": 0.0},
        close_prices={"AAPL": 100},
        current_positions={"AAPL": 20},
    )
    executor = _executor(
        plan,
        buying_power=0,
        positions={"AAPL": {"qty": 20.0, "market_value": 2000.0}},
    )

    report = executor.preflight_opg_plan(plan)

    assert report["approved"] is True
    assert report["buy_orders"] == 0
    assert report["sell_orders"] == 1
    assert report["projected_gross_notional"] == 2000
    assert report["cap_check_applied"] is False


def test_preflight_does_not_credit_unfilled_sells_to_fund_new_buys():
    from src.execution.opg_sizing import build_opg_sizing_plan

    plan = build_opg_sizing_plan(
        equity=2000,
        target_weights={"AAPL": 0.0, "MSFT": 0.10},
        close_prices={"AAPL": 100, "MSFT": 100},
        current_positions={"AAPL": 15, "MSFT": 0},
    )
    executor = _executor(
        plan,
        buying_power=2000,
        positions={
            "AAPL": {"qty": 15.0, "market_value": 1500.0},
            "MSFT": {"qty": 0.0, "market_value": 0.0},
        },
    )

    with pytest.raises(RuntimeError, match="exceeds hard cap"):
        executor.preflight_opg_plan(plan)

    executor.trading_client.submit_order.assert_not_called()


def test_preflight_blocks_new_order_when_same_symbol_has_active_broker_order():
    plan = _plan()
    executor = _executor(plan)
    executor._get_open_orders = Mock(return_value=[
        SimpleNamespace(
            id="existing-sell", symbol="AAPL", side="sell",
            status="accepted", qty="1", filled_qty="0",
            limit_price="100", notional=None,
        )
    ])

    with pytest.raises(RuntimeError, match="already exists for planned symbol"):
        executor.preflight_opg_plan(plan)

    executor.trading_client.submit_order.assert_not_called()


def test_preflight_blocks_terminal_day_fill_until_position_reconciliation():
    plan = _plan()
    executor = _executor(plan)
    executor.state["order_submission_confirmed"] = {
        "day-order-1": {
            "status": "filled",
            "position_reconciled": False,
        }
    }

    with pytest.raises(RuntimeError, match="DAY order reconciliation"):
        executor.preflight_opg_plan(plan)

    executor.trading_client.submit_order.assert_not_called()


def test_preflight_blocks_when_day_submission_state_is_unresolved():
    plan = _plan()
    executor = _executor(plan)
    executor.state["order_submission_unresolved"] = {
        "MSFT": {"client_order_id": "unknown-day-submit"}
    }

    with pytest.raises(RuntimeError, match="DAY order reconciliation"):
        executor.preflight_opg_plan(plan)

    executor.trading_client.submit_order.assert_not_called()


def test_preflight_has_no_broker_mutation_side_effects():
    plan = _plan()
    executor = _executor(plan)

    report = executor.preflight_opg_plan(plan)

    assert report["approved"] is True
    executor.trading_client.submit_order.assert_not_called()
    executor.trading_client.cancel_order_by_id.assert_not_called()
    executor.trading_client.replace_order_by_id.assert_not_called()
    executor.trading_client.close_position.assert_not_called()


def test_preflight_does_not_fetch_assets_when_plan_has_no_orders():
    plan = _plan(
        target_weights={"AAPL": 0.30},
        close_prices={"AAPL": 100},
        current_positions={"AAPL": 3},
    )
    executor = _executor(
        plan,
        buying_power=0,
        positions={"AAPL": {"qty": 3.0, "market_value": 300.0}},
        assets={},
    )

    report = executor.preflight_opg_plan(plan)

    assert report["approved"] is True
    assert report["buy_orders"] == 0
    assert report["sell_orders"] == 0
    executor.trading_client.get_asset.assert_not_called()



def test_preflight_accepts_operationally_scaled_plan_with_larger_strategic_equity():
    from src.execution.opg_sizing import build_opg_sizing_plan

    plan = build_opg_sizing_plan(
        equity=100000,
        sizing_capital=1000,
        target_weights={"AAPL": 0.20, "MSFT": 0.15},
        close_prices={"AAPL": 100, "MSFT": 50},
        current_positions={"AAPL": 0, "MSFT": 0},
    )
    executor = _executor(plan, buying_power=1000, equity=100000)

    report = executor.preflight_opg_plan(plan)

    assert report["approved"] is True
    assert report["equity"] == pytest.approx(100000.0)
    assert report["estimated_buy_notional"] == pytest.approx(350.0)
    assert report["projected_gross_notional"] == pytest.approx(350.0)
    executor.trading_client.submit_order.assert_not_called()


def test_preflight_rejects_account_equity_drift_after_sizing():
    plan = _plan()
    executor = _executor(plan, equity=2000)

    with pytest.raises(RuntimeError, match="equity changed"):
        executor.preflight_opg_plan(plan)

    executor.trading_client.submit_order.assert_not_called()


def test_preflight_rejects_sizing_capital_that_does_not_match_operational_budget():
    from src.execution.opg_sizing import build_opg_sizing_plan

    plan = build_opg_sizing_plan(
        equity=1000,
        sizing_capital=500,
        target_weights={"AAPL": 0.20},
        close_prices={"AAPL": 100},
        current_positions={"AAPL": 0},
    )
    executor = _executor(plan, equity=1000)

    with pytest.raises(RuntimeError, match="operational budget"):
        executor.preflight_opg_plan(plan)

    executor.trading_client.submit_order.assert_not_called()
