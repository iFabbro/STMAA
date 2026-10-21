import numpy as np
import pandas as pd
import pytest
from src.backtest.backtest_engine import ExecutionConsistentBacktest


def test_backtest_engine_creation():
    engine = ExecutionConsistentBacktest(
        initial_capital=100000, transaction_cost=0.001, slippage=0.0005
    )
    assert engine.initial_capital == 100000


def test_metrics_calculation():
    engine = ExecutionConsistentBacktest()
    dates = pd.date_range("2024-01-01", periods=100, freq="D")
    values = pd.Series(
        100000 * np.exp(np.cumsum(np.random.default_rng(7).normal(0, 0.01, 100))),
        index=dates,
    )
    metrics = engine._calculate_metrics(values)
    assert "sharpe_ratio" in metrics
    assert "max_drawdown" in metrics
    assert "cumulative_return" in metrics


def test_multi_asset_returns_are_temporal_not_cross_sectional():
    prices = pd.DataFrame(
        {"Close_A": [100.0, 110.0, 121.0], "Close_B": [100.0, 90.0, 81.0]},
        index=pd.date_range("2024-01-01", periods=3, freq="D"),
    )
    engine = ExecutionConsistentBacktest(
        initial_capital=10000, transaction_cost=0, slippage=0
    )
    result = engine.run_backtest(
        prices, lambda _ts, _row: np.array([1.0, 0.0]), ["A", "B"]
    )
    assert result["portfolio_values"].iloc[0] == pytest.approx(10000)
    assert result["portfolio_values"].iloc[1] == pytest.approx(11000)
    assert result["portfolio_values"].iloc[2] == pytest.approx(12100)
    assert result["final_value"] == pytest.approx(12100)


def test_turnover_costs_are_charged_on_rebalance_not_as_weight_scaling():
    prices = pd.DataFrame(
        {"Close_A": [100.0, 100.0, 100.0]},
        index=pd.date_range("2024-01-01", periods=3, freq="D"),
    )
    engine = ExecutionConsistentBacktest(
        initial_capital=10000, transaction_cost=0.01, slippage=0.02
    )
    result = engine.run_backtest(
        prices, lambda _ts, _row: np.array([0.5]), ["A"]
    )
    assert result["portfolio_values"].iloc[0] == pytest.approx(9850.0)
    assert result["portfolio_values"].iloc[-1] == pytest.approx(9850.0)


def test_invalid_prices_or_weight_shapes_fail_explicitly():
    dates = pd.date_range("2024-01-01", periods=2, freq="D")
    invalid_prices = pd.DataFrame({"Close_A": [100.0, np.nan]}, index=dates)
    engine = ExecutionConsistentBacktest(transaction_cost=0, slippage=0)
    with pytest.raises(ValueError, match="Close prices"):
        engine.run_backtest(
            invalid_prices, lambda _ts, _row: np.array([1.0]), ["A"]
        )
    valid_prices = pd.DataFrame({"Close_A": [100.0, 101.0]}, index=dates)
    with pytest.raises(ValueError, match="Expected 1 target weights"):
        engine.run_backtest(
            valid_prices, lambda _ts, _row: np.array([0.5, 0.5]), ["A"]
        )
