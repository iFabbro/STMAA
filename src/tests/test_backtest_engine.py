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


def test_max_drawdown_includes_initial_rebalance_cost():
    prices = pd.DataFrame(
        {"Close_A": [100.0, 100.0]},
        index=pd.date_range("2024-01-01", periods=2, freq="D"),
    )
    engine = ExecutionConsistentBacktest(
        initial_capital=10000, transaction_cost=0.01, slippage=0.02
    )
    result = engine.run_backtest(
        prices, lambda _ts, _row: np.array([0.5]), ["A"]
    )
    assert result["portfolio_values"].iloc[0] == pytest.approx(9850.0)
    assert result["metrics"]["max_drawdown"] == pytest.approx(-0.015)


def test_turnover_uses_drifted_weights_after_market_returns():
    prices = pd.DataFrame(
        {"Close_A": [100.0, 200.0]},
        index=pd.date_range("2024-01-01", periods=2, freq="D"),
    )
    engine = ExecutionConsistentBacktest(
        initial_capital=10000, transaction_cost=0.01, slippage=0.02
    )
    targets = iter([np.array([0.5]), np.array([2.0 / 3.0])])
    result = engine.run_backtest(
        prices, lambda _ts, _row: next(targets), ["A"]
    )
    # First rebalance costs 150; the 100% price rise drifts the holding to
    # 2/3 of equity, so the matching second target creates no extra turnover.
    assert result["portfolio_values"].iloc[0] == pytest.approx(9850.0)
    assert result["portfolio_values"].iloc[-1] == pytest.approx(14775.0)


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


def test_backtest_rejects_unsorted_timestamps():
    dates = pd.to_datetime(["2024-01-02", "2024-01-01"])
    prices = pd.DataFrame({"Close_A": [101.0, 100.0]}, index=dates)
    engine = ExecutionConsistentBacktest(transaction_cost=0, slippage=0)
    with pytest.raises(ValueError, match="chronological order"):
        engine.run_backtest(
            prices, lambda _ts, _row: np.array([1.0]), ["A"]
        )


def test_sharpe_and_sortino_use_periodic_returns_not_cagr():
    engine = ExecutionConsistentBacktest(initial_capital=100.0)
    values = pd.Series([101.0, 100.0, 102.0])
    metrics = engine._calculate_metrics(values)

    equity = np.array([100.0, 101.0, 100.0, 102.0])
    returns = equity[1:] / equity[:-1] - 1.0
    expected_sharpe = returns.mean() / returns.std(ddof=1) * np.sqrt(252)
    downside = np.minimum(returns, 0.0)
    expected_sortino = returns.mean() / np.sqrt(np.mean(downside ** 2)) * np.sqrt(252)

    assert metrics["sharpe_ratio"] == pytest.approx(expected_sharpe)
    assert metrics["sortino_ratio"] == pytest.approx(expected_sortino)
    assert metrics["cumulative_return"] == pytest.approx(0.02)
