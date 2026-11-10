"""Tests for execution-aligned equal-weight buy-and-hold benchmarking."""

import numpy as np
import pandas as pd
import pytest

from src.backtest.benchmark import evaluate_equal_weight_buy_and_hold


def _prices(rows=4):
    index = pd.date_range("2024-01-02", periods=rows, freq="B")
    return pd.DataFrame(
        {
            "Open_A": [10.0, 10.0, 11.0, 12.0][:rows],
            "Close_A": [10.0, 11.0, 12.0, 13.0][:rows],
            "Open_B": [20.0, 20.0, 20.0, 20.0][:rows],
            "Close_B": [20.0, 20.0, 20.0, 20.0][:rows],
        },
        index=index,
    )


def _run(data):
    return evaluate_equal_weight_buy_and_hold(
        data,
        ["A", "B"],
        initial_capital=1000.0,
        transaction_cost=0.001,
        slippage=0.0005,
        max_position_size=0.2,
        max_gross_exposure=0.5,
    )


def test_equal_weight_benchmark_uses_same_caps_and_keeps_residual_cash():
    result = _run(_prices())
    assert result["target_weights"] == pytest.approx([0.2, 0.2])
    assert result["gross_target_exposure"] == pytest.approx(0.4)
    assert result["holding_policy"] == "buy_and_hold_no_rebalancing"


def test_equal_weight_benchmark_enters_next_open_and_pays_cost_once():
    result = _run(_prices())
    expected_turnover = 0.4
    expected_cost = 1000.0 * expected_turnover * 0.0015
    assert result["entry_execution"] == "next_bar_open"
    assert result["total_transaction_costs"] == pytest.approx(expected_cost)
    assert result["total_turnover"] == pytest.approx(expected_turnover)
    assert result["average_turnover"] == pytest.approx(expected_turnover / 3.0)


def test_equal_weight_benchmark_does_not_rebalance_after_initial_entry():
    result = _run(_prices())
    assert result["total_turnover"] == pytest.approx(0.4)
    assert result["total_transaction_costs"] == pytest.approx(0.6)


@pytest.mark.parametrize(
    "data",
    [
        pd.DataFrame(),
        pd.DataFrame(
            {"Open_A": [10.0], "Close_A": [10.0]},
            index=pd.date_range("2024-01-02", periods=1),
        ),
    ],
)
def test_equal_weight_benchmark_rejects_too_few_observations(data):
    with pytest.raises(ValueError, match="at least two"):
        evaluate_equal_weight_buy_and_hold(
            data,
            ["A"],
            initial_capital=1000.0,
            transaction_cost=0.001,
            slippage=0.0005,
            max_position_size=0.2,
            max_gross_exposure=0.5,
        )


def test_equal_weight_benchmark_rejects_invalid_prices():
    data = _prices()
    data.loc[data.index[1], "Open_A"] = np.nan
    with pytest.raises(ValueError, match="finite"):
        _run(data)


def test_single_asset_spy_benchmark_uses_requested_exposure_without_ppo_position_cap():
    from src.backtest.benchmark import evaluate_single_asset_buy_and_hold

    data = _prices().rename(columns={"Open_A": "Open_SPY", "Close_A": "Close_SPY"})
    for exposure in (0.5, 1.0):
        result = evaluate_single_asset_buy_and_hold(
            data, "SPY", exposure=exposure, initial_capital=1000.0,
            transaction_cost=0.001, slippage=0.0005,
        )
        assert result["target_weights"] == pytest.approx([exposure])
        assert result["gross_target_exposure"] == pytest.approx(exposure)
        assert result["target_exposure"] == pytest.approx(exposure)
        assert result["entry_execution"] == "next_bar_open"


@pytest.mark.parametrize("exposure", [0, -0.1, 1.01, float("nan"), True])
def test_single_asset_benchmark_rejects_invalid_exposure(exposure):
    from src.backtest.benchmark import evaluate_single_asset_buy_and_hold

    with pytest.raises(ValueError, match="exposure"):
        evaluate_single_asset_buy_and_hold(
            _prices().rename(columns={"Open_A": "Open_SPY", "Close_A": "Close_SPY"}),
            "SPY", exposure=exposure, initial_capital=1000.0,
            transaction_cost=0.001, slippage=0.0005,
        )


def test_equal_weight_benchmark_opt_in_timeseries_tracks_drifted_exposure():
    result = evaluate_equal_weight_buy_and_hold(
        _prices(), ["A", "B"], initial_capital=1000.0,
        transaction_cost=0.0, slippage=0.0, max_position_size=0.2,
        max_gross_exposure=0.5, include_timeseries=True,
    )
    assert result["timeseries_contract"] == "benchmark_daily_exposure_v1"
    assert result["tickers"] == ["A", "B"]
    assert len(result["timeseries"]) == result["steps"] == 3
    first_close = result["timeseries"][0]["close_weights"]
    final_close = result["timeseries"][-1]["close_weights"]
    # A appreciates 10% between entry at Open and the first Close; close
    # weights therefore differ from the equal target weights at execution.
    assert first_close == pytest.approx([220.0 / 1020.0, 200.0 / 1020.0])
    assert final_close[0] > first_close[0]
    assert final_close[1] < first_close[1]
    assert result["timeseries"][-1]["turnover"] == pytest.approx(0.0)
