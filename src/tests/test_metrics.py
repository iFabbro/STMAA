import numpy as np
import pytest
from src.utils.metrics import Metrics, PortfolioMetrics
from src.utils.portfolio_metrics import PortfolioMetrics as StandalonePortfolioMetrics

def test_sharpe_annualized():
    r = np.array([0.01, 0.02, -0.01, 0.03])
    assert Metrics.sharpe(r, annualize=True, periods_per_year=252) != 0.0

def test_sortino_annualized():
    r = np.array([0.01, 0.02, -0.01, 0.03])
    assert Metrics.sortino(r, annualize=True, periods_per_year=252) != 0.0

def test_max_drawdown_basic():
    eq = np.array([100.0, 120.0, 110.0, 130.0, 90.0, 140.0])
    dd = Metrics.max_drawdown(eq)
    assert dd < 0.0
    assert round(dd, 2) == -0.31

def test_drawdown_duration_basic():
    eq = np.array([100.0, 120.0, 110.0, 105.0, 130.0, 90.0, 95.0, 140.0])
    assert Metrics.drawdown_duration(eq) >= 2

def test_total_return_basic():
    eq = np.array([100.0, 110.0, 120.0])
    assert round(Metrics.total_return(eq), 2) == 0.20



@pytest.mark.parametrize("metrics_class", [PortfolioMetrics, StandalonePortfolioMetrics])
@pytest.mark.parametrize("capital", [0, -1, float("nan"), float("inf")])
def test_portfolio_metrics_reject_invalid_initial_capital(metrics_class, capital):
    with pytest.raises(ValueError, match="initial_capital"):
        metrics_class(initial_capital=capital)


@pytest.mark.parametrize("metrics_class", [PortfolioMetrics, StandalonePortfolioMetrics])
@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), -float("inf")])
def test_portfolio_metrics_reject_invalid_equity_updates(metrics_class, value):
    metrics = metrics_class(initial_capital=1000)
    with pytest.raises(ValueError, match="portfolio_value"):
        metrics.update(value)
    assert metrics.equity_curve == [1000.0]



def test_standalone_portfolio_metrics_uses_shared_metric_definitions():
    metrics = StandalonePortfolioMetrics(initial_capital=1000)
    metrics.update(1100)
    result = metrics.get_all_metrics()
    assert result["total_return"] == pytest.approx(0.1)
    assert result["max_drawdown"] == pytest.approx(0.0)



def test_portfolio_metrics_implementations_share_equity_and_metric_contract():
    metrics = [
        PortfolioMetrics(initial_capital=1000),
        StandalonePortfolioMetrics(initial_capital=1000),
    ]
    for instance in metrics:
        instance.update(1100)
        instance.update(900)

    first, second = [instance.get_all_metrics() for instance in metrics]
    assert first.keys() == second.keys()
    for key in first:
        if isinstance(first[key], (int, float)):
            assert first[key] == pytest.approx(second[key])
        else:
            assert first[key] == second[key]
    assert metrics[0].equity_curve == metrics[1].equity_curve == [
        1000.0, 1100.0, 900.0
    ]
    assert first["total_return"] == pytest.approx(-0.1)
    assert first["max_drawdown"] == pytest.approx(-200 / 1100)
