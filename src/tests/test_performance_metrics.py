import numpy as np
import pytest

from src.backtest.performance_metrics import calculate_performance_metrics


def test_performance_metrics_calculate_returns_and_risk_ratios():
    metrics = calculate_performance_metrics(
        [100.0, 110.0, 99.0, 108.9],
        periods_per_year=3,
        turnover=[0.2, 0.1, 0.3],
        transaction_costs=[3.0, 1.0, 2.0],
    )
    assert metrics["total_return"] == pytest.approx(0.089)
    assert metrics["cagr"] == pytest.approx(0.089)
    assert metrics["annualized_volatility"] == pytest.approx(0.2)
    assert metrics["sharpe_ratio"] == pytest.approx(0.5)
    assert metrics["sortino_ratio"] == pytest.approx(1.0)
    assert metrics["max_drawdown"] == pytest.approx(-0.1)
    assert metrics["calmar_ratio"] == pytest.approx(0.89)
    assert metrics["total_turnover"] == pytest.approx(0.6)
    assert metrics["average_turnover"] == pytest.approx(0.2)
    assert metrics["total_transaction_costs"] == pytest.approx(6.0)
    assert metrics["periods"] == 3


def test_performance_metrics_apply_annual_risk_free_rate_to_each_period():
    metrics = calculate_performance_metrics(
        [100.0, 100.0, 110.0],
        periods_per_year=2,
        risk_free_rate=0.1025,
    )
    assert metrics["sharpe_ratio"] == pytest.approx(0.0, abs=1e-10)
    # The first excess return is negative, so downside deviation is defined;
    # the zero mean excess return therefore produces a zero Sortino ratio.
    assert metrics["sortino_ratio"] == pytest.approx(0.0, abs=1e-10)


def test_performance_metrics_mark_undefined_ratios_as_none():
    metrics = calculate_performance_metrics([100.0, 100.0, 100.0])
    assert metrics["sharpe_ratio"] is None
    assert metrics["sortino_ratio"] is None
    assert metrics["calmar_ratio"] is None


@pytest.mark.parametrize(
    "equity_curve",
    [[], [100.0], [100.0, 0.0], [100.0, np.nan], [100.0, np.inf]],
)
def test_performance_metrics_reject_invalid_equity_curves(equity_curve):
    with pytest.raises(ValueError, match="equity_curve"):
        calculate_performance_metrics(equity_curve)


def test_performance_metrics_reject_invalid_frequency_risk_free_rate_and_diagnostics():
    with pytest.raises(ValueError, match="periods_per_year"):
        calculate_performance_metrics([100.0, 101.0], periods_per_year=0)
    with pytest.raises(ValueError, match="risk_free_rate"):
        calculate_performance_metrics([100.0, 101.0], risk_free_rate=-1.0)
    with pytest.raises(ValueError, match="turnover"):
        calculate_performance_metrics([100.0, 101.0], turnover=[-0.1])
    with pytest.raises(ValueError, match="transaction_costs"):
        calculate_performance_metrics([100.0, 101.0], transaction_costs=[np.nan])
