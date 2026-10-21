import numpy as np
import pytest
from src.strategy.risk_overlay import RiskOverlayModule


def test_risk_overlay_creation():
    overlay = RiskOverlayModule(
        max_position_size=0.2, vix_threshold_high=30, vix_threshold_low=15
    )
    assert overlay.max_position_size == 0.2


def test_vix_scaling():
    overlay = RiskOverlayModule()
    assert overlay._calculate_vix_scaling(12) == 1.0
    assert overlay._calculate_vix_scaling(35) == 0.5
    scale_mid = overlay._calculate_vix_scaling(22)
    assert 0.5 < scale_mid < 1.0


def test_position_cap_survives_vix_scaling_without_renormalization():
    overlay = RiskOverlayModule(max_position_size=0.2)
    result = overlay.apply(
        None, np.array([0.8, 0.8, -0.7]), ["A", "B", "C"], vix=35
    )
    np.testing.assert_allclose(result, np.array([0.1, 0.1, -0.1]))
    assert np.max(np.abs(result)) <= 0.2
    assert np.abs(result).sum() == pytest.approx(0.3)


def test_low_vix_keeps_caps_and_drawdown_circuit_breaker():
    overlay = RiskOverlayModule(max_position_size=0.2, max_drawdown_threshold=0.15)
    result = overlay.apply(None, np.array([0.8, 0.1]), ["A", "B"], vix=12)
    np.testing.assert_allclose(result, np.array([0.2, 0.1]))
    stopped = overlay.apply(
        None, np.array([0.1, 0.1]), ["A", "B"], vix=12, portfolio_value=80000
    )
    np.testing.assert_array_equal(stopped, np.zeros(2))


def test_invalid_weights_and_vix_fail_explicitly():
    overlay = RiskOverlayModule(max_position_size=0.2)
    with pytest.raises(ValueError, match="finite"):
        overlay.apply(None, np.array([np.nan]), ["A"])
    with pytest.raises(ValueError, match="vix"):
        overlay.apply(None, np.array([0.1]), ["A"], vix=float("nan"))
    with pytest.raises(ValueError, match="Expected 1 weights"):
        overlay.apply(None, np.array([0.1, 0.2]), ["A"])
