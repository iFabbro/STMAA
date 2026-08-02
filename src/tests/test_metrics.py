import numpy as np
from src.utils.metrics import Metrics

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
