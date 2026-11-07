from decimal import Decimal

import pytest

from src.execution.opg_sizing import build_opg_sizing_plan


def test_integer_sizing_floors_target_quantities_and_records_residuals():
    plan = build_opg_sizing_plan(
        equity=5000.0,
        target_weights={"A": 0.20, "B": 0.15, "C": 0.10},
        close_prices={"A": 90.0, "B": 160.0, "C": 700.0},
        current_positions={"A": 3, "B": 5, "C": 0},
    )

    assert plan.contract == "opg_moo_integer_floor_v2_operational_cap"
    assert plan.model_contract_version == 7
    assert plan.equity == pytest.approx(5000.0)
    assert plan.assets["A"].target_quantity == 11
    assert plan.assets["B"].target_quantity == 4
    assert plan.assets["C"].target_quantity == 0
    assert plan.assets["A"].delta_quantity == 8
    assert plan.assets["B"].delta_quantity == -1
    assert plan.assets["C"].delta_quantity == 0
    assert plan.assets["A"].side == "buy"
    assert plan.assets["B"].side == "sell"
    assert plan.assets["C"].side is None

    assert plan.assets["A"].target_notional == pytest.approx(1000.0)
    assert plan.assets["A"].integer_notional == pytest.approx(990.0)
    assert plan.assets["A"].rounding_residual == pytest.approx(10.0)
    assert plan.assets["B"].rounding_residual == pytest.approx(110.0)
    assert plan.assets["C"].rounding_residual == pytest.approx(500.0)
    assert plan.total_target_notional == pytest.approx(2250.0)
    assert plan.total_integer_notional == pytest.approx(1630.0)
    assert plan.total_rounding_residual == pytest.approx(620.0)
    assert plan.integer_gross_weight == pytest.approx(0.326)
    assert plan.target_gross_weight == pytest.approx(0.45)


def test_each_asset_rounding_residual_is_non_negative_and_less_than_close():
    plan = build_opg_sizing_plan(
        equity=12345.67,
        target_weights={"A": 0.17, "B": 0.23},
        close_prices={"A": 37.25, "B": 101.10},
        current_positions={"A": 0, "B": 0},
    )

    for asset in plan.assets.values():
        assert 0 <= asset.rounding_residual < asset.close_price
        assert asset.target_quantity == int(
            Decimal(str(asset.target_notional)) / Decimal(str(asset.close_price))
        )


def test_exact_integer_target_has_zero_rounding_residual():
    plan = build_opg_sizing_plan(
        equity=1000,
        target_weights={"A": 0.2},
        close_prices={"A": 100},
        current_positions={"A": 0},
    )

    asset = plan.assets["A"]
    assert asset.target_quantity == 2
    assert asset.rounding_residual == pytest.approx(0.0)
    assert asset.deviation_weight == pytest.approx(0.0)


def test_weight_deviation_is_integer_weight_minus_ppo_v7_target():
    plan = build_opg_sizing_plan(
        equity=1000,
        target_weights={"A": 0.25},
        close_prices={"A": 300},
        current_positions={"A": 0},
    )

    asset = plan.assets["A"]
    assert asset.target_quantity == 0
    assert asset.integer_weight == pytest.approx(0.0)
    assert asset.deviation_weight == pytest.approx(-0.25)


def test_target_weight_cash_residual_is_not_redistributed():
    plan = build_opg_sizing_plan(
        equity=1000,
        target_weights={"A": 0.10, "B": 0.20},
        close_prices={"A": 100, "B": 100},
        current_positions={"A": 0, "B": 0},
    )

    assert plan.assets["A"].target_quantity == 1
    assert plan.assets["B"].target_quantity == 2
    assert plan.total_target_notional == pytest.approx(300.0)
    assert plan.total_rounding_residual == pytest.approx(0.0)
    assert plan.target_cash_weight == pytest.approx(0.70)


def test_plan_serialization_records_model_target_and_execution_deviation():
    plan = build_opg_sizing_plan(
        equity=1000,
        target_weights={"A": 0.25},
        close_prices={"A": 300},
        current_positions={"A": 0},
    )

    record = plan.to_dict()
    assert record["model_contract_version"] == 7
    assert record["execution_sizing_contract"] == "opg_moo_integer_floor_v2_operational_cap"
    assert record["sizing_capital"] == pytest.approx(1000.0)
    assert record["assets"]["A"]["ppo_target_weight"] == pytest.approx(0.25)
    assert record["assets"]["A"]["integer_sized_weight"] == pytest.approx(0.0)
    assert record["assets"]["A"]["weight_deviation"] == pytest.approx(-0.25)
    assert record["assets"]["A"]["target_quantity"] == 0
    assert record["totals"]["rounding_residual"] == pytest.approx(250.0)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"equity": 0}, "equity"),
        ({"equity": -1}, "equity"),
        ({"equity": float("nan")}, "equity"),
        ({"target_weights": {"A": -0.1}}, "target weight"),
        ({"target_weights": {"A": 1.1}}, "target weight"),
        ({"target_weights": {"A": float("inf")}}, "target weight"),
        ({"close_prices": {"A": 0}}, "close price"),
        ({"close_prices": {"A": float("nan")}}, "close price"),
        ({"current_positions": {"A": -1}}, "short"),
        ({"current_positions": {"A": 0.5}}, "fractional"),
        ({"current_positions": {"A": float("nan")}}, "position"),
    ],
)
def test_invalid_inputs_fail_closed(kwargs, message):
    arguments = {
        "equity": 1000,
        "target_weights": {"A": 0.2},
        "close_prices": {"A": 100},
        "current_positions": {"A": 0},
    }
    arguments.update(kwargs)
    with pytest.raises(ValueError, match=message):
        build_opg_sizing_plan(**arguments)


@pytest.mark.parametrize(
    ("target_weights", "close_prices", "current_positions"),
    [
        ({"A": 0.2, "B": 0.1}, {"A": 100}, {"A": 0, "B": 0}),
        ({"A": 0.2}, {"A": 100, "B": 50}, {"A": 0}),
    ],
)
def test_target_weight_and_close_price_symbol_sets_must_match(
    target_weights, close_prices, current_positions
):
    with pytest.raises(ValueError, match="same symbols"):
        build_opg_sizing_plan(
            equity=1000,
            target_weights=target_weights,
            close_prices=close_prices,
            current_positions=current_positions,
        )


def test_total_target_weight_must_not_exceed_one():
    with pytest.raises(ValueError, match="gross target weight"):
        build_opg_sizing_plan(
            equity=1000,
            target_weights={"A": 0.7, "B": 0.4},
            close_prices={"A": 100, "B": 100},
            current_positions={"A": 0, "B": 0},
        )


def test_boolean_values_are_not_accepted_as_numeric_inputs():
    with pytest.raises(ValueError, match="equity"):
        build_opg_sizing_plan(
            equity=True,
            target_weights={"A": 0.2},
            close_prices={"A": 100},
            current_positions={"A": 0},
        )



def test_unmodeled_fractional_account_position_blocks_entire_batch():
    with pytest.raises(ValueError, match="fractional position for LEGACY"):
        build_opg_sizing_plan(
            equity=1000,
            target_weights={"A": 0.2},
            close_prices={"A": 100},
            current_positions={"A": 0, "LEGACY": 0.25},
        )


def test_unmodeled_whole_share_position_is_checked_but_not_included_in_plan():
    plan = build_opg_sizing_plan(
        equity=1000,
        target_weights={"A": 0.2},
        close_prices={"A": 100},
        current_positions={"A": 0, "LEGACY": 0},
    )

    assert list(plan.assets) == ["A"]
    assert plan.assets["A"].target_quantity == 2



def test_unmodeled_nonzero_account_position_blocks_instead_of_being_ignored():
    with pytest.raises(ValueError, match="unmodeled account position for LEGACY"):
        build_opg_sizing_plan(
            equity=1000,
            target_weights={"A": 0.2},
            close_prices={"A": 100},
            current_positions={"A": 0, "LEGACY": 2},
        )


def test_operational_sizing_cap_scales_targets_below_strategic_account_equity():
    plan = build_opg_sizing_plan(
        equity=100000.0,
        sizing_capital=1000.0,
        target_weights={"A": 0.20, "B": 0.15, "C": 0.08},
        close_prices={"A": 100.0, "B": 50.0, "C": 80.0},
        current_positions={"A": 0, "B": 0, "C": 0},
    )

    assert plan.equity == pytest.approx(100000.0)
    assert plan.sizing_capital == pytest.approx(1000.0)
    assert plan.assets["A"].target_notional == pytest.approx(200.0)
    assert plan.assets["A"].target_quantity == 2
    assert plan.assets["B"].target_notional == pytest.approx(150.0)
    assert plan.assets["B"].target_quantity == 3
    assert plan.assets["C"].target_notional == pytest.approx(80.0)
    assert plan.assets["C"].target_quantity == 1
    assert plan.total_target_notional == pytest.approx(430.0)
    assert plan.total_integer_notional == pytest.approx(430.0)
    assert plan.integer_gross_weight == pytest.approx(0.43)


def test_sizing_capital_must_be_positive_and_not_exceed_verified_equity():
    with pytest.raises(ValueError, match="sizing_capital"):
        build_opg_sizing_plan(
            equity=1000,
            sizing_capital=0,
            target_weights={"A": 0.2},
            close_prices={"A": 100},
            current_positions={"A": 0},
        )
    with pytest.raises(ValueError, match="sizing_capital"):
        build_opg_sizing_plan(
            equity=1000,
            sizing_capital=1001,
            target_weights={"A": 0.2},
            close_prices={"A": 100},
            current_positions={"A": 0},
        )
